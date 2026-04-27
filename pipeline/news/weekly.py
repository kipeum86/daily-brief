"""Weekly news collection and issue clustering helpers."""

from __future__ import annotations

import copy
import json
import logging
import re
from collections import Counter
from datetime import date, datetime, timedelta
from typing import Any

from pipeline.models import Article
from pipeline.ai.translate import translate_news
from pipeline.news.collector import collect_articles, fill_missing_descriptions
from pipeline.news.dedup import canonicalize_url, containment_similarity, extract_topic_tokens
from pipeline.news.filters import keyword_filter
from pipeline.news.naver import collect_naver_news

logger = logging.getLogger("daily-brief.news.weekly")

_GOOGLE_NEWS_WINDOW_RE = re.compile(r"when:(\d+)d")
_CLUSTER_MIN_OVERLAP = 2
_CLUSTER_SIMILARITY = 0.4
_JSON_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)
_DATE_IN_TEXT_RE = re.compile(r"(20\d{2})[-./](\d{1,2})[-./](\d{1,2})")
_DATE_IN_URL_RE = re.compile(r"/(20\d{2})/(\d{1,2})/(\d{1,2})/")

_WEEKLY_SELECTOR_PROMPT_WORLD = """\
You are a financial editor preparing a weekly recap for investors.
Choose the {top_n} most important weekly issues from the shortlist.

Selection criteria:
- Economic or market significance matters more than raw mention counts alone.
- Use mention count and source diversity as evidence that the issue persisted across the week.
- Maximize source diversity when possible. Prefer a strong alternative outlet over a second or third pick from the same outlet.
- Prefer geopolitics, macro policy, trade, central banks, major corporate or market-moving stories.
- Avoid selecting overlapping stories about the same issue.

Return ONLY a JSON array of selected indices.
Example: [0, 3, 5]"""

_WEEKLY_SELECTOR_PROMPT_KOREA = """\
You are a Korean financial editor preparing a weekly recap.
Choose the {top_n} most important DOMESTIC Korean weekly issues from the shortlist.

Selection criteria:
- Prioritize Korean policy, Korean economy, Korean markets, major Korean corporates and industries.
- Mention count and source diversity matter, but importance comes first.
- Maximize source diversity when possible. Do not stack many picks from the same outlet if strong alternatives exist.
- Exclude low-signal general society, education, ceremony, and local civic stories unless they clearly affect markets, policy, or the economy.
- Exclude foreign issues even if Korean outlets covered them.
- Avoid selecting overlapping stories about the same issue.

Return ONLY a JSON array of selected indices.
Example: [0, 3, 5]"""

_WEEKLY_BUCKET_PROMPT = """\
You are classifying weekly news issues for a bilingual recap.

Classify each issue into one of two buckets:
- "korea": primarily about domestic Korean policy, economy, markets, companies, industries, regulation, housing, employment, inflation, exports, or Korean politics.
- "world": everything else.

Important:
- If a Korean outlet covers a foreign issue such as US politics, Middle East conflict, China policy, Japan diplomacy, Ukraine war, or EU regulation, it is still "world".
- Use the title and summary to decide the main topic, not the outlet name alone.

Return ONLY a JSON array of objects with this exact shape:
[{"id": 0, "bucket": "world"}, {"id": 1, "bucket": "korea"}]"""

