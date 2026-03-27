from __future__ import annotations

import json
from pathlib import Path

from pipeline.news.quality_gates import (
    check_article_count,
    check_category_balance,
    check_cross_section_dedup,
    check_korea_purity,
    check_source_diversity,
    run_quality_gates,
)


def test_check_article_count_exact_5(sample_articles):
    violations = []
    world = sample_articles[:5]
    result = check_article_count(world, 5, sample_articles[:8], violations, section="world")
    assert len(result) == 5
    assert violations == []


def test_check_article_count_pad_from_candidates(sample_articles):
    violations = []
    result = check_article_count(sample_articles[:3], 5, sample_articles[:8], violations, section="world")
    assert len(result) == 5
    assert violations


def test_check_article_count_trim_excess(sample_articles):
    violations = []
    result = check_article_count(sample_articles[:7], 5, sample_articles[:8], violations, section="world")
    assert len(result) == 5
    assert violations[0]["check"] == "article_count"


def test_check_source_diversity_world_max_2(sample_articles):
    violations = []
    articles = [sample_articles[0], sample_articles[13], sample_articles[1], sample_articles[2], sample_articles[3]]
    result = check_source_diversity(articles, 2, sample_articles[:6], violations, section="world")
    sources = [article["source"] for article in result]
    assert max(sources.count(source) for source in sources) <= 2


def test_check_source_diversity_korea_max_1(sample_articles):
    violations = []
    articles = [sample_articles[5], sample_articles[10], sample_articles[6], sample_articles[7], sample_articles[8]]
    candidates = [sample_articles[5], sample_articles[6], sample_articles[7], sample_articles[8], sample_articles[9], sample_articles[12]]
    result = check_source_diversity(articles, 1, candidates, violations, section="korea")
    sources = [article["source"] for article in result]
    assert max(sources.count(source) for source in sources) == 1


def test_check_source_diversity_swap_with_candidate(sample_articles):
    violations = []
    articles = [sample_articles[5], sample_articles[10], sample_articles[6], sample_articles[7], sample_articles[8]]
    candidates = [sample_articles[5], sample_articles[6], sample_articles[7], sample_articles[8], sample_articles[9], sample_articles[12]]
    result = check_source_diversity(articles, 1, candidates, violations, section="korea")
    assert any(article["source"] == "뉴스1" for article in result)
    assert violations


def test_check_korea_purity_all_domestic_pass(sample_articles):
    violations = []
    korea = [sample_articles[5], sample_articles[6], sample_articles[7], sample_articles[8], sample_articles[9]]
    result = check_korea_purity(korea, korea, violations)
    assert result == korea
    assert violations == []


def test_check_korea_purity_international_keyword_flagged(sample_articles):
    violations = []
    korea = [sample_articles[10], sample_articles[6], sample_articles[7], sample_articles[8], sample_articles[9]]
    candidates = [sample_articles[5], sample_articles[6], sample_articles[7], sample_articles[8], sample_articles[9], sample_articles[12]]
    result = check_korea_purity(korea, candidates, violations)
    assert all("이란" not in article["title"] for article in result)
    assert violations


def test_check_korea_purity_edge_case_bilateral(sample_articles):
    violations = []
    korea = [sample_articles[11], sample_articles[6], sample_articles[7], sample_articles[8], sample_articles[9]]
    result = check_korea_purity(korea, korea, violations)
    assert any("FTA" in article["title"] for article in result)


def test_check_category_balance_3_categories_pass(sample_articles):
    violations = []
    articles = [sample_articles[5], sample_articles[6], sample_articles[7], sample_articles[12], sample_articles[8]]
    result = check_category_balance(articles, 3, sample_articles[5:13], violations, section="korea")
    assert len({article["category"] for article in result}) >= 3
    assert violations == []


def test_check_category_balance_all_same_force_swap(sample_articles):
    violations = []
    articles = [sample_articles[5], sample_articles[8], sample_articles[5] | {"url": "https://example.com/alt1"}, sample_articles[8] | {"url": "https://example.com/alt2"}, sample_articles[5] | {"url": "https://example.com/alt3"}]
    candidates = [sample_articles[5], sample_articles[6], sample_articles[7], sample_articles[8], sample_articles[9], sample_articles[12]]
    result = check_category_balance(articles, 3, candidates, violations, section="korea")
    assert len({article["category"] for article in result}) >= 3
    assert violations


def test_check_cross_section_dedup_no_overlap(sample_articles):
    violations = []
    world, korea = check_cross_section_dedup(sample_articles[:5], sample_articles[5:10], violations)
    assert len(world) == 5
    assert len(korea) == 5
    assert violations == []


def test_check_cross_section_dedup_overlap_removed(sample_articles):
    violations = []
    overlapping = sample_articles[0] | {"bucket": "korea", "source": "연합뉴스", "url": "https://example.com/overlap"}
    world, korea = check_cross_section_dedup([sample_articles[0]], [overlapping], violations)
    assert len(world) + len(korea) == 1
    assert violations


def test_run_quality_gates_full_pipeline(sample_articles, sample_config):
    world_candidates = sample_articles[:5] + [sample_articles[13]]
    korea_candidates = [sample_articles[10], sample_articles[5], sample_articles[6], sample_articles[7], sample_articles[8], sample_articles[9], sample_articles[12]]
    world, korea = run_quality_gates(world_candidates, korea_candidates, sample_config)
    assert len(world) == 5
    assert len(korea) == 5
    assert max([world.count(article) for article in world], default=1) >= 1
    assert len({article["source"] for article in korea}) >= 4


def test_quality_log_written_on_violation(sample_articles, sample_config):
    world_candidates = sample_articles[:5]
    korea_candidates = [sample_articles[10], sample_articles[5], sample_articles[6], sample_articles[7], sample_articles[8], sample_articles[9]]
    run_quality_gates(world_candidates, korea_candidates, sample_config)
    log_path = Path(sample_config["output"]["dir"]) / "data" / "quality-log.json"
    assert log_path.exists()
    payload = json.loads(log_path.read_text(encoding="utf-8"))
    assert payload
