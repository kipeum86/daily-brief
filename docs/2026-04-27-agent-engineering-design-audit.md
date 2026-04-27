# Daily Brief 에이전트 엔지니어링/디자인 감사

작성일: 2026-04-27

이 문서는 `daily-brief` 프로젝트의 에이전트 출력 품질, 토큰 효율, 구조, 기능, 프롬프트 설계를 실제 구현 가능한 개선 항목으로 정리한 것입니다. 목적은 코드 설명이 아니라, 현재 산출물의 실패 모드를 줄이고 운영 품질을 높이는 변경 목록을 제공하는 것입니다.

## 요약

가장 먼저 고쳐야 할 문제는 네 가지입니다.

1. 검증 게이트가 실패해도 프로세스가 성공 종료되어 GitHub Pages 배포가 계속됩니다.
2. 검증 함수 자체가 예외를 던지면 실패가 아니라 통과로 계산될 수 있습니다.
3. 뉴스 품질 게이트가 잘못된 후보를 제거한 뒤, 다른 제약을 맞추는 과정에서 다시 저품질 또는 잘못 분류된 기사를 넣을 수 있습니다.
4. LLM 출력 실패 또는 번역 실패가 빈 인사이트/원문 노출로 이어지고, 현재 배포 차단 결함 때문에 실제 사용자에게 노출될 수 있습니다.

이 네 가지를 먼저 수정하면 한국 섹션 오염, 빈 AI 인사이트, 미번역 페이지, 검증 실패 산출물 배포를 크게 줄일 수 있습니다.

## 검증 피드백 반영 메모

`docs/2026-04-27-audit-verification.md`의 피드백 중 다음은 반영했습니다.

- `pipeline/verify/gate.py`에서 broken check를 warning으로만 처리하고 `checks_passed`를 증가시키는 문제를 P0로 승격했습니다.
- `pipeline/llm/claude.py`가 이미 구현되어 있으므로, provider 관련 제안은 "Claude 신규 구현"이 아니라 `_get_provider()` dispatcher 연결과 OpenAI 문구 정리로 수정했습니다.
- AI HTML sanitize는 필요하지만 즉시 배포 차단급 취약점으로 단정하지 않고 P2 방어 보강으로 표현을 낮췄습니다.
- LLM/번역 실패는 기존 content/translation 검증이 일부 잡고 있으나, 배포 차단 결함 때문에 무력화된다는 종속성을 명시했습니다.

반영하지 않은 부분:

- GitHub Actions deploy step에 별도 `if:` 조건을 추가하는 방식은 필수로 채택하지 않았습니다. `main.py`가 검증 실패 시 non-zero로 종료하면 기본적으로 다음 step은 실행되지 않으므로, 우선은 종료 코드 기반 차단을 권장합니다.

## 권장 구현 순서

### P0: 배포 차단 동작 수정

대상 파일:

- `main.py`
- `.github/workflows/morning-brief.yml`
- `.github/workflows/weekly-recap.yml`

문제:

- `run_pre_deploy_checks()`가 실패해도 `main.py`는 최종적으로 `return 0`을 반환합니다.
- 워크플로우는 `python main.py` 다음에 무조건 `peaceiris/actions-gh-pages` 배포를 실행합니다.
- 현재 `output/data/verification-log.json`에 `passed: false`가 남아 있어도 배포가 진행될 수 있습니다.

왜 중요한가:

- 검증 게이트가 품질 보증 장치가 아니라 로그 장치로만 동작합니다.
- 이메일은 차단되어도 웹 배포는 계속될 수 있어, 사용자에게 실패 산출물이 노출됩니다.

구체적 수정안:

`main.py`에서 daily 검증 실패 시 즉시 실패 종료합니다.

```python
if not gate_passed:
    logger.warning("Stage 9/10: Skipped (verification failed)")
    return 1
```

weekly 경로도 동일하게 수정합니다.

```python
elif not weekly_gate_passed:
    logger.warning("Weekly email skipped (verification failed)")
    return 1
```

워크플로우는 `python main.py`가 non-zero로 종료되면 기본적으로 다음 step이 실행되지 않습니다. 따라서 별도 `if:` 조건보다 먼저 종료 코드가 확실히 실패하도록 만드는 것이 핵심입니다.

추가로 GitHub Actions에 테스트 단계를 넣습니다.

```yaml
- name: Run tests
  run: python -m pytest -q
```

검증 방법:

```bash
python3 -m pytest -q
python3 main.py --dry-run --date 2026-04-04
```

의도적으로 검증 실패 케이스를 만들었을 때 프로세스 종료 코드가 `1`인지 확인합니다.

### P0: 검증 체크 예외를 통과로 계산하는 문제 수정

대상 파일:

