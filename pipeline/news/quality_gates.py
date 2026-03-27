"""Post-AI-selection quality gates for news curation consistency."""

from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

from pipeline.news.dedup import containment_similarity, extract_topic_tokens

logger = logging.getLogger(__name__)

INTERNATIONAL_KEYWORDS = {
    "Trump", "Biden", "EU", "China", "Iran", "Israel", "NATO", "UN",
    "Fed", "ECB", "BOJ", "Pentagon", "Congress", "White House", "Brexit",
    "Taliban", "Ukraine", "Russia", "Putin", "Xi Jinping", "Gaza",
    "트럼프", "바이든", "유럽연합", "중국", "이란", "이스라엘", "나토",
    "우크라이나", "러시아", "푸틴", "시진핑", "가자",
}

DOMESTIC_KEYWORDS = {
    "한국", "국내", "정부", "한은", "한국은행", "코스피", "코스닥",
    "부동산", "법원", "국회", "대통령", "총리", "서울", "경제부",
    "기재부", "산업부", "과기부",
}


def _article_key(article: dict[str, Any]) -> str:
    return article.get("url", "") or f"{article.get('source', '')}|{article.get('title', '')}"


def _candidate_sort_key(article: dict[str, Any]) -> tuple[int, int, str]:
    rank = int(article.get("rank", 9999) or 9999)
    coverage = int(article.get("coverage_score", 1) or 1)
    return (-coverage, -max(0, 10000 - rank), article.get("published_date", ""))


def _ordered_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(candidates, key=_candidate_sort_key, reverse=True)


def _next_candidate(
    candidates: list[dict[str, Any]],
    current: list[dict[str, Any]],
    predicate,
) -> dict[str, Any] | None:
    current_keys = {_article_key(article) for article in current}
    for candidate in _ordered_candidates(candidates):
        if _article_key(candidate) in current_keys:
            continue
        if predicate(candidate):
            return candidate
    return None


def check_article_count(
    articles: list[dict[str, Any]],
    target: int,
    all_candidates: list[dict[str, Any]],
    violations: list[dict[str, Any]],
    section: str = "",
) -> list[dict[str, Any]]:
    current = list(articles)
    if len(current) > target:
        violations.append({
            "check": "article_count",
            "section": section,
            "detail": f"{len(current)} articles selected",
            "action": f"trimmed to {target}",
        })
        return current[:target]

    while len(current) < target:
        replacement = _next_candidate(all_candidates, current, lambda _candidate: True)
        if replacement is None:
            break
        current.append(replacement)
        violations.append({
            "check": "article_count",
            "section": section,
            "detail": f"{len(current) - 1} articles available",
            "action": f"padded with {replacement.get('source', '')}: {replacement.get('title', '')}",
        })
    return current


def check_source_diversity(
    articles: list[dict[str, Any]],
    max_per_source: int,
    candidates: list[dict[str, Any]],
    violations: list[dict[str, Any]],
    section: str,
) -> list[dict[str, Any]]:
    current = list(articles)
    counts = Counter(article.get("source", "") for article in current)
    for index in range(len(current) - 1, -1, -1):
        article = current[index]
        source = article.get("source", "")
        if counts[source] <= max_per_source:
            continue
        replacement = _next_candidate(
            candidates,
            current,
            lambda candidate: counts[candidate.get("source", "")] < max_per_source,
        )
        if replacement is None:
            continue
        current[index] = replacement
        counts[source] -= 1
        counts[replacement.get("source", "")] += 1
        violations.append({
            "check": "source_diversity",
            "section": section,
            "detail": f"{source} exceeded max_per_source={max_per_source}",
            "action": f"replaced with {replacement.get('source', '')}: {replacement.get('title', '')}",
        })
    return current


