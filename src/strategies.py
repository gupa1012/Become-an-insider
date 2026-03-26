"""
Stock strategy tools derived from the academic paper
"151 Trading Strategies" (Kakushadze & Serur, 2018) – SSRN 3247865.

Implemented strategies (stock-focused, Chapter 3 of the paper)
──────────────────────────────────────────────────────────────
1. Price Momentum          (§ 3.1)
2. Mean Reversion          (§ 3.9)
3. Moving-Average Crossover (§ 3.12)
4. Support & Resistance     (§ 3.14)
5. Pairs Trading            (§ 3.8)

Data source
───────────
All strategies consume daily OHLCV bars fetched via **yfinance** (Yahoo
Finance).  No API key is required.  The helper ``fetch_daily_history``
wraps ``yf.Ticker.history`` and returns a pandas DataFrame sorted
ascending by date with columns [Open, High, Low, Close, Volume].
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def fetch_daily_history(
    symbol: str,
    period: str = "1y",
) -> Optional[pd.DataFrame]:
    """
    Download daily OHLCV history for *symbol*.

    Parameters
    ----------
    symbol : str
        Ticker symbol understood by yfinance (e.g. ``"AAPL"``).
    period : str
        Look-back window accepted by yfinance (e.g. ``"6mo"``, ``"1y"``).

    Returns
    -------
    pd.DataFrame | None
        DataFrame with columns ``[Open, High, Low, Close, Volume]``,
        sorted ascending by date.  ``None`` on failure.
    """
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period=period, interval="1d", auto_adjust=True)
        if df.empty:
            return None
        df.index = pd.to_datetime(df.index)
        return df[["Open", "High", "Low", "Close", "Volume"]].sort_index()
    except Exception as exc:
        logger.warning("Failed to fetch history for %s: %s", symbol, exc)
        return None


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class StrategyResult:
    """Holds the output of a strategy analysis for a single symbol."""

    symbol: str
    strategy: str
    signal: str            # "BUY" | "SELL" | "HOLD" | "NEUTRAL"
    score: float           # Numeric score (interpretation depends on strategy)
    details: dict = field(default_factory=dict)
    computed_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def __str__(self) -> str:
        ts = self.computed_at.strftime("%Y-%m-%d %H:%M UTC")
        parts = [
            f"[{self.strategy}] {self.symbol}: {self.signal} "
            f"(score={self.score:+.4f})  [{ts}]",
        ]
        for k, v in self.details.items():
            if isinstance(v, float):
                parts.append(f"  {k}: {v:.4g}")
            else:
                parts.append(f"  {k}: {v}")
        return "\n".join(parts)


# ---------------------------------------------------------------------------
# 1. Price Momentum  (§ 3.1)
# ---------------------------------------------------------------------------

def momentum_screen(
    symbols: list[str],
    formation_months: int = 12,
    skip_months: int = 1,
    top_n: int = 5,
    bottom_n: int = 5,
) -> list[StrategyResult]:
    """
    Rank stocks by cumulative return over a *formation_months* look-back
    window, skipping the most recent *skip_months* (to avoid the short-term
    mean-reversion effect – see Jegadeesh & Titman, 1993).

    Returns the **top_n** winners (BUY) and **bottom_n** losers (SELL),
    sorted by score descending.

    Data source: yfinance daily close prices.

    Parameters
    ----------
    symbols : list[str]
        Ticker symbols to screen.
    formation_months : int
        Number of months in the look-back formation period (default 12).
    skip_months : int
        Recent months to skip (default 1).
    top_n, bottom_n : int
        How many winners / losers to return.
    """
    # Convert months to approximate trading days
    formation_days = formation_months * 21
    skip_days = skip_months * 21
    total_days = formation_days + skip_days + 5  # small buffer

    results: list[StrategyResult] = []

    for sym in symbols:
        df = fetch_daily_history(sym, period=f"{int(total_days * 1.5 / 21 + 1)}mo")
        if df is None or len(df) < formation_days + skip_days:
            continue

        closes = df["Close"]
        # Skip most recent skip_days
        end_idx = len(closes) - skip_days if skip_days > 0 else len(closes)
        start_idx = max(end_idx - formation_days, 0)

        if start_idx >= end_idx or end_idx < 1:
            continue

        p_start = float(closes.iloc[start_idx])
        p_end = float(closes.iloc[end_idx - 1])
        if p_start <= 0:
            continue

        cum_return = (p_end / p_start) - 1.0

        results.append(StrategyResult(
            symbol=sym,
            strategy="MOMENTUM",
            signal="HOLD",     # will be updated below
            score=cum_return,
            details={
                "formation_months": formation_months,
                "skip_months": skip_months,
                "start_price": p_start,
                "end_price": p_end,
                "cumulative_return_pct": cum_return * 100,
            },
        ))

    # Sort by score descending
    results.sort(key=lambda r: r.score, reverse=True)

    # Label winners and losers
    for r in results[:top_n]:
        r.signal = "BUY"
    for r in results[-bottom_n:]:
        if r.signal != "BUY":   # avoid overlap when len < top_n + bottom_n
            r.signal = "SELL"
    for r in results:
        if r.signal == "HOLD":
            r.signal = "NEUTRAL"

    return results


# ---------------------------------------------------------------------------
# 2. Mean Reversion – single cluster  (§ 3.9)
# ---------------------------------------------------------------------------

def mean_reversion_screen(
    symbols: list[str],
    lookback_days: int = 21,
) -> list[StrategyResult]:
    """
    Compute demeaned returns for a cluster of stocks and generate
    mean-reversion signals.

    Stocks with returns *below* the cluster mean are labelled BUY
    (expected to revert up), and those *above* are labelled SELL.

    Data source: yfinance daily close prices (last *lookback_days*).

    Parameters
    ----------
    symbols : list[str]
        Tickers that form a single correlated cluster (e.g. same sector).
    lookback_days : int
        Days over which to compute log returns (default 21 ≈ 1 month).
    """
    period = f"{int(lookback_days * 2.5 / 21 + 2)}mo"
    returns: dict[str, float] = {}

    for sym in symbols:
        df = fetch_daily_history(sym, period=period)
        if df is None or len(df) < lookback_days + 1:
            continue
        closes = df["Close"]
        p_start = float(closes.iloc[-(lookback_days + 1)])
        p_end = float(closes.iloc[-1])
        if p_start <= 0:
            continue
        returns[sym] = float(np.log(p_end / p_start))

    if len(returns) < 2:
        return []

    mean_return = float(np.mean(list(returns.values())))

    results: list[StrategyResult] = []
    for sym, ret in returns.items():
        demeaned = ret - mean_return
        # Negative demeaned → under-performed cluster → BUY (expect revert up)
        signal = "BUY" if demeaned < 0 else "SELL"
        results.append(StrategyResult(
            symbol=sym,
            strategy="MEAN_REVERSION",
            signal=signal,
            score=-demeaned,          # higher positive score = stronger BUY
            details={
                "log_return": ret,
                "cluster_mean_return": mean_return,
                "demeaned_return": demeaned,
                "lookback_days": lookback_days,
            },
        ))

    results.sort(key=lambda r: r.score, reverse=True)
    return results


# ---------------------------------------------------------------------------
# 3. Moving-Average Crossover  (§ 3.12)
# ---------------------------------------------------------------------------

def moving_average_crossover(
    symbol: str,
    short_window: int = 10,
    long_window: int = 30,
    daily_df: Optional[pd.DataFrame] = None,
) -> Optional[StrategyResult]:
    """
    Two-moving-average crossover signal for a single stock.

    * BUY  when MA(short) > MA(long)  (golden cross)
    * SELL when MA(short) < MA(long)  (death cross)

    Data source: yfinance daily close prices.

    Parameters
    ----------
    symbol : str
        Ticker symbol.
    short_window, long_window : int
        Lengths (in trading days) of the fast and slow MAs.
    daily_df : pd.DataFrame | None
        Pre-fetched OHLCV DataFrame (if ``None``, data is fetched).
    """
    if daily_df is None:
        needed = int(long_window * 3)
        daily_df = fetch_daily_history(
            symbol, period=f"{max(needed // 21 + 2, 3)}mo"
        )
    if daily_df is None or len(daily_df) < long_window + 1:
        return None

    closes = daily_df["Close"]
    ma_short = float(closes.iloc[-short_window:].mean())
    ma_long = float(closes.iloc[-long_window:].mean())
    current_price = float(closes.iloc[-1])

    if ma_long == 0:
        return None

    # Score: normalised distance between the two MAs
    spread_pct = (ma_short - ma_long) / ma_long * 100.0
    signal = "BUY" if ma_short > ma_long else "SELL"

    return StrategyResult(
        symbol=symbol,
        strategy="MA_CROSSOVER",
        signal=signal,
        score=spread_pct,
        details={
            "short_window": short_window,
            "long_window": long_window,
            "ma_short": ma_short,
            "ma_long": ma_long,
            "current_price": current_price,
            "spread_pct": spread_pct,
        },
    )


# ---------------------------------------------------------------------------
# 4. Support & Resistance / Pivot Point  (§ 3.14)
# ---------------------------------------------------------------------------

def support_resistance(
    symbol: str,
    daily_df: Optional[pd.DataFrame] = None,
) -> Optional[StrategyResult]:
    """
    Compute pivot-point support and resistance levels and generate a signal.

    * BUY   when current price > pivot but < resistance (room to run)
    * SELL  when current price < pivot but > support (room to fall)
    * HOLD  when price is at or beyond resistance / support

    Formulas (§ 3.14 of the paper):
        C (pivot) = (P_H + P_L + P_C) / 3
        R (resistance) = 2C − P_L
        S (support) = 2C − P_H

    Data source: yfinance daily OHLCV (previous day's bar).

    Parameters
    ----------
    symbol : str
        Ticker symbol.
    daily_df : pd.DataFrame | None
        Pre-fetched OHLCV DataFrame.
    """
    if daily_df is None:
        daily_df = fetch_daily_history(symbol, period="5d")
    if daily_df is None or len(daily_df) < 2:
        return None

    prev = daily_df.iloc[-2]   # previous completed day
    p_high = float(prev["High"])
    p_low = float(prev["Low"])
    p_close = float(prev["Close"])

    pivot = (p_high + p_low + p_close) / 3.0
    resistance = 2.0 * pivot - p_low
    support = 2.0 * pivot - p_high

    current_price = float(daily_df["Close"].iloc[-1])

    if pivot == 0:
        return None

    # Determine signal
    if current_price > pivot:
        signal = "BUY" if current_price < resistance else "HOLD"
    elif current_price < pivot:
        signal = "SELL" if current_price > support else "HOLD"
    else:
        signal = "HOLD"

    score = (current_price - pivot) / pivot * 100.0

    return StrategyResult(
        symbol=symbol,
        strategy="SUPPORT_RESISTANCE",
        signal=signal,
        score=score,
        details={
            "pivot": pivot,
            "resistance": resistance,
            "support": support,
            "current_price": current_price,
            "prev_high": p_high,
            "prev_low": p_low,
            "prev_close": p_close,
        },
    )


# ---------------------------------------------------------------------------
# 5. Pairs Trading  (§ 3.8)
# ---------------------------------------------------------------------------

def pairs_trading(
    symbol_a: str,
    symbol_b: str,
    lookback_days: int = 60,
    df_a: Optional[pd.DataFrame] = None,
    df_b: Optional[pd.DataFrame] = None,
) -> Optional[list[StrategyResult]]:
    """
    Classic pairs-trading signal for two historically correlated stocks.

    Compute demeaned log-returns over *lookback_days*.  The stock with
    positive demeaned return is "rich" (SELL) and the other is "cheap" (BUY).

    Returns a list of two :class:`StrategyResult` objects (one per leg)
    or ``None`` if data is insufficient.

    Data source: yfinance daily close prices.

    Parameters
    ----------
    symbol_a, symbol_b : str
        The two ticker symbols forming the pair.
    lookback_days : int
        Period over which to compare returns (default 60).
    df_a, df_b : pd.DataFrame | None
        Pre-fetched OHLCV DataFrames.
    """
    period = f"{max(lookback_days // 21 + 3, 4)}mo"

    if df_a is None:
        df_a = fetch_daily_history(symbol_a, period=period)
    if df_b is None:
        df_b = fetch_daily_history(symbol_b, period=period)

    if df_a is None or df_b is None:
        return None
    if len(df_a) < lookback_days + 1 or len(df_b) < lookback_days + 1:
        return None

    p_a_start = float(df_a["Close"].iloc[-(lookback_days + 1)])
    p_a_end = float(df_a["Close"].iloc[-1])
    p_b_start = float(df_b["Close"].iloc[-(lookback_days + 1)])
    p_b_end = float(df_b["Close"].iloc[-1])

    if p_a_start <= 0 or p_b_start <= 0:
        return None

    r_a = float(np.log(p_a_end / p_a_start))
    r_b = float(np.log(p_b_end / p_b_start))
    mean_r = (r_a + r_b) / 2.0
    dem_a = r_a - mean_r
    dem_b = r_b - mean_r

    # Compute correlation over the lookback window for informational purposes
    aligned = pd.DataFrame({
        "a": df_a["Close"].iloc[-(lookback_days + 1):].pct_change().dropna().values[
             :lookback_days],
        "b": df_b["Close"].iloc[-(lookback_days + 1):].pct_change().dropna().values[
             :lookback_days],
    })
    corr = float(aligned["a"].corr(aligned["b"])) if len(aligned) > 1 else float("nan")

    sig_a = "SELL" if dem_a > 0 else "BUY"
    sig_b = "SELL" if dem_b > 0 else "BUY"

    result_a = StrategyResult(
        symbol=symbol_a,
        strategy="PAIRS_TRADING",
        signal=sig_a,
        score=-dem_a,
        details={
            "pair": f"{symbol_a}/{symbol_b}",
            "log_return": r_a,
            "demeaned_return": dem_a,
            "pair_correlation": corr,
            "lookback_days": lookback_days,
        },
    )
    result_b = StrategyResult(
        symbol=symbol_b,
        strategy="PAIRS_TRADING",
        signal=sig_b,
        score=-dem_b,
        details={
            "pair": f"{symbol_a}/{symbol_b}",
            "log_return": r_b,
            "demeaned_return": dem_b,
            "pair_correlation": corr,
            "lookback_days": lookback_days,
        },
    )
    return [result_a, result_b]


# ---------------------------------------------------------------------------
# Convenience – run all strategies for the default stock watchlist
# ---------------------------------------------------------------------------

def run_all_strategies(
    symbols: Optional[list[str]] = None,
) -> dict[str, list[StrategyResult]]:
    """
    Execute every implemented strategy and return results keyed by name.

    When *symbols* is ``None``, the six default stocks from the watchlist
    (``XOM``, ``CVX``, ``TSLA``, ``AAPL``, ``NVDA``, ``META``) are used.

    Data source: all data is fetched live from **yfinance** (Yahoo Finance).
    No API key is required.
    """
    if symbols is None:
        symbols = ["XOM", "CVX", "TSLA", "AAPL", "NVDA", "META"]

    output: dict[str, list[StrategyResult]] = {}

    # 1. Momentum
    output["MOMENTUM"] = momentum_screen(symbols)

    # 2. Mean reversion (treat all symbols as one cluster)
    output["MEAN_REVERSION"] = mean_reversion_screen(symbols)

    # 3. MA crossover (per symbol)
    ma_results: list[StrategyResult] = []
    for sym in symbols:
        r = moving_average_crossover(sym)
        if r is not None:
            ma_results.append(r)
    output["MA_CROSSOVER"] = ma_results

    # 4. Support & resistance (per symbol)
    sr_results: list[StrategyResult] = []
    for sym in symbols:
        r = support_resistance(sym)
        if r is not None:
            sr_results.append(r)
    output["SUPPORT_RESISTANCE"] = sr_results

    # 5. Pairs trading (first two symbols as a sample pair)
    if len(symbols) >= 2:
        pair = pairs_trading(symbols[0], symbols[1])
        output["PAIRS_TRADING"] = pair if pair is not None else []
    else:
        output["PAIRS_TRADING"] = []

    return output
