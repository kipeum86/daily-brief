"""3-stage deduplication: URL -> topic tokens -> EventKey."""

import hashlib
import logging
import re
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pipeline.models import Article, DedupSnapshot, ProcessedArticle

logger = logging.getLogger(__name__)

_TRACKING_PREFIXES = ("utm_", "fbclid", "gclid", "mc_", "mkt_tok", "ref", "source")

_STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will", "would",
    "could", "should", "may", "might", "can", "shall", "must", "need",
    "it", "its", "this", "that", "these", "those", "he", "she", "they",
    "we", "you", "his", "her", "their", "our", "my", "your",
    "not", "no", "nor", "so", "if", "then", "than", "too", "very",
    "just", "about", "also", "more", "some", "any", "all", "each",
    "how", "what", "when", "where", "which", "who", "why",
    "new", "says", "said", "one", "two", "get", "got", "into",
    "up", "out", "over", "after", "before", "between",
    "\uc740", "\ub294", "\uc774", "\uac00", "\uc744", "\ub97c", "\uc758", "\uc5d0", "\uc5d0\uc11c", "\ub85c", "\uc73c\ub85c",
    "\uc640", "\uacfc", "\ub3c4",
})

_TOKEN_RE = re.compile(r"[a-z0-9\uac00-\ud7af]{2,}", re.IGNORECASE)


def canonicalize_url(url: str) -> str:
    """Normalize URL for dedup: strip tracking params, www, trailing slash."""
    parts = urlsplit(url.strip())
    host = parts.hostname or ""
    if host.startswith("www."):
        host = host[4:]
    path = re.sub(r"/+", "/", parts.path).rstrip("/") or "/"
    params = [
        (k, v) for k, v in parse_qsl(parts.query)
        if not any(k.lower().startswith(p) for p in _TRACKING_PREFIXES)
    ]
    params.sort()
    query = urlencode(params)
    return urlunsplit(("https", host, path, query, ""))


def extract_topic_tokens(text: str) -> set[str]:
    """Extract normalized topic tokens, filtering stopwords."""
    tokens = set(_TOKEN_RE.findall(text.lower()))
    return tokens - _STOPWORDS


def containment_similarity(a: set[str], b: set[str]) -> float:
    """Containment similarity: overlap / min(len(a), len(b))."""
    if not a or not b:
        return 0.0
    overlap = len(a & b)
    return overlap / min(len(a), len(b))


def build_event_key(
    event: dict,
    time_bucket: str = "month",
    hash_len: int = 16,
) -> str:
    """Build EventKey hash from event metadata."""
    jurisdiction = _normalize_text(event.get("jurisdiction", ""))
    event_type = _normalize_text(event.get("event_type", ""))
    actors = sorted(_normalize_text(a) for a in event.get("actors", []))
    obj = _normalize_text(event.get("object", ""))
    action = _normalize_text(event.get("action", ""))
    time_hint = event.get("time_hint", "")

    bucket = _time_to_bucket(time_hint, time_bucket)

    raw = "|".join([jurisdiction, event_type, ",".join(actors), obj, action, bucket])
    return hashlib.sha256(raw.encode()).hexdigest()[:hash_len]


def deduplicate_articles(
    articles: list[Article],
    snapshot: DedupSnapshot,
    dedup_config: dict,
) -> list[Article]:
    """Pre-LLM dedup: URL canonicalization + topic token similarity."""
    source_thresh = dedup_config.get("source_similarity_threshold", 0.75)
    cross_thresh = dedup_config.get("cross_similarity_threshold", 0.60)
    min_overlap = dedup_config.get("min_overlap_tokens", 3)

    seen_urls: set[str] = set(snapshot.canonical_urls)
    seen_tokens: list[set[str]] = list(snapshot.topic_token_sets)
    source_tokens: dict[str, list[set[str]]] = dict(snapshot.source_topic_token_sets)
    result: list[Article] = []

    for article in articles:
        canon = canonicalize_url(article.url)

        if canon in seen_urls:
            logger.debug("URL dedup: %s", article.title[:60])
            continue

        tokens = extract_topic_tokens(f"{article.title} {article.description}")
        if not tokens:
            result.append(article)
            seen_urls.add(canon)
            continue

        src_tokens = source_tokens.get(article.source, [])
        if _is_similar(tokens, src_tokens, source_thresh, min_overlap):
            logger.debug("Source-topic dedup: %s", article.title[:60])
            continue

        if _is_similar(tokens, seen_tokens, cross_thresh, min_overlap):
            logger.debug("Cross-topic dedup: %s", article.title[:60])
            continue

        seen_urls.add(canon)
        seen_tokens.append(tokens)
        source_tokens.setdefault(article.source, []).append(tokens)
        result.append(article)

    logger.info("Dedup: %d -> %d articles", len(articles), len(result))
    return result


