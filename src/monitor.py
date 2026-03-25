"""
Main monitoring loop for the unusual trading activity monitor.

Usage
─────
Run directly:
    python -m src.monitor

Or use the CLI entry-point (defined in setup.py / pyproject.toml):
    become-insider

Optional environment variables (or .env file):
    CHECK_INTERVAL_SECONDS=60   # How often to scan (default: 60 s)
    VOLUME_SPIKE_THRESHOLD=3.0  # Vol multiple to flag (default: 3×)
    PRICE_CHANGE_THRESHOLD_PCT=1.5
    ALERT_COOLDOWN_SECONDS=300  # Suppress duplicate alerts (default: 5 min)
    … (see src/config.py for the full list)

Alert channels are enabled via environment variables:
    CONSOLE_ALERTS=true          (default: true)
    EMAIL_ENABLED=true
    SLACK_ENABLED=true
    TELEGRAM_ENABLED=true
    DISCORD_ENABLED=true
"""

from __future__ import annotations

import argparse
import logging
import signal as _signal
import sys
import time
from datetime import datetime, timezone
from typing import Optional

from .alerts import dispatch_signals, format_signal_console
from .assets import DEFAULT_WATCHLIST, Asset
from .config import Config, get_config, set_config
from .detectors import Signal, scan_watchlist

logger = logging.getLogger(__name__)

_BANNER = r"""
  ╔══════════════════════════════════════════════════════════════════════╗
  ║           🕵️  BECOME AN INSIDER – Unusual Activity Monitor           ║
  ║      Spots volume spikes & price moves before the crowd does         ║
  ╚══════════════════════════════════════════════════════════════════════╝
"""


# ---------------------------------------------------------------------------
# Cooldown tracker
# ---------------------------------------------------------------------------

class CooldownTracker:
    """Tracks the last alert time per (symbol, signal_type) to avoid spam."""

    def __init__(self, cooldown_seconds: int = 300) -> None:
        self._cooldown = cooldown_seconds
        self._last_alert: dict[tuple[str, str], float] = {}

    def is_allowed(self, signal: Signal) -> bool:
        key = (signal.asset.symbol, signal.signal_type)
        now = time.monotonic()
        last = self._last_alert.get(key, 0.0)
        if now - last >= self._cooldown:
            self._last_alert[key] = now
            return True
        return False

    def filter(self, signals: list[Signal]) -> list[Signal]:
        return [s for s in signals if self.is_allowed(s)]


# ---------------------------------------------------------------------------
# Monitor
# ---------------------------------------------------------------------------

class Monitor:
    """
    Encapsulates the monitoring loop.

    Parameters
    ----------
    watchlist : list[Asset]
        Assets to monitor.  Defaults to ``DEFAULT_WATCHLIST``.
    config : Config
        Runtime configuration.  Falls back to ``get_config()``.
    max_iterations : int | None
        If set, stop after this many scan iterations (useful for testing).
    """

    def __init__(
        self,
        watchlist: Optional[list[Asset]] = None,
        config: Optional[Config] = None,
        max_iterations: Optional[int] = None,
    ) -> None:
        self.watchlist = watchlist or DEFAULT_WATCHLIST
        self.config = config or get_config()
        self.max_iterations = max_iterations
        self._cooldown = CooldownTracker(self.config.alert_cooldown_seconds)
        self._running = False
        self._iteration = 0
        self._total_signals = 0

    # ------------------------------------------------------------------
    def run_once(self) -> list[Signal]:
        """Run a single scan cycle and dispatch any new signals."""
        logger.debug("Starting scan #%d …", self._iteration + 1)
        scan_start = time.monotonic()

        signals = scan_watchlist(self.watchlist, self.config)
        fresh = self._cooldown.filter(signals)

        if fresh:
            dispatch_signals(fresh, self.config)
            self._total_signals += len(fresh)
        else:
            _ts = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
            print(
                f"  [{_ts}] Scan #{self._iteration + 1} complete – "
                f"no unusual activity detected ({len(self.watchlist)} assets scanned)"
            )

        elapsed = time.monotonic() - scan_start
        logger.debug("Scan completed in %.1f s", elapsed)
        self._iteration += 1
        return fresh

    # ------------------------------------------------------------------
    def start(self) -> None:
        """Enter the monitoring loop (blocks until stopped)."""
        print(_BANNER)
        print(
            f"  Monitoring {len(self.watchlist)} assets every "
            f"{self.config.check_interval_seconds}s.\n"
            f"  Volume spike threshold : {self.config.volume_spike_threshold}×\n"
            f"  Price velocity threshold: {self.config.price_change_threshold_pct}%\n"
            f"  Alert cooldown         : {self.config.alert_cooldown_seconds}s\n"
            f"  Press Ctrl+C to stop.\n"
        )

        self._running = True
        self._iteration = 0

        # Graceful shutdown on SIGINT / SIGTERM
        def _handle_stop(signum, frame):  # type: ignore[type-arg]
            print("\n  Stopping monitor…")
            self._running = False

        _signal.signal(_signal.SIGINT, _handle_stop)
        _signal.signal(_signal.SIGTERM, _handle_stop)

        while self._running:
            self.run_once()

            if self.max_iterations is not None and self._iteration >= self.max_iterations:
                break

            if not self._running:
                break

            # Sleep in small increments so SIGINT is handled promptly
            sleep_remaining = self.config.check_interval_seconds
            while sleep_remaining > 0 and self._running:
                chunk = min(sleep_remaining, 1)
                time.sleep(chunk)
                sleep_remaining -= chunk

        print(
            f"\n  Monitor stopped after {self._iteration} scan(s), "
            f"{self._total_signals} signal(s) dispatched."
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="become-insider",
        description="Monitor unusual trading activity across asset classes.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run with defaults (console alerts, 60-second interval):
  python -m src.monitor

  # Custom thresholds:
  python -m src.monitor --interval 30 --volume-threshold 4 --price-threshold 2

  # Enable Slack alerts (also requires SLACK_WEBHOOK_URL env var):
  SLACK_ENABLED=true python -m src.monitor

  # Single scan (useful for testing):
  python -m src.monitor --once
""",
    )
    p.add_argument(
        "--interval",
        type=int,
        metavar="SECONDS",
        help="Seconds between scans (default: from CHECK_INTERVAL_SECONDS env or 60)",
    )
    p.add_argument(
        "--volume-threshold",
        type=float,
        metavar="MULTIPLIER",
        help="Volume spike multiplier (default: 3.0)",
    )
    p.add_argument(
        "--price-threshold",
        type=float,
        metavar="PCT",
        help="Price change %% threshold (default: 1.5)",
    )
    p.add_argument(
        "--cooldown",
        type=int,
        metavar="SECONDS",
        help="Alert cooldown in seconds (default: 300)",
    )
    p.add_argument(
        "--once",
        action="store_true",
        help="Run a single scan then exit",
    )
    p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose (DEBUG) logging",
    )
    return p


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        level=log_level,
    )

    cfg = get_config()

    # Apply CLI overrides
    if args.interval is not None:
        cfg.check_interval_seconds = args.interval
    if args.volume_threshold is not None:
        cfg.volume_spike_threshold = args.volume_threshold
    if args.price_threshold is not None:
        cfg.price_change_threshold_pct = args.price_threshold
    if args.cooldown is not None:
        cfg.alert_cooldown_seconds = args.cooldown

    max_iterations = 1 if args.once else None
    monitor = Monitor(config=cfg, max_iterations=max_iterations)
    monitor.start()
    return 0


if __name__ == "__main__":
    sys.exit(main())
