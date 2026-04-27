"""Tests for the verification gate orchestrator."""
from __future__ import annotations

from pipeline.verify.gate import GateResult, run_pre_deploy_checks, run_weekly_checks


def test_gate_result_all_pass():
    r = GateResult(passed=True, errors=[], warnings=[], checks_run=5, checks_passed=5)
    assert r.passed is True
    assert r.checks_run == 5


def test_gate_result_with_errors():
    r = GateResult(passed=False, errors=["bad price"], warnings=[], checks_run=5, checks_passed=4)
    assert r.passed is False
    assert len(r.errors) == 1


def test_run_pre_deploy_checks_returns_gate_result():
    """Smoke test — with empty/minimal data, gate should still return a result."""
    result = run_pre_deploy_checks(
        markets={"kr": [], "us": [], "fx": [], "commodities": [], "crypto": [], "risk": []},
        holidays={"kospi_holiday": False, "nyse_holiday": False, "holiday_names": {}},
        articles_ko=[],
        articles_en=[],
        insight_ko="",
        insight_en="",
        html_path="",
        en_html_path="",
        run_date="2026-04-04",
        config={},
        no_llm=True,
    )
    assert isinstance(result, GateResult)
    assert isinstance(result.errors, list)
    assert isinstance(result.warnings, list)


def test_run_pre_deploy_checks_fails_when_check_raises(monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("broken verifier")

    monkeypatch.setattr("pipeline.verify.gate._run_html", boom)

    result = run_pre_deploy_checks(
        markets={"kr": [], "us": [], "fx": [], "commodities": [], "crypto": [], "risk": []},
        holidays={"kospi_holiday": False, "nyse_holiday": False, "holiday_names": {}},
        articles_ko=[],
        articles_en=[],
        insight_ko="",
        insight_en="",
        html_path="",
        en_html_path="",
        run_date="2026-04-04",
        config={},
        no_llm=True,
    )

    assert result.passed is False
    assert any("verifier error" in error for error in result.errors)


def test_run_weekly_checks_fails_when_check_raises(monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("broken weekly verifier")

    monkeypatch.setattr("pipeline.verify.gate._run_weekly", boom)

    result = run_weekly_checks(
        weekly_data={},
        html_path="",
        no_llm=True,
    )

    assert result.passed is False
    assert any("verifier error" in error for error in result.errors)


def test_run_pre_deploy_checks_writes_github_summary(monkeypatch, tmp_path):
    summary_path = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_path))
    monkeypatch.setattr("pipeline.verify.gate._save_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("pipeline.verify.gate._run_market_data", lambda *_args: ([], []))
    monkeypatch.setattr("pipeline.verify.gate._run_insight", lambda *_args: ([], []))
    monkeypatch.setattr("pipeline.verify.gate._run_translation", lambda *_args: ([], []))
    monkeypatch.setattr("pipeline.verify.gate._run_content", lambda *_args: ([], []))
    monkeypatch.setattr(
        "pipeline.verify.gate._run_html",
        lambda *_args: (["html missing"], ["html warning"]),
    )

    result = run_pre_deploy_checks(
        markets={"kr": [], "us": [], "fx": [], "commodities": [], "crypto": [], "risk": []},
        holidays={"kospi_holiday": False, "nyse_holiday": False, "holiday_names": {}},
        articles_ko=[],
        articles_en=[],
        insight_ko="",
        insight_en="",
        html_path="",
        en_html_path="",
        run_date="2026-04-04",
        config={},
        no_llm=True,
        write_summary=True,
    )

    text = summary_path.read_text(encoding="utf-8")
    assert result.passed is False
    assert "Daily Brief Verification" in text
    assert "passed: false" in text
    assert "checks: 4/5" in text
    assert "html missing" in text
    assert "html warning" in text


def test_run_pre_deploy_checks_skips_github_summary_by_default(monkeypatch, tmp_path):
    summary_path = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_path))
    monkeypatch.setattr("pipeline.verify.gate._save_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("pipeline.verify.gate._run_market_data", lambda *_args: ([], []))
    monkeypatch.setattr("pipeline.verify.gate._run_insight", lambda *_args: ([], []))
    monkeypatch.setattr("pipeline.verify.gate._run_translation", lambda *_args: ([], []))
    monkeypatch.setattr("pipeline.verify.gate._run_content", lambda *_args: ([], []))
    monkeypatch.setattr("pipeline.verify.gate._run_html", lambda *_args: ([], []))

    run_pre_deploy_checks(
        markets={"kr": [], "us": [], "fx": [], "commodities": [], "crypto": [], "risk": []},
        holidays={"kospi_holiday": False, "nyse_holiday": False, "holiday_names": {}},
        articles_ko=[],
        articles_en=[],
        insight_ko="",
        insight_en="",
        html_path="",
        en_html_path="",
        run_date="2026-04-04",
        config={},
        no_llm=True,
    )

    assert not summary_path.exists()


def test_run_weekly_checks_writes_github_summary(monkeypatch, tmp_path):
    summary_path = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_path))
    monkeypatch.setattr(
        "pipeline.verify.gate._run_weekly",
        lambda *_args: ([], ["weekly warning"]),
    )

    result = run_weekly_checks(
        weekly_data={"week_id": "2026-W17"},
        html_path="",
        no_llm=True,
        write_summary=True,
    )

    text = summary_path.read_text(encoding="utf-8")
    assert result.passed is True
    assert "Weekly Recap Verification" in text
    assert "2026-W17" in text
    assert "passed: true" in text
    assert "checks: 1/1" in text
    assert "weekly warning" in text
