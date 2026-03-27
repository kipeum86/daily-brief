from __future__ import annotations

from pipeline.news.selector import select_and_classify_news


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
