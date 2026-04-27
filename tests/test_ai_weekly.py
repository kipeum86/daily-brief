from __future__ import annotations

import pytest

from pipeline.ai.weekly import (
    generate_weekly_recap,
    render_weekly_recap_markdown,
    validate_weekly_recap_payload,
)


class _JsonProvider:
    def __init__(self, payload):
        self.payload = payload

    def complete_json(self, _system: str, _user: str, max_retries: int = 3):
        return self.payload


def _weekly_payload():
    return {
        "core_theme": [
            "이번 주 시장은 금리 인하 속도 조절과 반도체 수출 회복 사이에서 위험 선호를 재평가했습니다."
        ],
        "market_review": [
            "코스피는 반도체 기대가 지수를 지지했지만 원화 약세가 외국인 수급의 부담으로 남았습니다.",
            "미국 증시는 기술주 실적 기대와 금리 민감도가 동시에 커진 흐름이었습니다.",
        ],
        "top_stories": [
            {
                "story": "연준 인하 경로 재평가",
                "meaning": "금리 기대가 다시 조정되면서 성장주 밸류에이션 부담이 커졌습니다.",
            },
            {
                "story": "반도체 수출 회복",
                "meaning": "한국 시장에서는 수출 회복이 지수 하단을 받치는 핵심 변수였습니다.",
            },
        ],
        "watch_next_week": [
            "미국 물가 지표가 금리 기대를 다시 흔들 수 있습니다.",
            "한국 수출 데이터와 원화 흐름을 함께 확인해야 합니다.",
        ],
    }


def _weekly_data():
    return {
        "start_date": "2026-04-20",
        "end_date": "2026-04-24",
        "snapshot_count": 5,
        "news_pool_count": 20,
        "news_source_count": 8,
        "unique_story_count": 10,
        "markets": {
            "cards": [
                {"name": "KOSPI", "start_price": 2700, "end_price": 2750, "weekly_change_pct": 1.85},
                {"name": "S&P 500", "start_price": 5200, "end_price": 5250, "weekly_change_pct": 0.96},
            ],
        },
        "world_news_raw": [{"title": "Fed signals slower cuts"}],
        "korea_news_raw": [{"title": "한국 반도체 수출 회복"}],
        "world_news_ko": [{"title": "연준 인하 속도 조절", "summary": "미국 금리 기대가 조정됐습니다."}],
        "world_news_en": [{"title": "Fed signals slower cuts", "summary": "US rate expectations shifted."}],
        "korea_news_ko": [{"title": "한국 반도체 수출 회복", "summary": "수출 회복 기대가 커졌습니다."}],
        "korea_news_en": [{"title": "Korea chip exports recover", "summary": "Export recovery hopes improved."}],
    }


def test_generate_weekly_recap_renders_validated_json(monkeypatch):
    monkeypatch.setattr("pipeline.ai.weekly._get_provider", lambda _config, task=None: _JsonProvider(_weekly_payload()))

    result = generate_weekly_recap({"llm": {"provider": "test"}}, _weekly_data(), lang="ko")

    assert "## This Week's Core Theme" in result
    assert "## Market Review" in result
    assert "## Top Stories" in result
    assert "## What to Watch Next Week" in result
    assert "연준 인하 경로 재평가" in result


def test_validate_weekly_recap_payload_rejects_missing_sections():
    with pytest.raises(ValueError, match="missing required"):
        validate_weekly_recap_payload({"core_theme": ["only one field"]})


def test_render_weekly_recap_markdown_strips_model_markup():
    payload = _weekly_payload()
    payload["core_theme"] = ["## <b>금리</b>와 수출이 이번 주 핵심 변수였습니다."]

    markdown = render_weekly_recap_markdown(validate_weekly_recap_payload(payload))

    assert "<b>" not in markdown
    assert "## ##" not in markdown
