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
    "기재부", "산업부", "과기부", "삼성", "현대", "SK", "LG",
    "네이버", "카카오", "수출", "원화", "한국 선박", "우리 기업",
}

LOW_VALUE_KEYWORDS = {
    "인사발령", "인사 발령", "부고", "운세", "로또", "날씨",
    "부임", "전보", "승진인사", "프로야구", "축구", "골프",
    "연예", "드라마", "예능", "맛집", "홀인원", "선발투수",
}


def _article_key(article: dict[str, Any]) -> str:
    return article.get("url", "") or f"{article.get('source', '')}|{article.get('title', '')}"


def _candidate_sort_key(article: dict[str, Any]) -> tuple[int, int, str]:
    rank = int(article.get("rank", 9999) or 9999)
    coverage = int(article.get("coverage_score", 1) or 1)
    return (coverage, -rank, article.get("published_date", ""))


def _ordered_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(candidates, key=_candidate_sort_key, reverse=True)


def _next_candidate(
    candidates: list[dict[str, Any]],
    current: list[dict[str, Any]],
    predicate,
    base_predicate=lambda _candidate: True,
) -> dict[str, Any] | None:
    current_keys = {_article_key(article) for article in current}
    for candidate in _ordered_candidates(candidates):
        if _article_key(candidate) in current_keys:
            continue
        if not base_predicate(candidate):
            continue
        if predicate(candidate):
            return candidate
    return None


def _article_text(article: dict[str, Any]) -> str:
    return f"{article.get('title', '')} {article.get('description', '') or article.get('summary', '')}"


def _is_low_value(article: dict[str, Any]) -> bool:
    text = _article_text(article).lower()
    return any(keyword.lower() in text for keyword in LOW_VALUE_KEYWORDS)


def _has_korea_direct_impact(text: str) -> bool:
    lowered = text.lower()
    impact_terms = {
        "한국", "국내", "우리", "수출", "원화", "코스피", "코스닥",
        "한국 선박", "한국 기업", "국내 기업", "삼성", "현대", "sk", "lg",
    }
    return any(term.lower() in lowered for term in impact_terms)


def _is_domestic_korea(article: dict[str, Any]) -> bool:
    text = _article_text(article)
    lowered = text.lower()
    domestic_hits = sum(1 for keyword in DOMESTIC_KEYWORDS if keyword.lower() in lowered)
    international_hits = sum(1 for keyword in INTERNATIONAL_KEYWORDS if keyword.lower() in lowered)
    if domestic_hits <= 0:
        return False
    if international_hits <= 0:
        return True
    return _has_korea_direct_impact(text)


def is_valid_world_candidate(article: dict[str, Any]) -> bool:
    return not _is_low_value(article)


def is_valid_korea_candidate(article: dict[str, Any]) -> bool:
    return _is_domestic_korea(article) and not _is_low_value(article)


def check_article_count(
    articles: list[dict[str, Any]],
    target: int,
    all_candidates: list[dict[str, Any]],
    violations: list[dict[str, Any]],
    section: str = "",
    base_predicate=lambda _candidate: True,
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
        replacement = _next_candidate(
            all_candidates,
            current,
            lambda _candidate: True,
            base_predicate=base_predicate,
        )
        if replacement is None:
            violations.append({
                "check": "article_count",
                "section": section,
                "severity": "error",
                "detail": f"{len(current)} valid articles available",
                "action": f"could not pad to {target}",
            })
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
    base_predicate=lambda _candidate: True,
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
            base_predicate=base_predicate,
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
    for index in range(len(current) - 1, -1, -1):
        article = current[index]
        if is_valid_korea_candidate(article):
            continue

        replacement = _next_candidate(
            korea_candidates,
            current,
            lambda candidate: True,
            base_predicate=is_valid_korea_candidate,
        )
        if replacement is None:
            current.pop(index)
            violations.append({
                "check": "korea_purity",
                "section": "korea",
                "severity": "error",
                "detail": article.get("title", ""),
                "action": "removed invalid korea article; no valid replacement available",
            })
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
    base_predicate=lambda _candidate: True,
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
            base_predicate=base_predicate,
        )
        if missing_candidate is None:
            violations.append({
                "check": "category_balance",
                "section": section,
                "severity": "warning",
                "detail": f"only {len(categories)} categories available",
                "action": "kept higher-quality candidates instead of forcing balance",
            })
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


