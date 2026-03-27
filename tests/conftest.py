from __future__ import annotations

import pytest


def _article(
    title: str,
    source: str,
    url: str,
    description: str,
    published_date: str,
    bucket: str,
    category: str,
    *,
    rank: int = 1,
    coverage_score: int = 1,
):
    return {
        "title": title,
        "source": source,
        "url": url,
        "description": description,
        "summary": description,
        "published_date": published_date,
        "bucket": bucket,
        "category": category,
        "rank": rank,
        "coverage_score": coverage_score,
    }


@pytest.fixture
def sample_articles():
    return [
        _article("Fed signals slower rate cuts", "Reuters", "https://example.com/reuters-fed", "The Fed guided markets toward a slower easing path.", "2026-03-28", "world", "economy", rank=1, coverage_score=3),
        _article("AP says China export rebound lifts Asia trade outlook", "AP News", "https://example.com/ap-china-trade", "Chinese export momentum improved regional trade expectations.", "2026-03-28", "world", "economy", rank=2, coverage_score=3),
        _article("BBC: Iran conflict raises shipping risk", "BBC World", "https://example.com/bbc-iran", "Security risks in the Middle East threatened shipping lanes.", "2026-03-28", "world", "security", rank=3, coverage_score=2),
        _article("Guardian covers EU AI regulation vote", "The Guardian", "https://example.com/guardian-eu-ai", "EU lawmakers advanced a major AI framework.", "2026-03-28", "world", "tech", rank=4, coverage_score=2),
        _article("CNBC: Apple supplier outlook lifts chip shares", "CNBC", "https://example.com/cnbc-chips", "Strong supplier commentary supported semiconductor stocks.", "2026-03-28", "world", "corporate", rank=5, coverage_score=2),
        _article("연합뉴스: 한국은행 금리 동결 가능성", "연합뉴스", "https://example.com/yna-bok", "한국은행이 물가와 환율 흐름을 보며 기준금리 동결을 검토한다.", "2026-03-28", "korea", "economy", rank=1, coverage_score=3),
        _article("조선일보: 반도체 수출 회복세 확대", "조선일보", "https://example.com/chosun-chip", "삼성과 SK의 수출 회복이 경기 개선 기대를 키웠다.", "2026-03-28", "korea", "corporate", rank=2, coverage_score=3),
        _article("한겨레: 부동산 정책 조정 검토", "한겨레", "https://example.com/hani-housing", "정부가 대출 규제와 세제 보완을 함께 검토하고 있다.", "2026-03-28", "korea", "politics", rank=3, coverage_score=2),
        _article("한국경제: 코스피 외국인 순매수 확대", "한국경제", "https://example.com/hk-kospi", "코스피에서 외국인 자금 유입이 확대됐다.", "2026-03-28", "korea", "economy", rank=4, coverage_score=2),
        _article("매일경제: 배터리 업계 투자 재개", "매일경제", "https://example.com/mk-battery", "국내 배터리 기업들이 설비 투자를 재개하고 있다.", "2026-03-28", "korea", "corporate", rank=5, coverage_score=2),
        _article("연합뉴스: 이란 충돌에 유가 급등", "연합뉴스", "https://example.com/yna-iran", "이란과 이스라엘 긴장이 국제 유가를 밀어 올렸다.", "2026-03-28", "world", "security", rank=6, coverage_score=2),
        _article("조선일보: 한국-미국 FTA 협상, 정부 대응 논의", "조선일보", "https://example.com/chosun-fta", "정부가 한국 수출업계 대응 방안을 논의했다.", "2026-03-28", "korea", "politics", rank=6, coverage_score=2),
        _article("뉴스1: 노동시장 구조개혁 법안 논의", "뉴스1", "https://example.com/news1-labor", "국회가 노동시장 구조개혁 법안을 검토 중이다.", "2026-03-28", "korea", "society", rank=7, coverage_score=1),
        _article("Reuters: NATO ministers discuss defense budgets", "Reuters", "https://example.com/reuters-nato", "Allied ministers met to discuss defense spending targets.", "2026-03-28", "world", "security", rank=7, coverage_score=2),
        _article("MBC: 교육부 대입 제도 개편 발표", "MBC", "https://example.com/mbc-education", "교육부가 대입 제도 개편안을 발표했다.", "2026-03-28", "korea", "society", rank=8, coverage_score=1),
    ]


@pytest.fixture
def sample_config(tmp_path):
    return {
        "news": {"top_n": 5},
        "output": {"dir": str(tmp_path / "output")},
    }


@pytest.fixture
def mock_provider():
    class Provider:
        def __init__(self, response: str):
            self.response = response

        def complete(self, system: str, user: str) -> str:
            return self.response

    return Provider
