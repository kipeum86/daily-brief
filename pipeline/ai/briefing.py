"""AI briefing generation — turns market data + news into editorial insight."""

import logging

from pipeline.ai.prompts import get_system_prompt, build_briefing_prompt
from pipeline.llm.base import LLMProvider

logger = logging.getLogger(__name__)


def _get_provider(config: dict) -> LLMProvider:
    """Instantiate an LLM provider based on config."""
    llm_cfg = config.get("llm", {})
    provider_name = llm_cfg.get("provider", "gemini")
    model = llm_cfg.get("model", "")

    if provider_name == "gemini":
        from pipeline.llm.gemini import GeminiProvider
        return GeminiProvider(model=model)
    else:
        raise ValueError(f"Unsupported LLM provider: {provider_name}")


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
        AI-generated insight text as a Markdown string.
        Returns empty string on failure (caller handles graceful degradation).
    """
    try:
        provider = _get_provider(config)

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
        insight = provider.complete(system_prompt, user_prompt)

        if not insight or not insight.strip():
            logger.warning("LLM returned empty briefing")
            return ""

        logger.info("AI briefing generated (%d chars)", len(insight))
        return insight.strip()

    except Exception:
        logger.exception("Failed to generate AI briefing")
        return ""
