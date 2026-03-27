"""Weekly news collection and issue clustering helpers."""

from __future__ import annotations

import copy
import logging
import re
from collections import Counter
from datetime import date, datetime, timedelta
from typing import Any

from pipeline.models import Article
from pipeline.news.collector import collect_articles, fill_missing_descriptions
from pipeline.news.dedup import canonicalize_url, containment_similarity, extract_topic_tokens
from pipeline.news.filters import keyword_filter
from pipeline.news.naver import collect_naver_news

logger = logging.getLogger("daily-brief.news.weekly")

_GOOGLE_NEWS_WINDOW_RE = re.compile(r"when:(\d+)d")
_CLUSTER_MIN_OVERLAP = 2
_CLUSTER_SIMILARITY = 0.4


def _build_korea_source_names(config: dict) -> set[str]:
    korea_source_names: set[str] = set()
    for korea_key in ("korea", "korea_major"):
        korea_cfg = config.get("news", {}).get(korea_key, [])
        if isinstance(korea_cfg, list):
            for src in korea_cfg:
                if isinstance(src, dict):
                    korea_source_names.add(src.get("name", ""))
        elif isinstance(korea_cfg, dict):
            korea_source_names.add("네이버뉴스")
    return korea_source_names


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
    for article in deduped:
        published_day = _parse_date(article.published_date)
        if published_day and not (start_day <= published_day <= end_day):
            continue
        windowed.append(article)

    logger.info(
        "Weekly news pool prepared: %d collected -> %d filtered -> %d exact-deduped -> %d in window",
        len(articles),
        len(filtered),
        len(deduped),
        len(windowed),
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
            })
            continue

        best_cluster["articles"].append(article)
        best_cluster["sources"].add(article.get("source", ""))
        best_cluster["dates"].add(article.get("published_date", ""))
        best_cluster["token_counts"].update(tokens)
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
        "topic_tokens": extract_topic_tokens(f"{article.title} {summary}"),
    }


def _decorate_display(article: dict[str, Any]) -> dict[str, Any]:
    entry = dict(article)
    entry["summary"] = entry.get("summary") or entry.get("description", "")
    entry["source_count"] = int(entry.get("source_count", 0) or 0)
    entry["appearances"] = int(entry.get("appearances", 0) or 0)
    entry["active_days"] = int(entry.get("active_days", 0) or 0)
    entry.pop("topic_tokens", None)
    return entry


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

    world_candidates = _cluster_bucket_articles(
        [item for item in normalized if item.get("bucket") == "world"],
        end_day=end_day,
    )
    korea_candidates = _cluster_bucket_articles(
        [item for item in normalized if item.get("bucket") == "korea"],
        end_day=end_day,
    )

    if provider:
        logger.info("Weekly issue ranking uses heuristic cluster scores; LLM remains reserved for recap writing")

    selected_world = world_candidates[:top_n]
    selected_korea = korea_candidates[:top_n]

    display_items = selected_world + selected_korea
    fill_missing_descriptions(display_items[: max(4, top_n * 2)])
    for item in display_items:
        if item.get("description") and not item.get("summary"):
            item["summary"] = item["description"]

    display_world = [_decorate_display(item) for item in selected_world]
    display_korea = [_decorate_display(item) for item in selected_korea]

    return {
        "world_raw": selected_world,
        "korea_raw": selected_korea,
        "world_ko": display_world,
        "world_en": display_world,
        "korea_ko": display_korea,
        "korea_en": display_korea,
        "unique_story_count": len(world_candidates) + len(korea_candidates),
        "news_pool_count": len(normalized),
        "news_source_count": len({item.get("source", "") for item in normalized if item.get("source", "")}),
    }
