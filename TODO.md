# Daily Brief — TODO

## Phase 1: Foundation
- [ ] GitHub 레포 생성 (`kipeum86/daily-brief`)
- [ ] 프로젝트 구조 scaffolding (pipeline/, templates/, static/, tests/)
- [ ] config.yaml 스키마 설계 (데이터 소스, RSS 피드, 구독자, AI 모델 설정)
- [ ] .env.example 작성 (ANTHROPIC_API_KEY, FRED_API_KEY, EMAIL_API_KEY)

## Phase 2: Data Collection
- [ ] Yahoo Finance (yfinance) 시장 데이터 수집 모듈
  - KOSPI, KOSDAQ, S&P 500, Nasdaq, VIX, 환율, 원자재, 암호화폐
- [ ] FRED API 매크로 지표 수집 (미 국채 10Y, 달러 인덱스)
- [ ] RSS 뉴스 수집 모듈
  - 글로벌: Reuters, AP News, BBC World
  - 한국: 네이버 뉴스 RSS (안정성 우선)
  - 경제: CNBC, MarketWatch
- [ ] 뉴스 중복 제거 로직 (auto-newsbriefing 패턴 참고)

## Phase 3: AI Briefing
- [ ] Claude API 연동 (Haiku 모델, config.yaml에서 모델 설정 가능)
- [ ] 뉴스 요약 프롬프트 설계
- [ ] 시장+뉴스 교차 인사이트 프롬프트 설계
- [ ] 저녁/아침 브리핑별 프롬프트 분리

## Phase 4: Dashboard (Web)
- [ ] Jinja2 대시보드 템플릿 (base.html + 섹션별 partial)
- [ ] Chart.js 시장 차트 (지수 변동, 환율 등)
- [ ] Plotly.js treemap 히트맵 (업종별/섹터별 등락, Finviz 스타일)
- [ ] Dark theme 디자인
- [ ] 모바일 반응형 레이아웃
- [ ] 브리핑 아카이브 (날짜별 과거 브리핑 보존)

## Phase 5: Email
- [ ] Jinja2 이메일 템플릿 (인라인 CSS, JS 없음)
- [ ] 히트맵 → HTML 테이블 + 배경색 셀 변환
- [ ] 차트 → matplotlib (Agg backend) PNG → base64 인라인 임베딩
- [ ] Resend API 이메일 발송 연동
- [ ] 구독자 관리 (config.yaml)

## Phase 6: Automation
- [ ] GitHub Actions 저녁 워크플로우 (cron: '30 7 * * 1-5' UTC = KST 16:30)
- [ ] GitHub Actions 아침 워크플로우 (cron: '30 22 * * 1-5' UTC = KST 07:30)
- [ ] GitHub Pages 배포 (peaceiris/actions-gh-pages, gh-pages 브랜치)
- [ ] 에러 핸들링 + graceful degradation

## Phase 7: Launch
- [ ] 본인 사용 테스트 (1주일)
- [ ] 친구 초대 (이메일 구독)
- [ ] GitHub 포트폴리오 등록

## 10x Vision (향후)
- [ ] 포트폴리오 연동 — 내 보유 종목과 매크로 변화 연결
- [ ] 히스토리 트래킹 — 과거 브리핑 트렌드 시각화
- [ ] 맞춤 관심사 — config.yaml 관심 섹터/키워드 필터링
- [ ] 한국은행 ECOS API 연동
