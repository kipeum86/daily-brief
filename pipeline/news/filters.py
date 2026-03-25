"""Keyword filtering for news articles."""

import logging
import re

from pipeline.models import Article

logger = logging.getLogger(__name__)


def keyword_filter(
    articles: list[Article],
    keywords_config: dict,
) -> list[Article]:
    """Filter articles by include/exclude keyword regex matching."""
    include = keywords_config.get("include", [])
    exclude = keywords_config.get("exclude", [])

    if not include:
        result = list(articles)
    else:
        pattern = re.compile("|".join(re.escape(k) for k in include), re.IGNORECASE)
        result = [
            a for a in articles
            if pattern.search(f"{a.title} {a.description}")
        ]

    if exclude:
        ex_pattern = re.compile("|".join(re.escape(k) for k in exclude), re.IGNORECASE)
        result = [
            a for a in result
            if not ex_pattern.search(f"{a.title} {a.description}")
        ]

    logger.info("Keyword filter: %d -> %d articles", len(articles), len(result))
    return result
