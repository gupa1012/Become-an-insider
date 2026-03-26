"""
Unit tests for the alert system in src/alerts.py.

These tests mock network calls and validate formatting, dispatch logic,
and cooldown behaviour.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.alerts import (
    dispatch_signal,
    dispatch_signals,
    format_signal_console,
    format_signal_text,
    send_console,
    send_discord,
    send_email,
    send_slack,
    send_telegram,
)
from src.assets import Asset, CATEGORY_OIL_GAS, CATEGORY_CRYPTO
from src.config import Config
from src.detectors import Signal
from src.monitor import CooldownTracker

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_OIL = Asset("CL=F", "WTI Crude", CATEGORY_OIL_GAS)
_BTC = Asset("BTC-USD", "Bitcoin", CATEGORY_CRYPTO)


def _make_signal(
    asset: Asset = _OIL,
    signal_type: str = "VOLUME_SPIKE",
    severity: str = "HIGH",
) -> Signal:
    return Signal(
        asset=asset,
        signal_type=signal_type,
        severity=severity,
        description="Test spike",
        current_price=75.5,
        price_change_pct=3.2,
        current_volume=5_000_000.0,
        avg_volume=1_000_000.0,
        volume_ratio=5.0,
        detected_at=datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc),
    )


# ---------------------------------------------------------------------------
# format_signal_text
# ---------------------------------------------------------------------------

class TestFormatSignalText:
    def test_contains_asset_name(self):
        text = format_signal_text(_make_signal())
        assert "WTI Crude" in text

    def test_contains_symbol(self):
        text = format_signal_text(_make_signal())
        assert "CL=F" in text

    def test_contains_signal_type(self):
        text = format_signal_text(_make_signal())
        assert "VOLUME_SPIKE" in text

    def test_contains_severity(self):
        text = format_signal_text(_make_signal())
        assert "HIGH" in text

    def test_contains_price(self):
        text = format_signal_text(_make_signal())
        assert "75.5" in text

    def test_contains_vol_ratio(self):
        text = format_signal_text(_make_signal())
        assert "5.0" in text

    def test_no_vol_ratio_when_none(self):
        sig = _make_signal()
        sig.volume_ratio = None
        text = format_signal_text(sig)
        assert "Vol Ratio" not in text


# ---------------------------------------------------------------------------
# format_signal_console
# ---------------------------------------------------------------------------

class TestFormatSignalConsole:
    def test_contains_emoji(self):
        text = format_signal_console(_make_signal())
        assert "📊" in text  # VOLUME_SPIKE emoji

    def test_price_velocity_emoji(self):
        sig = _make_signal(signal_type="PRICE_VELOCITY")
        text = format_signal_console(sig)
        assert "🚀" in text

    def test_cross_asset_emoji(self):
        sig = _make_signal(signal_type="CROSS_ASSET")
        text = format_signal_console(sig)
        assert "🔗" in text


# ---------------------------------------------------------------------------
# send_email
# ---------------------------------------------------------------------------

class TestSendEmail:
    def test_disabled_returns_false(self):
        cfg = Config(email_enabled=False)
        assert send_email(_make_signal(), cfg) is False

    def test_missing_credentials_returns_false(self):
        cfg = Config(email_enabled=True)  # all creds empty
        assert send_email(_make_signal(), cfg) is False

    def test_smtp_failure_returns_false_and_no_raise(self):
        cfg = Config(
            email_enabled=True,
            email_username="u@example.com",
            email_password="secret",
            email_from="u@example.com",
            email_to="v@example.com",
        )
        with patch("smtplib.SMTP") as mock_smtp:
            mock_smtp.return_value.__enter__.return_value.sendmail.side_effect = (
                OSError("connection refused")
            )
            # Should not raise
            result = send_email(_make_signal(), cfg)
            assert result is False

    def test_success_path(self):
        cfg = Config(
            email_enabled=True,
            email_username="u@example.com",
            email_password="secret",
            email_from="u@example.com",
            email_to="v@example.com",
        )
        with patch("smtplib.SMTP") as mock_smtp:
            ctx = MagicMock()
            mock_smtp.return_value.__enter__.return_value = ctx
            ctx.sendmail.return_value = {}
            result = send_email(_make_signal(), cfg)
            assert result is True


# ---------------------------------------------------------------------------
# send_slack
# ---------------------------------------------------------------------------

class TestSendSlack:
    def test_disabled_returns_false(self):
        cfg = Config(slack_enabled=False)
        assert send_slack(_make_signal(), cfg) is False

    def test_no_webhook_url_returns_false(self):
        cfg = Config(slack_enabled=True, slack_webhook_url="")
        assert send_slack(_make_signal(), cfg) is False

    def test_success(self):
        cfg = Config(slack_enabled=True, slack_webhook_url="https://hooks.slack.com/x")
        with patch("requests.post") as mock_post:
            mock_post.return_value.raise_for_status = MagicMock()
            result = send_slack(_make_signal(), cfg)
            assert result is True
            mock_post.assert_called_once()

    def test_http_failure_returns_false(self):
        cfg = Config(slack_enabled=True, slack_webhook_url="https://hooks.slack.com/x")
        with patch("requests.post") as mock_post:
            mock_post.side_effect = OSError("network error")
            result = send_slack(_make_signal(), cfg)
            assert result is False


# ---------------------------------------------------------------------------
# send_telegram
# ---------------------------------------------------------------------------

class TestSendTelegram:
    def test_disabled_returns_false(self):
        cfg = Config(telegram_enabled=False)
        assert send_telegram(_make_signal(), cfg) is False

    def test_missing_token_returns_false(self):
        cfg = Config(
            telegram_enabled=True,
            telegram_bot_token="",
            telegram_chat_id="12345",
        )
        assert send_telegram(_make_signal(), cfg) is False

    def test_success(self):
        cfg = Config(
            telegram_enabled=True,
            telegram_bot_token="token123",
            telegram_chat_id="12345",
        )
        with patch("requests.post") as mock_post:
            mock_post.return_value.raise_for_status = MagicMock()
            result = send_telegram(_make_signal(), cfg)
            assert result is True


# ---------------------------------------------------------------------------
# send_discord
# ---------------------------------------------------------------------------

class TestSendDiscord:
    def test_disabled_returns_false(self):
        cfg = Config(discord_enabled=False)
        assert send_discord(_make_signal(), cfg) is False

    def test_success(self):
        cfg = Config(discord_enabled=True, discord_webhook_url="https://discord.com/x")
        with patch("requests.post") as mock_post:
            mock_post.return_value.raise_for_status = MagicMock()
            result = send_discord(_make_signal(), cfg)
            assert result is True

    def test_colour_mapping_high(self):
        cfg = Config(discord_enabled=True, discord_webhook_url="https://discord.com/x")
        with patch("requests.post") as mock_post:
            mock_post.return_value.raise_for_status = MagicMock()
            send_discord(_make_signal(severity="HIGH"), cfg)
            payload = mock_post.call_args.kwargs["json"]
            assert payload["embeds"][0]["color"] == 0xFF0000


# ---------------------------------------------------------------------------
# dispatch_signals – deduplication
# ---------------------------------------------------------------------------

class TestDispatchSignals:
    def test_dispatches_each_unique_symbol_type_once(self, capsys):
        cfg = Config(console_alerts=True, email_enabled=False,
                     slack_enabled=False, telegram_enabled=False,
                     discord_enabled=False)
        signals = [
            _make_signal(_OIL, "VOLUME_SPIKE", "HIGH"),
            _make_signal(_OIL, "VOLUME_SPIKE", "MEDIUM"),  # duplicate key
            _make_signal(_BTC, "VOLUME_SPIKE", "HIGH"),
        ]
        dispatch_signals(signals, cfg)
        captured = capsys.readouterr()
        # OIL VOLUME_SPIKE should appear once, BTC once
        assert captured.out.count("CL=F") == 1
        assert captured.out.count("BTC-USD") == 1


# ---------------------------------------------------------------------------
# CooldownTracker
# ---------------------------------------------------------------------------

class TestCooldownTracker:
    def test_first_call_is_allowed(self):
        tracker = CooldownTracker(cooldown_seconds=60)
        sig = _make_signal()
        assert tracker.is_allowed(sig) is True

    def test_second_call_within_cooldown_is_blocked(self):
        tracker = CooldownTracker(cooldown_seconds=60)
        sig = _make_signal()
        tracker.is_allowed(sig)
        assert tracker.is_allowed(sig) is False

    def test_different_symbols_are_independent(self):
        tracker = CooldownTracker(cooldown_seconds=60)
        sig_oil = _make_signal(_OIL)
        sig_btc = _make_signal(_BTC)
        tracker.is_allowed(sig_oil)
        assert tracker.is_allowed(sig_btc) is True

    def test_filter_removes_duplicates(self):
        tracker = CooldownTracker(cooldown_seconds=60)
        sig = _make_signal()
        result = tracker.filter([sig, sig])
        assert len(result) == 1
