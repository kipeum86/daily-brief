"""Send the daily briefing email via Resend API.

Public API:
    send_email(config, html_body, date_str) → bool
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger("daily-brief.deliver.mailer")


def _extract_first_line(insight: str, max_len: int = 60) -> str:
    """Extract the first meaningful line from insight text for the subject.

    Strips HTML tags and truncates to max_len characters.
    """
    import re

    # Strip HTML tags
    clean = re.sub(r"<[^>]+>", "", insight or "")
    # Collapse whitespace
    clean = re.sub(r"\s+", " ", clean).strip()

    if not clean:
        return ""

    # Take first sentence or up to max_len chars
    first_line = clean.split(".")[0].strip()
    if len(first_line) > max_len:
        first_line = first_line[: max_len - 1] + "…"

    return first_line


def send_email(
    config: dict[str, Any],
    html_body: str,
    date_str: str,
    insight_text: str = "",
) -> bool:
    """Send the briefing email to all subscribers via Resend API.

    Args:
        config: Loaded config dict (needs email.sender_name, email.sender_email,
                email.subject_prefix, email.subscribers).
        html_body: Fully rendered HTML email body.
        date_str: Human-readable date string (e.g. "2026년 3월 24일 월요일").
        insight_text: Raw AI insight text (used for subject line snippet).

    Returns:
        True if email was sent successfully, False otherwise.
    """
    # Check if email delivery is enabled
    email_config = config.get("email", {})
    if not email_config.get("enabled", False):
        logger.info("Email delivery disabled in config — skipping")
        return False

    # Check for Resend API key
    api_key = os.environ.get("RESEND_API_KEY", "")
    if not api_key:
        logger.warning(
            "RESEND_API_KEY not set — email delivery skipped. "
            "Set the environment variable to enable email sending."
        )
        return False

    # Validate subscribers
    subscribers = email_config.get("subscribers", [])
    if not subscribers:
        logger.warning("No subscribers configured in config.email.subscribers")
        return False

    # Build subject line
    prefix = email_config.get("subject_prefix", "Daily Brief")
    snippet = _extract_first_line(insight_text)
    if snippet:
        subject = f"{prefix} · {date_str} — {snippet}"
    else:
        subject = f"{prefix} · {date_str}"

    # Sender
    sender_name = email_config.get("sender_name", "Daily Brief")
    sender_email = email_config.get("sender_email", "brief@yourdomain.com")
    from_address = f"{sender_name} <{sender_email}>"

    try:
        import resend

        resend.api_key = api_key

        params = {
            "from": from_address,
            "to": subscribers,
            "subject": subject,
            "html": html_body,
        }

        response = resend.Emails.send(params)
        logger.info(
            "Email sent successfully to %d recipient(s): %s",
            len(subscribers),
            response.get("id", "unknown"),
        )
        return True

    except ImportError:
        logger.error(
            "resend package not installed. Run: pip install resend"
        )
        return False

    except Exception as exc:
        logger.error("Failed to send email via Resend: %s", exc, exc_info=True)
        return False
