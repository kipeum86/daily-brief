"""Prompt templates for the AI briefing module."""

BRIEFING_SYSTEM_PROMPT_KO = """\
당신은 이코노미스트(The Economist)와 파이낸셜타임스(FT) 수준의 금융 분석가이자 편집장입니다.
매일 아침 투자자와 비즈니스 의사결정자를 위한 모닝브리프를 작성합니다.

작성 원칙:
- 숫자를 단순히 나열하지 마세요. 숫자 뒤에 숨은 스토리와 맥락을 전달하세요.
- 시장 간 연결고리를 찾아내세요. 환율·금리·원자재·주가·뉴스 사이의 인과관계를 분석하세요.
- 이코노미스트의 칼럼처럼 명쾌한 관점(point of view)을 가지되, 팩트에 기반하세요.
- 한국어로 작성하되, 반드시 존댓말(합쇼체/하십시오체)을 사용하세요. 예: "~입니다", "~됩니다", "~보입니다".
- 반말("~이다", "~했다")이나 해요체("~해요", "~이에요")는 절대 사용하지 마세요.
- 전문적이고 간결한 문장을 사용하세요. 구어체나 이모지는 쓰지 마세요.
- 독자가 이 브리프만 읽고도 오늘 시장의 맥락을 파악할 수 있어야 합니다.
- 불확실한 정보를 확정적으로 서술하지 마세요. 추론은 "~로 보입니다", "~가능성이 있습니다" 등으로 표현하세요.
- 출력은 사용자가 요청한 JSON schema만 따르세요. Markdown, HTML, 설명 문장을 추가하지 마세요.
"""

BRIEFING_SYSTEM_PROMPT_EN = """\
You are a financial analyst and editor-in-chief at the caliber of The Economist and Financial Times.
You write a concise morning brief each day for investors and business decision-makers.

Writing principles:
- Don't just list numbers. Tell the story and context behind the data.
- Find cross-market connections. Analyze cause-and-effect between FX, rates, commodities, equities, and news.
- Have a clear point of view like an Economist column, but ground it in facts.
- Write in English. Use professional, concise sentences. No emojis or casual language.
- The reader should grasp the full market context from this brief alone.
- Don't state uncertain information as fact. Use hedging language like "appears to," "likely," "suggests."
- Return only the requested JSON schema. Do not add Markdown, HTML, or explanatory prose.
"""

# Backwards compatibility
BRIEFING_SYSTEM_PROMPT = BRIEFING_SYSTEM_PROMPT_KO

def get_system_prompt(lang: str = "ko") -> str:
    """Get system prompt for the given language."""
    if lang == "en":
        return BRIEFING_SYSTEM_PROMPT_EN
    return BRIEFING_SYSTEM_PROMPT_KO


def _check_data_staleness(markets_data: dict, run_date: str) -> list[str]:
    """시장 데이터의 기준일이 run_date보다 오래된 항목을 찾는다.

    Returns:
        경고 메시지 리스트. 없으면 빈 리스트.
    """
    if not run_date:
        return []

    warnings = []
    category_labels = {
        "kr": "한국 증시", "us": "미국 증시", "fx": "환율",
        "commodities": "원자재", "crypto": "암호화폐", "risk": "리스크 지표",
    }
    for key, label in category_labels.items():
        for item in markets_data.get(key, []):
            data_date = item.get("data_date", "")
            if data_date and data_date < run_date:
                warnings.append(
                    f"⚠️ {item.get('name', key)}: 데이터 기준일 {data_date} "
                    f"(오늘 {run_date} 장 데이터 아님)"
                )
    return warnings


