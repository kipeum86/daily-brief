# Daily Brief

AI-powered daily market & news briefing — Economist × FT editorial style.

매일 아침, 한국+미국 시장 데이터와 글로벌/국내 뉴스를 AI가 분석하여 한국어·영어 브리핑을 자동 생성합니다.

**Live:** [kipeum86.github.io/daily-brief](https://kipeum86.github.io/daily-brief/)

---

## Features

- **AI Insight** — Gemini가 시장+뉴스를 교차 분석한 에디토리얼 브리핑
- **Markets** — KOSPI, S&P 500, 환율, 원자재, 크립토, VIX + 스파크라인
- **Market Pulse** — VIX/환율/지수 조합 Risk-On/Off 게이지
- **S&P Sectors** — 11개 섹터 ETF 미니 히트맵
- **News** — 글로벌 (Reuters, BBC, Guardian, Al Jazeera, AP, NPR) + 한국 (네이버 API)
- **Bilingual** — 한국어/영어 토글 (뉴스 자동 번역)
- **Email** — Gmail SMTP로 매일 아침 BCC 발송
- **Archive** — 과거 브리핑 날짜별 탐색

## Quick Start

### 1. Fork & Clone

```bash
git clone https://github.com/kipeum86/daily-brief.git
cd daily-brief
```

### 2. API 키 발급

| 서비스 | 용도 | 발급 |
|--------|------|------|
| **Google AI Studio** | Gemini AI | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) |
| **Naver Developers** | 한국 뉴스 | [developers.naver.com](https://developers.naver.com) → 뉴스 검색 API |
| **Gmail** | 이메일 발송 | [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords) (2단계 인증 필요) |
| FRED | 경제 지표 (선택) | [fred.stlouisfed.org/docs/api](https://fred.stlouisfed.org/docs/api/api_key.html) |

### 3. 환경 설정

```bash
cp .env.example .env
# .env 파일에 API 키 입력

cp subscribers.example.txt subscribers.txt
# subscribers.txt에 수신자 이메일 추가 (한 줄에 하나)
```

### 4. 로컬 실행

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python main.py --dry-run        # 이메일 없이 테스트
python main.py                  # 전체 실행 (이메일 포함)
python main.py --no-llm         # AI 없이 데이터만
```

### 5. GitHub Actions 자동화

Repository Settings → Secrets → Actions에 추가:

| Secret | 값 |
|--------|---|
| `GOOGLE_API_KEY` | Gemini API 키 |
| `NAVER_CLIENT_ID` | 네이버 Client ID |
| `NAVER_CLIENT_SECRET` | 네이버 Client Secret |
| `GMAIL_ADDRESS` | 발신 Gmail 주소 |
| `GMAIL_APP_PASSWORD` | Gmail 앱 비밀번호 |
| `SUBSCRIBERS` | 수신자 이메일 (쉼표 구분) |

GitHub Pages 활성화: Settings → Pages → Source: `gh-pages` branch.

매일 KST 05:00에 자동 실행됩니다.

## Architecture

```
main.py                  # 파이프라인 오케스트레이터
pipeline/
├── markets/             # yfinance + FRED (fallback)
├── news/                # RSS + 네이버 API
├── ai/                  # Gemini 브리핑 + 번역
├── llm/                 # Pluggable LLM providers
├── render/              # Jinja2 → HTML (대시보드 + 이메일)
└── deliver/             # Gmail SMTP + Google Sheets
templates/
├── dashboard/           # 웹 대시보드 (Economist 스타일)
└── email/               # HTML 이메일 (인라인 CSS)
```

## Config

`config.yaml`에서 시장 티커, 뉴스 소스, LLM 모델 등을 설정할 수 있습니다.

```yaml
llm:
  provider: "gemini"           # gemini, claude, openai
  model: "gemini-2.5-flash"
```

## License

MIT
