"""Send the daily briefing email via Gmail SMTP.

Public API:
    send_email(config, html_body, date_str, insight_text) → bool
"""

from __future__ import annotations

import logging
import os
import re
import smtplib
from pathlib import Path
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

logger = logging.getLogger("daily-brief.deliver.mailer")


def _extract_first_line(insight: str, max_len: int = 60) -> str:
    """Extract the first meaningful line from insight text for the subject."""
    clean = re.sub(r"<[^>]+>", "", insight or "")
    clean = re.sub(r"\s+", " ", clean).strip()
    if not clean:
        return ""
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
    """Send the briefing email to all subscribers via Gmail SMTP.

    환경변수:
        GMAIL_ADDRESS: 발신 Gmail 주소
        GMAIL_APP_PASSWORD: Gmail 앱 비밀번호 (16자리)

    Returns:
        True if email was sent successfully, False otherwise.
    """
    email_config = config.get("email", {})
    if not email_config.get("enabled", False):
        logger.info("Email delivery disabled in config — skipping")
        return False

    gmail_address = os.environ.get("GMAIL_ADDRESS", "")
    gmail_password = os.environ.get("GMAIL_APP_PASSWORD", "")

    if not gmail_address or not gmail_password:
        logger.warning(
            "GMAIL_ADDRESS / GMAIL_APP_PASSWORD not set — email skipped. "
            "Set in .env or GitHub Secrets."
        )
        return False

    # 1) 환경변수 SUBSCRIBERS (GitHub Actions용)
    # 2) subscribers.txt 파일 (로컬용, .gitignore에 포함)
    # 3) config fallback
    env_subscribers = os.environ.get("SUBSCRIBERS", "")
    if env_subscribers:
        subscribers = [s.strip() for s in env_subscribers.split(",") if s.strip()]
    else:
        sub_file = Path(__file__).parent.parent.parent / email_config.get("subscribers_file", "subscribers.txt")
        if sub_file.exists():
            subscribers = [s.strip() for s in sub_file.read_text().splitlines() if s.strip() and not s.startswith("#")]
        else:
            subscribers = email_config.get("subscribers", [])
    if not subscribers:
        logger.warning("No subscribers configured in config.email.subscribers")
        return False

    # Build subject (non-breaking space 등 특수문자 제거)
    prefix = email_config.get("subject_prefix", "Daily Brief")
    snippet = _extract_first_line(insight_text)
    subject = f"{prefix} - {date_str}"
    if snippet:
        subject = f"{subject} - {snippet}"
    # ASCII 호환 안 되는 특수 공백 등 제거
    subject = subject.replace("\xa0", " ").replace("\u200b", "")

    sender_name = email_config.get("sender_name", "Daily Brief")
    sender_email = email_config.get("sender_email", gmail_address)

    try:
        from email.message import EmailMessage

        # \xa0 등 non-ascii 공백을 일반 공백으로 치환
        html_body = html_body.replace("\xa0", " ").replace("\u200b", "")
        subject = subject.replace("\xa0", " ").replace("\u200b", "")

        msg = EmailMessage()
        msg["From"] = f"Daily Brief <{sender_email}>"
        msg["To"] = sender_email
        msg["Bcc"] = ", ".join(subscribers)
        msg["Subject"] = subject
        msg.set_content("Daily Brief - view in HTML email client", charset="utf-8")
        msg.add_alternative(html_body, subtype="html", charset="utf-8")

        all_recipients = [sender_email] + subscribers
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_address, gmail_password)
            server.send_message(msg, to_addrs=all_recipients)

        logger.info("Email sent to %d recipient(s) via Gmail SMTP", len(subscribers))
        return True

    except Exception as exc:
        logger.error("Failed to send email via Gmail: %s", exc)
        return False
