"""Tests for content completeness checks."""
from __future__ import annotations

from pipeline.verify.checks.content import check_content_completeness


def _article(title, bucket, source="test"):
    return {"title": title, "bucket": bucket, "source": source, "url": f"http://{source}/{title[:10]}",
            "summary": "요약", "description": "요약"}


def _articles(n_world=5, n_korea=5):
    arts = []
    for i in range(n_world):
        arts.append(_article(f"World news {i}", "world", f"source{i}"))
    for i in range(n_korea):
        arts.append(_article(f"한국 국내 뉴스 {i}", "korea", f"kr_source{i}"))
    return arts


def test_all_good():
    arts = _articles()
    insight = "충분히 긴 인사이트 " * 30
    errors, _ = check_content_completeness(arts, arts, insight, insight, "2026-04-04", no_llm=False)
    assert errors == []


def test_missing_insight():
    arts = _articles()
    errors, _ = check_content_completeness(arts, arts, "", "good insight " * 30, "2026-04-04", no_llm=False)
    assert any("Korean insight" in e for e in errors)


def test_too_few_world():
    arts = [_article(f"Korea {i}", "korea") for i in range(5)]
    insight = "충분히 긴 인사이트 " * 30
    errors, _ = check_content_completeness(arts, arts, insight, insight, "2026-04-04", no_llm=False)
    assert any("world" in e.lower() for e in errors)


def test_korea_purity_international_article():
    arts = _articles(n_world=5, n_korea=0)
    arts.append(_article("트럼프 이란 전쟁 폭격 강화", "korea"))
    arts.append(_article("한국 경제 성장", "korea"))
    arts.append(_article("서울 부동산 가격 급등", "korea"))
    insight = "충분히 긴 인사이트 " * 30
    errors, _ = check_content_completeness(arts, arts, insight, insight, "2026-04-04", no_llm=False)
    assert any("international" in e.lower() or "국제" in e for e in errors)


def test_korea_purity_junk_article():
    arts = _articles(n_world=5, n_korea=0)
    arts.append(_article("국방부 인사발령 소식", "korea"))
    arts.append(_article("한국 경제 성장", "korea"))
    arts.append(_article("서울 부동산 가격 급등", "korea"))
    insight = "충분히 긴 인사이트 " * 30
    errors, _ = check_content_completeness(arts, arts, insight, insight, "2026-04-04", no_llm=False)
    assert any("low-value" in e.lower() or "잡" in e for e in errors)


def test_cross_section_duplicate_url():
    arts = [
        _article("Iran war", "world", "reuters"),
        _article("이란 전쟁", "korea", "reuters"),
    ]
    arts[1]["url"] = arts[0]["url"]
    insight = "충분히 긴 인사이트 " * 30
    errors, _ = check_content_completeness(arts + _articles(3, 3), arts + _articles(3, 3), insight, insight, "2026-04-04", no_llm=False)
    assert any("duplicate" in e.lower() for e in errors)
