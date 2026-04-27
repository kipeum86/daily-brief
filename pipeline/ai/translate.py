"""Translate news headlines and summaries between Korean and English."""

import json
import logging
import re
from typing import Any

from pipeline.llm.base import LLMProvider

logger = logging.getLogger(__name__)

_HAS_KOREAN = re.compile(r"[가-힣]")
_ENGLISH_WORD = re.compile(r"[A-Za-z]{2,}")


def _sanitize_json(text: str) -> str:
    """Best-effort fix for common LLM JSON mistakes."""
    # Strip markdown fences
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    # Remove trailing commas before } or ]
    text = re.sub(r",\s*([}\]])", r"\1", text)
    # Try to extract JSON array if surrounded by extra text
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if match:
        text = match.group(0)
    return text

TRANSLATE_SYSTEM_PROMPT = """\
You are a professional news translator. Translate news headlines and summaries accurately and naturally.
Preserve the factual content. Do not add commentary or interpretation.
Output ONLY valid JSON — no markdown fences, no extra text."""


def korean_ratio(text: str) -> float:
    """Return Hangul share among Hangul/Latin letters in text."""
    hangul = len(re.findall(r"[가-힣]", text or ""))
    latin = len(re.findall(r"[A-Za-z]", text or ""))
    total = hangul + latin
    return 1.0 if total == 0 else hangul / total


def english_ratio(text: str) -> float:
    """Return Latin share among Hangul/Latin letters in text."""
    hangul = len(re.findall(r"[가-힣]", text or ""))
    latin = len(re.findall(r"[A-Za-z]", text or ""))
    total = hangul + latin
    return 1.0 if total == 0 else latin / total


def looks_like_language(text: str, target_lang: str) -> bool:
    """Heuristic language check for translated news title/summary text."""
    text = (text or "").strip()
    if not text:
        return False
    if target_lang == "ko":
        return bool(_HAS_KOREAN.search(text)) and korean_ratio(text) >= 0.25
    if target_lang == "en":
        return bool(_ENGLISH_WORD.search(text)) and english_ratio(text) >= 0.55
    raise ValueError(f"Unsupported target language: {target_lang}")


def _normalize_language(value: Any) -> str:
    lang = str(value or "").strip().lower().replace("_", "-")
    if lang.startswith("ko"):
        return "ko"
    if lang.startswith("en"):
        return "en"
    return lang


def _article_text_fields(article: Any) -> tuple[str, str]:
    if isinstance(article, dict):
        return (
            str(article.get("title", "") or ""),
            str(article.get("summary", "") or article.get("description", "") or ""),
        )
    return (
        str(getattr(article, "title", "") or ""),
        str(getattr(article, "description", "") or getattr(article, "body", "") or ""),
    )


def _parse_translation_response(response: str) -> list[dict[str, Any]]:
    sanitized = _sanitize_json(response)
    payload = json.loads(sanitized)
    if not isinstance(payload, list):
        raise ValueError("translation output must be a JSON array")
    if not all(isinstance(item, dict) for item in payload):
        raise ValueError("translation output items must be JSON objects")
    return payload


def _validate_translation_payload(
    payload: list[dict[str, Any]],
    articles: list[Any],
    target_lang: str,
) -> dict[int, dict[str, Any]]:
    expected_ids = set(range(len(articles)))
    seen_ids: set[int] = set()
    validated: dict[int, dict[str, Any]] = {}

    for item in payload:
        item_id = item.get("id")
        if not isinstance(item_id, int):
            raise ValueError(f"translation item missing integer id: {item}")
        if item_id not in expected_ids:
            raise ValueError(f"translation item id out of range: {item_id}")
        if item_id in seen_ids:
            raise ValueError(f"duplicate translation item id: {item_id}")
        seen_ids.add(item_id)

        language = _normalize_language(item.get("language"))
        if language != target_lang:
            raise ValueError(f"translation item {item_id} has language '{item.get('language')}', expected '{target_lang}'")

        title = str(item.get("title", "") or "").strip()
        summary = str(item.get("summary", "") or "").strip()
        original_title, original_summary = _article_text_fields(articles[item_id])

        if original_title and not title:
            raise ValueError(f"translation item {item_id} has empty title")
        if title and not looks_like_language(title, target_lang):
            raise ValueError(f"translation item {item_id} title not in target language: '{title[:80]}'")
        if original_summary and not summary:
            raise ValueError(f"translation item {item_id} has empty summary")
        if summary and not looks_like_language(summary, target_lang):
            raise ValueError(f"translation item {item_id} summary not in target language: '{summary[:80]}'")

        unchanged_terms = item.get("unchanged_terms", [])
        if unchanged_terms is None:
            unchanged_terms = []
        if not isinstance(unchanged_terms, list):
            raise ValueError(f"translation item {item_id} unchanged_terms must be a list")

        validated[item_id] = {
            "id": item_id,
            "title": title,
            "summary": summary,
            "language": target_lang,
            "unchanged_terms": [str(term) for term in unchanged_terms],
        }

    missing = expected_ids - seen_ids
    if missing:
        raise ValueError(f"translation output missing id(s): {sorted(missing)}")

    return validated


def translate_news(
    provider: LLMProvider,
    articles: list[dict],
    target_lang: str,
    strict: bool = False,
) -> list[dict]:
    """Translate a list of news articles to the target language.

    Args:
        provider: LLM provider instance.
        articles: List of dicts with 'title', 'summary', 'source', 'url'.
        target_lang: "ko" for Korean, "en" for English.
        strict: If True, raise translation failures instead of returning
            untranslated originals.

    Returns:
        Same list with 'title' and 'summary' translated.
        On failure, returns original articles unchanged unless strict=True.
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
Return ONLY a JSON array. Each item must have this exact shape:
{{
  "id": 0,
  "title": "translated title",
  "summary": "translated summary",
  "language": "{target_lang}",
  "unchanged_terms": ["KOSPI", "Fed"]
}}

Rules:
- id must match the input id.
- language must be exactly "{target_lang}".
- Translate title and summary. If the input summary is empty, return an empty summary.
- Preserve tickers, company names, product names, and official institution names in unchanged_terms when appropriate.
- Do not add facts, commentary, markdown, or HTML.

Input:
{json.dumps(items, ensure_ascii=False, indent=2)}

Output (JSON array only):"""

    try:
        max_retries = 2
        last_error = None
        for attempt in range(max_retries):
            try:
                response = provider.complete(TRANSLATE_SYSTEM_PROMPT, user_prompt)
                translated = _parse_translation_response(response)
                trans_map = _validate_translation_payload(translated, articles, target_lang)
                break  # success
            except (json.JSONDecodeError, Exception) as e:
                last_error = e
                logger.warning("Translation attempt %d/%d failed: %s", attempt + 1, max_retries, e)
                if attempt < max_retries - 1:
                    continue
                raise last_error

        result = []
        for i, art in enumerate(articles):
            t = trans_map[i]
            if isinstance(art, dict):
                entry = dict(art)
                entry["title"] = t.get("title", art.get("title", ""))
                entry["summary"] = t.get("summary", art.get("summary", ""))
                entry["translation_language"] = t.get("language", target_lang)
                entry["translation_unchanged_terms"] = t.get("unchanged_terms", [])
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
        logger.exception("News translation to %s failed", lang_name)
        if strict:
            raise
        logger.warning("Using original articles after non-strict translation failure")
        return articles
