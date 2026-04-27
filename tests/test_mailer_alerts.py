"""Tests for operational email alerts."""
from __future__ import annotations

from pipeline.deliver import mailer


class FakeSMTP:
    instances: list["FakeSMTP"] = []

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.logged_in = None
        self.sent = []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def login(self, address: str, password: str) -> None:
        self.logged_in = (address, password)

    def send_message(self, message, to_addrs=None) -> None:
        self.sent.append((message, to_addrs))


def test_send_failure_email_goes_to_sender_only(monkeypatch):
    FakeSMTP.instances = []
    monkeypatch.setenv("GMAIL_ADDRESS", "gmail@example.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "secret")
    monkeypatch.setattr(mailer.smtplib, "SMTP_SSL", FakeSMTP)

    sent = mailer.send_failure_email(
        {
            "email": {
                "sender_name": "Daily Brief",
                "sender_email": "sender@example.com",
            },
            "alerts": {"failure_email_enabled": True},
        },
        "[Daily Brief] verification failed",
        "Errors:\n- bad market data",
    )

    assert sent is True
    smtp = FakeSMTP.instances[0]
    assert smtp.logged_in == ("gmail@example.com", "secret")
    message, to_addrs = smtp.sent[0]
    assert to_addrs == ["sender@example.com"]
    assert message["To"] == "sender@example.com"
    assert message["Subject"] == "[Daily Brief] verification failed"


def test_send_failure_email_respects_disabled_config(monkeypatch):
    FakeSMTP.instances = []
    monkeypatch.setenv("GMAIL_ADDRESS", "gmail@example.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "secret")
    monkeypatch.setattr(mailer.smtplib, "SMTP_SSL", FakeSMTP)

    sent = mailer.send_failure_email(
        {"alerts": {"failure_email_enabled": False}},
        "failure",
        "body",
    )

    assert sent is False
    assert FakeSMTP.instances == []