- `pipeline/verify/gate.py`
- `tests/test_verify_gate.py`
- `tests/test_verify_weekly.py`

문제:

- `run_pre_deploy_checks()`와 `run_weekly_checks()`는 개별 check 함수가 예외를 던지면 warning만 남기고 `checks_passed += 1`로 처리합니다.
- 그 결과 검증 코드의 import 오류, 런타임 오류, 파서 오류가 실제 실패가 아니라 "검사 통과"처럼 계산될 수 있습니다.

왜 중요한가:

- 검증 게이트는 산출물 품질을 막는 마지막 방어선입니다.
- 검증 코드 자체가 깨졌을 때 통과시키면, 가장 위험한 상태에서 안전장치가 사라집니다.

구체적 수정안:

broken check는 error로 승격하십시오.

```python
except Exception as exc:
    logger.exception("Check '%s' raised exception", name)
    all_errors.append(f"Check '{name}' failed due to verifier error: {exc}")
```

최종 pass 조건도 명확히 합니다.

```python
passed = len(all_errors) == 0 and checks_passed == checks_run
```

weekly gate도 같은 정책을 적용합니다.

```python
except Exception as exc:
    logger.exception("Weekly check '%s' raised exception", name)
    all_errors.append(f"Weekly check '{name}' failed due to verifier error: {exc}")
```

테스트 추가:

```python
def test_gate_fails_when_check_raises(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("broken verifier")

    monkeypatch.setattr("pipeline.verify.gate._run_html", boom)
    result = run_pre_deploy_checks(...)
    assert result.passed is False
    assert any("verifier error" in e for e in result.errors)
```

### P0: 품질 게이트 재삽입 로직 수정

대상 파일:

- `pipeline/news/quality_gates.py`
- `pipeline/news/selector.py`
- `tests/test_quality_gates.py`
- `tests/test_selector.py`

문제:

- `check_korea_purity()`가 국제 뉴스를 제거해도 이후 `check_category_balance()`나 `check_article_count()`가 같은 성격의 후보를 다시 넣을 수 있습니다.
- `check_category_balance()`는 관련성보다 카테고리 다양성을 우선해서 스포츠, 날씨, 생활성 기사 같은 저가치 후보를 넣을 수 있습니다.
- 실제 `quality-log.json`에는 Korea purity 수정 후 스포츠/날씨/국제 뉴스가 대체 기사로 들어간 기록이 있습니다.

왜 중요한가:

- 한국 섹션은 투자자용 국내 뉴스라는 핵심 계약을 지켜야 합니다.
- 형식적 균형 때문에 주제 품질이 낮아지면 브리프 전체 신뢰도가 떨어집니다.

구체적 수정안:

1. hard constraint 후보 필터를 추가합니다.

```python
LOW_VALUE_KEYWORDS = {
    "인사발령", "인사 발령", "부고", "운세", "로또", "날씨",
    "부임", "전보", "승진인사", "프로야구", "축구", "골프",
    "연예", "드라마", "예능", "맛집",
}

def _is_low_value(article: dict[str, Any]) -> bool:
    text = f"{article.get('title', '')} {article.get('description', '') or article.get('summary', '')}".lower()
    return any(keyword.lower() in text for keyword in LOW_VALUE_KEYWORDS)

def is_valid_world_candidate(article: dict[str, Any]) -> bool:
    return not _is_low_value(article)

def is_valid_korea_candidate(article: dict[str, Any]) -> bool:
    return _is_domestic_korea(article) and not _is_low_value(article)
```

`pipeline/verify/checks/content.py`의 `_LOW_VALUE_KEYWORDS`와 중복을 만들지 않도록, 공통 상수는 `pipeline/news/quality_gates.py`에 두고 content check가 이를 import하도록 정리하는 편이 좋습니다.

2. `_next_candidate()`에 `base_predicate`를 추가해서 모든 보충/교체 후보가 먼저 hard constraint를 통과하도록 합니다.

```python
def _next_candidate(
    candidates: list[dict[str, Any]],
    current: list[dict[str, Any]],
    predicate,
    base_predicate=lambda _a: True,
) -> dict[str, Any] | None:
    ...
    if not base_predicate(candidate):
        continue
    if predicate(candidate):
        return candidate
```

이 변경은 기존 `_next_candidate()` 호출부 전체를 함께 수정해야 합니다. 특히 `check_article_count()`의 현재 `lambda _candidate: True` 패딩은 section별 `base_predicate`를 반드시 받도록 바꿔야 합니다.

3. `run_quality_gates()` 순서를 hard constraint 중심으로 바꿉니다.

권장 순서:

1. 후보 풀 사전 필터링
2. article count 보충
3. source diversity
4. cross-section dedup
5. korea purity 재검증
6. low-value 재검증
7. category balance는 가능한 경우에만 적용
8. 최종 hard validation 실패 시 error 반환 또는 예외 발생

