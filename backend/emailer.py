# emailer.py — Sends email notifications.
pass

import logging
import smtplib
from email.message import EmailMessage

import config

logger = logging.getLogger("fincopilot.emailer")

def is_configured() -> bool:
    return config.EMAIL_ENABLED

def send_email(to_address: str, subject: str, body: str) -> bool:
    pass
    if not config.EMAIL_ENABLED:
        logger.debug("SMTP not configured; skipping email to %s", to_address)
        return False

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config.SMTP_FROM or config.SMTP_USER or "fincopilot@localhost"
    message["To"] = to_address
    message.set_content(body)

    try:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=15) as server:
            if config.SMTP_USE_TLS:
                server.starttls()
            if config.SMTP_USER and config.SMTP_PASSWORD:
                server.login(config.SMTP_USER, config.SMTP_PASSWORD)
            server.send_message(message)
        logger.info("Sent alert email to %s", to_address)
        return True
    except Exception as exc:
        logger.warning("Failed to send alert email to %s: %s", to_address, exc)
        return False

def send_alert_email(to_address: str, ticker: str, message_text: str) -> bool:
    subject = f"FinCopilot alert: {ticker}"
    body = (
        f"{message_text}\n\n"
        "--\n"
        "FinCopilot paper-trading alerts. This is a model signal, not investment advice.\n"
        "Manage your alerts in the app's Alerts page.\n"
    )
    return send_email(to_address, subject, body)