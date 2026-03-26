"""
Anomaly detection logic for the unusual trading activity monitor.

Detection strategies
────────────────────
1. Volume Spike
   current_volume > threshold × rolling_average_volume

2. Price Velocity
   |price_change_pct| > threshold within the most recent bar

3. Cross-Asset Coordination
   ≥ N distinct asset categories show spikes simultaneously (suggests
   coordinated / macro-driven activity rather than single-stock news)

Each detector returns a list of :class:`Signal` objects.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf

from .assets import Asset, DEFAULT_WATCHLIST
from .config import get_config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class Signal:
    """A detected anomaly for a single asset."""
    asset: Asset
    signal_type: str          # "VOLUME_SPIKE" | "PRICE_VELOCITY" | "CROSS_ASSET"
    severity: str             # "HIGH" | "MEDIUM" | "LOW"
    description: str
    current_price: Optional[float] = None
    price_change_pct: Optional[float] = None
    current_volume: Optional[float] = None
    avg_volume: Optional[float] = None
    volume_ratio: Optional[float] = None
    detected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __str__(self) -> str:
        parts = [
            f"[{self.severity}] {self.signal_type} – {self.asset.name} "
            f"({self.asset.symbol})"
        ]
        if self.current_price is not None:
            parts.append(f"Price: {self.current_price:.4g}")
        if self.price_change_pct is not None:
            parts.append(f"Δ%: {self.price_change_pct:+.2f}%")
        if self.volume_ratio is not None:
            parts.append(f"Vol ratio: {self.volume_ratio:.1f}×")
        parts.append(f"[{self.detected_at.strftime('%H:%M:%S UTC')}]")
        return "  ".join(parts)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fetch_ohlcv(symbol: str, lookback_periods: int) -> Optional[pd.DataFrame]:
    """
    Download recent OHLCV bars for *symbol*.

    We fetch enough bars to compute a rolling average.  yfinance's '1d'
    interval is used for the long-term baseline, '5m' for the live bar.
    Returns a DataFrame with columns [Open, High, Low, Close, Volume] sorted
    ascending by time, or None on failure.
    """
    try:
        # Daily bars for the rolling baseline
        ticker = yf.Ticker(symbol)
        df = ticker.history(period="60d", interval="1d", auto_adjust=True)
        if df.empty:
            return None
        df.index = pd.to_datetime(df.index)
        return df[["Open", "High", "Low", "Close", "Volume"]].sort_index()
    except Exception as exc:
        logger.warning("Failed to fetch data for %s: %s", symbol, exc)
        return None


def _fetch_intraday(symbol: str, interval: str = "5m") -> Optional[pd.DataFrame]:
    """
    Download recent intraday bars for *symbol* (last trading session).
    Returns a DataFrame sorted ascending by time, or None on failure.
    """
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period="1d", interval=interval, auto_adjust=True)
        if df.empty:
            return None
        df.index = pd.to_datetime(df.index)
        return df[["Open", "High", "Low", "Close", "Volume"]].sort_index()
    except Exception as exc:
        logger.warning("Failed to fetch intraday data for %s: %s", symbol, exc)
        return None


def _rolling_avg_volume(daily_df: pd.DataFrame, n: int) -> float:
    """Compute the average daily volume over the last *n* completed bars."""
    if len(daily_df) < 2:
        return float("nan")
    # Exclude the most recent (possibly incomplete) bar
    history = daily_df["Volume"].iloc[-(n + 1) : -1]
    if history.empty or history.isna().all():
        return float("nan")
    return float(history.mean())


# ---------------------------------------------------------------------------
# Individual detectors
# ---------------------------------------------------------------------------

def detect_volume_spike(
    asset: Asset,
    daily_df: pd.DataFrame,
    intraday_df: Optional[pd.DataFrame],
    config=None,
) -> Optional[Signal]:
    """
    Return a Signal if the most recent trading volume is unusually high.

    The comparison uses the *intraday accumulated volume* (if available)
    normalised to a full-day equivalent, falling back to the last daily bar.
    """
    if config is None:
        config = get_config()

    avg_vol = _rolling_avg_volume(daily_df, config.volume_lookback_periods)
    if np.isnan(avg_vol) or avg_vol <= 0:
        return None

    # Prefer intraday accumulated volume (normalised to full day)
    current_vol: Optional[float] = None
    current_price: Optional[float] = None

    if intraday_df is not None and not intraday_df.empty:
        accumulated = float(intraday_df["Volume"].sum())
        # Normalise: estimate full-day volume based on elapsed fraction of day
        # We use the number of completed 5-min bars out of 78 (full US session)
        bars_completed = len(intraday_df)
        bars_in_day = 78  # 6.5 h × 12 bars/h
        elapsed_fraction = min(bars_completed / bars_in_day, 1.0)
        if elapsed_fraction > 0.05:  # at least 5 % of the day must have passed
            current_vol = accumulated / elapsed_fraction
        current_price = float(intraday_df["Close"].iloc[-1])

    if current_vol is None:
        # Fall back to last completed daily bar
        if len(daily_df) < 1:
            return None
        current_vol = float(daily_df["Volume"].iloc[-1])
        current_price = float(daily_df["Close"].iloc[-1])

    if current_vol is None or np.isnan(current_vol) or current_vol <= 0:
        return None

    ratio = current_vol / avg_vol
    if ratio < config.volume_spike_threshold:
        return None

    if ratio >= 5.0:
        severity = "HIGH"
    elif ratio >= config.volume_spike_threshold * 1.33:
        severity = "MEDIUM"
    else:
        severity = "LOW"

    return Signal(
        asset=asset,
        signal_type="VOLUME_SPIKE",
        severity=severity,
        description=(
            f"Volume is {ratio:.1f}× the {config.volume_lookback_periods}-period average "
            f"({current_vol:,.0f} vs avg {avg_vol:,.0f})"
        ),
        current_price=current_price,
        current_volume=current_vol,
        avg_volume=avg_vol,
        volume_ratio=ratio,
    )


def detect_price_velocity(
    asset: Asset,
    intraday_df: Optional[pd.DataFrame],
    config=None,
) -> Optional[Signal]:
    """
    Return a Signal if price has moved sharply in the most recent bar(s).

    We look at the percentage change from the open of the session to the
    latest price.
    """
    if config is None:
        config = get_config()

    if intraday_df is None or len(intraday_df) < 2:
        return None

    session_open = float(intraday_df["Open"].iloc[0])
    latest_close = float(intraday_df["Close"].iloc[-1])

    if session_open == 0:
        return None

    pct_change = (latest_close - session_open) / session_open * 100.0

    if abs(pct_change) < config.price_change_threshold_pct:
        return None

    abs_change = abs(pct_change)
    if abs_change >= config.price_change_threshold_pct * 3:
        severity = "HIGH"
    elif abs_change >= config.price_change_threshold_pct * 2:
        severity = "MEDIUM"
    else:
        severity = "LOW"

    direction = "up" if pct_change > 0 else "down"
    return Signal(
        asset=asset,
        signal_type="PRICE_VELOCITY",
        severity=severity,
        description=(
            f"Price moved {pct_change:+.2f}% ({direction}) from session open "
            f"{session_open:.4g} → {latest_close:.4g}"
        ),
        current_price=latest_close,
        price_change_pct=pct_change,
    )


def detect_cross_asset_coordination(
    signals: list[Signal],
    config=None,
) -> list[Signal]:
    """
    Given a list of already-detected signals, return additional CROSS_ASSET
    signals when multiple *different* asset categories are spiking at the
    same time.
    """
    if config is None:
        config = get_config()

    categories: dict[str, list[Signal]] = {}
    for sig in signals:
        categories.setdefault(sig.asset.category, []).append(sig)

    if len(categories) < config.cross_asset_min_categories:
        return []

    cat_names = ", ".join(sorted(categories.keys()))
    num_assets = sum(len(v) for v in categories.values())
    severity = "HIGH" if len(categories) >= 3 else "MEDIUM"

    # Create one synthetic cross-asset signal per spiking category
    extra: list[Signal] = []
    for cat, cat_signals in categories.items():
        representative = cat_signals[0]
        extra.append(
            Signal(
                asset=representative.asset,
                signal_type="CROSS_ASSET",
                severity=severity,
                description=(
                    f"Cross-asset coordination detected across {len(categories)} "
                    f"categories ({cat_names}) — {num_assets} instruments spiking "
                    "simultaneously"
                ),
            )
        )

    return extra


# ---------------------------------------------------------------------------
# Top-level scanner
# ---------------------------------------------------------------------------

def scan_watchlist(
    watchlist: Optional[list[Asset]] = None,
    config=None,
) -> list[Signal]:
    """
    Scan every asset in *watchlist* and return all detected signals.

    This is the main entry-point called by the monitor loop.
    """
    if config is None:
        config = get_config()
    if watchlist is None:
        watchlist = DEFAULT_WATCHLIST

    signals: list[Signal] = []

    for asset in watchlist:
        daily_df = _fetch_ohlcv(asset.symbol, config.volume_lookback_periods)
        if daily_df is None:
            continue

        intraday_df = _fetch_intraday(asset.symbol, interval="5m")

        vol_signal = detect_volume_spike(asset, daily_df, intraday_df, config)
        if vol_signal:
            signals.append(vol_signal)
            logger.info("Volume spike detected: %s", vol_signal)

        price_signal = detect_price_velocity(asset, intraday_df, config)
        if price_signal:
            signals.append(price_signal)
            logger.info("Price velocity detected: %s", price_signal)

    # Cross-asset analysis after individual scans
    cross_signals = detect_cross_asset_coordination(signals, config)
    signals.extend(cross_signals)
    for sig in cross_signals:
        logger.info("Cross-asset signal: %s", sig)

    return signals
