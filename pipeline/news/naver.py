"""네이버 뉴스 검색 API를 사용한 한국 뉴스 수집."""

import logging
import os
import re
from datetime import datetime
from typing import Any

import requests

logger = logging.getLogger(__name__)

NAVER_NEWS_URL = "https://openapi.naver.com/v1/search/news.json"


def _strip_html(text: str) -> str:
    """HTML 태그 및 &quot; 등 엔티티 제거."""
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&quot;", '"').replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return text.strip()


def collect_naver_news(config: dict) -> list[dict[str, Any]]:
    """네이버 뉴스 검색 API로 한국 뉴스를 수집한다.

    Args:
        config: 전체 config dict. config["news"]["korea"] 섹션 사용.

    Returns:
        기사 리스트 [{"title", "summary", "source", "url", "published", "category"}]
    """
    client_id = os.environ.get("NAVER_CLIENT_ID", "")
    client_secret = os.environ.get("NAVER_CLIENT_SECRET", "")

    if not client_id or not client_secret:
        logger.warning("NAVER_CLIENT_ID/NAVER_CLIENT_SECRET 미설정 — 네이버 뉴스 건너뜀")
        return []

    korea_config = config.get("news", {}).get("korea", {})
    queries = korea_config.get("queries", ["한국 경제"])
    display = korea_config.get("display", 5)
    sort = korea_config.get("sort", "date")

    headers = {
        "X-Naver-Client-Id": client_id,
        "X-Naver-Client-Secret": client_secret,
    }

    all_articles: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    for query in queries:
        try:
            resp = requests.get(
                NAVER_NEWS_URL,
                params={"query": query, "display": display, "sort": sort},
                headers=headers,
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()

            for item in data.get("items", []):
                url = item.get("originallink") or item.get("link", "")

                # URL 중복 방지
                if url in seen_urls:
                    continue
                seen_urls.add(url)

                # 날짜 파싱
                pub_date = item.get("pubDate", "")
                try:
                    published = datetime.strptime(pub_date, "%a, %d %b %Y %H:%M:%S %z")
                    published_str = published.strftime("%Y-%m-%d %H:%M")
                except (ValueError, TypeError):
                    published_str = pub_date

                all_articles.append({
                    "title": _strip_html(item.get("title", "")),
                    "summary": _strip_html(item.get("description", "")),
                    "source": "네이버뉴스",
                    "url": url,
                    "published": published_str,
                    "category": "korea",
                })

        except Exception as exc:
            logger.warning("네이버 뉴스 검색 실패 (query=%s): %s", query, exc)

    # 중복 제거 후 최신순 정렬
    all_articles.sort(key=lambda x: x.get("published", ""), reverse=True)

    # config의 top_n 제한
    top_n = config.get("news", {}).get("top_n", 5)
    result = all_articles[:top_n]

    logger.info("네이버 뉴스: %d개 쿼리 → %d개 기사 수집 (중복 제거 후 %d개 → top %d)",
                len(queries), len(all_articles), len(all_articles), len(result))

    return result
