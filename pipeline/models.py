"""Data models for the daily-brief pipeline."""

from dataclasses import dataclass, field


@dataclass
class Article:
    """A news article collected from RSS."""
    title: str
    url: str
    source: str
    description: str = ""
    published_date: str = ""
    body: str = ""


@dataclass
class AIResult:
    """LLM analysis result for an article."""
    summary: list[str] = field(default_factory=list)
    category: str = "ETC"
    event: dict = field(default_factory=dict)
    event_key: str = ""
    is_primary: bool = True
    duplicate_of: str = ""
    confidence: str = "high"


@dataclass
class ProcessedArticle:
    """Article with AI analysis attached."""
    article: Article
    ai_result: AIResult


@dataclass
class DedupSnapshot:
    """Deduplication state loaded from history."""
    urls: set[str] = field(default_factory=set)
    canonical_urls: set[str] = field(default_factory=set)
    topic_token_sets: list[set[str]] = field(default_factory=list)
    source_topic_token_sets: dict[str, list[set[str]]] = field(default_factory=dict)
    event_keys: set[str] = field(default_factory=set)


@dataclass
class MarketData:
    """A single market data point (index, FX pair, commodity, etc.)."""
    ticker: str
    name: str
    price: float = 0.0
    change_pct: float = 0.0
    volume: float = 0.0


@dataclass
class BriefingResult:
    """Final assembled briefing output."""
    date: str
    markets: list[MarketData] = field(default_factory=list)
    news: list[ProcessedArticle] = field(default_factory=list)
    insight_text: str = ""
    brief_type: str = "daily"  # daily, weekly, monthly
