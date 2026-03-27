"""RSS feed collection and article body extraction."""

import logging
import re
from datetime import datetime, timedelta, timezone

import feedparser
import requests
from bs4 import BeautifulSoup

from pipeline.models import Article

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (daily-brief bot; +https://github.com/kipeum86/daily-brief)"
}


def collect_articles(config: dict) -> tuple[list[Article], list[str]]:
    """Collect articles from RSS sources across all news categories.

    Reads config["news"] which has category keys (world, korea, finance)
    each containing a list of {name, url} dicts.

    Returns (articles, failed_sources).
    """
    news_config = config.get("news", {})
    days_back = news_config.get("days_back", 2)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)

    articles: list[Article] = []
    failed: list[str] = []
    source_count = 0

    # Iterate over news categories (world, korea, finance, etc.)
    for category, sources in news_config.items():
        if not isinstance(sources, list):
            continue  # skip scalar config keys like top_n, days_back
        source_count += len(sources)

        for src in sources:
            url = src if isinstance(src, str) else src.get("url", "")
            name = src.get("name", url) if isinstance(src, dict) else url
            try:
                feed = feedparser.parse(url)
                max_per_source = news_config.get("max_per_source", 15)
                count = 0
                for entry in feed.entries:
                    if count >= max_per_source:
                        break
                    pub_date = _parse_date(entry)
                    if pub_date and pub_date < cutoff:
                        continue
                    articles.append(Article(
                        title=entry.get("title", "").strip(),
                        url=entry.get("link", "").strip(),
                        source=name,
                        description=_clean_html(entry.get("summary", "")),
                        published_date=pub_date.strftime("%Y-%m-%d") if pub_date else "",
                    ))
                    count += 1
            except Exception as e:
                logger.warning("Feed failed: %s — %s", name, e)
                failed.append(name)

    logger.info("Collected %d articles from %d sources (%d failed)",
                len(articles), source_count, len(failed))
    return articles, failed


def extract_body(url: str, min_content_length: int = 200) -> str:
    """Extract article body text via HTTP + BeautifulSoup."""
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        text = re.sub(r"\n{3,}", "\n\n", text)
        if len(text) < min_content_length:
            return ""
        return text
    except Exception as e:
        logger.debug("Body extraction failed for %s: %s", url, e)
        return ""


def _build_excerpt(text: str, max_chars: int = 240) -> str:
    """Create a concise one- or two-sentence excerpt from article body text."""
    clean = re.sub(r"\s+", " ", text or "").strip()
    if not clean:
        return ""
    if len(clean) <= max_chars:
        return clean

    sentences = re.split(r"(?<=[.!?。！？])\s+", clean)
    excerpt = ""
    for sentence in sentences:
        candidate = sentence if not excerpt else f"{excerpt} {sentence}"
        if len(candidate) > max_chars:
            break
        excerpt = candidate

    if excerpt and len(excerpt) >= max_chars // 2:
        return excerpt
    return clean[: max_chars - 1].rstrip() + "…"


def fill_missing_descriptions(articles: list, max_chars: int = 240) -> int:
    """Populate missing article descriptions from extracted article bodies.

    Returns the number of articles filled.
    """
    filled = 0
    for article in articles:
        if isinstance(article, dict):
            description = article.get("description", "") or article.get("summary", "")
            body = article.get("body", "")
            url = article.get("url", "")
            title = article.get("title", "")
        else:
            description = getattr(article, "description", "")
            body = getattr(article, "body", "")
            url = getattr(article, "url", "")
            title = getattr(article, "title", "")

        if description:
            continue

        if not body and url:
            body = extract_body(url)

        excerpt = _build_excerpt(body, max_chars=max_chars) if body else ""
        fallback_text = excerpt or title
        if not fallback_text:
            continue

        if isinstance(article, dict):
            if body and not article.get("body"):
                article["body"] = body
            article["description"] = fallback_text
        else:
            if body and not getattr(article, "body", ""):
                article.body = body
            article.description = fallback_text
        filled += 1

    if filled:
        logger.info("Filled missing descriptions for %d article(s)", filled)
    return filled


def _parse_date(entry) -> datetime | None:
    """Parse feedparser date to timezone-aware datetime."""
    from time import mktime
    for attr in ("published_parsed", "updated_parsed"):
        parsed = getattr(entry, attr, None)
        if parsed:
            try:
                return datetime.fromtimestamp(mktime(parsed), tz=timezone.utc)
            except (TypeError, ValueError, OverflowError):
                continue
    return None


def _clean_html(text: str) -> str:
    """Strip HTML tags from text."""
    if not text:
        return ""
    return BeautifulSoup(text, "html.parser").get_text(strip=True)
