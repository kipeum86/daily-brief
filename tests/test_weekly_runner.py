from __future__ import annotations

import sys

import pytest

from pipeline.weekly import run_weekly_recap


def test_run_weekly_recap_requires_renderer(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "pipeline.render.weekly", None)

    with pytest.raises(ModuleNotFoundError):
        run_weekly_recap(
            config={"output": {"dir": str(tmp_path)}},
            run_date="2026-04-26",
            output_dir=str(tmp_path),
            no_llm=True,
        )
