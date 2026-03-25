"""Translate news headlines and summaries between Korean and English."""

import json
import logging
from pipeline.llm.base import LLMProvider

logger = logging.getLogger(__name__)

TRANSLATE_SYSTEM_PROMPT = """\
You are a professional news translator. Translate news headlines and summaries accurately and naturally.
Preserve the factual content. Do not add commentary or interpretation.
Output ONLY valid JSON — no markdown fences, no extra text."""


def translate_news(
    provider: LLMProvider,
    articles: list[dict],
    target_lang: str,
) -> list[dict]:
    """Translate a list of news articles to the target language.

    Args:
        provider: LLM provider instance.
        articles: List of dicts with 'title', 'summary', 'source', 'url'.
        target_lang: "ko" for Korean, "en" for English.

    Returns:
        Same list with 'title' and 'summary' translated.
        On failure, returns original articles unchanged.
    """
    if not articles:
        return articles

    lang_name = "Korean" if target_lang == "ko" else "English"

    # Build batch prompt — translate all at once for efficiency
    items = []
    for i, art in enumerate(articles):
        if isinstance(art, dict):
            title = art.get("title", "")
            summary = art.get("summary", "") or art.get("description", "")
        else:
            title = getattr(art, "title", "")
            summary = getattr(art, "description", "") or getattr(art, "body", "")
        items.append({"id": i, "title": title, "summary": summary})

    user_prompt = f"""Translate the following news items to {lang_name}.
Return a JSON array with the same structure (id, title, summary), translated.

Input:
{json.dumps(items, ensure_ascii=False, indent=2)}

Output (JSON array only):"""

    try:
        response = provider.complete(TRANSLATE_SYSTEM_PROMPT, user_prompt)
        # Parse JSON from response
        response = response.strip()
        if response.startswith("```"):
            response = response.split("\n", 1)[1].rsplit("```", 1)[0]
        translated = json.loads(response)

        # Map translations back
        trans_map = {item["id"]: item for item in translated}
        result = []
        for i, art in enumerate(articles):
            t = trans_map.get(i, {})
            if isinstance(art, dict):
                entry = dict(art)
                entry["title"] = t.get("title", art.get("title", ""))
                entry["summary"] = t.get("summary", art.get("summary", ""))
            else:
                # Article dataclass — create a copy with translated fields
                from pipeline.models import Article
                entry = Article(
                    title=t.get("title", getattr(art, "title", "")),
                    url=getattr(art, "url", ""),
                    source=getattr(art, "source", ""),
                    description=t.get("summary", getattr(art, "description", "")),
                    published_date=getattr(art, "published_date", ""),
                    body=getattr(art, "body", ""),
                )
            result.append(entry)

        logger.info("Translated %d news items to %s", len(result), lang_name)
        return result

    except Exception:
        logger.exception("News translation to %s failed — using originals", lang_name)
        return articles
