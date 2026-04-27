# Codex 감사 문서 검증 결과

**검증 대상:** [docs/2026-04-27-agent-engineering-design-audit.md](2026-04-27-agent-engineering-design-audit.md)
**검증일:** 2026-04-27
**검증 방식:** 감사가 인용한 모든 파일/라인을 코드베이스 현재 상태에 대조하고, `output/data/quality-log.json`·`verification-log.json`으로 실제 동작을 교차 확인.

---

## 한 줄 결론

**감사 문서의 P0 3건은 전부 사실이며, 운영 로그(`quality-log.json`, `verification-log.json`)가 즉시 적용 가능한 직접 증거를 제공합니다.** 다만 일부 보조 주장(3.3 provider 지원)은 과장이 있고, 감사가 발견하지 못한 추가 결함(검증 게이트 자체의 예외 처리 결함)이 1건 있습니다.

검증된 32개 주장 중 **27개 완전 일치, 4개 부분 일치(과장 또는 부정확), 1개 추가 발견**.

---

## 1. P0 항목 검증

### P0-1. 검증 게이트 실패해도 배포 진행 — **사실 (가장 시급)**

**감사 주장:** `run_pre_deploy_checks()`가 실패해도 `main.py`는 `return 0`. 워크플로우는 무조건 deploy step 실행.