4. `category_balance`는 hard fail 조건이 아니라 soft reranking으로 격하합니다.

```python
if len(categories) < min_categories:
    replacement = _next_candidate(
        candidates,
        current,
        predicate=lambda c: c.get("category") not in categories,
        base_predicate=section_predicate,
    )
    if replacement is None:
        violations.append({
            "check": "category_balance",
            "severity": "warning",
            "action": "kept higher-quality candidates instead of forcing balance",
        })
        return current
```

5. 최종 검증 함수를 추가합니다.

```python
def validate_final_selection(world, korea, top_n):
    errors = []
    if len(world) < top_n:
        errors.append(f"world has {len(world)} articles")
    if len(korea) < top_n:
        errors.append(f"korea has {len(korea)} articles")
    for article in korea:
        if not is_valid_korea_candidate(article):
            errors.append(f"invalid korea article: {article.get('title', '')[:80]}")
    return errors
```

테스트 추가:

- 한국 후보에 국제 기사만 충분히 들어왔을 때 최종 결과가 국제 기사로 채워지지 않는지 확인
- category balance 후보가 low-value이면 교체하지 않는지 확인
- article count를 채우지 못하면 조용히 5개를 만드는 대신 검증 실패로 이어지는지 확인

### P0: 빈 인사이트와 번역 실패를 hard fail 처리

대상 파일:

- `pipeline/ai/briefing.py`
- `pipeline/ai/translate.py`
- `main.py`
- `pipeline/verify/checks/translation.py`
- `pipeline/verify/checks/content.py`

문제:

- `generate_briefing()`은 실패 시 빈 문자열을 반환합니다.
- `translate_news()`는 실패 시 원문 기사를 그대로 반환합니다.
- 이후 검증이 실패해도 현재 구조에서는 배포가 차단되지 않을 수 있습니다.
- content/translation 검증은 빈 인사이트와 미번역 제목을 일부 잡지만, 앞선 P0 배포 차단 결함이 있는 한 그 검증 결과가 실제 배포를 막지 못합니다.

왜 중요한가:

- 빈 인사이트는 이 프로젝트의 핵심 기능 실패입니다.
- 번역 실패가 원문 노출로 이어지면 한국어/영어 토글의 제품 계약이 깨집니다.

구체적 수정안:

1. 단기적으로는 빈 응답을 예외로 승격합니다.

`generate_briefing()`에서 빈 문자열 반환 대신 예외를 올리고 `main.py`에서 실패 종료합니다.

```python
if not insight or not insight.strip():
    raise RuntimeError("LLM returned empty briefing")
```

2. 중기적으로 LLM 실패 결과 타입을 명시합니다.

```python
@dataclass
class LLMTaskResult:
    ok: bool
    text: str = ""
    error: str = ""
    model: str = ""
```

3. 번역 실패 시 원문 fallback을 허용하지 않는 모드를 추가합니다.

```python
def translate_news(provider, articles, target_lang, strict: bool = True):
    ...
    except Exception:
        if strict:
            raise
        return articles
```

4. `main.py`에서는 production path에서 `strict=True`를 사용합니다.

```python
world_ko = translate_news(provider, world_news, target_lang="ko", strict=True)
korea_en = translate_news(provider, korea_news, target_lang="en", strict=True)
```

검증 방법:

- mock provider가 빈 문자열을 반환할 때 `main.py`가 실패 종료하는 테스트 추가
- 번역 응답이 invalid JSON일 때 원문이 렌더링되지 않는 테스트 추가

## 1. Output Quality

### 1.1 한국 섹션 분류 계약이 최종 산출물에서 깨짐

문제:

- `pipeline/news/selector.py`는 topic-based bucket 분류를 시도하지만, 품질 게이트와 보충 로직이 최종적으로 bucket 의미를 보존하지 못합니다.
- 실제 산출물에서 The Guardian의 캐나다 ICE 기사 같은 국제 뉴스가 `bucket: korea`로 들어갑니다.

왜 중요한가:

- Korea 섹션은 "한국 국내 이슈"라는 제품 계약을 갖습니다.
- 이 계약이 깨지면 브리프를 읽는 투자자는 섹션 구조를 신뢰할 수 없습니다.

수정안:

- `bucket`은 LLM 출력값을 그대로 믿지 말고, 최종 전 단계에서 deterministic validator를 통과하게 하십시오.
- Korea bucket 조건을 다음처럼 명확히 하십시오.

```python
def _is_domestic_korea(article: dict[str, Any]) -> bool:
    text = _article_text(article)
    domestic_hits = ...
    international_hits = ...
    source_is_korean = article.get("source") in KOREAN_SOURCES
    return domestic_hits > 0 and (international_hits == 0 or _has_korea_direct_impact(text))
```

