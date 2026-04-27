"""AI briefing generation — turns market data + news into editorial insight."""

import json
import logging
import re
from typing import Any

from pipeline.ai.prompts import get_system_prompt, build_briefing_prompt
from pipeline.llm.base import LLMProvider

logger = logging.getLogger(__name__)


_BRIEFING_REQUIRED_FIELDS = ("key_insight", "market_overview", "cross_market_signals")


def _model_for_task(llm_cfg: dict, task: str | None = None) -> str:
    """Return the configured model for an LLM task, preserving legacy fallback."""
    if not task:
        return llm_cfg.get("model", "")

    task_model_keys = {
        "analysis": ("analysis_model", "briefing_model", "model"),
        "briefing": ("analysis_model", "briefing_model", "model"),
        "weekly": ("weekly_model", "analysis_model", "briefing_model", "model"),
        "selection": ("selection_model", "model"),
        "translation": ("translation_model", "selection_model", "model"),
    }
    for key in task_model_keys.get(task, ("model",)):
        model = llm_cfg.get(key)
        if model:
            return model
    return ""


def _get_provider(config: dict, task: str | None = None) -> LLMProvider:
    """Instantiate an LLM provider based on config."""
    llm_cfg = config.get("llm", {})
    provider_name = llm_cfg.get("provider", "gemini")
    model = _model_for_task(llm_cfg, task)

    if provider_name == "gemini":
        from pipeline.llm.gemini import GeminiProvider
        return GeminiProvider(model=model, fallback_models=llm_cfg.get("fallback_models"))
    if provider_name == "claude":
        from pipeline.llm.claude import ClaudeProvider
        return ClaudeProvider(model=model)
    else:
        raise ValueError(f"Unsupported LLM provider: {provider_name}")


