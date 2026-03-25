"""AI 기반 뉴스 중요도 선별 — 잡스러운 기사 제거, 핵심 뉴스만 선택."""

import json
import logging
from pipeline.llm.base import LLMProvider

logger = logging.getLogger(__name__)

SELECTOR_PROMPT = """\
You are an editor at The Economist. From the following news headlines, select the {top_n} most important ones that a global investor and business decision-maker MUST know today.

Selection criteria:
- Market-moving events (central bank decisions, major economic data, geopolitical shifts)
- Major policy changes (trade, regulation, fiscal policy)
- Significant corporate news (earnings surprises, M&A, leadership changes at major companies)
- Geopolitical developments with economic impact

DIVERSITY RULES:
- Maximize source diversity — do NOT pick multiple articles from the same outlet
- No overlapping topics — each selected article must cover a DIFFERENT subject
- Spread across different regions/themes when possible

EXCLUDE:
- Routine personnel appointments ("인사 발령")
- Celebrity/entertainment news
- Local crime/accidents
- Repetitive/duplicate stories (even from different sources)
- Opinion pieces or editorials

Return ONLY a JSON array of the selected headline indices (0-based).
Example: [0, 3, 5, 8, 12]"""


def select_top_news(
    provider: LLMProvider,
    articles: list,
    top_n: int = 5,
) -> list:
    """AI가 중요도 기준으로 상위 N개 뉴스를 선별한다.

    Args:
        provider: LLM provider instance.
        articles: 전체 기사 리스트 (dict 또는 Article 객체).
        top_n: 선별할 기사 수.

    Returns:
        선별된 기사 리스트. 실패 시 원본에서 앞에서 top_n개 반환.
    """
    if len(articles) <= top_n:
        return articles

    # 헤드라인 목록 생성
    headlines = []
    for i, art in enumerate(articles):
        if isinstance(art, dict):
            title = art.get("title", "")
            source = art.get("source", "")
        else:
            title = getattr(art, "title", "")
            source = getattr(art, "source", "")
        headlines.append(f"[{i}] [{source}] {title}")

    user_prompt = f"Headlines:\n" + "\n".join(headlines) + f"\n\nSelect the {top_n} most important. Return JSON array of indices only:"

    try:
        system = SELECTOR_PROMPT.format(top_n=top_n)
        response = provider.complete(system, user_prompt)

        # JSON 파싱
        response = response.strip()
        if response.startswith("```"):
            response = response.split("\n", 1)[1].rsplit("```", 1)[0]
        indices = json.loads(response)

        # 유효한 인덱스만 필터
        selected = []
        for idx in indices:
            if isinstance(idx, int) and 0 <= idx < len(articles):
                selected.append(articles[idx])

        if selected:
            logger.info("AI 뉴스 선별: %d개 중 %d개 선택", len(articles), len(selected))
            return selected[:top_n]

    except Exception:
        logger.exception("AI 뉴스 선별 실패 — 최신순 fallback")

    return articles[:top_n]