- "한국 선박 26척은?", "한국 수출 영향"처럼 국제 이슈지만 한국 직접 영향이 있는 경우를 별도 함수로 분리하십시오.

```python
def _has_korea_direct_impact(text: str) -> bool:
    return any(token in text for token in ("한국", "국내", "우리 기업", "수출", "원화", "코스피", "한국 선박"))
```

### 1.2 인사이트가 headline-only 근거로 생성됨

문제:

- `build_briefing_prompt()`는 주요 뉴스에서 source/title/category만 사용합니다.
- 기사 요약과 발행일이 빠져 있어 모델이 헤드라인만 보고 시장 내러티브를 만들게 됩니다.

왜 중요한가:

- 헤드라인만으로는 이벤트의 규모, 시점, 시장 영향의 방향을 판단하기 어렵습니다.
- 그럴듯하지만 근거 약한 cross-market 연결이 나올 가능성이 큽니다.

수정안:

뉴스 입력 형식을 다음처럼 바꾸십시오.

```text
## 주요 뉴스
- id: W1
  bucket: world
  source: Reuters
  published: 2026-04-04
  title: ...
  summary: ...
```

프롬프트에 다음 제약을 추가하십시오.

```text
- 각 핵심 판단은 입력된 시장 데이터 또는 뉴스 id에 근거해야 합니다.
- 입력에 없는 사실, 원인, 전망을 새로 만들지 마십시오.
- 근거가 약한 경우 "가능성이 있습니다"로 표현하고 단정하지 마십시오.
```

렌더링에는 근거 id를 표시하지 않더라도, 내부 JSON 출력에는 `supporting_ids`를 포함시키는 방식을 권장합니다.

### 1.3 브리핑 분량 지시가 구조와 충돌함

문제:

- 한국어 prompt는 `오늘의 핵심`, `시장 동향`, `크로스마켓 시그널 2~3개`를 요구하면서 전체 300~500자로 제한합니다.
- 이 분량은 세 섹션을 논리적으로 완성하기에 부족합니다.

왜 중요한가:

- 모델이 수치를 생략하거나, 각 섹션을 형식만 맞춘 얕은 문장으로 채울 가능성이 높습니다.

수정안:

한국어 지시를 다음처럼 바꾸십시오.

```text
### 분량
- 오늘의 핵심: 2문장
- 시장 동향: 한국/미국 각각 2문장
- 크로스마켓 시그널: 3개 bullet
- 전체 650~900자
```

### 1.4 시장 데이터 신선도 검증이 prompt 경고에 과도하게 의존함

문제:

- `_check_data_staleness()`는 prompt에 경고를 넣지만, LLM이 이를 반드시 지킨다는 보장이 없습니다.
- `check_market_data()`는 `data_date`가 `target_date`보다 오래된 경우를 직접 error/warning으로 다루지 않습니다.

왜 중요한가:

- 휴장 또는 지연 데이터가 "오늘 움직임"처럼 서술되면 금융 브리프에서 치명적입니다.

수정안:

`pipeline/verify/checks/market_data.py`에 stale date 검증을 추가하십시오.

```python
def _check_data_dates(markets, holidays, run_date, errors, warnings):
    target_date = holidays.get("target_date") or run_date
    for section, items in markets.items():
        for item in items:
            data_date = item.get("data_date")
            if not data_date:
                warnings.append(f"{item.get('name')}: data_date missing")
                continue
            if data_date < target_date and section not in _holiday_sections(holidays):
                errors.append(f"{item.get('name')}: stale data_date {data_date}, target {target_date}")
```

## 2. Token Efficiency

### 2.1 뉴스 selector가 전체 기사 목록을 고가 모델에 직접 전달함

문제:

- `select_and_classify_news()`는 모든 기사에 최대 220자 summary를 붙여 LLM에 보냅니다.
- 100~200개 기사 기준 selector 한 번에 수만 토큰이 들어갈 수 있습니다.

왜 중요한가:

- 비용과 지연 시간이 커집니다.
- 긴 후보 목록에서는 모델이 뒤쪽 항목을 덜 보거나, source diversity와 topic diversity를 일관되게 지키기 어렵습니다.

수정안:

2단계 선별 구조로 바꾸십시오.

1. deterministic pre-rank
   - source별 최신/중요 후보 최대 5개
   - topic token cluster별 대표 기사 1~2개
   - low-value keyword 제거
2. LLM final selection
   - world 후보 최대 30개
   - korea 후보 최대 30개

예시:

```python
def build_llm_candidate_pool(all_articles, max_per_source=5, max_total=60):
    filtered = [a for a in all_articles if not _is_low_value(a)]
    clustered = cluster_by_topic(filtered)
    representatives = pick_cluster_representatives(clustered)
    return representatives[:max_total]
```

