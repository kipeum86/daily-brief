from __future__ import annotations

from pipeline.news.selector import select_and_classify_news


class JsonProvider:
    def __init__(self, payload):
        self.payload = payload
        self.user_prompt = ""

    def complete_json(self, system: str, user: str, max_retries: int = 3):
        self.user_prompt = user
        return self.payload


def test_select_and_classify_returns_both_buckets(sample_articles, mock_provider, sample_config):
    response = """
    [
      {"index": 0, "bucket": "world", "category": "economy", "rank": 1},
      {"index": 1, "bucket": "world", "category": "economy", "rank": 2},
      {"index": 5, "bucket": "korea", "category": "economy", "rank": 1},
      {"index": 6, "bucket": "korea", "category": "corporate", "rank": 2}
    ]
    """
    provider = mock_provider(response)
    result = select_and_classify_news(provider, sample_articles, top_n=2, config=sample_config)
    assert "world" in result and "korea" in result
    assert result["world"]
    assert result["korea"]


def test_select_and_classify_accepts_structured_json_object(sample_articles, sample_config):
    provider = JsonProvider({
        "world": [
            {"index": 0, "bucket": "world", "category": "economy", "rank": 1},
            {"index": 2, "bucket": "world", "category": "security", "rank": 2},
        ],
        "korea": [
            {"index": 5, "bucket": "korea", "category": "economy", "rank": 1},
            {"index": 6, "bucket": "korea", "category": "corporate", "rank": 2},
        ],
        "warnings": [],
    })

    result = select_and_classify_news(provider, sample_articles, top_n=2, config=sample_config)

    assert result["world"][0]["title"] == sample_articles[0]["title"]
    assert result["korea"][0]["title"] == sample_articles[5]["title"]


def test_select_and_classify_limits_selector_prompt_size(sample_articles, sample_config):
    articles = [
        article | {
            "url": f"{article['url']}-{index}",
            "description": article["description"] + (" extra context" * 80),
        }
        for index, article in enumerate(sample_articles * 8)
    ]
    provider = JsonProvider({
        "world": [{"index": 0, "bucket": "world", "category": "economy", "rank": 1}],
        "korea": [{"index": 5, "bucket": "korea", "category": "economy", "rank": 1}],
        "warnings": [],
    })
    config = {
        **sample_config,
        "llm": {"max_input_chars": 1800},
        "news": {"top_n": 2, "selector_candidates_per_bucket": 20, "selector_max_articles": 40},
    }

    select_and_classify_news(provider, articles, top_n=2, config=config)

    assert len(provider.user_prompt) <= 1800
    assert provider.user_prompt.count("\n\n[") < len(articles)


def test_topic_classification_korean_outlet_iran_goes_world(sample_articles, mock_provider, sample_config):
    response = """
    [
      {"index": 10, "bucket": "world", "category": "security", "rank": 1}
    ]
    """
    provider = mock_provider(response)
    result = select_and_classify_news(provider, sample_articles, top_n=1, config=sample_config)
    assert result["world"][0]["bucket"] == "world"
    assert "이란" in result["world"][0]["title"]


def test_topic_classification_domestic_stays_korea(sample_articles, mock_provider, sample_config):
    response = """
    [
      {"index": 6, "bucket": "korea", "category": "corporate", "rank": 1}
    ]
    """
    provider = mock_provider(response)
    result = select_and_classify_news(provider, sample_articles, top_n=1, config=sample_config)
    assert result["korea"][0]["bucket"] == "korea"
    assert "반도체" in result["korea"][0]["title"]


def test_source_diversity_enforced_in_results(sample_articles, mock_provider, sample_config):
    response = """
    [
      {"index": 5, "bucket": "korea", "category": "economy", "rank": 1},
      {"index": 10, "bucket": "korea", "category": "security", "rank": 2},
      {"index": 6, "bucket": "korea", "category": "corporate", "rank": 3},
      {"index": 7, "bucket": "korea", "category": "politics", "rank": 4},
      {"index": 8, "bucket": "korea", "category": "economy", "rank": 5}
    ]
    """
    provider = mock_provider(response)
    result = select_and_classify_news(provider, sample_articles, top_n=2, config=sample_config)
    sources = [article["source"] for article in result["korea"]]
    assert max(sources.count(source) for source in sources) <= 1


def test_fallback_on_invalid_json_response(sample_articles, mock_provider, sample_config):
    provider = mock_provider("not valid json")
    result = select_and_classify_news(provider, sample_articles, top_n=3, config=sample_config)
    assert result["world"]
    assert result["korea"]
