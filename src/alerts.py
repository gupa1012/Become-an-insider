"""
Alert delivery for the unusual trading activity monitor.

Supported channels
──────────────────
• Console  – always available; colourised terminal output
• E-mail   – SMTP (works with Gmail, SendGrid SMTP relay, etc.)
• Slack    – Incoming Webhook
• Telegram – Bot API
• Discord  – Incoming Webhook

All network-based channels are best-effort: failures are logged as warnings
and do NOT crash the monitor.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import List

import requests

from .config import get_config
from .detectors import Signal

logger = logging.getLogger(__name__)

try:
    from colorama import Fore, Style, init as colorama_init
    colorama_init(autoreset=True)
    _HAS_COLORAMA = True
except ImportError:
    _HAS_COLORAMA = False


# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------

_SEVERITY_COLOURS = {
    "HIGH": "\033[91m",    # bright red
    "MEDIUM": "\033[93m",  # bright yellow
    "LOW": "\033[96m",     # bright cyan
}
_RESET = "\033[0m"


def _colour(text: str, severity: str) -> str:
    if _HAS_COLORAMA:
        colour_map = {
            "HIGH": Fore.RED,
            "MEDIUM": Fore.YELLOW,
            "LOW": Fore.CYAN,
        }
        return f"{colour_map.get(severity, '')}{text}{Style.RESET_ALL}"
    code = _SEVERITY_COLOURS.get(severity, "")
    return f"{code}{text}{_RESET}" if code else text


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

_DIVIDER = "─" * 72

_SIGNAL_TYPE_EMOJI = {
    "VOLUME_SPIKE": "📊",
    "PRICE_VELOCITY": "🚀",
    "CROSS_ASSET": "🔗",
}


def format_signal_console(signal: Signal) -> str:
    """Return a formatted, colourised string for terminal output."""
    emoji = _SIGNAL_TYPE_EMOJI.get(signal.signal_type, "⚠️")
    header = _colour(
        f"{emoji}  [{signal.severity}] {signal.signal_type} — "
        f"{signal.asset.name} ({signal.asset.symbol})",
        signal.severity,
    )
    lines = [
        _DIVIDER,
        header,
        f"   Category : {signal.asset.category}",
        f"   Detail   : {signal.description}",
    ]
    if signal.current_price is not None:
        lines.append(f"   Price    : {signal.current_price:.6g}")
    if signal.price_change_pct is not None:
        lines.append(f"   Δ Price  : {signal.price_change_pct:+.2f}%")
    if signal.volume_ratio is not None:
        lines.append(f"   Vol Ratio: {signal.volume_ratio:.1f}×")
    lines.append(f"   Time     : {signal.detected_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    lines.append(_DIVIDER)
    return "\n".join(lines)


def format_signal_text(signal: Signal) -> str:
    """Return a plain-text representation suitable for e-mail / webhooks."""
    lines = [
        f"[{signal.severity}] {signal.signal_type}",
        f"Asset    : {signal.asset.name} ({signal.asset.symbol})",
        f"Category : {signal.asset.category}",
        f"Detail   : {signal.description}",
    ]
    if signal.current_price is not None:
        lines.append(f"Price    : {signal.current_price:.6g}")
    if signal.price_change_pct is not None:
        lines.append(f"Δ Price  : {signal.price_change_pct:+.2f}%")
    if signal.volume_ratio is not None:
        lines.append(f"Vol Ratio: {signal.volume_ratio:.1f}×")
    lines.append(f"Time     : {signal.detected_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Individual senders
# ---------------------------------------------------------------------------

def send_console(signal: Signal) -> None:
    """Print a colourised alert to stdout."""
    print(format_signal_console(signal))


def send_email(signal: Signal, config=None) -> bool:
    """Send an e-mail alert via SMTP. Returns True on success."""
    if config is None:
        config = get_config()
    if not config.email_enabled:
        return False
    if not (config.email_username and config.email_password and
            config.email_from and config.email_to):
        logger.warning("E-mail alert skipped: incomplete SMTP configuration")
        return False

    subject = (
        f"[{signal.severity}] Unusual Activity: {signal.asset.name} "
        f"({signal.signal_type})"
    )
    body = format_signal_text(signal)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = config.email_from
    msg["To"] = config.email_to
    msg.attach(MIMEText(body, "plain"))

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP(config.email_smtp_host, config.email_smtp_port) as server:
            server.ehlo()
            server.starttls(context=context)
            server.login(config.email_username, config.email_password)
            server.sendmail(config.email_from, config.email_to, msg.as_string())
        logger.info("E-mail alert sent to %s", config.email_to)
        return True
    except Exception as exc:
        logger.warning("Failed to send e-mail alert: %s", exc)
        return False


def send_slack(signal: Signal, config=None) -> bool:
    """Post an alert to a Slack channel via Incoming Webhook. Returns True on success."""
    if config is None:
        config = get_config()
    if not config.slack_enabled or not config.slack_webhook_url:
        return False

    emoji = _SIGNAL_TYPE_EMOJI.get(signal.signal_type, "⚠️")
    payload = {
        "text": (
            f"{emoji} *[{signal.severity}] {signal.signal_type}* — "
            f"{signal.asset.name} ({signal.asset.symbol})\n"
            f"```{format_signal_text(signal)}```"
        )
    }
    try:
        resp = requests.post(config.slack_webhook_url, json=payload, timeout=10)
        resp.raise_for_status()
        logger.info("Slack alert sent")
        return True
    except Exception as exc:
        logger.warning("Failed to send Slack alert: %s", exc)
        return False


def send_telegram(signal: Signal, config=None) -> bool:
    """Send an alert via Telegram Bot API. Returns True on success."""
    if config is None:
        config = get_config()
    if not config.telegram_enabled or not (
        config.telegram_bot_token and config.telegram_chat_id
    ):
        return False

    emoji = _SIGNAL_TYPE_EMOJI.get(signal.signal_type, "⚠️")
    text = (
        f"{emoji} *\\[{signal.severity}\\] {signal.signal_type}*\n"
        f"*{signal.asset.name}* \\({signal.asset.symbol}\\)\n"
        f"```\n{format_signal_text(signal)}\n```"
    )
    url = (
        f"https://api.telegram.org/bot{config.telegram_bot_token}/sendMessage"
    )
    payload = {
        "chat_id": config.telegram_chat_id,
        "text": text,
        "parse_mode": "MarkdownV2",
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        logger.info("Telegram alert sent")
        return True
    except Exception as exc:
        logger.warning("Failed to send Telegram alert: %s", exc)
        return False


def send_discord(signal: Signal, config=None) -> bool:
    """Post an alert to a Discord channel via Incoming Webhook. Returns True on success."""
    if config is None:
        config = get_config()
    if not config.discord_enabled or not config.discord_webhook_url:
        return False

    emoji = _SIGNAL_TYPE_EMOJI.get(signal.signal_type, "⚠️")
    colour_map = {"HIGH": 0xFF0000, "MEDIUM": 0xFFAA00, "LOW": 0x00CCFF}
    colour = colour_map.get(signal.severity, 0x888888)

    payload = {
        "embeds": [
            {
                "title": (
                    f"{emoji} [{signal.severity}] {signal.signal_type} — "
                    f"{signal.asset.name} ({signal.asset.symbol})"
                ),
                "description": format_signal_text(signal),
                "color": colour,
                "timestamp": signal.detected_at.isoformat(),
            }
        ]
    }
    try:
        resp = requests.post(config.discord_webhook_url, json=payload, timeout=10)
        resp.raise_for_status()
        logger.info("Discord alert sent")
        return True
    except Exception as exc:
        logger.warning("Failed to send Discord alert: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

def dispatch_signal(signal: Signal, config=None) -> None:
    """
    Dispatch a signal to all configured alert channels.

    This is the single entry-point used by the monitor loop.
    """
    if config is None:
        config = get_config()

    if config.console_alerts:
        send_console(signal)

    if config.email_enabled:
        send_email(signal, config)

    if config.slack_enabled:
        send_slack(signal, config)

    if config.telegram_enabled:
        send_telegram(signal, config)

    if config.discord_enabled:
        send_discord(signal, config)


def dispatch_signals(signals: List[Signal], config=None) -> None:
    """Dispatch a list of signals, deduplicated by symbol+type per call."""
    seen: set[tuple[str, str]] = set()
    for sig in signals:
        key = (sig.asset.symbol, sig.signal_type)
        if key not in seen:
            seen.add(key)
            dispatch_signal(sig, config)
