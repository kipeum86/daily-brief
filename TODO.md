# Daily Brief — TODO

## v1: Core Pipeline ✅
- [x] 프로젝트 구조 scaffolding (24개 Python 파일)
- [x] config.yaml + .env.example
- [x] auto-newsbriefing 모듈 copy-and-own (collector, dedup, llm, config, models)
- [x] Markets 데이터 수집 (yfinance + FRED fallback + 병렬 수집)
- [x] S&P 섹터 ETF 11개 + 마켓 펄스 (Risk-On/Off)
- [x] 스파크라인 SVG (곡선 보간 + 그라디언트)
- [x] 뉴스 수집: 글로벌 RSS (Reuters, BBC, Guardian, Al Jazeera, AP, NPR)
- [x] 뉴스 수집: 한국 네이버 API (키워드 검색, 국내 이슈만)
- [x] 뉴스 중복 제거 (3단계 dedup)
- [x] AI 브리핑 (Gemini 기본, pluggable provider)
- [x] 한/영 토글 (2개 HTML 생성 + 뉴스 번역)
- [x] Jinja2 대시보드 (Economist × FT 에디토리얼 스타일)
- [x] 이메일 템플릿 (인라인 CSS, matplotlib 차트)
- [x] Resend API 이메일 발송
- [x] Google Sheets 아카이브
- [x] GitHub Actions (KST 06:30 cron)
- [x] main.py 오케스트레이터 (graceful degradation, CLI)
- [x] 과거 브리핑 탐색 (◀▶ 네비 + /archive 페이지)
- [x] dry-run 검증 (14 tickers, 186 articles, 에러 0)

## v1 남은 작업
- [ ] GitHub 레포 생성 + push
- [ ] GitHub Pages 활성화
- [ ] Gemini API 키 설정 후 full run (AI 인사이트 포함)
- [ ] 네이버 API 키 설정 (NAVER_CLIENT_ID, NAVER_CLIENT_SECRET)
- [ ] 본인 사용 테스트 (1주일)
- [ ] 테스트 작성 (핵심+중요 7개 파일)

## v1.5: Recap
- [ ] Weekly Recap (매주 토요일, Sheets 데이터 기반)
- [ ] Monthly Recap (매월 초, 월간 성과 + 전망)

## v2: 위젯 & 배포
- [ ] JSON API 출력 (output/api/latest.json) — 위젯/외부 연동용
- [ ] iOS 위젯 (Scriptable) — 잠금화면 KOSPI, VIX, AI 인사이트
- [ ] macOS 위젯 (Übersicht) — 데스크톱 위젯
- [ ] Android 위젯 (KWGT/Tasker)
- [ ] PWA manifest.json + 서비스워커
- [ ] App Store 배포 검토 (Apple $99/년, Microsoft $19)

## v2: 디자인 강화
- [ ] Plotly.js treemap 히트맵 (Finviz 스타일)
- [ ] Dark theme 토글
- [ ] browse daemon 비주얼 QA (/design-review)

## v2: 데이터 강화
- [ ] 한국은행 ECOS API
- [ ] 경제 캘린더 (FOMC, 고용지표 등)
- [ ] 포트폴리오 연동 — 보유 종목 × 매크로 변화
- [ ] 맞춤 관심사 — 관심 섹터/키워드 필터링
- [ ] ParlaWatch 스타일 DB 기반 웹 (검색, 필터, 날짜 범위)
