"""
Asset class definitions and watchlists for the unusual trading activity monitor.

Each asset entry contains:
  - symbol  : ticker symbol understood by the data provider
  - name    : human-readable name shown in alerts
  - category: broad asset class (used for grouping / cross-asset signals)
  - provider: which data provider to use ('yfinance' or 'coingecko')
  - cg_id   : CoinGecko coin id (only needed when provider == 'coingecko')
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

CATEGORY_OIL_GAS = "Oil & Gas Futures"
CATEGORY_METALS = "Metals Futures"
CATEGORY_CRYPTO = "Crypto"
CATEGORY_EQUITY_INDEX = "Equity Index"
CATEGORY_STOCKS = "Stocks"
CATEGORY_FOREX = "Forex"
CATEGORY_BONDS = "Bonds"


@dataclass
class Asset:
    symbol: str
    name: str
    category: str
    provider: str = "yfinance"
    cg_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Default watchlist – covers the asset classes mentioned in the issue.
# Users can extend this list or replace it via the --watchlist CLI flag.
# ---------------------------------------------------------------------------

DEFAULT_WATCHLIST: list[Asset] = [
    # ── Oil & Gas ──────────────────────────────────────────────────────────
    Asset("CL=F", "WTI Crude Oil Futures", CATEGORY_OIL_GAS),
    Asset("BZ=F", "Brent Crude Oil Futures", CATEGORY_OIL_GAS),
    Asset("NG=F", "Natural Gas Futures", CATEGORY_OIL_GAS),
    Asset("RB=F", "RBOB Gasoline Futures", CATEGORY_OIL_GAS),
    Asset("HO=F", "Heating Oil Futures", CATEGORY_OIL_GAS),

    # ── Metals ─────────────────────────────────────────────────────────────
    Asset("GC=F", "Gold Futures", CATEGORY_METALS),
    Asset("SI=F", "Silver Futures", CATEGORY_METALS),
    Asset("HG=F", "Copper Futures", CATEGORY_METALS),
    Asset("PL=F", "Platinum Futures", CATEGORY_METALS),

    # ── Cryptocurrencies ───────────────────────────────────────────────────
    Asset("BTC-USD", "Bitcoin", CATEGORY_CRYPTO, provider="yfinance"),
    Asset("ETH-USD", "Ethereum", CATEGORY_CRYPTO, provider="yfinance"),
    Asset("SOL-USD", "Solana", CATEGORY_CRYPTO, provider="yfinance"),
    Asset("XRP-USD", "XRP", CATEGORY_CRYPTO, provider="yfinance"),

    # ── Equity Indices ─────────────────────────────────────────────────────
    Asset("ES=F", "S&P 500 Futures", CATEGORY_EQUITY_INDEX),
    Asset("NQ=F", "Nasdaq 100 Futures", CATEGORY_EQUITY_INDEX),
    Asset("YM=F", "Dow Jones Futures", CATEGORY_EQUITY_INDEX),
    Asset("RTY=F", "Russell 2000 Futures", CATEGORY_EQUITY_INDEX),
    Asset("SPY", "S&P 500 ETF", CATEGORY_EQUITY_INDEX),
    Asset("QQQ", "Nasdaq 100 ETF", CATEGORY_EQUITY_INDEX),

    # ── High-cap Stocks (Trump-sensitive sectors) ──────────────────────────
    Asset("XOM", "ExxonMobil", CATEGORY_STOCKS),
    Asset("CVX", "Chevron", CATEGORY_STOCKS),
    Asset("TSLA", "Tesla", CATEGORY_STOCKS),
    Asset("AAPL", "Apple", CATEGORY_STOCKS),
    Asset("NVDA", "Nvidia", CATEGORY_STOCKS),
    Asset("META", "Meta Platforms", CATEGORY_STOCKS),

    # ── Forex (USD pairs most sensitive to US policy) ──────────────────────
    Asset("EURUSD=X", "EUR/USD", CATEGORY_FOREX),
    Asset("USDJPY=X", "USD/JPY", CATEGORY_FOREX),
    Asset("GBPUSD=X", "GBP/USD", CATEGORY_FOREX),
    Asset("DX-Y.NYB", "US Dollar Index", CATEGORY_FOREX),

    # ── Bonds ──────────────────────────────────────────────────────────────
    Asset("ZN=F", "10-Year T-Note Futures", CATEGORY_BONDS),
    Asset("ZB=F", "30-Year T-Bond Futures", CATEGORY_BONDS),
    Asset("TLT", "20+ Year Treasury ETF", CATEGORY_BONDS),
]


def get_watchlist_by_category(category: str) -> list[Asset]:
    """Return all assets in the given category."""
    return [a for a in DEFAULT_WATCHLIST if a.category == category]


def get_asset(symbol: str) -> Optional[Asset]:
    """Look up an asset by its symbol (case-insensitive)."""
    symbol_upper = symbol.upper()
    for asset in DEFAULT_WATCHLIST:
        if asset.symbol.upper() == symbol_upper:
            return asset
    return None
