"""
Unit tests for the strategy tools in src/strategies.py.

These tests use synthetic pandas DataFrames to avoid any network calls.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from src.strategies import (
    StrategyResult,
    mean_reversion_screen,
    momentum_screen,
    moving_average_crossover,
    pairs_trading,
    support_resistance,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_daily_df(
    closes: list[float],
    opens: list[float] | None = None,
    highs: list[float] | None = None,
    lows: list[float] | None = None,
    volumes: list[float] | None = None,
) -> pd.DataFrame:
    """Build a synthetic daily OHLCV DataFrame."""
    n = len(closes)
    base = datetime(2024, 1, 1)
    idx = pd.date_range(start=base, periods=n, freq="D")
    if opens is None:
        opens = [c * 0.99 for c in closes]
    if highs is None:
        highs = [c * 1.01 for c in closes]
    if lows is None:
        lows = [c * 0.99 for c in closes]
    if volumes is None:
        volumes = [1_000_000.0] * n
    return pd.DataFrame(
        {
            "Open": opens,
            "High": highs,
            "Low": lows,
            "Close": closes,
            "Volume": volumes,
        },
        index=idx,
    )


# ---------------------------------------------------------------------------
# StrategyResult
# ---------------------------------------------------------------------------

class TestStrategyResult:
    def test_str_contains_key_fields(self):
        r = StrategyResult(
            symbol="AAPL",
            strategy="MOMENTUM",
            signal="BUY",
            score=0.15,
            details={"formation_months": 12},
        )
        s = str(r)
        assert "MOMENTUM" in s
        assert "AAPL" in s
        assert "BUY" in s

    def test_str_formats_float_details(self):
        r = StrategyResult(
            symbol="X",
            strategy="TEST",
            signal="HOLD",
            score=0.0,
            details={"some_float": 3.14159},
        )
        s = str(r)
        assert "3.142" in s


# ---------------------------------------------------------------------------
# Momentum screen
# ---------------------------------------------------------------------------

class TestMomentumScreen:
    def _mock_fetch(self, returns_map: dict[str, float], n_days: int = 300):
        """Return a patched ``fetch_daily_history`` that builds synthetic DFs."""
        def _fetch(symbol, period="1y"):
            if symbol not in returns_map:
                return None
            # Build a DF where the close linearly moves from 100 to 100*(1+r)
            total_return = returns_map[symbol]
            closes = list(np.linspace(100.0, 100.0 * (1 + total_return), n_days))
            return _make_daily_df(closes)
        return _fetch

    def test_winners_labelled_buy(self):
        returns = {"A": 0.50, "B": 0.30, "C": -0.10, "D": -0.40}
        with patch("src.strategies.fetch_daily_history",
                    side_effect=self._mock_fetch(returns)):
            results = momentum_screen(
                list(returns.keys()),
                formation_months=12,
                skip_months=0,
                top_n=2,
                bottom_n=2,
            )
        buys = [r for r in results if r.signal == "BUY"]
        sells = [r for r in results if r.signal == "SELL"]
        assert len(buys) == 2
        assert len(sells) == 2
        # Winners should have higher scores
        assert buys[0].score > buys[1].score
        assert buys[0].symbol == "A"

    def test_losers_labelled_sell(self):
        returns = {"X": 0.10, "Y": -0.20, "Z": -0.50}
        with patch("src.strategies.fetch_daily_history",
                    side_effect=self._mock_fetch(returns)):
            results = momentum_screen(
                list(returns.keys()),
                formation_months=12,
                skip_months=0,
                top_n=1,
                bottom_n=1,
            )
        sells = [r for r in results if r.signal == "SELL"]
        assert len(sells) == 1
        assert sells[0].symbol == "Z"

    def test_empty_symbols_returns_empty(self):
        with patch("src.strategies.fetch_daily_history", return_value=None):
            results = momentum_screen([])
        assert results == []

    def test_insufficient_data_skipped(self):
        short_df = _make_daily_df([100.0] * 5)
        with patch("src.strategies.fetch_daily_history", return_value=short_df):
            results = momentum_screen(["A"], formation_months=12, skip_months=1)
        assert results == []

    def test_skip_months_shifts_window(self):
        # Closes: steady at 100 for 250 days, then jump to 150 for last 10
        # With skip=0 the formation window reaches the jump → higher return
        # With skip=1 (21 days), end_idx moves back past the jump → lower return
        closes = [100.0] * 250 + [150.0] * 10
        df = _make_daily_df(closes)
        with patch("src.strategies.fetch_daily_history", return_value=df):
            result_skip0 = momentum_screen(
                ["A"], formation_months=6, skip_months=0, top_n=1, bottom_n=0,
            )
            result_skip1 = momentum_screen(
                ["A"], formation_months=6, skip_months=1, top_n=1, bottom_n=0,
            )
        # With skip=0 the window includes the big jump → higher return
        assert result_skip0[0].score > result_skip1[0].score


# ---------------------------------------------------------------------------
# Mean Reversion screen
# ---------------------------------------------------------------------------

class TestMeanReversionScreen:
    def _mock_fetch(self, price_map: dict[str, tuple[float, float]]):
        """price_map: symbol → (start_price, end_price)"""
        def _fetch(symbol, period="3mo"):
            if symbol not in price_map:
                return None
            p_start, p_end = price_map[symbol]
            closes = list(np.linspace(p_start, p_end, 50))
            return _make_daily_df(closes)
        return _fetch

    def test_underperformer_labelled_buy(self):
        prices = {"A": (100, 110), "B": (100, 90)}
        with patch("src.strategies.fetch_daily_history",
                    side_effect=self._mock_fetch(prices)):
            results = mean_reversion_screen(list(prices.keys()), lookback_days=21)
        buys = [r for r in results if r.signal == "BUY"]
        sells = [r for r in results if r.signal == "SELL"]
        assert len(buys) == 1
        assert buys[0].symbol == "B"
        assert len(sells) == 1
        assert sells[0].symbol == "A"

    def test_single_stock_returns_empty(self):
        with patch("src.strategies.fetch_daily_history",
                    side_effect=self._mock_fetch({"A": (100, 110)})):
            results = mean_reversion_screen(["A"])
        assert results == []

    def test_all_same_returns_gives_zero_demeaned(self):
        prices = {"A": (100, 110), "B": (100, 110)}
        with patch("src.strategies.fetch_daily_history",
                    side_effect=self._mock_fetch(prices)):
            results = mean_reversion_screen(list(prices.keys()), lookback_days=21)
        for r in results:
            assert abs(r.details["demeaned_return"]) < 1e-10

    def test_score_is_negative_demeaned(self):
        prices = {"A": (100, 120), "B": (100, 80)}
        with patch("src.strategies.fetch_daily_history",
                    side_effect=self._mock_fetch(prices)):
            results = mean_reversion_screen(list(prices.keys()), lookback_days=21)
        for r in results:
            assert r.score == pytest.approx(-r.details["demeaned_return"])


# ---------------------------------------------------------------------------
# Moving Average Crossover
# ---------------------------------------------------------------------------

class TestMovingAverageCrossover:
    def test_golden_cross_is_buy(self):
        # Last 10 days rising sharply → short MA > long MA
        closes = [100.0] * 30 + list(np.linspace(100, 130, 10))
        df = _make_daily_df(closes)
        result = moving_average_crossover("TEST", short_window=10, long_window=30,
                                          daily_df=df)
        assert result is not None
        assert result.signal == "BUY"
        assert result.score > 0

    def test_death_cross_is_sell(self):
        # Last 10 days falling sharply → short MA < long MA
        closes = [100.0] * 30 + list(np.linspace(100, 70, 10))
        df = _make_daily_df(closes)
        result = moving_average_crossover("TEST", short_window=10, long_window=30,
                                          daily_df=df)
        assert result is not None
        assert result.signal == "SELL"
        assert result.score < 0

    def test_flat_market_near_zero_spread(self):
        closes = [100.0] * 50
        df = _make_daily_df(closes)
        result = moving_average_crossover("TEST", short_window=10, long_window=30,
                                          daily_df=df)
        assert result is not None
        assert abs(result.score) < 0.01

    def test_returns_none_for_insufficient_data(self):
        df = _make_daily_df([100.0] * 5)
        result = moving_average_crossover("TEST", short_window=10, long_window=30,
                                          daily_df=df)
        assert result is None

    def test_details_contain_ma_values(self):
        closes = [100.0] * 50
        df = _make_daily_df(closes)
        result = moving_average_crossover("TEST", daily_df=df)
        assert result is not None
        assert "ma_short" in result.details
        assert "ma_long" in result.details
        assert "current_price" in result.details


# ---------------------------------------------------------------------------
# Support & Resistance
# ---------------------------------------------------------------------------

class TestSupportResistance:
    def test_pivot_calculation(self):
        # Previous day: H=110, L=90, C=105
        # Pivot = (110+90+105)/3 = 101.67
        # R = 2*101.67 - 90 = 113.33
        # S = 2*101.67 - 110 = 93.33
        closes = [100.0, 105.0, 108.0]
        highs = [102.0, 110.0, 109.0]
        lows = [98.0, 90.0, 106.0]
        df = _make_daily_df(closes, highs=highs, lows=lows)
        result = support_resistance("TEST", daily_df=df)
        assert result is not None
        assert result.details["pivot"] == pytest.approx(101.6667, rel=1e-3)
        assert result.details["resistance"] == pytest.approx(113.3333, rel=1e-3)
        assert result.details["support"] == pytest.approx(93.3333, rel=1e-3)

    def test_price_above_pivot_is_buy(self):
        # Previous day: H=105, L=95, C=100 → pivot=100, R=105, S=95
        # Current close = 102 → above pivot (100), below resistance (105) → BUY
        closes = [90.0, 100.0, 102.0]
        highs = [92.0, 105.0, 103.0]
        lows = [88.0, 95.0, 101.0]
        df = _make_daily_df(closes, highs=highs, lows=lows)
        result = support_resistance("TEST", daily_df=df)
        assert result is not None
        assert result.signal == "BUY"

    def test_price_below_pivot_is_sell(self):
        # Previous day: H=105, L=95, C=100 → pivot=100, R=105, S=95
        # Current close = 98 → below pivot (100), above support (95) → SELL
        closes = [90.0, 100.0, 98.0]
        highs = [92.0, 105.0, 99.0]
        lows = [88.0, 95.0, 97.0]
        df = _make_daily_df(closes, highs=highs, lows=lows)
        result = support_resistance("TEST", daily_df=df)
        assert result is not None
        assert result.signal == "SELL"

    def test_returns_none_for_single_bar(self):
        df = _make_daily_df([100.0])
        result = support_resistance("TEST", daily_df=df)
        assert result is None


# ---------------------------------------------------------------------------
# Pairs Trading
# ---------------------------------------------------------------------------

class TestPairsTrading:
    def test_rich_stock_is_sell_cheap_is_buy(self):
        # A went up a lot, B stayed flat → A is "rich" (SELL), B is "cheap" (BUY)
        df_a = _make_daily_df(list(np.linspace(100, 130, 80)))
        df_b = _make_daily_df(list(np.linspace(100, 100, 80)))
        results = pairs_trading("A", "B", lookback_days=60, df_a=df_a, df_b=df_b)
        assert results is not None
        assert len(results) == 2
        a_result = next(r for r in results if r.symbol == "A")
        b_result = next(r for r in results if r.symbol == "B")
        assert a_result.signal == "SELL"
        assert b_result.signal == "BUY"

    def test_returns_none_for_insufficient_data(self):
        df_short = _make_daily_df([100.0] * 5)
        results = pairs_trading("A", "B", lookback_days=60,
                                df_a=df_short, df_b=df_short)
        assert results is None

    def test_details_contain_correlation(self):
        df_a = _make_daily_df(list(np.linspace(100, 120, 80)))
        df_b = _make_daily_df(list(np.linspace(100, 115, 80)))
        results = pairs_trading("A", "B", lookback_days=60, df_a=df_a, df_b=df_b)
        assert results is not None
        assert "pair_correlation" in results[0].details
        assert not math.isnan(results[0].details["pair_correlation"])

    def test_symmetric_returns_give_zero_demeaned(self):
        closes = list(np.linspace(100, 110, 80))
        df = _make_daily_df(closes)
        results = pairs_trading("A", "B", lookback_days=60, df_a=df, df_b=df)
        assert results is not None
        for r in results:
            assert abs(r.details["demeaned_return"]) < 1e-10

    def test_returns_none_when_one_leg_missing(self):
        df_a = _make_daily_df(list(np.linspace(100, 120, 80)))
        results = pairs_trading("A", "B", lookback_days=60,
                                df_a=df_a, df_b=None)
        assert results is None