### 2.2 한국어/영어 인사이트 독립 생성으로 비용이 2배 발생

문제:

- `main.py`는 `generate_briefing()`을 한국어와 영어로 각각 호출합니다.

왜 중요한가:

- 두 언어 버전이 서로 다른 판단을 만들 수 있습니다.
- 비용이 증가합니다.

수정안:

중간 표현을 구조화 JSON으로 한 번 생성하십시오.

```json
{
  "key_insight": {
    "claims": [
      {
        "meaning": "...",
        "supporting_market_ids": ["M1", "M4"],
        "supporting_news_ids": ["W2"]
      }
    ]
  },
  "market_overview": [...],
  "cross_market_signals": [...]
}
```

그 다음 언어별 렌더링은 저렴한 모델 또는 deterministic template로 처리합니다.

### 2.3 번역에 동일 고가 provider를 사용함

문제:

- `translate_news()`는 briefing과 같은 provider를 받습니다.
- 설정상 기본 모델이 Pro preview이면 단순 번역도 고가 모델로 실행될 수 있습니다.

왜 중요한가:

- 번역은 분석보다 낮은 모델로도 품질 손실이 작습니다.

수정안:

config를 분리하십시오.

```yaml
llm:
  analysis_model: "gemini-2.5-pro"
  selection_model: "gemini-2.5-flash"
  translation_model: "gemini-2.5-flash"
  fallback_models: ["gemini-2.5-flash"]
```

provider factory도 task별로 받도록 바꿉니다.

```python
provider = get_provider(config, task="translation")
```

### 2.4 `max_input_chars` 설정이 실제 호출에 적용되지 않음

문제:

- `config.yaml`에 `llm.max_input_chars`가 있지만 selector/briefing/translation prompt builder에서 일관되게 사용하지 않습니다.

왜 중요한가:

- 설정으로 비용 상한을 통제한다고 착각할 수 있습니다.

수정안:

공통 prompt budget helper를 추가하십시오.

```python
def truncate_items_by_budget(items, render_item, max_chars):
    result = []
    used = 0
    for item in items:
        text = render_item(item)
        if used + len(text) > max_chars:
            break
        result.append(item)
        used += len(text)
    return result
```

모든 LLM 호출 전에 budget을 적용하고 로그를 남기십시오.

```python
logger.info("Selector prompt budget: %d chars, %d articles", len(user_prompt), len(candidates))
```

## 3. Architecture and Structure

### 3.1 `main.py`가 지나치게 많은 책임을 가짐

문제:

- 수집, 필터링, LLM 호출, 번역, 렌더링, 검증, 발송이 한 함수에 밀집되어 있습니다.
- dict에 `bucket`, `category`, `summary`, `description` 등이 암묵적으로 붙고 사라집니다.

왜 중요한가:

- 한 단계의 fallback이 다음 단계의 입력 계약을 깨뜨려도 타입/구조상 잡히지 않습니다.
- 테스트가 stage 단위가 아니라 smoke test 중심이 되기 쉽습니다.

수정안:

단기:

- stage별 함수로 분리합니다.

```python
def collect_stage(config, run_date) -> RawInputs: ...
def select_news_stage(config, articles, no_llm) -> SelectedNews: ...
def generate_ai_stage(config, markets, selected_news) -> AIOutputs: ...
def render_stage(...) -> RenderedArtifacts: ...
def verify_stage(...) -> GateResult: ...
```

중기:

- `SelectedArticle` dataclass를 도입합니다.

```python
@dataclass
class SelectedArticle:
    title: str
    url: str
    source: str
    summary: str
    published_date: str
    bucket: Literal["world", "korea"]
    category: Literal["economy", "politics", "security", "tech", "society", "corporate"]
    rank: int
    coverage_score: int = 1
```

### 3.2 검증과 자동 수정이 같은 모듈에 섞여 있음

문제:

- `quality_gates.py`는 검증하면서 동시에 후보를 교체합니다.

왜 중요한가:

- 어떤 제약이 hard constraint이고 어떤 제약이 soft preference인지 알기 어렵습니다.
- 자동 수정이 또 다른 품질 문제를 만들 수 있습니다.

수정안:

세 계층으로 분리하십시오.

1. `validators.py`: 순수 검증, error/warning 반환
2. `rerankers.py`: 후보 재정렬 및 보충
3. `quality_gates.py`: orchestration만 담당

예시:

```python
errors = validate_hard_constraints(world, korea)
if errors:
    world, korea = repair_selection(...)
errors = validate_hard_constraints(world, korea)
if errors:
    raise QualityGateError(errors)
warnings = validate_soft_constraints(world, korea)
```

### 3.3 provider 문서와 실제 구현이 불일치함

문제:

