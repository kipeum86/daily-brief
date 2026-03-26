"""AI 기반 뉴스 중요도 선별 — 잡스러운 기사 제거, 핵심 뉴스만 선택."""

import json
import logging
from pipeline.llm.base import LLMProvider

logger = logging.getLogger(__name__)

SELECTOR_PROMPT_WORLD = """\
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

SELECTOR_PROMPT_KOREA = """\
You are an editor at a major Korean newspaper. From the following headlines published by Korean outlets, select the {top_n} most important DOMESTIC Korean news stories.

Selection criteria:
- Korean economic policy & data (한국은행, 기재부, 고용, 물가, 부동산)
- Korean corporate news (삼성, SK, 현대 등 주요 기업 실적·경영)
- Korean politics & regulation affecting markets
- Korean industry trends (반도체, 배터리, 자동차, K-콘텐츠)

CRITICAL — EXCLUDE international/foreign news:
- Wars, conflicts, or diplomacy between other countries (e.g. Iran, Ukraine, Middle East)
- US/China/Japan/EU politics or policy (unless directly about Korea)
- International disasters or events unrelated to Korea
- If a Korean outlet covers a foreign story, it is still foreign news — SKIP IT

DIVERSITY RULES:
- Maximize source diversity — do NOT pick multiple articles from the same outlet
- No overlapping topics — each selected article must cover a DIFFERENT subject

Return ONLY a JSON array of the selected headline indices (0-based).
Example: [0, 3, 5, 8, 12]"""


def select_top_news(
    provider: LLMProvider,
    articles: list,
    top_n: int = 5,
    category: str = "world",
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
        prompt_template = SELECTOR_PROMPT_KOREA if category == "korea" else SELECTOR_PROMPT_WORLD
        system = prompt_template.format(top_n=top_n)
        response = provider.complete(system, user_prompt)

        # JSON 파싱
        response = response.strip()
        if response.startswith("```"):
            response = response.split("\n", 1)[1].rsplit("```", 1)[0]
        indices = json.loads(response)

        # 유효한 인덱스만 필터
        # 소스가 다양한 경우(글로벌 뉴스)에만 소스당 1개 제한 적용
        # 소스가 단일(예: 네이버뉴스)이면 AI 선택을 그대로 존중
        all_sources = set()
        for art in articles:
            src = art.get("source", "") if isinstance(art, dict) else getattr(art, "source", "")
            all_sources.add(src)
        enforce_source_diversity = len(all_sources) > 3

        selected = []
        used_sources: set[str] = set()
        for idx in indices:
            if isinstance(idx, int) and 0 <= idx < len(articles):
                art = articles[idx]
                src = art.get("source", "") if isinstance(art, dict) else getattr(art, "source", "")
                if enforce_source_diversity and src in used_sources:
                    continue
                selected.append(art)
                used_sources.add(src)

        # 글로벌 뉴스에서 AI가 소스 다양성을 못 맞췄으면, 남은 슬롯을 다른 소스에서 채움
        if enforce_source_diversity and len(selected) < top_n:
            for art in articles:
                if len(selected) >= top_n:
                    break
                src = art.get("source", "") if isinstance(art, dict) else getattr(art, "source", "")
                if src not in used_sources:
                    selected.append(art)
                    used_sources.add(src)

        if selected:
            sources = [a.get("source", "") if isinstance(a, dict) else getattr(a, "source", "") for a in selected]
            logger.info("AI 뉴스 선별: %d개 중 %d개 선택 (소스: %s)", len(articles), len(selected), ", ".join(sources))
            return selected[:top_n]

    except Exception:
        logger.exception("AI 뉴스 선별 실패 — 최신순 fallback")

    return articles[:top_n]
