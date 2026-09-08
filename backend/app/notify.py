"""Email delivery for alerts — plain SMTP, configured from backend/.env.

Kept provider-agnostic: any SMTP host works (Gmail, Fastmail, Zoho, Resend's
SMTP bridge). Nothing here knows what an alert *is* — callers hand it a
subject, a plain-text body and an HTML body.
"""
from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate
from typing import List, Optional


class EmailNotConfigured(RuntimeError):
    """Raised when SMTP settings are missing — callers degrade, never crash."""


def _recipients() -> List[str]:
    raw = os.environ.get("ALERT_EMAIL_TO", "")
    return [a.strip() for a in raw.split(",") if a.strip()]


def is_configured() -> bool:
    return bool(
        os.environ.get("SMTP_HOST")
        and os.environ.get("SMTP_USER")
        and os.environ.get("SMTP_PASSWORD")
        and _recipients()
    )


def config_status() -> dict:
    """What the dashboard shows when alerts aren't wired up yet."""
    missing = [
        key
        for key in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "ALERT_EMAIL_TO")
        if not os.environ.get(key)
    ]
    return {
        "configured": not missing,
        "missing": missing,
        "host": os.environ.get("SMTP_HOST", ""),
        "from": os.environ.get("ALERT_EMAIL_FROM") or os.environ.get("SMTP_USER", ""),
        "to": _recipients(),
    }


def send_email(subject: str, text_body: str, html_body: Optional[str] = None) -> dict:
    """Send one alert. Raises EmailNotConfigured if SMTP settings are absent."""
    if not is_configured():
        raise EmailNotConfigured(
            "Email alerts are not configured. Set SMTP_HOST, SMTP_PORT, SMTP_USER, "
            "SMTP_PASSWORD and ALERT_EMAIL_TO in backend/.env."
        )

    host = os.environ["SMTP_HOST"]
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ["SMTP_USER"]
    password = os.environ["SMTP_PASSWORD"]
    sender = os.environ.get("ALERT_EMAIL_FROM") or user
    to = _recipients()

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(to)
    msg["Date"] = formatdate(localtime=True)
    msg.set_content(text_body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")

    context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=context, timeout=30) as s:
            s.login(user, password)
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=30) as s:
            s.starttls(context=context)
            s.login(user, password)
            s.send_message(msg)

    return {"sent": True, "to": to, "subject": subject}