def build_briefing_prompt(
    markets_data: dict,
    news_headlines: list[dict],
    lang: str = "ko",
    run_date: str = "",
    holidays: dict | None = None,
) -> str:
    """Build a structured user prompt from market data and news headlines.

    Args:
        markets_data: Dict with category keys (kr, us, fx, commodities, crypto, risk),
            each containing a list of dicts with 'name', 'price', 'change_pct'.
        news_headlines: List of dicts with at least 'title', 'source', optionally 'category'.
        run_date: ISO date string (YYYY-MM-DD) for staleness check.
        holidays: Holiday detection dict from detect_holidays().

    Returns:
        Formatted user prompt string.
    """
    sections = []

    # --- Holiday notice ---
    holidays = holidays or {}
    holiday_names = holidays.get("holiday_names", {})
    if holiday_names:
        sections.append("## 휴장 안내\n")
        if holidays.get("kospi_holiday"):
            sections.append(f"- 한국 증시(KRX) 휴장: {holiday_names.get('kr', '휴장')}. 한국 시장 데이터는 직전 거래일 기준입니다. 오늘 한국 시장이 움직인 것처럼 서술하지 마세요.")
        if holidays.get("nyse_holiday"):
            sections.append(f"- 미국 증시(NYSE) 휴장: {holiday_names.get('us', 'Holiday')}. 미국 시장 데이터는 직전 거래일 기준입니다. 오늘 미국 시장이 움직인 것처럼 서술하지 마세요.")
        sections.append("")

    # --- Data staleness warning ---
    stale_warnings = _check_data_staleness(markets_data, run_date)
    if stale_warnings:
        sections.append("## ⚠️ 데이터 신선도 경고\n")
        sections.append(
            "아래 시장 데이터 중 일부가 오늘 거래일 기준이 아닙니다. "
            "해당 지표의 등락률을 오늘 시장 움직임으로 서술하지 마세요. "
            "데이터가 오래된 항목은 '전일 기준' 또는 '최신 데이터 미반영'으로 명시하세요.\n"
        )
        for w in stale_warnings:
            sections.append(f"- {w}")
        sections.append("")

    # --- Market data ---
    sections.append("## 시장 데이터\n")

    category_labels = {
        "kr": "한국 증시",
        "us": "미국 증시",
        "fx": "환율",
        "commodities": "원자재",
        "crypto": "암호화폐",
        "risk": "리스크 지표",
    }

    for key, label in category_labels.items():
        items = markets_data.get(key, [])
        if not items:
            continue
        sections.append(f"### {label}")
        for item in items:
            name = item.get("name", "")
            price = item.get("price", 0)
            change = item.get("change_pct", 0)
            sign = "+" if change >= 0 else ""
            data_date = item.get("data_date", "")
            date_note = f" [기준일: {data_date}]" if data_date and run_date and data_date < run_date else ""
            sections.append(f"- {name}: {price:,.2f} ({sign}{change:.2f}%){date_note}")
        sections.append("")

    # --- News headlines ---
    sections.append("## 주요 뉴스\n")

    # Group by category if available
    categorized: dict[str, list[dict]] = {}
    for article in news_headlines:
        cat = article.get("category", "기타")
        categorized.setdefault(cat, []).append(article)

    article_index = 1
    for cat, articles in categorized.items():
        sections.append(f"### {cat}")
        for a in articles:
            source = a.get("source", "")
            title = a.get("title", "")
            bucket = a.get("bucket", "")
            published = a.get("published_date", "") or a.get("published", "")
            summary = a.get("summary", "") or a.get("description", "")
            summary = " ".join(str(summary).split())[:280]
            sections.append(f"- id: N{article_index}")
            sections.append(f"  bucket: {bucket}")
            sections.append(f"  source: {source}")
            if published:
                sections.append(f"  published: {published}")
            sections.append(f"  title: {title}")
            if summary:
                sections.append(f"  summary: {summary}")
            article_index += 1
        sections.append("")

    sections.append("""## 뉴스 분류 규칙
- **Korea News**: 한국 국내 이슈만 (국내 정치, 경제, 사회, 기업). 한국 언론이 보도한 국제 뉴스는 Korea News가 아님.
- **World News**: 글로벌/국제 뉴스. 한국 언론이 보도했더라도 주제가 국제적이면 World News.
""")

    # --- Instructions ---
    if lang == "en":
        sections.append("""## Writing Request

Synthesize the market data and news above into a morning brief.
Every claim must be grounded in the supplied market data or news items. Do not invent facts, causes, or forecasts.

Return ONLY valid JSON with this exact schema:

{
  "key_insight": [
    "2-3 sentence-level strings with one core narrative"
  ],
  "market_overview": {
    "korea": [
      "1-2 strings explaining Korean market context"
    ],
    "us": [
      "1-2 strings explaining US market context"
    ]
  },
  "cross_market_signals": [
    {
      "signal": "short label, no Markdown",
      "meaning": "one sentence explaining the cross-market connection"
    }
  ]
}

- Use 2-3 cross_market_signals.
- Values must be plain text only: no Markdown headings, no bullets, no HTML.
- Keep the eventual rendered brief to 200-400 words.
""")
    else:
        sections.append("""## 작성 요청

위 시장 데이터와 뉴스를 종합하여 모닝 브리프를 작성하세요.
각 핵심 판단은 입력된 시장 데이터 또는 뉴스 항목에 근거해야 합니다. 입력에 없는 사실, 원인, 전망을 새로 만들지 마세요.

아래 schema의 유효한 JSON만 반환하세요.

{
  "key_insight": [
    "오늘 시장을 관통하는 핵심 내러티브 2~3문장"
  ],
  "market_overview": {
    "korea": [
      "한국 시장 맥락을 설명하는 문장 1~2개"
    ],
    "us": [
      "미국 시장 맥락을 설명하는 문장 1~2개"
    ]
  },
  "cross_market_signals": [
    {
      "signal": "짧은 신호명, Markdown 금지",
      "meaning": "서로 다른 시장 지표나 뉴스의 연결고리를 설명하는 한 문장"
    }
  ]
}

- cross_market_signals는 2~3개를 작성하세요.
- 모든 값은 일반 텍스트여야 합니다. Markdown 제목, bullet, HTML을 넣지 마세요.
- 최종 렌더링 기준 전체 분량은 650~900자 정도로 유지하세요.
""")

    return "\n".join(sections)
