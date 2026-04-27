"""AI-based news selection helpers."""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from typing import Any

from pipeline.llm.base import LLMProvider
from pipeline.news.dedup import containment_similarity, extract_topic_tokens

logger = logging.getLogger(__name__)

SELECTOR_PROMPT_WORLD = """\
You are an editor at The Economist. From the following news headlines, select the {top_n} most important ones that a global investor and business decision-maker MUST know today.

Selection criteria:
- Market-moving events (central bank decisions, major economic data, geopolitical shifts)
- Major policy changes (trade, regulation, fiscal policy)
- Significant corporate news (earnings surprises, M&A, leadership changes at major companies)
- Geopolitical developments with economic impact

DIVERSITY RULES:
- Maximize source diversity — do NOT pick multiple articles from the same outlet
- No overlapping topics — each selected article must cover a DIFFERENT subject
- Spread across different regions/themes when possible

EXCLUDE:
- Routine personnel appointments ("인사 발령")
- Celebrity/entertainment news
- Local crime/accidents
- Repetitive/duplicate stories (even from different sources)
- Opinion pieces or editorials

Return ONLY a JSON array of the selected headline indices (0-based).
Example: [0, 3, 5, 8, 12]"""

SELECTOR_PROMPT_KOREA = """\
You are an editor at a major Korean newspaper. From the following headlines published by Korean outlets, select the {top_n} most important DOMESTIC Korean news stories.

Selection criteria:
- Korean economic policy & data (한국은행, 기재부, 고용, 물가, 부동산)
- Korean corporate news (삼성, SK, 현대 등 주요 기업 실적·경영)
- Korean politics & regulation affecting markets
- Korean industry trends (반도체, 배터리, 자동차, K-콘텐츠)

CRITICAL — EXCLUDE international/foreign news:
- Wars, conflicts, or diplomacy between other countries (e.g. Iran, Ukraine, Middle East)
- US/China/Japan/EU politics or policy (unless directly about Korea)
- International disasters or events unrelated to Korea
- If a Korean outlet covers a foreign story, it is still foreign news — SKIP IT

DIVERSITY RULES:
- Maximize source diversity — do NOT pick multiple articles from the same outlet
- No overlapping topics — each selected article must cover a DIFFERENT subject

Return ONLY a JSON array of the selected headline indices (0-based).
Example: [0, 3, 5, 8, 12]"""

UNIFIED_SELECTOR_PROMPT = """\
You are curating a bilingual investor briefing.

You will receive ONE pre-ranked mixed list of articles from world outlets and Korean outlets.
For each article you choose, classify it by TOPIC, not by source.

Return ONLY a JSON object in this exact shape:
{{
  "world": [
    {{"index": 0, "bucket": "world", "category": "economy", "rank": 1}}
  ],
  "korea": [
    {{"index": 4, "bucket": "korea", "category": "corporate", "rank": 1}}
  ],
  "warnings": []
}}

Rules:
- Return exactly {candidate_n} WORLD candidates and exactly {candidate_n} KOREA candidates if enough valid candidates exist.
- If fewer valid candidates exist, return all valid candidates and add a short warning string.
- bucket must be either "world" or "korea".
- category must be one of: economy, politics, security, tech, society, corporate.
- KOREA = domestic Korean affairs: Bank of Korea, KOSPI/KOSDAQ, Korean real estate, Korean companies, Korean politics, Korean courts, Korean ministries, Korean labor and regulation.
- WORLD = everything else, INCLUDING international news covered by Korean outlets.
- Example: a Yonhap article about Iran conflict is WORLD.
- Example: a Chosun article about Korean semiconductor exports is KOREA.
- Enforce source diversity in your picks:
  - WORLD: max 2 per outlet
  - KOREA: max 1 per outlet
- Prioritize stories that appear to have wide coverage across multiple outlets.
- Avoid selecting overlapping stories about the same topic.
"""

