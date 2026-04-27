from __future__ import annotations

import json

import pytest

from pipeline.ai.briefing import (
    _model_for_task,
    generate_briefing,
    render_briefing_markdown,
    validate_briefing_payload,
)
from pipeline.ai.prompts import build_briefing_prompt
from pipeline.ai.translate import translate_news


class _Provider:
    def __init__(self, response: str):
        self.response = response

    def complete(self, _system: str, _user: str) -> str:
        return self.response


class _JsonProvider:
    def __init__(self, payload):
        self.payload = payload

    def complete_json(self, _system: str, _user: str, max_retries: int = 3):
        return self.payload


def _briefing_payload():
    return {
        "key_insight": [
            "위험자산은 금리 경로와 반도체 수출 회복이라는 두 축 사이에서 방향을 찾고 있습니다.",
            "달러와 유가 흐름은 한국 수출주에 기회와 비용 부담을 동시에 만들고 있습니다.",
        ],
        "market_overview": {
            "korea": [
                "코스피는 외국인 수급과 반도체 기대가 지수 하단을 받치는 흐름입니다.",
                "원화 약세가 이어지면 수출주는 방어력을 얻지만 내수와 수입 물가에는 부담입니다.",
            ],
            "us": [
                "미국 시장은 연준의 인하 속도 조절 신호를 가격에 다시 반영하고 있습니다.",
                "기술주는 실적 기대가 남아 있지만 금리 민감도가 커졌습니다.",
            ],
        },
        "cross_market_signals": [
            {
                "signal": "달러 강세와 원화 약세",
                "meaning": "환율은 수출 기업의 매출 환산에는 우호적이지만 외국인 자금 유입에는 제약이 될 수 있습니다.",
            },
            {
                "signal": "유가와 물가 기대",
                "meaning": "유가가 반등하면 금리 인하 기대가 약해지고 위험자산의 밸류에이션 부담이 커질 수 있습니다.",
            },
        ],
    }


def test_model_for_task_uses_task_specific_model():
    llm_config = {
        "model": "pro",
        "analysis_model": "analysis-pro",
        "selection_model": "flash",
        "translation_model": "flash-lite",
    }

    assert _model_for_task(llm_config, task="analysis") == "analysis-pro"
    assert _model_for_task(llm_config, task="selection") == "flash"
    assert _model_for_task(llm_config, task="translation") == "flash-lite"


def test_model_for_task_preserves_legacy_model_fallback():
    llm_config = {"model": "legacy-pro"}

    assert _model_for_task(llm_config, task="analysis") == "legacy-pro"
    assert _model_for_task(llm_config, task="selection") == "legacy-pro"
    assert _model_for_task(llm_config, task="translation") == "legacy-pro"


def test_generate_briefing_raises_on_empty_response(monkeypatch):
    monkeypatch.setattr("pipeline.ai.briefing._get_provider", lambda _config, task=None: _Provider(""))

    with pytest.raises(RuntimeError, match="empty briefing"):
        generate_briefing(
            {"llm": {"provider": "test"}},
            markets_data={},
            news_articles=[],
            lang="ko",
            run_date="2026-04-04",
            holidays={},
        )


def test_generate_briefing_renders_validated_json(monkeypatch):
    monkeypatch.setattr("pipeline.ai.briefing._get_provider", lambda _config, task=None: _JsonProvider(_briefing_payload()))

    insight = generate_briefing(
        {"llm": {"provider": "test"}},
        markets_data={},
        news_articles=[],
        lang="ko",
        run_date="2026-04-04",
        holidays={},
    )

    assert "## Key Insight" in insight
    assert "## Market Overview" in insight
    assert "## Cross-Market Signals" in insight
    assert "위험자산은" in insight


def test_validate_briefing_payload_rejects_missing_sections():
    with pytest.raises(ValueError, match="missing required"):
        validate_briefing_payload({"key_insight": ["only one field"]})


def test_render_briefing_markdown_strips_model_markup():
    payload = _briefing_payload()
    payload["key_insight"] = ["## <b>시장</b> 방향성은 금리와 수출에 달려 있습니다."]

    markdown = render_briefing_markdown(validate_briefing_payload(payload), lang="ko")

    assert "<b>" not in markdown
    assert "## ##" not in markdown


def test_build_briefing_prompt_includes_news_context():
    prompt = build_briefing_prompt(
        markets_data={},
        news_headlines=[
            {
                "title": "Fed signals slower rate cuts",
                "source": "Reuters",
                "summary": "The Fed guided markets toward a slower easing path.",
                "published_date": "2026-04-04",
                "bucket": "world",
                "category": "economy",
            }
        ],
        lang="en",
        run_date="2026-04-04",
        holidays={},
    )

    assert "id: N1" in prompt
    assert "summary: The Fed guided markets toward a slower easing path." in prompt
    assert "Return ONLY valid JSON" in prompt


def test_translate_news_non_strict_returns_original_on_invalid_json():
    articles = [{"title": "Iran war escalates", "summary": "Summary", "bucket": "world"}]
    result = translate_news(_Provider("not json"), articles, target_lang="ko", strict=False)
    assert result == articles


def test_translate_news_strict_raises_on_invalid_json():
    articles = [{"title": "Iran war escalates", "summary": "Summary", "bucket": "world"}]
    with pytest.raises(Exception):
        translate_news(_Provider("not json"), articles, target_lang="ko", strict=True)


def test_translate_news_accepts_schema_valid_target_language():
    articles = [{"title": "Iran war escalates", "summary": "Shipping risk rises", "bucket": "world"}]
    response = json.dumps([
        {
            "id": 0,
            "title": "이란 전쟁 격화",
            "summary": "해상 운송 위험이 커지고 있습니다.",
            "language": "ko",
            "unchanged_terms": ["Iran"],
        }
    ], ensure_ascii=False)

    result = translate_news(_Provider(response), articles, target_lang="ko", strict=True)

    assert result[0]["title"] == "이란 전쟁 격화"
    assert result[0]["translation_language"] == "ko"
    assert result[0]["translation_unchanged_terms"] == ["Iran"]


def test_translate_news_strict_rejects_missing_language_field():
    articles = [{"title": "Iran war escalates", "summary": "Shipping risk rises", "bucket": "world"}]
    response = json.dumps([
        {
            "id": 0,
            "title": "이란 전쟁 격화",
            "summary": "해상 운송 위험이 커지고 있습니다.",
            "unchanged_terms": [],
        }
    ], ensure_ascii=False)

    with pytest.raises(ValueError, match="language"):
        translate_news(_Provider(response), articles, target_lang="ko", strict=True)


def test_translate_news_strict_rejects_wrong_language_text():
    articles = [{"title": "Iran war escalates", "summary": "Shipping risk rises", "bucket": "world"}]
    response = json.dumps([
        {
            "id": 0,
            "title": "Iran war escalates",
            "summary": "Shipping risk rises",
            "language": "ko",
            "unchanged_terms": [],
        }
    ])

    with pytest.raises(ValueError, match="target language"):
        translate_news(_Provider(response), articles, target_lang="ko", strict=True)


def test_translate_news_non_strict_returns_original_on_language_mismatch():
    articles = [{"title": "한국 경제 성장", "summary": "한국 증시가 상승했습니다.", "bucket": "korea"}]
    response = json.dumps([
        {
            "id": 0,
            "title": "한국 경제 성장",
            "summary": "한국 증시가 상승했습니다.",
            "language": "en",
            "unchanged_terms": [],
        }
    ], ensure_ascii=False)

    result = translate_news(_Provider(response), articles, target_lang="en", strict=False)

    assert result == articles
