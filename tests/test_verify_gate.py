"""Tests for the verification gate orchestrator."""
from __future__ import annotations

from pipeline.verify.gate import GateResult, run_pre_deploy_checks


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