- README는 Claude/OpenAI도 지원한다고 하지만 `_get_provider()`는 Gemini만 지원합니다.
- 단, `pipeline/llm/claude.py`의 `ClaudeProvider`는 이미 구현되어 있습니다. 현재 결함은 Claude 전체 미구현이 아니라 provider dispatcher가 Claude를 연결하지 않는 문제입니다.
- OpenAI provider 구현은 현재 코드베이스에 없습니다.

왜 중요한가:

- 운영자가 config만 바꾸면 provider가 바뀐다고 오해할 수 있습니다.

수정안:

1. Claude는 `_get_provider()`에 분기를 추가해 즉시 연결합니다.

```python
if provider_name == "claude":
    from pipeline.llm.claude import ClaudeProvider
    return ClaudeProvider(model=model)
```

2. OpenAI는 구현 전까지 README와 config 주석에서 지원 문구를 제거합니다.

권장:

- 단기적으로 Claude dispatcher를 연결하고, OpenAI는 문구를 제거하십시오. 이후 OpenAI provider를 실제 구현할 때 README에 다시 추가하는 편이 정확합니다.

### 3.4 stub fallback이 프로덕션 핵심 실패를 숨김

문제:

- `_import_or_stub()`는 핵심 모듈이 없을 때 빈 결과를 반환하는 stub을 사용합니다.
- 현재 dashboard render 실패는 `main.py`에서 critical로 처리되어 즉시 실패합니다. 더 큰 문제는 briefing/email/sheets 쪽 fallback이 핵심 실패와 부가 기능 실패를 같은 방식으로 흐리게 만든다는 점입니다.

왜 중요한가:

- 브리핑 모듈 누락은 복구 가능한 문제가 아니라 배포 차단 사유입니다.
- email/sheets는 부가 delivery이므로 best-effort로 남겨도 되지만, 그 경우에도 failure summary를 남겨야 합니다.

수정안:

- briefing/render/verify production path에서는 stub fallback을 제거합니다.
- email/sheets는 명시적으로 `optional delivery`로 분리하고, 실패 시 오류 수집 또는 운영 알림에 포함합니다.
- `--dev-graceful` 같은 명시 옵션에서만 stub을 허용합니다.

```python
def _import_required(module_path, func_name):
    import importlib
    mod = importlib.import_module(module_path)
    return getattr(mod, func_name)
```

## 4. Features

### 4.1 EventKey 기반 dedup은 미완성 기능임

문제:

- README는 3-stage dedup과 EventKey를 설명하지만, 현재 메인 경로는 pre-LLM URL/topic dedup 위주입니다.
- `build_event_key()`, `deduplicate_by_event_key()` 등은 실제 pipeline에서 연결되지 않습니다.

왜 중요한가:

- 유지보수자는 이미 구현된 기능으로 착각할 수 있습니다.
- 미사용 코드가 품질 문제 추적을 어렵게 합니다.

수정안:

선택지 A: 기능 제거

- README의 EventKey 언급 제거
- `event_key_enabled` 설정 제거
- 미사용 `AIResult`, `ProcessedArticle` 경로 정리

선택지 B: 실제 연결

- 기사 body/summary 분석 단계 추가
- event metadata 추출
- `build_event_key()` 생성
- trend snapshot에 event key 저장
- cross-run dedup에서 event key 사용

권장:

- 현재 핵심 문제는 curation 품질이므로, 단기적으로는 문서와 설정에서 제거하는 것이 낫습니다.

### 4.2 월간 모드는 기능처럼 노출되지만 실제로는 daily 재사용임

문제:

- `--brief-type monthly`는 30일 lookback을 적용할 뿐 월간 요약 구조가 없습니다.

왜 중요한가:

- 월간 리캡은 단순 daily 확장이 아니라 기간 집계, 주요 테마, 월간 성과, 다음 달 이벤트가 필요합니다.

수정안:

단기:

- CLI choices에서 `monthly` 제거 또는 `NotImplementedError` 처리

중기:

- weekly처럼 snapshot 기반 monthly runner 구현

```python
def run_monthly_recap(config, run_date, output_dir, no_llm=False):
    snapshots = load_monthly_snapshots(...)
    monthly_data = build_monthly_recap_data(...)
    return render_monthly_recap(...)
```

### 4.3 운영 알림 기능이 부족함

문제:

- 실패는 로그와 JSON 파일에 남지만, 운영자가 즉시 알기 어렵습니다.

왜 중요한가:

- 매일 자동 실행되는 브리프는 조용한 실패가 누적되기 쉽습니다.

수정안:

- GitHub Actions summary에 gate 결과를 기록합니다.
- 실패 시 발신자에게만 failure email을 보냅니다.

예시:

