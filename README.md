# Become-an-insider 🕵️

**Spot unusual trading activity across every major asset class within minutes — before the crowd does.**

Built for the scenario where large, coordinated trades appear in oil futures, Bitcoin, equities and other instruments shortly *before* a major news event. The tool continuously watches your watchlist, detects volume spikes and sharp price moves, and fires an alarm so you can position yourself accordingly.

Now also includes **five stock strategy tools** derived from the academic paper [*151 Trading Strategies*](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3247865) (Kakushadze & Serur, 2018).

---

## Features

| Feature | Detail |
|---|---|
| **Multi-asset coverage** | Oil & Gas futures, Metals, Crypto, Equity indices, individual Stocks, Forex, Bonds |
| **Volume spike detection** | Flags when current volume exceeds *N×* the rolling 20-day average (default: 3×) |
| **Price velocity detection** | Flags sharp moves from session open (default: ≥ 1.5 %) |
| **Cross-asset coordination** | Flags when ≥ 2 different asset classes spike *simultaneously* — the strongest "insider" signal |
| **Alert cooldown** | Suppresses duplicate alerts for the same instrument (default: 5 min) |
| **Multiple alert channels** | Console, e-mail (SMTP), Slack, Telegram, Discord |
| **Fully configurable** | All thresholds and credentials controlled via environment variables or `.env` |
| **No API key required** | Uses [yfinance](https://github.com/ranaroussi/yfinance) (Yahoo Finance) — zero sign-up |
| **Stock strategy tools** | Momentum, Mean Reversion, MA Crossover, Support/Resistance, Pairs Trading |

---

## Quick start

```bash
# 1. Clone
git clone https://github.com/gupa1012/Become-an-insider.git
cd Become-an-insider

# 2. Install dependencies (Python 3.10+)
pip install -r requirements.txt

# 3. Run (console output, 60-second scan interval)
python -m src.monitor
```

### Single scan (useful for testing or cron jobs)
```bash
python -m src.monitor --once
```

### Custom thresholds
```bash
python -m src.monitor \
  --interval 30 \          # scan every 30 s
  --volume-threshold 4 \   # flag at 4× average volume
  --price-threshold 2      # flag at ≥ 2 % price move
```

---

## Example output

```
  ╔══════════════════════════════════════════════════════════════════════╗
  ║           🕵️  BECOME AN INSIDER – Unusual Activity Monitor           ║
  ║      Spots volume spikes & price moves before the crowd does         ║
  ╚══════════════════════════════════════════════════════════════════════╝

  Monitoring 33 assets every 60s.
  Volume spike threshold : 3.0×
  Press Ctrl+C to stop.

────────────────────────────────────────────────────────────────────────
📊  [HIGH] VOLUME_SPIKE — WTI Crude Oil Futures (CL=F)
   Category : Oil & Gas Futures
   Detail   : Volume is 5.3× the 20-period average (18,432,000 vs avg 3,478,000)
   Price    : 82.47
   Vol Ratio: 5.3×
   Time     : 2024-06-01 14:32:07 UTC
────────────────────────────────────────────────────────────────────────
🔗  [HIGH] CROSS_ASSET — WTI Crude Oil Futures (CL=F)
   Category : Oil & Gas Futures
   Detail   : Cross-asset coordination detected across 3 categories
              (Crypto, Equity Index, Oil & Gas Futures) — 5 instruments
              spiking simultaneously
   Time     : 2024-06-01 14:32:07 UTC
────────────────────────────────────────────────────────────────────────
```

---

## Stock strategy tools (from *151 Trading Strategies*)

Five quantitative strategies from Chapter 3 (Stocks) of [SSRN 3247865](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3247865) are implemented in `src/strategies.py`. Each can be used as a standalone screening tool.

### Quick start

```python
from src.strategies import (
    momentum_screen,
    mean_reversion_screen,
    moving_average_crossover,
    support_resistance,
    pairs_trading,
    run_all_strategies,
)

# Run all strategies on the default stock watchlist
results = run_all_strategies()            # uses XOM, CVX, TSLA, AAPL, NVDA, META
for name, signals in results.items():
    print(f"\n── {name} ──")
    for s in signals:
        print(s)

# Or run individual strategies
momentum_results = momentum_screen(["AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA"])
ma_result = moving_average_crossover("TSLA")
pair = pairs_trading("XOM", "CVX")
```

### Strategy overview

| # | Strategy | Paper § | Signal | Key parameter |
|---|---|---|---|---|
| 1 | **Price Momentum** | § 3.1 | BUY winners / SELL losers by cumulative return over 12-month formation period (skip 1 month) | `formation_months`, `skip_months` |
| 2 | **Mean Reversion** | § 3.9 | BUY stocks whose return is below the cluster mean, SELL those above | `lookback_days` (default 21) |
| 3 | **Moving-Average Crossover** | § 3.12 | BUY on golden cross (MA₁₀ > MA₃₀), SELL on death cross | `short_window`, `long_window` |
| 4 | **Support & Resistance** | § 3.14 | Pivot = (H+L+C)/3; R = 2·Pivot−L; S = 2·Pivot−H. BUY above pivot, SELL below | Previous day's OHLC |
| 5 | **Pairs Trading** | § 3.8 | Short the "rich" leg, buy the "cheap" leg of a correlated stock pair | `lookback_days` (default 60) |

### Where does the data come from?

All data — for both the existing anomaly monitor **and** the new strategy tools — is fetched from **Yahoo Finance** via the [`yfinance`](https://github.com/ranaroussi/yfinance) Python package. No API key or account is required.

| Use case | Data fetched | yfinance call | Typical look-back |
|---|---|---|---|
| **Volume spike detection** (`detectors.py`) | Daily OHLCV bars | `Ticker.history(period="60d", interval="1d")` | 60 days |
| **Price velocity detection** (`detectors.py`) | Intraday 5-min bars | `Ticker.history(period="1d", interval="5m")` | 1 day (current session) |
| **Momentum screen** (`strategies.py`) | Daily close prices | `Ticker.history(period="…", interval="1d")` | ~13 months (12 + 1 skip) |
| **Mean reversion** (`strategies.py`) | Daily close prices | `Ticker.history(period="…", interval="1d")` | ~21 trading days |
| **MA crossover** (`strategies.py`) | Daily close prices | `Ticker.history(period="…", interval="1d")` | ~90 trading days |
| **Support & resistance** (`strategies.py`) | Daily OHLC bars | `Ticker.history(period="5d", interval="1d")` | 2 days (prev + current) |
| **Pairs trading** (`strategies.py`) | Daily close prices | `Ticker.history(period="…", interval="1d")` | 60 trading days |

**Important:** yfinance retrieves data from Yahoo Finance's public endpoints. Data is delayed (typically 15 min for US equities). Crypto pairs (e.g. `BTC-USD`) are available 24/7. Futures symbols use Yahoo's convention (e.g. `CL=F` for WTI Crude).

---

## Alert channels

Configure alert channels via environment variables (copy `.env.example` to `.env`):

### Telegram (recommended for mobile alerts)
```bash
TELEGRAM_ENABLED=true
TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
TELEGRAM_CHAT_ID=987654321
```

### Slack
```bash
SLACK_ENABLED=true
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/T.../B.../xxx
```

### Discord
```bash
DISCORD_ENABLED=true
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/.../...
```

### E-mail
```bash
EMAIL_ENABLED=true
EMAIL_SMTP_HOST=smtp.gmail.com
EMAIL_SMTP_PORT=587
EMAIL_USERNAME=you@gmail.com
EMAIL_PASSWORD=your_app_password   # Gmail: use an App Password
EMAIL_FROM=you@gmail.com
EMAIL_TO=recipient@example.com
```

---

## Configuration reference

| Variable | Default | Description |
|---|---|---|
| `CHECK_INTERVAL_SECONDS` | `60` | Seconds between scans |
| `VOLUME_LOOKBACK_PERIODS` | `20` | Daily bars used for rolling average |
| `VOLUME_SPIKE_THRESHOLD` | `3.0` | Multiple above avg volume to trigger alert |
| `PRICE_CHANGE_THRESHOLD_PCT` | `1.5` | % move from session open to trigger alert |
| `CROSS_ASSET_MIN_CATEGORIES` | `2` | Min categories spiking for cross-asset alert |
| `ALERT_COOLDOWN_SECONDS` | `300` | Seconds before re-alerting same instrument |
| `CONSOLE_ALERTS` | `true` | Print alerts to terminal |
| `EMAIL_ENABLED` | `false` | Enable e-mail alerts |
| `SLACK_ENABLED` | `false` | Enable Slack alerts |
| `TELEGRAM_ENABLED` | `false` | Enable Telegram alerts |
| `DISCORD_ENABLED` | `false` | Enable Discord alerts |

---

## Watched assets (default)

| Category | Symbols |
|---|---|
| Oil & Gas Futures | `CL=F` `BZ=F` `NG=F` `RB=F` `HO=F` |
| Metals Futures | `GC=F` `SI=F` `HG=F` `PL=F` |
| Crypto | `BTC-USD` `ETH-USD` `SOL-USD` `XRP-USD` |
| Equity Index | `ES=F` `NQ=F` `YM=F` `RTY=F` `SPY` `QQQ` |
| Stocks | `XOM` `CVX` `TSLA` `AAPL` `NVDA` `META` |
| Forex | `EURUSD=X` `USDJPY=X` `GBPUSD=X` `DX-Y.NYB` |
| Bonds | `ZN=F` `ZB=F` `TLT` |

---

## Running tests

```bash
pip install pytest
python -m pytest tests/ -v
```

---

## Project structure

```
Become-an-insider/
├── src/
│   ├── assets.py      # Asset watchlist definitions
│   ├── config.py      # All configuration (env-var driven)
│   ├── detectors.py   # Volume spike, price velocity & cross-asset detection
│   ├── alerts.py      # Alert formatting & delivery (console/email/Slack/Telegram/Discord)
│   ├── monitor.py     # Main monitoring loop + CLI entry-point
│   └── strategies.py  # Stock strategy tools (momentum, mean-reversion, MA, S/R, pairs)
├── tests/
│   ├── test_detectors.py
│   ├── test_alerts.py
│   └── test_strategies.py
├── ssrn-3247865.pdf   # "151 Trading Strategies" (Kakushadze & Serur, 2018)
├── .env.example       # Template for environment variables
└── requirements.txt
```

---

## Disclaimer

This tool is for **informational purposes only**. It does not constitute financial advice. Trading involves substantial risk of loss. Always do your own research.