_JSON_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)
_DOMESTIC_KEYWORDS = {
    "한국", "국내", "서울", "부산", "코스피", "코스닥", "한은", "한국은행", "기재부",
    "산업부", "과기부", "국회", "법원", "대통령", "총리", "부동산", "수출", "원화",
    "삼성", "sk", "현대", "lg", "카카오", "네이버", "반도체", "배터리", "조선", "철강",
}
_INTERNATIONAL_KEYWORDS = {
    "미국", "중국", "일본", "유럽", "eu", "fed", "fomc", "백악관", "의회", "트럼프",
    "바이든", "우크라이나", "러시아", "푸틴", "이란", "이스라엘", "가자", "나토",
    "브렉시트", "china", "japan", "iran", "israel", "ukraine", "russia", "white house",
}
_CATEGORY_KEYWORDS = {
    "economy": {"inflation", "cpi", "gdp", "rate", "rates", "금리", "물가", "환율", "수출", "무역", "부동산", "주택", "고용", "예산", "세제"},
    "politics": {"election", "elections", "parliament", "congress", "president", "정부", "국회", "대통령", "총리", "정책", "규제", "법원", "헌재"},
    "security": {"war", "military", "defense", "sanction", "security", "conflict", "nato", "iran", "israel", "ukraine", "전쟁", "군사", "안보", "제재"},
    "tech": {"ai", "chip", "chips", "semiconductor", "software", "cyber", "반도체", "ai", "기술", "플랫폼", "데이터", "클라우드"},
    "society": {"labor", "strike", "education", "health", "weather", "welfare", "노동", "교육", "복지", "의료", "환경"},
    "corporate": {"earnings", "merger", "acquisition", "ceo", "factory", "company", "기업", "실적", "투자", "공장", "인수", "합병"},
}
_VALID_CATEGORIES = tuple(_CATEGORY_KEYWORDS.keys())