```python
def write_github_summary(gate_result):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as f:
        f.write("## Daily Brief Verification\n")
        f.write(f"- passed: {gate_result.passed}\n")
        for error in gate_result.errors:
            f.write(f"- ERROR: {error}\n")
```

### 4.4 AI HTML 안전 처리 부족

문제:

- AI markdown 결과가 HTML로 변환된 뒤 `Markup`으로 safe 처리됩니다.

왜 중요한가:

- 현재 Python Markdown의 기본 동작과 모델 출력 특성을 고려하면 즉시 배포 차단급 위험으로 단정할 문제는 아닙니다.
- 다만 외부 뉴스 텍스트가 prompt에 들어가고, 변환된 HTML을 `Markup`으로 신뢰하므로 `javascript:` URL, 의도치 않은 raw HTML, attribute injection에 대한 심층 방어가 필요합니다.

수정안:

P2 보강으로 `bleach` allowlist를 적용합니다.

```python
import bleach

ALLOWED_TAGS = ["p", "h2", "h3", "ul", "ol", "li", "strong", "em", "br"]

def _sanitize_insight_html(html: str) -> str:
    return bleach.clean(html, tags=ALLOWED_TAGS, attributes={}, strip=True)
```

`_md_to_html()` 반환 직후 sanitize하십시오.

## 5. Prompt Engineering Specifics

### 5.1 selector output schema가 강제되지 않음

문제:

- prompt는 JSON array만 반환하라고 하지만, 실제 파싱은 regex 기반 sanitize 후 `json.loads()`입니다.
- schema validation이 없어 필드 누락, 잘못된 rank, bucket/category 오염을 후처리에서 조용히 보정합니다.

왜 중요한가:

- 모델이 규칙을 어겼는지, 후처리가 품질을 바꿨는지 추적하기 어렵습니다.

수정안:

`complete_json()`과 schema 검증을 사용하십시오.

```python
def validate_selector_payload(payload):
    if not isinstance(payload, list):
        raise ValueError("selector output must be a list")
    for item in payload:
        if not isinstance(item.get("index"), int):
            raise ValueError("missing index")
        if item.get("bucket") not in {"world", "korea"}:
            raise ValueError("invalid bucket")
        if item.get("category") not in _VALID_CATEGORIES:
            raise ValueError("invalid category")
```

Gemini provider에서는 `response_mime_type="application/json"`을 selector에도 사용하십시오.

### 5.2 "up to" 후보 수 지시가 downstream padding을 유발함

문제:

- selector prompt는 bucket별 후보를 `up to candidate_n`만 선택하라고 합니다.
- 모델이 적게 반환하면 휴리스틱 후보가 padding됩니다.

왜 중요한가:

- 가장 중요한 품질 판단을 LLM이 아니라 fallback 휴리스틱이 하게 됩니다.

수정안:

prompt를 다음처럼 바꾸십시오.

```text
Return exactly N world candidates and exactly N korea candidates if enough valid candidates exist.
If fewer valid candidates exist, return all valid candidates and include:
{"error": "insufficient_valid_korea_candidates", "available": 3}
```

출력 schema도 배열 단독 대신 object로 바꾸는 것을 권장합니다.

```json
{
  "world": [...],
  "korea": [...],
  "warnings": []
}
```

### 5.3 브리핑 prompt의 형식 지시가 렌더러와 완전히 계약화되어 있지 않음

문제:

- prompt는 Markdown headings를 요구하지만, 렌더러는 어떤 heading이 와도 HTML로 변환합니다.
- 섹션 누락 또는 제목 변형을 구조적으로 막지 않습니다.

왜 중요한가:

- 모델이 제목을 바꾸거나 섹션을 합쳐도 검증은 길이만 통과할 수 있습니다.

수정안:

인사이트를 Markdown이 아니라 JSON으로 생성한 뒤 렌더러에서 Markdown/HTML을 조립하십시오.

```json
{
  "key_insight": ["...", "..."],
  "market_overview": {
    "korea": ["...", "..."],
    "us": ["...", "..."]
  },
  "cross_market_signals": [
    {"signal": "...", "meaning": "..."}
  ]
}
```

렌더러가 이 구조를 고정된 heading으로 출력하게 하면 포맷 안정성이 올라갑니다.

### 5.4 번역 prompt는 JSON shape만 요구하고 언어 검증은 약함

문제:

- 번역 결과에 한국어/영어가 섞여도 regex 기반 언어 검증만 수행합니다.
- 영어 제목 안에 고유명사만 있어도 통과/실패가 부정확할 수 있습니다.

왜 중요한가:

- 번역 품질이 낮아도 구조적으로는 정상처럼 보입니다.

수정안:

번역 prompt에 다음 필드를 추가하십시오.

```json
{
  "id": 0,
  "title": "...",
  "summary": "...",
  "language": "ko",
  "unchanged_terms": ["KOSPI", "Fed"]
}
```

