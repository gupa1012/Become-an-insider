"""
Unit tests for the anomaly detection logic in src/detectors.py.

These tests use synthetic pandas DataFrames to avoid any network calls.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from src.assets import Asset, CATEGORY_OIL_GAS, CATEGORY_CRYPTO, CATEGORY_STOCKS
from src.config import Config
from src.detectors import (
    Signal,
    _rolling_avg_volume,
    detect_volume_spike,
    detect_price_velocity,
    detect_cross_asset_coordination,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_daily_df(
    volumes: list[float],
    close_prices: list[float] | None = None,
    open_prices: list[float] | None = None,
) -> pd.DataFrame:
    """Build a synthetic daily OHLCV DataFrame."""
    n = len(volumes)
    base = datetime(2024, 1, 1)
    idx = pd.date_range(start=base, periods=n, freq="D")
    closes = close_prices if close_prices else [100.0] * n
    opens = open_prices if open_prices else [99.0] * n
    return pd.DataFrame(
        {
            "Open": opens,
            "High": [c * 1.01 for c in closes],
            "Low": [c * 0.99 for c in closes],
            "Close": closes,
            "Volume": volumes,
        },
        index=idx,
    )


def _make_intraday_df(
    session_open: float,
    latest_close: float,
    total_volume: float = 500_000.0,
    bars: int = 40,
) -> pd.DataFrame:
    """Build a synthetic intraday OHLCV DataFrame."""
    base = datetime(2024, 1, 2, 9, 30)
    idx = pd.date_range(start=base, periods=bars, freq="5min")
    vol_per_bar = total_volume / bars
    closes = np.linspace(session_open, latest_close, bars)
    return pd.DataFrame(
        {
            "Open": [session_open] + list(closes[:-1]),
            "High": closes * 1.005,
            "Low": closes * 0.995,
            "Close": closes,
            "Volume": [vol_per_bar] * bars,
        },
        index=idx,
    )


_OIL = Asset("CL=F", "WTI Crude", CATEGORY_OIL_GAS)
_BTC = Asset("BTC-USD", "Bitcoin", CATEGORY_CRYPTO)
_STOCK = Asset("AAPL", "Apple", CATEGORY_STOCKS)

_DEFAULT_CONFIG = Config(
    volume_lookback_periods=20,
    volume_spike_threshold=3.0,
    price_change_threshold_pct=1.5,
    cross_asset_min_categories=2,
)


# ---------------------------------------------------------------------------
# _rolling_avg_volume
# ---------------------------------------------------------------------------

class TestRollingAvgVolume:
    def test_returns_mean_of_last_n_excluding_latest(self):
        volumes = [100.0] * 20 + [999.0]  # last bar is the "live" bar
        df = _make_daily_df(volumes)
        avg = _rolling_avg_volume(df, n=20)
        assert avg == pytest.approx(100.0)

    def test_insufficient_data_returns_nan(self):
        df = _make_daily_df([100.0])
        avg = _rolling_avg_volume(df, n=20)
        assert math.isnan(avg)

    def test_empty_df_returns_nan(self):
        df = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
        avg = _rolling_avg_volume(df, n=5)
        assert math.isnan(avg)


# ---------------------------------------------------------------------------
# detect_volume_spike
# ---------------------------------------------------------------------------

class TestDetectVolumeSpike:
    def test_no_spike_returns_none(self):
        volumes = [1_000_000.0] * 21
        daily = _make_daily_df(volumes)
        result = detect_volume_spike(_OIL, daily, None, _DEFAULT_CONFIG)
        assert result is None

    def test_spike_detected_without_intraday(self):
        # avg = 1 M; last bar = 4 M → ratio 4× ≥ threshold 3×
        volumes = [1_000_000.0] * 20 + [4_000_000.0]
        daily = _make_daily_df(volumes)
        result = detect_volume_spike(_OIL, daily, None, _DEFAULT_CONFIG)
        assert result is not None
        assert result.signal_type == "VOLUME_SPIKE"
        assert result.volume_ratio == pytest.approx(4.0)

    def test_spike_severity_high(self):
        volumes = [1_000_000.0] * 20 + [6_000_000.0]
        daily = _make_daily_df(volumes)
        result = detect_volume_spike(_OIL, daily, None, _DEFAULT_CONFIG)
        assert result is not None
        assert result.severity == "HIGH"

    def test_spike_severity_medium(self):
        # threshold 3×; medium starts at 3 × 1.33 = ~4×; use 4.5× < 5×
        volumes = [1_000_000.0] * 20 + [4_500_000.0]
        daily = _make_daily_df(volumes)
        result = detect_volume_spike(_OIL, daily, None, _DEFAULT_CONFIG)
        assert result is not None
        assert result.severity == "MEDIUM"

    def test_spike_severity_low(self):
        # just above threshold (3×) but below medium (4×)
        volumes = [1_000_000.0] * 20 + [3_200_000.0]
        daily = _make_daily_df(volumes)
        result = detect_volume_spike(_OIL, daily, None, _DEFAULT_CONFIG)
        assert result is not None
        assert result.severity == "LOW"

    def test_intraday_volume_used_when_available(self):
        # Daily avg is 1 M; intraday accumulated (projected full-day) is 5 M
        volumes = [1_000_000.0] * 21
        daily = _make_daily_df(volumes)
        # 40 bars / 78 bars-per-day ≈ 51 % elapsed; accumulated = 2.6 M
        # projected full day ≈ 2.6 M / 0.51 ≈ 5.1 M → ratio ≈ 5.1×
        intraday = _make_intraday_df(100.0, 105.0, total_volume=2_600_000.0, bars=40)
        result = detect_volume_spike(_OIL, daily, intraday, _DEFAULT_CONFIG)
        assert result is not None
        assert result.volume_ratio > 3.0

    def test_returns_none_when_insufficient_history(self):
        daily = _make_daily_df([500_000.0])
        result = detect_volume_spike(_OIL, daily, None, _DEFAULT_CONFIG)
        assert result is None

    def test_asset_is_preserved_in_signal(self):
        volumes = [1_000_000.0] * 20 + [5_000_000.0]
        daily = _make_daily_df(volumes)
        result = detect_volume_spike(_BTC, daily, None, _DEFAULT_CONFIG)
        assert result is not None
        assert result.asset is _BTC


# ---------------------------------------------------------------------------
# detect_price_velocity
# ---------------------------------------------------------------------------

class TestDetectPriceVelocity:
    def test_no_signal_below_threshold(self):
        intraday = _make_intraday_df(100.0, 101.0)  # 1 % change < 1.5 %
        result = detect_price_velocity(_OIL, intraday, _DEFAULT_CONFIG)
        assert result is None

    def test_upward_velocity_detected(self):
        intraday = _make_intraday_df(100.0, 105.0)  # 5 % up
        result = detect_price_velocity(_OIL, intraday, _DEFAULT_CONFIG)
        assert result is not None
        assert result.signal_type == "PRICE_VELOCITY"
        assert result.price_change_pct == pytest.approx(5.0, rel=1e-3)

    def test_downward_velocity_detected(self):
        intraday = _make_intraday_df(100.0, 96.0)  # −4 %
        result = detect_price_velocity(_OIL, intraday, _DEFAULT_CONFIG)
        assert result is not None
        assert result.price_change_pct < 0

    def test_severity_high_at_triple_threshold(self):
        # threshold = 1.5 %; HIGH starts at 3× = 4.5 %
        intraday = _make_intraday_df(100.0, 106.0)  # 6 % up
        result = detect_price_velocity(_OIL, intraday, _DEFAULT_CONFIG)
        assert result is not None
        assert result.severity == "HIGH"

    def test_severity_medium(self):
        # 2× threshold = 3 % but < 3× threshold
        intraday = _make_intraday_df(100.0, 103.5)
        result = detect_price_velocity(_OIL, intraday, _DEFAULT_CONFIG)
        assert result is not None
        assert result.severity == "MEDIUM"

    def test_returns_none_for_insufficient_bars(self):
        base = datetime(2024, 1, 2, 9, 30)
        idx = pd.date_range(start=base, periods=1, freq="5min")
        df = pd.DataFrame(
            {"Open": [100.0], "High": [101.0], "Low": [99.0],
             "Close": [105.0], "Volume": [1000.0]},
            index=idx,
        )
        result = detect_price_velocity(_OIL, df, _DEFAULT_CONFIG)
        assert result is None

    def test_returns_none_for_none_intraday(self):
        result = detect_price_velocity(_OIL, None, _DEFAULT_CONFIG)
        assert result is None


# ---------------------------------------------------------------------------
# detect_cross_asset_coordination
# ---------------------------------------------------------------------------

class TestDetectCrossAssetCoordination:
    def _make_signal(self, asset: Asset) -> Signal:
        return Signal(
            asset=asset,
            signal_type="VOLUME_SPIKE",
            severity="HIGH",
            description="test",
            volume_ratio=4.0,
        )

    def test_no_cross_asset_single_category(self):
        signals = [self._make_signal(_OIL)]
        result = detect_cross_asset_coordination(signals, _DEFAULT_CONFIG)
        assert result == []

    def test_cross_asset_two_categories(self):
        signals = [self._make_signal(_OIL), self._make_signal(_BTC)]
        result = detect_cross_asset_coordination(signals, _DEFAULT_CONFIG)
        assert len(result) > 0
        assert all(s.signal_type == "CROSS_ASSET" for s in result)

    def test_cross_asset_three_categories_is_high(self):
        signals = [
            self._make_signal(_OIL),
            self._make_signal(_BTC),
            self._make_signal(_STOCK),
        ]
        result = detect_cross_asset_coordination(signals, _DEFAULT_CONFIG)
        assert any(s.severity == "HIGH" for s in result)

    def test_empty_signals_returns_empty(self):
        result = detect_cross_asset_coordination([], _DEFAULT_CONFIG)
        assert result == []

    def test_min_categories_respected(self):
        cfg = Config(cross_asset_min_categories=3)
        signals = [self._make_signal(_OIL), self._make_signal(_BTC)]
        result = detect_cross_asset_coordination(signals, cfg)
        assert result == []


# ---------------------------------------------------------------------------
# Signal.__str__
# ---------------------------------------------------------------------------

class TestSignalStr:
    def test_str_contains_key_fields(self):
        sig = Signal(
            asset=_OIL,
            signal_type="VOLUME_SPIKE",
            severity="HIGH",
            description="Big spike",
            current_price=75.50,
            volume_ratio=5.2,
        )
        s = str(sig)
        assert "VOLUME_SPIKE" in s
        assert "HIGH" in s
        assert "WTI Crude" in s
        assert "5.2" in s