def _strip_json_fences(text: str) -> str:
    """Remove common Markdown code fences around model JSON."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return text


def _parse_briefing_json(text: str) -> dict[str, Any]:
    """Parse a JSON object from a model response."""
    text = _strip_json_fences(text)
    if not text:
        raise RuntimeError("LLM returned empty briefing")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise
        payload = json.loads(match.group(0))
    if not isinstance(payload, dict):
        raise ValueError("briefing output must be a JSON object")
    return payload


def _clean_plain_text(value: Any) -> str:
    """Normalize a model text field and remove formatting that can break rendering."""
    text = " ".join(str(value or "").split())
    text = re.sub(r"<[^>]*>", "", text)
    text = re.sub(r"^#+\s*", "", text)
    text = re.sub(r"^[-*]\s*", "", text)
    return text.strip()


def _text_list(payload: dict[str, Any], field: str, *, min_items: int = 1, max_items: int = 3) -> list[str]:
    value = payload.get(field)
    if not isinstance(value, list):
        raise ValueError(f"briefing field '{field}' must be a list")
    items = [_clean_plain_text(item) for item in value]
    items = [item for item in items if item]
    if len(items) < min_items:
        raise ValueError(f"briefing field '{field}' needs at least {min_items} non-empty item(s)")
    return items[:max_items]


def validate_briefing_payload(payload: Any) -> dict[str, Any]:
    """Validate and normalize the structured daily briefing payload."""
    if not isinstance(payload, dict):
        raise ValueError("briefing output must be a JSON object")

    missing = [field for field in _BRIEFING_REQUIRED_FIELDS if field not in payload]
    if missing:
        raise ValueError(f"briefing output missing required field(s): {', '.join(missing)}")

    market_overview = payload.get("market_overview")
    if not isinstance(market_overview, dict):
        raise ValueError("briefing field 'market_overview' must be an object")

    signals = payload.get("cross_market_signals")
    if not isinstance(signals, list):
        raise ValueError("briefing field 'cross_market_signals' must be a list")

    normalized_signals: list[dict[str, str]] = []
    for item in signals:
        if not isinstance(item, dict):
            raise ValueError("each cross_market_signals item must be an object")
        signal = _clean_plain_text(item.get("signal"))
        meaning = _clean_plain_text(item.get("meaning"))
        if signal and meaning:
            normalized_signals.append({"signal": signal, "meaning": meaning})

    if not normalized_signals:
        raise ValueError("briefing field 'cross_market_signals' needs at least one complete item")

    return {
        "key_insight": _text_list(payload, "key_insight", min_items=1, max_items=3),
        "market_overview": {
            "korea": _text_list(market_overview, "korea", min_items=1, max_items=3),
            "us": _text_list(market_overview, "us", min_items=1, max_items=3),
        },
        "cross_market_signals": normalized_signals[:3],
    }


def render_briefing_markdown(payload: dict[str, Any], lang: str = "ko") -> str:
    """Render a validated briefing payload into stable Markdown sections."""
    key_heading = "Today's Key Insight" if lang == "en" else "Key Insight"
    market_heading = "Market Overview"
    signal_heading = "Cross-Market Signals"
    korea_label = "Korea" if lang == "en" else "한국"
    us_label = "US" if lang == "en" else "미국"

    parts = [
        f"## {key_heading}",
        "",
        " ".join(payload["key_insight"]),
        "",
        f"## {market_heading}",
        "",
        f"**{korea_label}.** " + " ".join(payload["market_overview"]["korea"]),
        "",
        f"**{us_label}.** " + " ".join(payload["market_overview"]["us"]),
        "",
        f"## {signal_heading}",
        "",
    ]
    for item in payload["cross_market_signals"]:
        parts.append(f"- **{item['signal']}**: {item['meaning']}")
    return "\n".join(parts).strip()


def _call_briefing_json(provider: LLMProvider, system_prompt: str, user_prompt: str) -> dict[str, Any]:
    if hasattr(provider, "complete_json"):
        payload = provider.complete_json(system_prompt, user_prompt)
    else:
        payload = _parse_briefing_json(provider.complete(system_prompt, user_prompt))
    return validate_briefing_payload(payload)


def generate_briefing(
    config: dict,
    markets_data: dict,
    news_articles: list,
    lang: str = "ko",
    run_date: str = "",
    holidays: dict | None = None,
) -> str:
    """Generate an AI editorial briefing from market data and news.

    Args:
        config: Full application config dict (must contain 'llm' section).
        markets_data: Market data dict keyed by category
            (kr, us, fx, commodities, crypto, risk), each a list of dicts
            with 'name', 'price', 'change_pct'.
        news_articles: List of article-like dicts or objects with at least
            'title' and 'source' attributes/keys.
        lang: Language code — "ko" for Korean, "en" for English.
        run_date: ISO date string (YYYY-MM-DD) for data staleness check.
        holidays: Holiday detection dict from detect_holidays().

    Returns:
        AI-generated insight as a Markdown string rendered from validated JSON.

    Raises:
        RuntimeError: If the provider returns an empty briefing.
        Exception: Propagates provider/configuration failures so the caller can
            block rendering instead of publishing an empty insight.
    """
    provider = _get_provider(config, task="analysis")

    # Normalize news_articles to list[dict] for the prompt builder.
    headlines: list[dict] = []
    for article in news_articles:
        if isinstance(article, dict):
            headlines.append(article)
        else:
            headlines.append({
                "title": getattr(article, "title", ""),
                "source": getattr(article, "source", ""),
                "category": getattr(article, "category", "기타"),
            })

    user_prompt = build_briefing_prompt(markets_data, headlines, lang=lang, run_date=run_date, holidays=holidays)
    system_prompt = get_system_prompt(lang)

    logger.info("Generating AI briefing (provider=%s, lang=%s)", config.get("llm", {}).get("provider", "gemini"), lang)
    payload = _call_briefing_json(provider, system_prompt, user_prompt)
    insight = render_briefing_markdown(payload, lang=lang)

    if not insight or not insight.strip():
        raise RuntimeError("LLM returned empty briefing")

    logger.info("AI briefing generated (%d chars)", len(insight))
    return insight.strip()
