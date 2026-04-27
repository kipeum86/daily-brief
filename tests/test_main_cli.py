from __future__ import annotations

import sys

import pytest

import main


def test_parse_args_accepts_daily_and_weekly():
    assert main.parse_args(["--brief-type", "daily"]).brief_type == "daily"
    assert main.parse_args(["--brief-type", "weekly"]).brief_type == "weekly"


def test_parse_args_rejects_monthly_until_implemented():
    with pytest.raises(SystemExit):
        main.parse_args(["--brief-type", "monthly"])


def test_apply_brief_type_overrides_rejects_unsupported_type():
    with pytest.raises(ValueError, match="Unsupported brief type"):
        main._apply_brief_type_overrides({}, "monthly")


def test_generate_ai_stage_fails_when_briefing_module_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "pipeline.ai.briefing", None)
    errors: list[str] = []

    result = main._generate_ai_stage(
        config={"news": {"top_n": 5}},
        markets={},
        holidays={},
        articles=[],
        run_date="2026-04-04",
        no_llm=False,
        sections=[],
        errors=errors,
    )

    assert result is None
    assert any(error.startswith("ai_import:") for error in errors)


def test_render_stage_fails_when_dashboard_module_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "pipeline.render.dashboard", None)
    errors: list[str] = []
    ai = main.AIStageResult(
        insight="충분히 긴 인사이트",
        insight_en="Long enough insight",
        articles_ko=[],
        articles_en=[],
    )

    result = main._render_stage(
        config={},
        markets={},
        holidays={},
        articles=[],
        ai=ai,
        run_date="2026-04-04",
        output_dir="output",
        market_pulse={},
        sections=[],
        errors=errors,
    )

    assert result is None
    assert any(error.startswith("render:") for error in errors)


def test_failure_alert_stage_skips_email_in_dry_run(monkeypatch):
    calls = []

    def fake_import(*_args, **_kwargs):
        return lambda *_call_args, **_call_kwargs: calls.append(True)

    monkeypatch.setattr(main, "_import_or_stub", fake_import)

    main._failure_alert_stage(
        config={},
        brief_type="Daily Brief",
        run_label="2026-04-04",
        verification=main.VerificationStageResult(
            passed=False,
            errors=["broken gate"],
            warnings=[],
        ),
        dry_run=True,
    )

    assert calls == []
