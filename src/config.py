"""
Configuration for the unusual trading activity monitor.

Values are read from environment variables (or a .env file) so that secrets
(SMTP password, webhook URLs, Telegram tokens) are never hard-coded.

Every setting has a sensible default so the tool works out-of-the-box without
any configuration at all.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv is optional


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def _env_float(key: str, default: float) -> float:
    raw = _env(key)
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


def _env_int(key: str, default: int) -> int:
    raw = _env(key)
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _env_bool(key: str, default: bool = False) -> bool:
    raw = _env(key).lower()
    if raw in ("1", "true", "yes"):
        return True
    if raw in ("0", "false", "no"):
        return False
    return default


@dataclass
class Config:
    # ── Detection thresholds ───────────────────────────────────────────────
    # Number of historical periods used to compute the rolling average volume.
    volume_lookback_periods: int = field(
        default_factory=lambda: _env_int("VOLUME_LOOKBACK_PERIODS", 20)
    )
    # A volume spike is flagged when current_volume > threshold * avg_volume.
    volume_spike_threshold: float = field(
        default_factory=lambda: _env_float("VOLUME_SPIKE_THRESHOLD", 3.0)
    )
    # Percentage price change (absolute) that triggers a price-velocity alert.
    price_change_threshold_pct: float = field(
        default_factory=lambda: _env_float("PRICE_CHANGE_THRESHOLD_PCT", 1.5)
    )
    # Minimum number of *different* asset categories showing spikes at the
    # same scan to trigger a "coordinated / cross-asset" alert.
    cross_asset_min_categories: int = field(
        default_factory=lambda: _env_int("CROSS_ASSET_MIN_CATEGORIES", 2)
    )

    # ── Monitor loop ───────────────────────────────────────────────────────
    # How often (in seconds) the main loop fetches fresh data.
    check_interval_seconds: int = field(
        default_factory=lambda: _env_int("CHECK_INTERVAL_SECONDS", 60)
    )
    # Silence repeated alerts for the same symbol for this many seconds.
    alert_cooldown_seconds: int = field(
        default_factory=lambda: _env_int("ALERT_COOLDOWN_SECONDS", 300)
    )

    # ── Alert channels ─────────────────────────────────────────────────────
    # Console (always on)
    console_alerts: bool = field(
        default_factory=lambda: _env_bool("CONSOLE_ALERTS", True)
    )
    # E-mail (SMTP)
    email_enabled: bool = field(
        default_factory=lambda: _env_bool("EMAIL_ENABLED", False)
    )
    email_smtp_host: str = field(
        default_factory=lambda: _env("EMAIL_SMTP_HOST", "smtp.gmail.com")
    )
    email_smtp_port: int = field(
        default_factory=lambda: _env_int("EMAIL_SMTP_PORT", 587)
    )
    email_username: str = field(
        default_factory=lambda: _env("EMAIL_USERNAME")
    )
    email_password: str = field(
        default_factory=lambda: _env("EMAIL_PASSWORD")
    )
    email_from: str = field(
        default_factory=lambda: _env("EMAIL_FROM")
    )
    email_to: str = field(
        default_factory=lambda: _env("EMAIL_TO")
    )
    # Slack (incoming webhook)
    slack_enabled: bool = field(
        default_factory=lambda: _env_bool("SLACK_ENABLED", False)
    )
    slack_webhook_url: str = field(
        default_factory=lambda: _env("SLACK_WEBHOOK_URL")
    )
    # Telegram bot
    telegram_enabled: bool = field(
        default_factory=lambda: _env_bool("TELEGRAM_ENABLED", False)
    )
    telegram_bot_token: str = field(
        default_factory=lambda: _env("TELEGRAM_BOT_TOKEN")
    )
    telegram_chat_id: str = field(
        default_factory=lambda: _env("TELEGRAM_CHAT_ID")
    )
    # Discord (incoming webhook)
    discord_enabled: bool = field(
        default_factory=lambda: _env_bool("DISCORD_ENABLED", False)
    )
    discord_webhook_url: str = field(
        default_factory=lambda: _env("DISCORD_WEBHOOK_URL")
    )


# Singleton used by the rest of the package.
_config: Optional[Config] = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config()
    return _config


def set_config(cfg: Config) -> None:
    """Override the global config (useful in tests)."""
    global _config
    _config = cfg