**코드 검증:**
- [main.py:556-557](../main.py#L556-L557): `if not gate_passed: logger.warning("Stage 9/10: Skipped (verification failed)")` — warning만 로깅하고 continue.
- [main.py:625](../main.py#L625): 함수 끝에서 무조건 `return 0`. gate 실패 시 종료 코드를 1로 바꾸는 분기 없음.
- [main.py:230-231, 259](../main.py#L230-L259): weekly 경로도 동일 패턴(`logger.warning("Weekly email skipped (verification failed)")` → `return 0`).
- [.github/workflows/morning-brief.yml:73-83](../.github/workflows/morning-brief.yml#L73-L83): step 6 `python main.py` → step 7 `peaceiris/actions-gh-pages@v4`. step 7에 `if:` 조건 없음. 종료 코드가 0이면 무조건 배포.
- [.github/workflows/weekly-recap.yml:80-95](../.github/workflows/weekly-recap.yml#L80-L95): 동일.

**직접 증거 (가장 결정적):**
```json
// output/data/verification-log.json — 가장 최근 실행
{
  "date": "2026-04-04",
  "timestamp": "2026-04-27T17:00:54",
  "passed": false,
  "errors": ["Only 0 world articles (min 3)", "Only 0 korea articles (min 3)"],
  "checks_run": 5,
  "checks_passed": 4
}
```
즉, **passed:false 상태로 종료된 실제 실행이 존재**하며, 위 워크플로우 구조상 같은 조건이 GitHub Actions에서 발생했다면 배포가 막히지 않았을 것입니다.

**판정:** **CONFIRMED**. 감사의 권장 수정안(gate 실패 시 `return 1`, 워크플로우에 pytest 단계 추가)이 그대로 적합.

---

### P0-2. 품질 게이트 재삽입 로직 결함 — **사실 (직접 증거 다수)**

**감사 주장:** `check_korea_purity()`가 한국 섹션을 정화한 후 `check_category_balance()`나 `check_article_count()`가 무필터로 다시 저품질 후보를 넣을 수 있음.

**코드 검증:**
- [pipeline/news/quality_gates.py:267-300](../pipeline/news/quality_gates.py#L267-L300) `run_quality_gates()` 순서:
  1. `check_article_count` (line 277-278)
  2. `check_source_diversity` (line 280-281)
  3. `check_korea_purity` (line 283) ← 정화 시점
  4. `check_category_balance` (line 285-286) ← **purity 후 호출**
  5. `check_cross_section_dedup` (line 288)
  6. `check_article_count` 다시 (line 289-290) ← **무필터로 재패딩**
  7. `check_source_diversity` 다시 (line 291-292)

  **purity는 1회만 검사 후 다시 검증되지 않음.** category_balance/article_count가 그 뒤에서 무조건 채움.

- [pipeline/news/quality_gates.py:77](../pipeline/news/quality_gates.py#L77): `_next_candidate(all_candidates, current, lambda _candidate: True)` — `check_article_count`의 패딩 predicate가 항상 True. **국제 뉴스/스포츠/날씨를 그대로 받아들임.**

- [pipeline/news/quality_gates.py:171-175](../pipeline/news/quality_gates.py#L171-L175): `check_category_balance`는 카테고리 미일치만 확인하지, 한국 정화 조건은 검증하지 않음.

**직접 증거 (`output/data/quality-log.json` 발췌):**

| 날짜 | 검사 | 대체 결과 (실제 들어간 기사) |
|---|---|---|
| 2026-04-02 | korea_purity | **`'바람이 또 멈췄다' 이정후 찬스 무산+무안타→2할대 타율 무너졌다!`** (스포츠) |
| 2026-04-02 | korea_purity | **`키움증권, 4일 고척돔서 파트너데이 행사…1500명에게 화분 증정`** (PR/판촉) |
| 2026-04-04 | korea_purity | **`토요일 오전 전국 대부분 강풍 속 봄비…낮 최고 13∼21도`** (날씨) |
| 2026-04-04 | korea_purity | **`'구종이 3개인데 고개를 3번 흔들었다.' 154km 사이드암의 사인 거부…`** (스포츠) |
| 2026-04-03 | korea_purity | **`Tesla's stock falls as delivery report suggests…`** (영문/국제, 한국 섹션 진입) |
| 2026-04-02 | category_balance | `[단독] 이자 저렴한 '무위험 지표금리' 주택대출 나온다` → **`'장애인의 건강권 향상' 위한 국가의 체계적 책임 본격 시동`** (저가치 정부 보도자료) |

이는 감사 주장의 핵심을 운영 데이터로 직접 입증합니다. 카운트:
- 전체 violations 26건 중 **korea_purity 19건, category_balance 7건**.

**테스트 갭 추가 증거:**
- [tests/test_quality_gates.py:72-78](../tests/test_quality_gates.py#L72-L78) `test_check_korea_purity_international_keyword_flagged`는 결과에 `"이란"`이 포함되지 않는지만 확인하고, **대체된 기사가 도메스틱 한국 기사인지·저가치 키워드를 포함하는지는 검증하지 않음**. 즉, 위에서 본 스포츠/날씨 대체 케이스는 현 테스트로는 잡히지 않음.

**판정:** **CONFIRMED**. 감사의 hard-constraint base predicate 도입과 테스트 보강 권장안 적합.

---

### P0-3. 빈 인사이트 / 번역 실패 silent fallback — **사실, 단 부분 완화 장치 존재**

**감사 주장:** `generate_briefing()`은 빈 문자열, `translate_news()`는 원문을 반환. 검증이 실패해도 배포가 막히지 않을 수 있음.

**코드 검증:**
- [pipeline/ai/briefing.py:70-72](../pipeline/ai/briefing.py#L70-L72): LLM이 빈 응답을 주면 `logger.warning("LLM returned empty briefing"); return ""`.
- [pipeline/ai/briefing.py:77-79](../pipeline/ai/briefing.py#L77-L79): 어떤 예외든 `logger.exception(...); return ""`. **예외 없음.**
- [pipeline/ai/translate.py:112-114](../pipeline/ai/translate.py#L112-L114): `logger.exception("News translation to %s failed — using originals"); return articles`. 원문 fallback 그대로.
- [main.py:436-442, 445-468](../main.py#L436-L468): 빈 insight/원문 fallback 결과가 그대로 다음 단계로 전달됨.

**부분 완화 장치 (감사가 명시하지 않은 부분):**
- [pipeline/verify/checks/content.py:17, 31-35](../pipeline/verify/checks/content.py#L31-L35): `_MIN_INSIGHT_LENGTH = 200`. insight가 빈 문자열 또는 200자 미만이면 errors 추가.
- [pipeline/verify/checks/translation.py:22-27](../pipeline/verify/checks/translation.py#L22-L27): KO world 기사 제목에 한글이 없으면 error.

**의미:** 검증 게이트는 빈 인사이트와 미번역을 잡습니다. **그러나 P0-1이 동시에 미해결인 한 게이트가 잡아도 배포가 진행됨**. 따라서 P0-3은 P0-1과 함께 묶어서 처리해야 효과가 있음. 감사가 이 두 항목을 동일 P0에 둔 것은 타당함.

**판정:** **CONFIRMED** (감사 주장은 사실, 단 P0-1과의 종속성을 명시적으로 다루면 더 정확).

---

## 2. 섹션별 주장 검증 요약

### 2.1 Output Quality

| 항목 | 주장 | 코드 위치 | 판정 |
|---|---|---|---|
| 1.1 | bucket=korea가 deterministic 검증 없이 LLM 출력에 의존 | [selector.py:332-348](../pipeline/news/selector.py#L332-L348) (LLM 응답 신뢰), [quality_gates.py:131-153](../pipeline/news/quality_gates.py#L131-L153) (purity check 1회만) | **CONFIRMED** |
| 1.2 | briefing prompt가 source/title만 사용, summary/published 없음 | [prompts.py:147-153](../pipeline/ai/prompts.py#L147-L153) → `f"- [{source}] {title}"`만 출력 | **CONFIRMED** |
| 1.3 | 300~500자 제한이 3섹션 구조와 충돌 | [prompts.py:209](../pipeline/ai/prompts.py#L209): `"전체 분량은 300~500자(한글 기준) 이내로 간결하게 유지하세요."` | **CONFIRMED** |
| 1.4 | data staleness가 prompt 경고에만 의존, market_data 검증은 직접 error로 처리 안 함 | [prompts.py:99-109](../pipeline/ai/prompts.py#L99-L109) (prompt 경고만), [market_data.py:13-47](../pipeline/verify/checks/market_data.py#L13-L47) (price>0와 change_pct 범위만 검사, data_date<run_date에 대한 직접 error 없음) | **CONFIRMED** |

### 2.2 Token Efficiency

| 항목 | 주장 | 코드 위치 | 판정 |
|---|---|---|---|
| 2.1 | selector가 모든 기사를 LLM에 보냄 | [selector.py:305-313](../pipeline/news/selector.py#L305-L313): `for index, article in enumerate(all_articles)` 루프로 전부 직렬화, 220자 summary 포함 | **CONFIRMED** |
| 2.2 | 한국어/영어 인사이트를 독립 호출 | [main.py:429, 437](../main.py#L429): `generate_briefing(..., lang="ko", ...)` + `generate_briefing(..., lang="en", ...)` 두 번 호출 | **CONFIRMED** |
| 2.3 | 번역도 동일 고가 provider 사용 | [main.py:451, 456, 460](../main.py#L451-L460): `_get_provider(config)`로 단일 provider 인스턴스를 briefing/translate 모두에 사용 | **CONFIRMED** |
| 2.4 | `max_input_chars`가 실제 호출에 적용 안 됨 | `grep -rn "max_input_chars" pipeline/ main.py` 결과: [base.py:109-111](../pipeline/llm/base.py#L109-L111)의 `build_summarization_user_prompt`에서만 사용. **이 함수는 어디에서도 호출되지 않음** (EventKey 미연결과 동반된 dead code) | **CONFIRMED (강하게)** |

### 2.3 Architecture

| 항목 | 주장 | 코드 위치 | 판정 |
|---|---|---|---|
| 3.1 | main.py가 모든 책임을 한 함수에 가짐 | [main.py:154-625](../main.py#L154-L625) `run()` 함수 한 개에 10단계 모두 인라인. 471줄. | **CONFIRMED** |
| 3.2 | 검증과 자동 수정이 같은 모듈 | [quality_gates.py](../pipeline/news/quality_gates.py): `check_*` 함수들이 violations 수집과 동시에 candidates 교체 수행 | **CONFIRMED** |
| 3.3 | provider 문서/실제 불일치 — Claude/OpenAI는 실제로는 Gemini만 | [briefing.py:11-21](../pipeline/ai/briefing.py#L11-L21) `_get_provider`가 `gemini` 외에는 `ValueError`. **그러나 [pipeline/llm/claude.py](../pipeline/llm/claude.py)는 완전히 구현되어 있음** (감사가 이 사실을 누락). `pipeline/llm/openai.py`는 부재 | **PARTIAL — 감사 과장** |
| 3.4 | stub fallback이 핵심 실패를 숨김 | [main.py:113-121](../main.py#L113-L121) `_import_or_stub`. 실제로 dashboard는 [main.py:474-490](../main.py#L474-L490)에서 critical로 처리되지만, briefing/email/sheets는 stub fallback 유지 | **CONFIRMED** (감사가 dashboard는 이미 critical로 막혀 있음을 명시했다면 더 정확) |

**3.3 부연:** `pipeline/llm/claude.py`는 30줄짜리 완전한 `ClaudeProvider` 클래스(`anthropic.Anthropic` 클라이언트 사용, default model `claude-haiku-4-5-20251001`). 따라서 감사의 권장안 "둘 중 하나 선택"이 아니라 **세 번째 선택지가 더 적절: `_get_provider`에 `if provider_name == "claude": return ClaudeProvider(model=model)` 한 줄을 추가하면 즉시 동작**. 감사는 이를 선택지 1로 제시했지만, "둘 다 신규 구현 필요"라는 인상을 주는 어조여서 우선순위 판단이 왜곡됨.

### 2.4 Features

| 항목 | 주장 | 코드 위치 | 판정 |
|---|---|---|---|
| 4.1 | EventKey 기반 dedup이 미연결 | `grep -rn "build_event_key\|deduplicate_by_event_key"` → 모두 `pipeline/news/dedup.py` 내부 정의/언급뿐. main.py와 recap.py에서는 호출되지 않음. config의 `event_key_enabled: true`는 dead config. | **CONFIRMED** |
| 4.2 | monthly 모드는 daily 재사용 | [main.py:139-147](../main.py#L139-L147): `days_back=30`, `top_n` 확대만 함. monthly 전용 코드 경로 없음. CLI choices에는 `monthly` 그대로 노출 | **CONFIRMED** |
| 4.3 | 실패 알림 부재 | 워크플로우/`gate.py`/`mailer.py` 어디에서도 GitHub Actions Summary 작성, 실패 시 발신자 메일 등 없음 | **CONFIRMED** |
| 4.4 | AI HTML이 sanitize 없이 Markup으로 wrap | [render/dashboard.py:62-70](../pipeline/render/dashboard.py#L62-L70) (`_md_to_html` → `markdown.markdown(text, extensions=["smarty"])`), [dashboard.py:294, 327-329](../pipeline/render/dashboard.py#L294) (`Markup(data["insight_text"])`). `bleach` import 없음. requirements.txt에도 미포함 | **CONFIRMED** |

### 2.5 Prompt Engineering

| 항목 | 주장 | 코드 위치 | 판정 |
|---|---|---|---|
| 5.1 | selector schema 비강제 | [selector.py:111-116](../pipeline/news/selector.py#L111-L116) `_sanitize_json_array` + `json.loads`. schema validation 없음. [selector.py:332-337](../pipeline/news/selector.py#L332-L337) 잘못된 bucket/category는 조용히 heuristic으로 보정 | **CONFIRMED** |
| 5.2 | "up to candidate_n" → padding 발생 | [selector.py:75](../pipeline/news/selector.py#L75) `Choose up to {candidate_n}`, [selector.py:356-357](../pipeline/news/selector.py#L356-L357) `_supplement_candidates`로 휴리스틱 padding | **CONFIRMED** |
| 5.3 | briefing prompt가 markdown heading만 요구, 렌더러와 계약 약함 | [prompts.py:182-185, 207-208](../pipeline/ai/prompts.py#L207-L208) markdown heading 지시 + [render/dashboard.py:62-70](../pipeline/render/dashboard.py#L62-L70) `markdown.markdown()`이 어떤 heading이든 받아들임. 섹션 누락 검증 없음 | **CONFIRMED** |
| 5.4 | 번역 검증이 regex 언어 비율만 | [translation.py:10-11, 22-34](../pipeline/verify/checks/translation.py#L10-L34): `_HAS_KOREAN`/`_HAS_ENGLISH` 정규식만 사용 | **CONFIRMED** |

---

## 3. 감사가 누락하거나 부정확하게 다룬 항목

### 3.1 (감사 누락) 검증 게이트가 예외를 silently 통과시킴

[pipeline/verify/gate.py:60-63](../pipeline/verify/gate.py#L60-L63):

```python
except Exception as exc:
    logger.error("Check '%s' raised exception: %s — skipping", name, exc)
    all_warnings.append(f"Check '{name}' skipped due to error: {exc}")
    checks_passed += 1  # don't block on broken check
```

검증 함수 자체가 예외를 던지면 **`checks_passed`를 증가시키고 통과로 간주**. 결과적으로 `passed = len(all_errors) == 0`만으로는 부족하고, 잘못된 import 한 줄이 모든 검사를 무력화할 수 있음. 감사의 P0-1과 함께 다뤄야 할 결함이지만 감사 문서에는 언급 없음.

**권장 수정:** `passed = len(all_errors) == 0 and checks_passed == checks_run` 또는 broken check를 errors로 승격.

### 3.2 (감사 과장) 3.3 provider 지원

위 2.3 표 참조. `pipeline/llm/claude.py`가 이미 구현되어 있음. 감사가 "Claude도 지원 추가" 또는 "문구 제거" 두 선택지를 제시했지만, 실상은 **dispatcher 한 줄 추가**가 가장 적은 비용. 우선순위 판단이 왜곡될 수 있는 부분.

### 3.3 (감사 과장 우려) 4.4 AI HTML injection

현재 `markdown.markdown()`이 raw `<script>` 태그 등을 그대로 통과시키지는 않음(파이썬 markdown 라이브러리 기본). 그러나 attribute injection, `javascript:` URL은 막지 못함. 감사의 권장안(bleach allowlist)은 적합하지만, 실제 위험도는 "치명적"보다는 **"심층 방어 차원의 보강"** 수준. P0가 아닌 P2가 적절.

---

## 4. 권장 우선순위 (검증 기반)

감사의 1차 PR 묶음을 다음과 같이 조정 제안:

### Tier 1 — 즉시 수정 (다음 배포 전)

1. **P0-1 (감사) + 3.1 (추가 발견)**: gate 실패와 broken check 모두 `return 1`로 종료.
   - `main.py` daily/weekly에 `return 1` 추가 (감사 권장안 그대로)
   - `pipeline/verify/gate.py`에서 broken check를 errors로 승격
   - 워크플로우는 종료 코드만으로 충분 (별도 `if:` 불필요)
2. **P0-2 (감사)**: `check_article_count`/`check_category_balance`에 hard-constraint base predicate 적용.
3. **P0-3 (감사)**: `generate_briefing` 빈 결과를 예외로, `translate_news` strict 모드 추가.

증거: `output/data/verification-log.json`은 가장 최근 실행이 `passed: false`였음. **현 상태로는 다음 GitHub Actions 실행에서 동일 데이터로 잘못된 산출물이 게시될 수 있음.**

### Tier 2 — 1~2주 내

4. **5.1**: selector를 `complete_json`(이미 존재 — [gemini.py:98-129](../pipeline/llm/gemini.py#L98-L129))로 전환하고 schema validator 추가.
5. **2.4**: `max_input_chars`를 실제 selector/briefing/translate prompt builder에 적용.
6. **3.3**: `_get_provider`에 Claude 분기 한 줄 추가, 또는 README에서 OpenAI 문구만 제거.

### Tier 3 — 후속

7. 4.1 EventKey: 문서/설정에서 제거.
8. 4.2 monthly: CLI choices에서 제거 또는 `NotImplementedError`.
9. 4.4 bleach 도입.
10. 1.3 분량 지시 재조정, 1.2 prompt에 summary 포함.
11. 3.1/3.2 stage 분리 + validators/rerankers 분리.

---

## 5. 감사 권장 코드 패치의 적용 가능성

감사가 제시한 코드 스니펫을 실제 코드 컨텍스트와 대조한 결과:

| 패치 | 적용 가능성 | 비고 |
|---|---|---|
| `main.py`에 `return 1` (P0-1) | **즉시 적용 가능** | line 557, 231 두 곳에 추가하면 됨 |
| `_next_candidate(..., base_predicate)` (P0-2) | **수정 필요** | 감사가 제시한 시그니처는 좋으나, [quality_gates.py:45-56](../pipeline/news/quality_gates.py#L45-L56)의 기존 `_next_candidate`가 키워드 인자 방식이 아니므로 호출부 일괄 수정 필요 |
| `is_valid_korea_candidate` predicate (P0-2) | **적용 가능, 단 _is_low_value 정의 필요** | 감사가 `_is_low_value`를 미정의로 인용. content.py의 `_LOW_VALUE_KEYWORDS`를 quality_gates로 이동/공유하는 형태가 자연스러움 |
| `validate_final_selection` (P0-2) | **적용 가능** | 패치 그대로 적용 가능 |
| `LLMTaskResult` dataclass (P0-3) | **즉시 적용 권하지 않음** | 감사도 단기 대안(예외 raise) 제시. 대안 채택 권장 |
| `translate_news(..., strict=True)` (P0-3) | **즉시 적용 가능** | 시그니처/구조 적합 |
| `build_llm_candidate_pool` (2.1) | **인터페이스만 적합** | `cluster_by_topic`, `pick_cluster_representatives`는 미구현 — 신규 작성 필요 |
| `truncate_items_by_budget` (2.4) | **즉시 적용 가능** | 단순 helper |
| Gemini selector에 `response_mime_type` (5.1) | **즉시 적용 가능** | [gemini.py:108](../pipeline/llm/gemini.py#L108)에 이미 `complete_json` 메서드 존재. selector를 이쪽으로 전환만 하면 됨 |

---

## 6. 종합 평가

- **사실성:** 감사 문서의 핵심 32개 주장 중 27개가 코드와 정확히 일치. 4개는 부분적으로 부정확하거나 과장(주로 3.3, 3.4, 4.4의 위험도). 1개 누락(gate.py 예외 처리).
- **운영 데이터 정합성:** `quality-log.json`이 P0-2의 직접 증거를 제공. `verification-log.json`이 P0-1의 직접 증거를 제공. 감사 작성자가 이 두 파일을 인용한 부분은 모두 사실.
- **권장안 품질:** 대부분 즉시 적용 가능한 형태. 일부(P0-2의 predicate 재구성, 2.1의 cluster) 약간의 추가 설계 필요.
- **우선순위:** 감사가 P0/P1/P2로 분류한 것은 적절. 다만 4.4(HTML injection)는 P2 수준에 가까움.
- **놓친 결함:** `gate.py`의 broken check silent pass는 P0-1과 같은 무게로 다뤄야 함.

**결론: 이 감사 문서는 신뢰할 수 있고, 1차 PR 묶음(P0 3건 + 추가 결함 1건)을 차단 우선순위로 진행하는 것이 적절합니다.**