def deduplicate_by_event_key(
    processed: list[ProcessedArticle],
) -> list[ProcessedArticle]:
    """Post-LLM dedup: unify same-event articles via EventKey."""
    seen_keys: dict[str, int] = {}
    for i, pa in enumerate(processed):
        key = pa.ai_result.event_key
        if not key:
            continue
        if key in seen_keys:
            pa.ai_result.is_primary = False
            pa.ai_result.duplicate_of = key
        else:
            seen_keys[key] = i
            pa.ai_result.is_primary = True
    return processed


def load_trend_snapshot(trends_dir: str, days: int = 30) -> DedupSnapshot:
    """Load dedup snapshot from local trends/*.txt files."""
    import os
    from datetime import timedelta, timezone

    snapshot = DedupSnapshot()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    if not os.path.isdir(trends_dir):
        return snapshot

    for fname in sorted(os.listdir(trends_dir)):
        if not fname.startswith("trend_") or not fname.endswith(".txt"):
            continue
        date_str = fname.replace("trend_", "").replace(".txt", "")
        try:
            file_date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if file_date < cutoff:
            continue

        filepath = os.path.join(trends_dir, fname)
        try:
            with open(filepath, encoding="utf-8") as f:
                content = f.read()
            for block in content.split("---"):
                block = block.strip()
                if not block:
                    continue
                for line in block.split("\n"):
                    if line.startswith("URL: "):
                        url = line[5:].strip()
                        snapshot.urls.add(url)
                        snapshot.canonical_urls.add(canonicalize_url(url))
                    elif line.startswith("EventKey: "):
                        snapshot.event_keys.add(line[10:].strip())
                first_line = block.split("\n")[0]
                if first_line.startswith("["):
                    title_part = first_line.split("]", 1)[-1].strip()
                    tokens = extract_topic_tokens(title_part)
                    if tokens:
                        snapshot.topic_token_sets.append(tokens)
        except Exception as e:
            logger.debug("Failed to parse trend file %s: %s", fname, e)

    return snapshot


def save_trend_file(
    trends_dir: str,
    processed: list[ProcessedArticle],
    run_date: str,
) -> str:
    """Save trend archive file. Returns filepath."""
    import os
    os.makedirs(trends_dir, exist_ok=True)
    filepath = os.path.join(trends_dir, f"trend_{run_date}.txt")
    lines = []
    for pa in processed:
        if not pa.ai_result.is_primary:
            continue
        lines.append(f"[{pa.ai_result.category}] {pa.article.title}")
        lines.append(f"URL: {pa.article.url}")
        summary_str = " | ".join(pa.ai_result.summary)
        lines.append(f"Summary: {summary_str}")
        if pa.ai_result.event_key:
            lines.append(f"EventKey: {pa.ai_result.event_key}")
        lines.append("---")
    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return filepath


def _normalize_text(text: str) -> str:
    """Lowercase, remove punctuation, normalize whitespace."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s]", "", text)
    return " ".join(text.split())


def _time_to_bucket(time_hint: str, bucket_type: str) -> str:
    """Convert date string to time bucket."""
    if not time_hint:
        return ""
    try:
        dt = datetime.strptime(time_hint[:10], "%Y-%m-%d")
        if bucket_type == "week":
            return f"{dt.year}-W{dt.isocalendar()[1]:02d}"
        return f"{dt.year}-{dt.month:02d}"
    except (ValueError, IndexError):
        return ""


def _is_similar(
    tokens: set[str],
    existing: list[set[str]],
    threshold: float,
    min_overlap: int,
) -> bool:
    """Check if tokens are similar to any existing token set."""
    for existing_tokens in existing:
        overlap = len(tokens & existing_tokens)
        if overlap >= min_overlap and containment_similarity(tokens, existing_tokens) >= threshold:
            return True
    return False