검증에서는 `language` 필드를 신뢰하지 말고, 제목/요약 모두에 대해 최소 언어 비율을 확인합니다.

```python
def korean_ratio(text):
    chars = [c for c in text if c.isalpha()]
    if not chars:
        return 0
    return sum(1 for c in chars if "가" <= c <= "힣") / len(chars)
```

## 구현 체크리스트

### 1차 PR: 배포 안전장치

- [ ] `main.py`에서 daily gate 실패 시 `return 1`
- [ ] weekly gate 실패 시 `return 1`
- [ ] `pipeline/verify/gate.py`에서 broken check를 warning이 아니라 error로 처리
- [ ] `passed = len(errors) == 0 and checks_passed == checks_run` 조건 적용
- [ ] GitHub Actions에 `python -m pytest -q` 단계 추가
- [ ] gate 실패 시 배포 step이 실행되지 않는지 확인

### 2차 PR: 뉴스 품질 게이트 안정화

- [ ] hard constraint predicate 추가
- [ ] Korea 후보 재삽입 방지
- [ ] low-value 후보 재삽입 방지
- [ ] category balance를 soft constraint로 변경
- [ ] final selection validator 추가
- [ ] 관련 테스트 추가

### 3차 PR: LLM 실패/번역 실패 처리

- [ ] 빈 briefing 반환 시 예외 발생
- [ ] 번역 strict mode 추가
- [ ] invalid JSON 번역 응답 테스트 추가
- [ ] 최종 HTML에 원문 미번역 world/korea가 노출되지 않는지 테스트

### 4차 PR: 토큰 비용 절감

- [ ] selector candidate pre-rank 추가
- [ ] selector 입력 후보 수 제한
- [ ] task별 model config 분리
- [ ] prompt budget helper 추가
- [ ] LLM 호출별 prompt char/token 추정 로그 추가

### 5차 PR: 구조화 출력 전환

- [ ] selector output object schema 적용
- [ ] briefing JSON schema 적용
- [ ] Markdown heading 의존 제거
- [ ] insight renderer를 schema 기반으로 변경

### 6차 PR: 문서/설정 정합성 정리

- [ ] `_get_provider()`에 Claude dispatcher 추가
- [ ] OpenAI provider 구현 전까지 README의 OpenAI 지원 문구 제거
- [ ] EventKey가 실제 연결되지 않았다면 README/config에서 EventKey 지원 문구 제거
- [ ] monthly mode를 숨기거나 명시적으로 미구현 처리

## 권장 테스트 시나리오

### 뉴스 선별

- 한국 언론의 이란/미국/중국 기사 → `world`
- 한국 언론의 한국은행/코스피/부동산/삼성 기사 → `korea`
- 한국 직접 영향이 있는 국제 기사 → `korea` 허용 여부 명시 테스트
- 스포츠/날씨/연예/부고/인사발령 → 최종 결과 제외
- 같은 source가 Korea에 2개 이상 들어오지 않는지 확인

### 번역

- world KO 페이지 제목에 한국어가 충분히 포함되는지 확인
- korea EN 페이지 제목에 영어가 충분히 포함되는지 확인
- invalid JSON 응답 시 strict mode에서 실패하는지 확인

### 인사이트

- market data와 반대 방향 서술 시 실패
- 휴장일에 "오늘 상승/하락" 서술 시 실패
- 빈 인사이트 또는 fallback message가 HTML에 있으면 실패

### 배포

- gate 실패 시 `python main.py` 종료 코드가 `1`
- 검증 check 함수가 예외를 던질 때도 `GateResult.passed`가 `False`
- GitHub Actions에서 deploy step이 실행되지 않음

## 삭제 또는 축소 후보

- EventKey dedup: 실제 연결 전까지 문서/설정에서 제거
- monthly mode: 구현 전까지 CLI에서 숨김
- `_import_or_stub()` production fallback: 제거
- README의 OpenAI 지원 문구: 실제 provider 구현 전까지 제거
- Claude 지원 문구: `pipeline/llm/claude.py`는 이미 있으므로 `_get_provider()` dispatcher 연결 후 유지

## 완료 기준

다음 조건을 만족하면 이번 감사의 핵심 리스크가 해소된 것으로 봅니다.

- 검증 실패 산출물이 Pages에 배포되지 않습니다.
- 검증 check 자체가 깨졌을 때 통과로 계산되지 않습니다.
- Korea 섹션에 국제 뉴스가 들어가는 테스트가 실패하지 않습니다.
- 번역 실패가 원문 노출로 이어지지 않습니다.
- 빈 AI 인사이트가 렌더링되지 않습니다.
- selector LLM 입력 후보 수가 상한으로 제한됩니다.
- README의 기능 설명과 실제 코드가 일치합니다.