def validate_final_selection(
    world: list[dict[str, Any]],
    korea: list[dict[str, Any]],
    target: int,
) -> list[str]:
    errors: list[str] = []
    if len(world) < target:
        errors.append(f"world has {len(world)} valid articles, target {target}")
    if len(korea) < target:
        errors.append(f"korea has {len(korea)} valid articles, target {target}")
    for article in world:
        if not is_valid_world_candidate(article):
            errors.append(f"invalid world article: {article.get('title', '')[:80]}")
    for article in korea:
        if not is_valid_korea_candidate(article):
            errors.append(f"invalid korea article: {article.get('title', '')[:80]}")
    return errors


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

    world_pool = [article for article in world_candidates if is_valid_world_candidate(article)]
    korea_pool = [article for article in korea_candidates if is_valid_korea_candidate(article)]
    removed_world = len(world_candidates) - len(world_pool)
    removed_korea = len(korea_candidates) - len(korea_pool)
    if removed_world:
        violations.append({
            "check": "candidate_filter",
            "section": "world",
            "detail": f"{removed_world} low-value candidate(s)",
            "action": "removed before quality gates",
        })
    if removed_korea:
        violations.append({
            "check": "candidate_filter",
            "section": "korea",
            "detail": f"{removed_korea} invalid or low-value candidate(s)",
            "action": "removed before quality gates",
        })

    world = world_pool[:top_n]
    korea = korea_pool[:top_n]

    world = check_article_count(world, top_n, world_pool, violations, section="world", base_predicate=is_valid_world_candidate)
    korea = check_article_count(korea, top_n, korea_pool, violations, section="korea", base_predicate=is_valid_korea_candidate)

    world = check_source_diversity(world, max_per_source=2, candidates=world_pool, violations=violations, section="world", base_predicate=is_valid_world_candidate)
    korea = check_source_diversity(korea, max_per_source=1, candidates=korea_pool, violations=violations, section="korea", base_predicate=is_valid_korea_candidate)

    korea = check_korea_purity(korea, korea_pool, violations)

    world = check_category_balance(world, min_categories=3, candidates=world_pool, violations=violations, section="world", base_predicate=is_valid_world_candidate)
    korea = check_category_balance(korea, min_categories=3, candidates=korea_pool, violations=violations, section="korea", base_predicate=is_valid_korea_candidate)

    world, korea = check_cross_section_dedup(world, korea, violations)
    world = check_article_count(world, top_n, world_pool, violations, section="world", base_predicate=is_valid_world_candidate)
    korea = check_article_count(korea, top_n, korea_pool, violations, section="korea", base_predicate=is_valid_korea_candidate)
    world = check_source_diversity(world, max_per_source=2, candidates=world_pool, violations=violations, section="world", base_predicate=is_valid_world_candidate)
    korea = check_source_diversity(korea, max_per_source=1, candidates=korea_pool, violations=violations, section="korea", base_predicate=is_valid_korea_candidate)

    world = [article for article in world if is_valid_world_candidate(article)]
    korea = [article for article in korea if is_valid_korea_candidate(article)]

    world = world[:top_n]
    korea = korea[:top_n]

    for error in validate_final_selection(world, korea, top_n):
        violations.append({
            "check": "final_selection",
            "severity": "error",
            "detail": error,
            "action": "left selection underfilled rather than inserting invalid article",
        })

    if violations:
        _log_violations(violations, config)

    return world, korea