_KOREA_HINTS = (
    "한국", "국내", "코스피", "코스닥", "원화", "원/달러", "한국은행", "한은", "기재부",
    "금통위", "부동산", "주택", "전세", "대출", "수출", "반도체", "삼성", "sk", "현대",
    "lg", "카카오", "네이버", "서울", "정부", "국회", "대통령실", "여당", "야당",
)
_KOREA_PRIORITY_HINTS = (
    "한국은행", "한은", "금리", "물가", "고용", "환율", "원화", "원/달러", "코스피", "코스닥",
    "부동산", "주택", "전세", "대출", "세제", "예산", "추경", "기재부", "금통위", "규제",
    "수출", "반도체", "배터리", "자동차", "조선", "철강", "에너지", "전력", "관세", "무역",
    "공장", "생산", "투자", "실적", "기업", "산업", "삼성", "sk", "현대", "lg", "카카오",
    "네이버", "롯데", "포스코", "은행", "금융", "증권", "채권", "탄소중립", "탄소", "기후",
)
_KOREA_LOW_SIGNAL_HINTS = (
    "학교", "교과서", "묘역", "추모", "기념", "축제", "사고", "범죄", "재판소원", "헌재",
    "개학", "교육", "공항", "날씨", "질병", "복지",
)
_KOREA_HARD_EXCLUDE_HINTS = (
    "기업pr", "brandbrief", "브랜드브리프", "현장 체험", "탐방 프로그램",
)
_KOREA_STRONG_SECTION_HINTS = (
    "/economy/", "/finance/", "/market/", "/realestate/", "/industry/", "/biz/", "/business/",
    "/money/", "/stock/", "/securities/", "/policy/", "/economy_", "경제", "증권", "금융",
    "산업", "기업", "부동산", "market", "finance", "economy",
)
_KOREA_LOW_SIGNAL_SECTION_HINTS = (
    "/sports/", "/sport/", "/baseball/", "/area/", "/capital/", "/culture/", "/entertain/",
    "/travel/", "/health/", "/world/", "/photo/", "/cartoon/", "/people/", "정치일반", "기업pr",
    "본문 스포츠", "본문 전국", "본문 지역", "본문 사회", "본문 문화", "본문 정치 정치일반",
    "본문 국제", "야구", "축구", "연예", "아이돌", "화재", "사망", "추모",
)
_KNOWN_KOREA_OUTLETS = {
    "연합뉴스", "조선일보", "중앙일보", "동아일보", "한겨레", "한국경제", "매일경제",
    "서울경제", "뉴시스", "머니투데이", "이데일리", "파이낸셜뉴스", "비즈워치",
    "아시아경제", "쿠키뉴스", "뉴스1", "헤럴드경제", "서울신문", "국민일보", "이투데이",
    "SBS", "SBS Biz", "KBS", "MBC", "JTBC", "YTN", "데일리안", "노컷뉴스", "네이버뉴스",
}
_WORLD_HINTS = (
    "미국", "중국", "일본", "유럽", "eu", "러시아", "우크라", "이란", "이스라엘",
    "하마스", "가자", "트럼프", "바이든", "fed", "fomc", "파월", "백악관",
    "middle east", "iran", "israel", "ukraine", "russia", "washington",
    "u.s.", "us ", "china", "japan", "europe", "european union",
)


def _build_korea_source_names(config: dict) -> set[str]:
    korea_source_names: set[str] = set(_KNOWN_KOREA_OUTLETS)
    for korea_key in ("korea", "korea_major"):
        korea_cfg = config.get("news", {}).get(korea_key, [])
        if isinstance(korea_cfg, list):
            for src in korea_cfg:
                if isinstance(src, dict):
                    korea_source_names.add(src.get("name", ""))
        elif isinstance(korea_cfg, dict):
            korea_source_names.add("네이버뉴스")
    return korea_source_names


def _is_korea_source(source: str, config: dict) -> bool:
    return source in _build_korea_source_names(config)


def _classify_bucket(article: Article, config: dict) -> str:
    return "korea" if article.source in _build_korea_source_names(config) else "world"


def _expand_google_news_window(url: str, days_back: int) -> str:
    if "when:" not in url:
        return url
    if _GOOGLE_NEWS_WINDOW_RE.search(url):
        return _GOOGLE_NEWS_WINDOW_RE.sub(f"when:{days_back}d", url)
    return url.replace("when:", f"when:{days_back}d")


