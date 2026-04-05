"""Tests for weekly recap checks."""
from __future__ import annotations
import tempfile, os

from pipeline.verify.checks.weekly import check_weekly_recap


def _weekly_data(snapshots=5, world=5, korea=5, insight="긴 인사이트 " * 30):
    return {
        "snapshot_count": snapshots,
        "markets": {"cards": [{"name": "KOSPI", "section": "kr", "weekly_change_pct": 2.0}, {"name": "S&P 500", "section": "us", "weekly_change_pct": 1.0}]},
        "world_news_ko": [{"title": f"뉴스 {i}", "bucket": "world"} for i in range(world)],
        "korea_news_ko": [{"title": f"한국 뉴스 {i}", "bucket": "korea"} for i in range(korea)],
        "world_news_en": [{"title": f"News {i}", "bucket": "world"} for i in range(world)],
        "korea_news_en": [{"title": f"Korea news {i}", "bucket": "korea"} for i in range(korea)],
        "insight_ko": insight,
        "insight_en": insight,
    }


def test_good_weekly():
    html = tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False)
    html.write("<html>" + "x" * 15000 + "</html>")
    html.close()
    try:
        errors, _ = check_weekly_recap(_weekly_data(), html.name, no_llm=False)
        assert errors == [], f"Unexpected: {errors}"
    finally:
        os.unlink(html.name)


def test_no_snapshots():
    errors, _ = check_weekly_recap(_weekly_data(snapshots=0), "", no_llm=False)
    assert any("snapshot" in e.lower() for e in errors)


def test_too_few_world():
    errors, _ = check_weekly_recap(_weekly_data(world=1), "", no_llm=True)
    assert any("world" in e.lower() for e in errors)


def test_missing_insight():
    errors, _ = check_weekly_recap(_weekly_data(insight=""), "", no_llm=False)
    assert any("insight" in e.lower() for e in errors)