def check_korea_purity(
    korea_articles: list[dict[str, Any]],
    korea_candidates: list[dict[str, Any]],
    violations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    current = list(korea_articles)
    for index, article in enumerate(list(current)):
        text = f"{article.get('title', '')} {article.get('description', '') or article.get('summary', '')}"
        has_international = any(keyword.lower() in text.lower() for keyword in INTERNATIONAL_KEYWORDS)
        has_domestic = any(keyword.lower() in text.lower() for keyword in DOMESTIC_KEYWORDS)
        if not has_international or has_domestic:
            continue

        replacement = _next_candidate(
            korea_candidates,
            current,
            lambda candidate: (
                not any(keyword.lower() in f"{candidate.get('title', '')} {candidate.get('description', '') or candidate.get('summary', '')}".lower() for keyword in INTERNATIONAL_KEYWORDS)
                or any(keyword.lower() in f"{candidate.get('title', '')} {candidate.get('description', '') or candidate.get('summary', '')}".lower() for keyword in DOMESTIC_KEYWORDS)
            ),
        )
        if replacement is None:
            continue
        current[index] = replacement
        violations.append({
            "check": "korea_purity",
            "section": "korea",
            "detail": article.get("title", ""),
            "action": f"replaced with {replacement.get('title', '')}",
        })
    return current


def check_category_balance(
    articles: list[dict[str, Any]],
    min_categories: int,
    candidates: list[dict[str, Any]],
    violations: list[dict[str, Any]],
    section: str,
) -> list[dict[str, Any]]:
    current = list(articles)
    categories = Counter(article.get("category", "") for article in current if article.get("category"))
    if len(categories) >= min_categories:
        return current

    needed = min_categories - len(categories)
    dominant = [category for category, _count in categories.most_common()]
    for _ in range(needed):
        missing_candidate = _next_candidate(
            candidates,
            current,
            lambda candidate: candidate.get("category", "") not in categories,
        )
        if missing_candidate is None:
            break

        replace_index = next(
            (
                idx for idx in range(len(current) - 1, -1, -1)
                if current[idx].get("category", "") in dominant
            ),
            len(current) - 1,
        )
        replaced = current[replace_index]
        current[replace_index] = missing_candidate
        categories[missing_candidate.get("category", "")] += 1
        violations.append({
            "check": "category_balance",
            "section": section,
            "detail": f"only {len(set(categories)) - 1} categories before swap",
            "action": f"replaced {replaced.get('title', '')} with {missing_candidate.get('title', '')}",
        })
    return current


def check_cross_section_dedup(
    world: list[dict[str, Any]],
    korea: list[dict[str, Any]],
    violations: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    world_result = list(world)
    korea_result = list(korea)
    world_tokens = {idx: extract_topic_tokens(article.get("title", "")) for idx, article in enumerate(world_result)}

    for korea_index in range(len(korea_result) - 1, -1, -1):
        korea_article = korea_result[korea_index]
        korea_key = _article_key(korea_article)
        korea_topic = extract_topic_tokens(korea_article.get("title", ""))
        for world_index, world_article in enumerate(world_result):
            if korea_key == _article_key(world_article):
                violations.append({
                    "check": "cross_section_dedup",
                    "section": "korea",
                    "detail": korea_article.get("title", ""),
                    "action": "removed duplicate URL from korea section",
                })
                korea_result.pop(korea_index)
                break

            world_topic = world_tokens.get(world_index, set())
            if not korea_topic or not world_topic:
                continue
            if containment_similarity(korea_topic, world_topic) >= 0.6:
                keep_world = int(world_article.get("coverage_score", 1) or 1) >= int(korea_article.get("coverage_score", 1) or 1)
                if keep_world:
                    violations.append({
                        "check": "cross_section_dedup",
                        "section": "korea",
                        "detail": korea_article.get("title", ""),
                        "action": "removed topic overlap in favor of world section",
                    })
                    korea_result.pop(korea_index)
                else:
                    violations.append({
                        "check": "cross_section_dedup",
                        "section": "world",
                        "detail": world_article.get("title", ""),
                        "action": "removed topic overlap in favor of korea section",
                    })
                    world_result.pop(world_index)
                break

    return world_result, korea_result


def _log_violations(violations: list[dict[str, Any]], config: dict) -> None:
    output_dir = Path(config.get("output", {}).get("dir", "output"))
    log_path = output_dir / "data" / "quality-log.json"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    existing: list[dict[str, Any]] = []
    if log_path.exists():
        try:
            existing = json.loads(log_path.read_text(encoding="utf-8"))
            if not isinstance(existing, list):
                existing = []
        except Exception:
            existing = []

    stamped = [{**entry, "date": date.today().isoformat()} for entry in violations]
    existing.extend(stamped)
    log_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Quality gates logged %d violation(s) → %s", len(stamped), log_path)


def run_quality_gates(world_candidates, korea_candidates, config):
    """Run all 5 quality checks. Returns (world_final, korea_final) of exactly top_n each."""
    top_n = config.get("news", {}).get("top_n", 5)
    violations: list[dict[str, Any]] = []

    world_pool = list(world_candidates)
    korea_pool = list(korea_candidates)
    world = world_pool[:top_n]
    korea = korea_pool[:top_n]

    world = check_article_count(world, top_n, world_pool, violations, section="world")
    korea = check_article_count(korea, top_n, korea_pool, violations, section="korea")

    world = check_source_diversity(world, max_per_source=2, candidates=world_pool, violations=violations, section="world")
    korea = check_source_diversity(korea, max_per_source=1, candidates=korea_pool, violations=violations, section="korea")

    korea = check_korea_purity(korea, korea_pool, violations)

    world = check_category_balance(world, min_categories=3, candidates=world_pool, violations=violations, section="world")
    korea = check_category_balance(korea, min_categories=3, candidates=korea_pool, violations=violations, section="korea")

    world, korea = check_cross_section_dedup(world, korea, violations)
    world = check_article_count(world, top_n, world_pool, violations, section="world")
    korea = check_article_count(korea, top_n, korea_pool, violations, section="korea")
    world = check_source_diversity(world, max_per_source=2, candidates=world_pool, violations=violations, section="world")
    korea = check_source_diversity(korea, max_per_source=1, candidates=korea_pool, violations=violations, section="korea")

    world = world[:top_n]
    korea = korea[:top_n]

    if violations:
        _log_violations(violations, config)

    return world, korea