def _build_weekly_news_config(config: dict, days_back: int) -> dict:
    weekly_config = copy.deepcopy(config)
    news_config = weekly_config.setdefault("news", {})
    news_config["days_back"] = max(days_back, int(news_config.get("days_back", days_back)))
    news_config["max_per_source"] = max(20, int(news_config.get("max_per_source", 15)))

    for section in ("world", "finance"):
        sources = news_config.get(section, [])
        if not isinstance(sources, list):
            continue
        for src in sources:
            if isinstance(src, dict) and src.get("url"):
                src["url"] = _expand_google_news_window(src["url"], days_back)

    korea_config = news_config.get("korea", {})
    if isinstance(korea_config, dict):
        korea_config["display"] = max(10, int(korea_config.get("display", 5)))
        korea_config["sort"] = "date"

    return weekly_config


def _sanitize_selector_json(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    match = _JSON_ARRAY_RE.search(text)
    if match:
        return match.group(0)
    return text


def _parse_date(value: str) -> date | None:
    if not value:
        return None
    trimmed = value.strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M", "%a, %d %b %Y %H:%M:%S %z"):
        try:
            return datetime.strptime(trimmed, fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(trimmed).date()
    except ValueError:
        return None


def _extract_date_from_text(*parts: str) -> date | None:
    """Best-effort date extraction from article text or URL fragments."""
    for part in parts:
        text = (part or "").strip()
        if not text:
            continue

        for pattern in (_DATE_IN_TEXT_RE, _DATE_IN_URL_RE):
            match = pattern.search(text)
            if not match:
                continue
            try:
                year, month, day = (int(group) for group in match.groups())
                return date(year, month, day)
            except ValueError:
                continue
    return None


def _collect_recent_articles(
    config: dict,
    news_window_start: str,
    news_window_end: str,
) -> list[Article]:
    start_day = date.fromisoformat(news_window_start)
    end_day = date.fromisoformat(news_window_end)
    days_back = max(7, (end_day - start_day).days + 1)
    weekly_config = _build_weekly_news_config(config, days_back)

    articles, failed_sources = collect_articles(weekly_config)
    if failed_sources:
        logger.warning("Weekly RSS sources failed: %s", ", ".join(failed_sources))

    korea_source = weekly_config.get("news", {}).get("korea", {}).get("source", "rss")
    if korea_source == "naver":
        naver_articles = collect_naver_news(
            weekly_config,
            display=max(10, int(weekly_config.get("news", {}).get("korea", {}).get("display", 10))),
            sort="date",
            days_back=days_back,
            top_n=None,
            limit_to_top_n=False,
        )
        for item in naver_articles:
            articles.append(Article(
                title=item.get("title", ""),
                url=item.get("url", ""),
                source=item.get("source", "네이버뉴스"),
                description=item.get("summary", ""),
                published_date=item.get("published", ""),
            ))
        logger.info("Weekly Naver articles added: %d", len(naver_articles))

    filtered = keyword_filter(articles, config.get("keywords", {}))
    deduped: list[Article] = []
    seen_keys: set[str] = set()
    for article in filtered:
        raw_key = article.url.strip() or f"{article.source}|{article.title}"
        try:
            dedup_key = canonicalize_url(raw_key) if article.url else raw_key
        except Exception:
            dedup_key = raw_key
        if dedup_key in seen_keys:
            continue
        seen_keys.add(dedup_key)
        deduped.append(article)

    windowed: list[Article] = []
    dropped_undated = 0
    for article in deduped:
        published_day = _parse_date(article.published_date)
        if published_day is None:
            published_day = _extract_date_from_text(
                article.description,
                article.title,
                article.url,
            )
            if published_day is not None:
                article.published_date = published_day.isoformat()
        if published_day is None:
            dropped_undated += 1
            continue
        if published_day and not (start_day <= published_day <= end_day):
            continue
        windowed.append(article)

    logger.info(
        "Weekly news pool prepared: %d collected -> %d filtered -> %d exact-deduped -> %d in window (%d undated dropped)",
        len(articles),
        len(filtered),
        len(deduped),
        len(windowed),
        dropped_undated,
    )
    return windowed


def _cluster_score(tokens: set[str], cluster: dict[str, Any]) -> float:
    if not tokens:
        return 0.0
    best_score = 0.0
    for cluster_tokens in (cluster.get("core_tokens") or set(), cluster.get("lead_tokens") or set()):
        if not cluster_tokens:
            continue
        overlap = len(tokens & cluster_tokens)
        similarity = containment_similarity(tokens, cluster_tokens)
        if overlap < _CLUSTER_MIN_OVERLAP and similarity < _CLUSTER_SIMILARITY:
            continue
        best_score = max(best_score, overlap + (similarity * 10))
    return best_score


def _refresh_cluster_tokens(cluster: dict[str, Any]) -> None:
    token_counts = cluster["token_counts"]
    shared_tokens = {token for token, count in token_counts.items() if count >= 2}
    if shared_tokens:
        cluster["core_tokens"] = shared_tokens
        return
    cluster["core_tokens"] = {
        token for token, _count in token_counts.most_common(8)
    }


def _cluster_bucket_articles(
    articles: list[dict[str, Any]],
    end_day: date,
) -> list[dict[str, Any]]:
    clusters: list[dict[str, Any]] = []
    ordered_articles = sorted(
        articles,
        key=lambda item: (item.get("published_date", ""), item.get("title", "")),
        reverse=True,
    )

    for article in ordered_articles:
        tokens = article.get("topic_tokens", set())
        best_cluster: dict[str, Any] | None = None
        best_score = 0.0
        for cluster in clusters:
            score = _cluster_score(tokens, cluster)
            if score > best_score:
                best_cluster = cluster
                best_score = score

        if best_cluster is None:
            clusters.append({
                "lead": article,
                "lead_tokens": set(tokens),
                "core_tokens": set(tokens),
                "token_counts": Counter(tokens),
                "articles": [article],
                "sources": {article.get("source", "")},
                "dates": {article.get("published_date", "")},
                "bucket_votes": Counter([article.get("bucket", "world")]),
            })
            continue

        best_cluster["articles"].append(article)
        best_cluster["sources"].add(article.get("source", ""))
        best_cluster["dates"].add(article.get("published_date", ""))
        best_cluster["token_counts"].update(tokens)
        best_cluster["bucket_votes"].update([article.get("bucket", "world")])
        lead = best_cluster["lead"]
        lead_score = (
            2 if article.get("summary") else 0,
            article.get("published_date", ""),
            len(article.get("title", "")),
        )
        current_score = (
            2 if lead.get("summary") else 0,
            lead.get("published_date", ""),
            len(lead.get("title", "")),
        )
        if lead_score > current_score:
            best_cluster["lead"] = article
            best_cluster["lead_tokens"] = set(tokens)
        _refresh_cluster_tokens(best_cluster)

    ranked: list[dict[str, Any]] = []
    for cluster in clusters:
        lead = dict(cluster["lead"])
        dates = sorted(day for day in cluster["dates"] if day)
        latest_date = dates[-1] if dates else lead.get("published_date", "")
        latest_day = _parse_date(latest_date)
        recency_bonus = 0
        if latest_day:
            gap = (end_day - latest_day).days
            if gap <= 1:
                recency_bonus = 4
            elif gap <= 3:
                recency_bonus = 2

        appearances = len(cluster["articles"])
        source_count = len([src for src in cluster["sources"] if src])
        active_days = len(dates)
        lead.update({
            "appearances": appearances,
            "source_count": source_count,
            "active_days": active_days,
            "latest_date": latest_date,
            "dates": dates,
            "score": (appearances * 5) + (source_count * 4) + (active_days * 3) + recency_bonus,
            "bucket_votes": dict(cluster["bucket_votes"]),
            "cluster_articles": [dict(item) for item in cluster["articles"]],
        })
        ranked.append(lead)

    ranked.sort(
        key=lambda item: (
            item.get("score", 0),
            item.get("appearances", 0),
            item.get("source_count", 0),
            item.get("latest_date", ""),
        ),
        reverse=True,
    )
    return ranked


def _normalize_article(article: Article, config: dict) -> dict[str, Any]:
    published_day = _parse_date(article.published_date)
    summary = article.description.strip()
    return {
        "title": article.title.strip(),
        "url": article.url.strip(),
        "source": article.source.strip(),
        "summary": summary,
        "description": summary,
        "published_date": published_day.isoformat() if published_day else (article.published_date[:10] if article.published_date else ""),
        "bucket": _classify_bucket(article, config),
        "is_korea_source": _is_korea_source(article.source.strip(), config),
        "topic_tokens": extract_topic_tokens(f"{article.title} {summary}"),
    }


def _decorate_display(article: dict[str, Any]) -> dict[str, Any]:
    entry = dict(article)
    entry["summary"] = entry.get("summary") or entry.get("description", "")
    entry["source_count"] = int(entry.get("source_count", 0) or 0)
    entry["appearances"] = int(entry.get("appearances", 0) or 0)
    entry["active_days"] = int(entry.get("active_days", 0) or 0)
    entry.pop("topic_tokens", None)
    entry.pop("cluster_articles", None)
    entry.pop("bucket_votes", None)
    entry.pop("is_korea_source", None)
    return entry


def _heuristic_issue_bucket(item: dict[str, Any]) -> str:
    text = " ".join([
        item.get("title", ""),
        item.get("summary", "") or item.get("description", ""),
    ]).lower()
    korea_hits = sum(1 for token in _KOREA_HINTS if token.lower() in text)
    world_hits = sum(1 for token in _WORLD_HINTS if token.lower() in text)

    votes = item.get("bucket_votes", {})
    korea_vote = int(votes.get("korea", 0) or 0)
    world_vote = int(votes.get("world", 0) or 0)

    if world_hits >= max(2, korea_hits + 1):
        return "world"
    if korea_hits >= max(1, world_hits):
        return "korea"
    if korea_vote > world_vote:
        return "korea"
    return "world"


def _korea_relevance_details(item: dict[str, Any]) -> dict[str, int]:
    text = " ".join([
        item.get("title", ""),
        item.get("summary", "") or item.get("description", ""),
        item.get("url", ""),
    ]).lower()
    priority_hits = sum(1 for token in _KOREA_PRIORITY_HINTS if token.lower() in text)
    low_signal_hits = sum(1 for token in _KOREA_LOW_SIGNAL_HINTS if token.lower() in text)
    hard_exclude_hits = sum(1 for token in _KOREA_HARD_EXCLUDE_HINTS if token.lower() in text)
    strong_section_hits = sum(1 for token in _KOREA_STRONG_SECTION_HINTS if token.lower() in text)
    weak_section_hits = sum(1 for token in _KOREA_LOW_SIGNAL_SECTION_HINTS if token.lower() in text)
    appearances = int(item.get("appearances", 0) or 0)
    source_count = int(item.get("source_count", 0) or 0)
    return {
        "priority_hits": priority_hits,
        "low_signal_hits": low_signal_hits,
        "hard_exclude_hits": hard_exclude_hits,
        "strong_section_hits": strong_section_hits,
        "weak_section_hits": weak_section_hits,
        "appearances": appearances,
        "source_count": source_count,
    }


def _is_viable_korea_candidate(item: dict[str, Any]) -> bool:
    details = _korea_relevance_details(item)
    if details["hard_exclude_hits"] > 0 and details["priority_hits"] <= 1 and details["source_count"] <= 1:
        return False
    has_core_signal = (
        details["priority_hits"] > 0
        or details["strong_section_hits"] > 0
        or details["appearances"] >= 2
        or details["source_count"] >= 2
    )
    if not has_core_signal:
        return False
    if (
        details["weak_section_hits"] > 0
        and details["priority_hits"] == 0
        and details["strong_section_hits"] == 0
        and details["source_count"] <= 1
        and details["appearances"] <= 1
    ):
        return False
    return True


def _is_relaxed_korea_candidate(item: dict[str, Any]) -> bool:
    details = _korea_relevance_details(item)
    if details["hard_exclude_hits"] > 0:
        return False
    if details["weak_section_hits"] >= 2 and details["priority_hits"] == 0:
        return False
    if details["low_signal_hits"] >= 2 and details["strong_section_hits"] == 0:
        return False
    return True


def _korea_relevance_score(item: dict[str, Any]) -> int:
    details = _korea_relevance_details(item)
    score = (details["priority_hits"] * 4) + (details["strong_section_hits"] * 3)
    score += min(details["appearances"], 3)
    score += min(details["source_count"], 2)
    score -= details["low_signal_hits"] * 2
    score -= details["hard_exclude_hits"] * 8
    score -= details["weak_section_hits"] * 5

    if not _is_viable_korea_candidate(item):
        score -= 8
    return score


def _classify_weekly_candidates(
    provider: Any | None,
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not candidates:
        return []

    if provider is None:
        return [
            {**item, "bucket": _heuristic_issue_bucket(item)}
            for item in candidates
        ]

    lines = []
    for idx, item in enumerate(candidates):
        summary = (item.get("summary") or item.get("description", "")).replace("\n", " ").strip()
        summary = re.sub(r"\s+", " ", summary)
        votes = item.get("bucket_votes", {})
        lines.append(
            f"[{idx}] [{item.get('source', '')}] {item.get('title', '')}\n"
            f"mentions={item.get('appearances', 0)} | sources={item.get('source_count', 0)} | "
            f"korea_votes={votes.get('korea', 0)} | world_votes={votes.get('world', 0)}\n"
            f"summary={summary[:220]}"
        )

    user_prompt = "Weekly issue bucket classification:\n\n" + "\n\n".join(lines)
    try:
        response = provider.complete(_WEEKLY_BUCKET_PROMPT, user_prompt)
        payload = json.loads(_sanitize_selector_json(response))
        bucket_map = {
            int(item["id"]): item["bucket"]
            for item in payload
            if isinstance(item, dict)
            and str(item.get("bucket", "")).lower() in {"world", "korea"}
            and isinstance(item.get("id"), int)
        }
        if bucket_map:
            return [
                {
                    **item,
                    "bucket": bucket_map.get(idx, _heuristic_issue_bucket(item)),
                }
                for idx, item in enumerate(candidates)
            ]
    except Exception:
        logger.exception("Weekly issue bucket classification failed — heuristic fallback")

    return [
        {**item, "bucket": _heuristic_issue_bucket(item)}
        for item in candidates
    ]


def _candidate_sort_tuple(item: dict[str, Any]) -> tuple[int, int, int, str]:
    return (
        int(item.get("relevance_score", 0) or 0),
        int(item.get("score", 0) or 0),
        int(item.get("appearances", 0) or 0),
        int(item.get("source_count", 0) or 0),
        item.get("latest_date", ""),
    )


def _pick_representative_article(
    candidate: dict[str, Any],
    bucket: str,
) -> dict[str, Any] | None:
    members = [dict(item) for item in candidate.get("cluster_articles", [])] or [dict(candidate)]

    if bucket == "world":
        preferred_pool = [item for item in members if not item.get("is_korea_source")]
        if not preferred_pool:
            return None
    else:
        preferred_pool = [item for item in members if item.get("is_korea_source")] or members

    preferred_pool.sort(
        key=lambda item: (
            1 if (item.get("summary") or item.get("description")) else 0,
            item.get("published_date", ""),
            len(item.get("title", "")),
        ),
        reverse=True,
    )
    representative = preferred_pool[0]
    merged = {**candidate, **representative, "bucket": bucket}
    merged["cluster_articles"] = members
    if bucket == "korea":
        merged["relevance_score"] = _korea_relevance_score(merged)
    else:
        merged["relevance_score"] = 0
    return merged


def _prepare_bucket_candidates(
    candidates: list[dict[str, Any]],
    bucket: str,
    minimum_items: int | None = None,
) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    for candidate in candidates:
        representative = _pick_representative_article(candidate, bucket)
        if representative is None:
            continue
        prepared.append(representative)
    prepared.sort(key=_candidate_sort_tuple, reverse=True)
    if bucket == "korea":
        strong = [
            item
            for item in prepared
            if int(item.get("relevance_score", 0) or 0) > 0 and _is_viable_korea_candidate(item)
        ]
        if strong and (minimum_items is None or len(strong) >= minimum_items):
            return strong
        if strong:
            seen_urls = {item.get("url", "") for item in strong if item.get("url", "")}
            relaxed = [
                item for item in prepared
                if _is_relaxed_korea_candidate(item)
                and ((item.get("url", "") not in seen_urls) or not item.get("url", ""))
            ]
            supplemented = list(strong)
            for item in relaxed:
                if item in supplemented:
                    continue
                supplemented.append(item)
                if minimum_items is not None and len(supplemented) >= minimum_items:
                    break
            if supplemented:
                return supplemented
    return prepared


def _enforce_source_diversity(
    selected: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    top_n: int,
    max_per_source: int | None = None,
) -> list[dict[str, Any]]:
    pool: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for item in selected + candidates:
        dedup_key = item.get("url", "") or f"{item.get('source', '')}|{item.get('title', '')}"
        if dedup_key in seen_urls:
            continue
        seen_urls.add(dedup_key)
        pool.append(item)

    by_source: dict[str, list[dict[str, Any]]] = {}
    source_order: list[str] = []
    for item in pool:
        source = item.get("source", "") or "(unknown)"
        if source not in by_source:
            by_source[source] = []
            source_order.append(source)
        by_source[source].append(item)

    diversified: list[dict[str, Any]] = []
    source_counts: Counter[str] = Counter()
    round_index = 0
    while len(diversified) < top_n:
        added_this_round = False
        for source in source_order:
            items = by_source.get(source, [])
            if round_index >= len(items):
                continue
            if max_per_source is not None and source_counts[source] >= max_per_source:
                continue
            diversified.append(items[round_index])
            source_counts[source] += 1
            added_this_round = True
            if len(diversified) >= top_n:
                return diversified[:top_n]
        if not added_this_round:
            break
        round_index += 1

    return diversified[:top_n]


def _select_weekly_clusters(
    provider: Any,
    candidates: list[dict[str, Any]],
    top_n: int,
    category: str,
) -> list[dict[str, Any]]:
    if len(candidates) <= top_n:
        return candidates

    shortlist_size = max(top_n * 3, top_n + 2)
    shortlist = candidates[:shortlist_size]
    lines = []
    for idx, item in enumerate(shortlist):
        summary = (item.get("summary") or item.get("description", "")).replace("\n", " ").strip()
        summary = re.sub(r"\s+", " ", summary)
        lines.append(
            f"[{idx}] [{item.get('source', '')}] {item.get('title', '')}\n"
            f"mentions={item.get('appearances', 0)} | sources={item.get('source_count', 0)} | "
            f"active_days={item.get('active_days', 0)} | latest={item.get('latest_date', '')}\n"
            f"summary={summary[:220]}"
        )

    system_prompt = (
        _WEEKLY_SELECTOR_PROMPT_KOREA
        if category == "korea"
        else _WEEKLY_SELECTOR_PROMPT_WORLD
    ).format(top_n=top_n)
    user_prompt = "Weekly issue shortlist:\n\n" + "\n\n".join(lines) + "\n\nReturn JSON array of indices only."

    try:
        response = provider.complete(system_prompt, user_prompt)
        indices = json.loads(_sanitize_selector_json(response))
        selected: list[dict[str, Any]] = []
        seen: set[int] = set()
        for idx in indices:
            if not isinstance(idx, int) or idx in seen:
                continue
            if 0 <= idx < len(shortlist):
                seen.add(idx)
                selected.append(shortlist[idx])
        if selected:
            return selected[:top_n]
    except Exception:
        logger.exception("Weekly %s cluster selection failed — heuristic order fallback", category)

    return shortlist[:top_n]


def build_weekly_news_digest(
    config: dict,
    start_date: str,
    end_date: str,
    provider: Any | None = None,
    top_n: int = 5,
) -> dict[str, Any]:
    """Build a weekly issue digest from a fresh 7-day news pool."""
    end_day = date.fromisoformat(end_date)
    week_start_day = date.fromisoformat(start_date)
    news_window_start = min(week_start_day, end_day - timedelta(days=6)).isoformat()
    articles = _collect_recent_articles(config, news_window_start, end_date)
    normalized = [_normalize_article(article, config) for article in articles if article.title]

    all_candidates = _cluster_bucket_articles(normalized, end_day=end_day)
    classified_candidates = _classify_weekly_candidates(provider, all_candidates)
    world_candidates = _prepare_bucket_candidates(
        [item for item in classified_candidates if item.get("bucket") == "world"],
        bucket="world",
    )
    korea_candidates = _prepare_bucket_candidates(
        [item for item in classified_candidates if item.get("bucket") == "korea"],
        bucket="korea",
        minimum_items=max(4, min(top_n, 5)),
    )

    logger.info(
        "Weekly candidate pools — world: %d | korea: %d (%s)",
        len(world_candidates),
        len(korea_candidates),
        ", ".join(dict.fromkeys(item.get("source", "") for item in korea_candidates if item.get("source", ""))) or "(none)",
    )

    selected_world = world_candidates[:top_n]
    selected_korea = korea_candidates[:top_n]

    if provider:
        selected_world = _select_weekly_clusters(provider, world_candidates, top_n=top_n, category="world")
        selected_korea = _select_weekly_clusters(provider, korea_candidates, top_n=top_n, category="korea")

    selected_world = _enforce_source_diversity(selected_world, world_candidates, top_n=top_n)
    selected_korea = _enforce_source_diversity(
        selected_korea,
        korea_candidates,
        top_n=top_n,
        max_per_source=1,
    )

    logger.info(
        "Weekly selected sources — world: %s | korea: %s",
        ", ".join(item.get("source", "") for item in selected_world) or "(none)",
        ", ".join(item.get("source", "") for item in selected_korea) or "(none)",
    )

    display_items = selected_world + selected_korea
    fill_missing_descriptions(display_items[: max(4, top_n * 2)])
    for item in display_items:
        if item.get("description") and not item.get("summary"):
            item["summary"] = item["description"]

    if provider:
        world_ko = translate_news(provider, selected_world, target_lang="ko", strict=True)
        korea_en = translate_news(provider, selected_korea, target_lang="en", strict=True)
    else:
        world_ko = list(selected_world)
        korea_en = list(selected_korea)

    display_world_ko = [_decorate_display(item) for item in world_ko]
    display_world_en = [_decorate_display(item) for item in selected_world]
    display_korea_ko = [_decorate_display(item) for item in selected_korea]
    display_korea_en = [_decorate_display(item) for item in korea_en]

    return {
        "world_raw": selected_world,
        "korea_raw": selected_korea,
        "world_ko": display_world_ko,
        "world_en": display_world_en,
        "korea_ko": display_korea_ko,
        "korea_en": display_korea_en,
        "unique_story_count": len(world_candidates) + len(korea_candidates),
        "news_pool_count": len(normalized),
        "news_source_count": len({item.get("source", "") for item in normalized if item.get("source", "")}),
    }