def _sanitize_json_array(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    match = _JSON_ARRAY_RE.search(text)
    return match.group(0) if match else text


def _article_text(article: dict[str, Any]) -> str:
    return " ".join(
        part for part in [
            article.get("title", ""),
            article.get("description", "") or article.get("summary", ""),
        ] if part
    ).lower()


def _normalize_article(article: Any, index: int) -> dict[str, Any]:
    if isinstance(article, dict):
        entry = dict(article)
    else:
        entry = {
            "title": getattr(article, "title", ""),
            "source": getattr(article, "source", ""),
            "url": getattr(article, "url", ""),
            "description": getattr(article, "description", "") or getattr(article, "body", ""),
            "published_date": getattr(article, "published_date", ""),
        }
    entry.setdefault("description", entry.get("summary", ""))
    entry["summary"] = entry.get("summary") or entry.get("description", "")
    entry["_original_index"] = index
    entry["_topic_tokens"] = extract_topic_tokens(f"{entry.get('title', '')} {entry.get('summary', '')}")
    return entry


def _guess_bucket(article: dict[str, Any]) -> str:
    text = _article_text(article)
    domestic_hits = sum(1 for token in _DOMESTIC_KEYWORDS if token.lower() in text)
    international_hits = sum(1 for token in _INTERNATIONAL_KEYWORDS if token.lower() in text)
    if international_hits > domestic_hits:
        return "world"
    if domestic_hits > 0:
        return "korea"
    return "world"


def _guess_category(article: dict[str, Any]) -> str:
    text = _article_text(article)
    scores = {
        category: sum(1 for token in keywords if token.lower() in text)
        for category, keywords in _CATEGORY_KEYWORDS.items()
    }
    best_category, best_score = max(scores.items(), key=lambda item: item[1])
    if best_score <= 0:
        return "economy" if _guess_bucket(article) == "world" else "corporate"
    return best_category


def _coverage_score(base: dict[str, Any], articles: list[dict[str, Any]]) -> int:
    base_tokens = base.get("_topic_tokens", set())
    if not base_tokens:
        return 1

    similar_sources = {base.get("source", "")}
    for candidate in articles:
        if candidate is base:
            continue
        tokens = candidate.get("_topic_tokens", set())
        if not tokens:
            continue
        overlap = len(base_tokens & tokens)
        similarity = containment_similarity(base_tokens, tokens)
        if overlap >= 2 or similarity >= 0.5:
            similar_sources.add(candidate.get("source", ""))
    return max(1, len([source for source in similar_sources if source]))


def _sort_key(article: dict[str, Any]) -> tuple[int, str, str]:
    return (
        int(article.get("coverage_score", 1) or 1),
        article.get("published_date", ""),
        article.get("title", ""),
    )


def _apply_source_cap(
    articles: list[dict[str, Any]],
    *,
    max_per_source: int,
    limit: int,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    seen_urls: set[str] = set()
    for article in articles:
        source = article.get("source", "") or "(unknown)"
        url_key = article.get("url", "") or f"{source}|{article.get('title', '')}"
        if url_key in seen_urls:
            continue
        if counts[source] >= max_per_source:
            continue
        selected.append(article)
        counts[source] += 1
        seen_urls.add(url_key)
        if len(selected) >= limit:
            break
    return selected


def _supplement_candidates(
    selected: list[dict[str, Any]],
    pool: list[dict[str, Any]],
    *,
    max_per_source: int,
    limit: int,
) -> list[dict[str, Any]]:
    combined = list(selected)
    counts = Counter(item.get("source", "") or "(unknown)" for item in combined)
    seen_urls = {
        item.get("url", "") or f"{item.get('source', '')}|{item.get('title', '')}"
        for item in combined
    }
    for article in pool:
        source = article.get("source", "") or "(unknown)"
        url_key = article.get("url", "") or f"{source}|{article.get('title', '')}"
        if url_key in seen_urls:
            continue
        if counts[source] >= max_per_source:
            continue
        combined.append(article)
        counts[source] += 1
        seen_urls.add(url_key)
        if len(combined) >= limit:
            break
    return combined[:limit]


def _heuristic_candidate_pools(
    all_articles: list[Any],
    candidate_n: int,
) -> dict[str, list[dict[str, Any]]]:
    normalized = [_normalize_article(article, index) for index, article in enumerate(all_articles)]
    for article in normalized:
        article["bucket"] = _guess_bucket(article)
        article["category"] = _guess_category(article)
        article["coverage_score"] = _coverage_score(article, normalized)

    world_pool = sorted(
        [article for article in normalized if article.get("bucket") == "world"],
        key=_sort_key,
        reverse=True,
    )
    korea_pool = sorted(
        [article for article in normalized if article.get("bucket") == "korea"],
        key=_sort_key,
        reverse=True,
    )

    world_selected = _apply_source_cap(world_pool, max_per_source=2, limit=candidate_n)
    korea_selected = _apply_source_cap(korea_pool, max_per_source=1, limit=candidate_n)
    return {
        "world": world_selected,
        "korea": korea_selected,
        "_world_pool": world_pool,
        "_korea_pool": korea_pool,
    }


def _parse_unified_response(text: str) -> list[dict[str, Any]]:
    payload = json.loads(_sanitize_json_array(text))
    return payload if isinstance(payload, list) else []


def _normalize_selector_payload(payload: Any) -> list[dict[str, Any]]:
    """Normalize selector JSON object/list outputs to a flat selection list."""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []

    selections: list[dict[str, Any]] = []
    for bucket in ("world", "korea"):
        items = payload.get(bucket, [])
        if not isinstance(items, list):
            continue
        for position, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                continue
            entry = dict(item)
            entry.setdefault("bucket", bucket)
            entry.setdefault("rank", position)
            selections.append(entry)
    return selections


def _validate_selector_payload(payload: Any) -> list[dict[str, Any]]:
    """Validate model selector output and return a flat selection list."""
    selections = _normalize_selector_payload(payload)
    if not selections:
        raise ValueError("selector output contained no selections")

    validated: list[dict[str, Any]] = []
    for item in selections:
        index = item.get("index")
        if not isinstance(index, int):
            raise ValueError(f"selector item missing integer index: {item}")
        bucket = str(item.get("bucket", "")).lower()
        if bucket not in {"world", "korea"}:
            raise ValueError(f"selector item has invalid bucket: {item}")
        category = str(item.get("category", "")).lower()
        if category not in _VALID_CATEGORIES:
            raise ValueError(f"selector item has invalid category: {item}")
        rank = item.get("rank", len(validated) + 1)
        if not isinstance(rank, int):
            raise ValueError(f"selector item has invalid rank: {item}")
        validated.append({
            **item,
            "bucket": bucket,
            "category": category,
            "rank": rank,
        })
    return validated


def _selector_config(config: dict | None, candidate_n: int) -> tuple[int, int, int]:
    cfg = config or {}
    news_cfg = cfg.get("news", {}) if isinstance(cfg.get("news", {}), dict) else {}
    llm_cfg = cfg.get("llm", {}) if isinstance(cfg.get("llm", {}), dict) else {}
    per_bucket = int(news_cfg.get("selector_candidates_per_bucket", max(candidate_n * 3, candidate_n)))
    max_items = int(news_cfg.get("selector_max_articles", per_bucket * 2))
    max_chars = int(llm_cfg.get("selector_max_input_chars", llm_cfg.get("max_input_chars", 8000)))
    return max(1, per_bucket), max(1, max_items), max(1000, max_chars)


def _build_llm_candidate_pool(
    heuristic: dict[str, list[dict[str, Any]]],
    *,
    per_bucket_limit: int,
    max_items: int,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    candidates.extend(heuristic.get("_world_pool", [])[:per_bucket_limit])
    candidates.extend(heuristic.get("_korea_pool", [])[:per_bucket_limit])

    seen_indices: set[int] = set()
    deduped: list[dict[str, Any]] = []
    for article in candidates:
        index = article.get("_original_index")
        if not isinstance(index, int) or index in seen_indices:
            continue
        deduped.append(article)
        seen_indices.add(index)
        if len(deduped) >= max_items:
            break
    return deduped


def _render_selector_article(article: dict[str, Any]) -> str:
    summary = re.sub(r"\s+", " ", article.get("summary", "")).strip()
    return (
        f"[{article.get('_original_index')}] [{article.get('source', '')}] {article.get('title', '')}\n"
        f"published={article.get('published_date', '')}\n"
        f"heuristic_bucket={article.get('bucket', '')}; coverage={article.get('coverage_score', 1)}\n"
        f"summary={summary[:220]}"
    )


def _build_selector_user_prompt(
    articles: list[dict[str, Any]],
    *,
    max_chars: int,
) -> str:
    header = "Mixed article list:\n\n"
    lines: list[str] = []
    used = len(header)
    for article in articles:
        rendered = _render_selector_article(article)
        next_used = used + len(rendered) + (2 if lines else 0)
        if lines and next_used > max_chars:
            break
        lines.append(rendered)
        used = next_used
    return header + "\n\n".join(lines)


def _call_unified_selector(provider, system_prompt: str, user_prompt: str) -> list[dict[str, Any]]:
    if hasattr(provider, "complete_json"):
        payload = provider.complete_json(system_prompt, user_prompt)
        return _validate_selector_payload(payload)

    response = provider.complete(system_prompt, user_prompt)
    try:
        payload = json.loads(response.strip())
    except json.JSONDecodeError:
        selections = _parse_unified_response(response)
        return _validate_selector_payload(selections)
    return _validate_selector_payload(payload)


def select_and_classify_news(
    provider,
    all_articles: list,
    top_n: int = 5,
    config: dict | None = None,
) -> dict:
    """
    Classify ALL articles by TOPIC and select buffered candidates per bucket.
    Returns {"world": [...], "korea": [...]} with bucket/category assigned.
    """
    candidate_n = top_n + 5
    heuristic = _heuristic_candidate_pools(all_articles, candidate_n)
    per_bucket_limit, max_items, max_chars = _selector_config(config, candidate_n)
    llm_candidates = _build_llm_candidate_pool(
        heuristic,
        per_bucket_limit=per_bucket_limit,
        max_items=max_items,
    )
    normalized = heuristic["_world_pool"] + heuristic["_korea_pool"]
    normalized_by_index = {item["_original_index"]: item for item in normalized}

    if provider is None or not all_articles:
        return {
            "world": heuristic["world"][:candidate_n],
            "korea": heuristic["korea"][:candidate_n],
        }

    system_prompt = UNIFIED_SELECTOR_PROMPT.format(candidate_n=candidate_n)
    user_prompt = _build_selector_user_prompt(llm_candidates, max_chars=max_chars)
    logger.info(
        "Unified selector prompt built with %d/%d articles (%d chars, max=%d)",
        len(re.findall(r"^\[", user_prompt, flags=re.MULTILINE)),
        len(all_articles),
        len(user_prompt),
        max_chars,
    )

    try:
        selections = _call_unified_selector(provider, system_prompt, user_prompt)
        ranked_world: list[dict[str, Any]] = []
        ranked_korea: list[dict[str, Any]] = []
        seen_indices: set[int] = set()

        for position, item in enumerate(selections, start=1):
            if not isinstance(item, dict):
                continue
            index = item.get("index")
            if not isinstance(index, int) or index in seen_indices:
                continue
            if index < 0 or index >= len(all_articles):
                continue
            base = normalized_by_index.get(index) or _normalize_article(all_articles[index], index)
            bucket = str(item.get("bucket", _guess_bucket(base))).lower()
            category = str(item.get("category", _guess_category(base))).lower()
            if bucket not in {"world", "korea"}:
                bucket = _guess_bucket(base)
            if category not in _VALID_CATEGORIES:
                category = _guess_category(base)
            annotated = {
                **base,
                "bucket": bucket,
                "category": category,
                "coverage_score": base.get("coverage_score", _coverage_score(base, list(normalized_by_index.values()))),
                "rank": int(item.get("rank", position) or position),
            }
            if bucket == "world":
                ranked_world.append(annotated)
            else:
                ranked_korea.append(annotated)
            seen_indices.add(index)

        ranked_world.sort(key=lambda item: (int(item.get("rank", 9999)), -int(item.get("coverage_score", 1) or 1)))
        ranked_korea.sort(key=lambda item: (int(item.get("rank", 9999)), -int(item.get("coverage_score", 1) or 1)))

        world_selected = _apply_source_cap(ranked_world, max_per_source=2, limit=candidate_n)
        korea_selected = _apply_source_cap(ranked_korea, max_per_source=1, limit=candidate_n)
        world_selected = _supplement_candidates(world_selected, heuristic["_world_pool"], max_per_source=2, limit=candidate_n)
        korea_selected = _supplement_candidates(korea_selected, heuristic["_korea_pool"], max_per_source=1, limit=candidate_n)

        return {
            "world": world_selected[:candidate_n],
            "korea": korea_selected[:candidate_n],
        }
    except Exception:
        logger.exception("Unified news selection failed — heuristic fallback")
        return {
            "world": heuristic["world"][:candidate_n],
            "korea": heuristic["korea"][:candidate_n],
        }


def select_top_news(
    provider: LLMProvider,
    articles: list,
    top_n: int = 5,
    category: str = "world",
) -> list:
    """Backward-compatible bucket-local selector."""
    if len(articles) <= top_n:
        return articles

    headlines = []
    for i, art in enumerate(articles):
        if isinstance(art, dict):
            title = art.get("title", "")
            source = art.get("source", "")
        else:
            title = getattr(art, "title", "")
            source = getattr(art, "source", "")
        headlines.append(f"[{i}] [{source}] {title}")

    user_prompt = (
        "Headlines:\n"
        + "\n".join(headlines)
        + f"\n\nSelect the {top_n} most important. Return JSON array of indices only:"
    )

    try:
        prompt_template = SELECTOR_PROMPT_KOREA if category == "korea" else SELECTOR_PROMPT_WORLD
        system = prompt_template.format(top_n=top_n)
        response = provider.complete(system, user_prompt)

        sanitized = _sanitize_json_array(response)
        indices = json.loads(sanitized)

        all_sources = set()
        for art in articles:
            src = art.get("source", "") if isinstance(art, dict) else getattr(art, "source", "")
            all_sources.add(src)
        enforce_source_diversity = len(all_sources) > 3

        selected = []
        used_sources: Counter[str] = Counter()
        max_per_source = 1 if category == "korea" else 2
        for idx in indices:
            if not isinstance(idx, int) or not (0 <= idx < len(articles)):
                continue
            art = articles[idx]
            src = art.get("source", "") if isinstance(art, dict) else getattr(art, "source", "")
            if enforce_source_diversity and used_sources[src] >= max_per_source:
                continue
            selected.append(art)
            used_sources[src] += 1
            if len(selected) >= top_n:
                break

        if enforce_source_diversity and len(selected) < top_n:
            for art in articles:
                if len(selected) >= top_n:
                    break
                src = art.get("source", "") if isinstance(art, dict) else getattr(art, "source", "")
                if used_sources[src] >= max_per_source:
                    continue
                selected.append(art)
                used_sources[src] += 1

        if selected:
            return selected[:top_n]
    except Exception:
        logger.exception("AI 뉴스 선별 실패 — 최신순 fallback")

    return articles[:top_n]
