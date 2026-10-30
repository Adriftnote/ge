# ge — AI 코딩 에이전트용 작업 그래프 코디네이터

[English](README.md)

**실험적 도구(experimental).** 개인 도구를 그대로 공개한다.

`ge` 는 일을 노드 그래프로 돌린다. 노드마다 워커 하나를 부르고, 워커 출력은 JSON 스키마에 맞아야 한다.
검사는 별도 노드가 하고, 모든 단계가 원장에 기록을 남긴다. 노드마다 다른 회사의 모델을 쓸 수 있다
(Claude, Codex, Cursor, Grok 의 명령줄 도구). 그래서 한 모델이 만들고 다른 모델이 검토한다.

```
patch (모델 A) ──► probe (임시 사본에서 테스트) ──► verify (모델 B, 항목별 판정) ──► apply (script, 사본에 적용)
```

잘못된 패치를 probe 가 막아 verify 와 apply 가 돌지 않은 런
(`./ge run examples/fix-and-test-bad.json --executor stub`, 원장에서 주요 칸만 남김):

```
{"event": "run_started", "spec": "fix-and-test-bad", "executor": "stub"}
{"event": "guard", "phase": "baseline", "ok": true, "problems": [], "checks": {"tests_pass": true}}
{"event": "node_started", "node": "patch"}
{"event": "guard", "phase": "preflight", "node": "patch", "ok": true, "problems": []}
{"event": "call", "node": "patch", "attempt": 1, "ok": true}
{"event": "node_done", "node": "patch", "ok": true}
{"event": "guard", "phase": "runtime_probe", "node": "patch", "ok": false, "problems": ["검사 실패 tests_pass: {\"why\": \"FAILED (failures=1)\"}"], "checks": {"tests_pass": false}}
{"event": "run_aborted", "node": "patch", "cause": "probe: 검사 실패 tests_pass: {\"why\": \"FAILED (failures=1)\"}"}
{"event": "run_done", "ok": false, "cause": "probe: 검사 실패 tests_pass: {\"why\": \"FAILED (failures=1)\"}"}
```

## 왜

여러 회사의 모델을 구독하고 있고, 이들이 같은 일을 나눠 맡게 하고 싶었다. 한 모델이 만들고 다른 모델이 결과를 검토한다.

그 전에 코딩 세션끼리 채팅하게 하는 MCP 서버 [yar-mcp-server](https://github.com/Adriftnote/yar-mcp-server) 를
만들었다. 내가 시험한 범위에서는 대화가 실제 작업으로 이어지지 않았고 토큰만 많이 들었다. 그래서 블렌더·후디니 같은
노드 편집기 방식으로 바꿨다. 앞 노드의 결과물이 다음 노드의 입력이 되고, 진행하는 동안 원본은 바꾸지 않는다.
노드 사이를 오가는 것은 대화가 아니라 결과물이다.

또 긴 한 문맥 안에서 모델이 열어 보지 않은 것을 확인했다고 넘어가는 일을 겪었다. 일을 노드로 나누고 검사 노드와
원장을 따로 두니 그런 경우가 보이게 됐다.

## 무엇을 하나

1. **명세(그래프)** — 노드와 엣지(`inputs`)를 적은 JSON 파일.
2. **노드마다 워커 하나** — 시도마다 워커 CLI 를 한 번 부른다. 출력은 노드의 스키마에 맞아야 하고,
   실패하면 오류를 붙여 다시 시도한다.
3. **preflight(입력 해시 확인)** — 계약(contract)이 있으면 워커를 부르기 전에 코디네이터가 입력 파일을 읽고
   sha256 을 대조한다. 바뀌었거나 워커가 닿지 못하면 워커를 부르지 않는다.
4. **만드는 노드와 검사 노드 분리** — 코디네이터는 rubric 을 verify 프롬프트에만 넣는다. 판정은 항목별
   (`pass` / `fail` / `unverifiable`) + 이유. probe(테스트·건수)가 모델이 판정하면 안 되는 항목을 채울 수 있다.
5. **apply 게이트** — `"apply": true` 노드는 verify 노드를 입력으로 받아야 하고(명세를 읽을 때 확인),
   모든 입력이 성공하고 판정이 모두 `pass` 일 때만 돈다(실행 때 확인).
6. **원장** — `.ge/runs/<run-id>/ledger.jsonl` 에 호출·해시·토큰·판정이 남는다. "런이 끝났다"와
   "검사가 통과시켰다"를 따로 적는다. `ge ledger` 는 역할별 합격률·재작업을 프롬프트+rubric+교훈 해시별로 보여 준다.

## 안전 경계

- 신뢰하는 명세와 스크립트만 실행한다. script·probe 노드는 내 권한으로 도는 일반 Python 이다.
- 비파괴는 기본 apply 의 동작이지 런타임 샌드박스가 아니다. 생성되는 `apply.py` 는 `GE_SRC` 로 준 사본
  (linked git worktree, 또는 `.ge-copy` 표식이 있는 폴더)에만 쓰고, 사본이 없거나 `verify.json` 이 없거나
  판정 중 `pass` 가 아닌 것이 있으면 멈춘다.
- 워커는 각 CLI 의 읽기 전용 모드로 돈다(실행기 표 참고). 그건 CLI 의 강제이지 ge 의 강제가 아니다.
- 원장에는 호스트 이름, 명세 절대경로, 워커 원문 위치가 남는다. 원장을 공유할 때는 지운다.

## 빠른 시작

Python 3.9+ 표준 라이브러리만 쓴다. 예시는 AI 없이 돈다.

```
git clone https://github.com/Adriftnote/ge && cd ge
./ge run examples/summarize.json        --executor stub
./ge run examples/fix-and-test.json     --executor stub
./ge run examples/fix-and-test-bad.json --executor stub    # probe 가 막는다, 종료코드 1
./ge progress
python3 -m unittest discover -s tests
```

`summarize`: 문서 요약 → 별도 검증 → script 가 Markdown 으로 렌더.
`fix-and-test`: 패치 제안 → probe 가 임시 사본에 적용해 테스트 → 검증 → acceptance gate → apply 가 고친 파일을
런 폴더에 쓴다. `--executor stub` 이면 노드는 `stub_output` 의 고정 출력을 낸다.

내 그래프 시작: `./ge new <이름> --cwd <레포> --target <파일>` 이 `graphs/<이름>.json` 과 `graphs/<이름>/` 을 만든다.
[docs/designing-graphs.md](docs/designing-graphs.md) 참고.

## 명세 형식

[README.md](README.md#spec-format) 의 예시를 본다. 프롬프트 안의 `{{input.<노드>}}` 는 그 노드 산출물,
`{{rubric}}` 은 rubric, `{{item}}` 은 fanout 항목, `{{<키>}}` 는 `--input 키=값`.
상대경로는 명세 폴더(또는 `graphs/` 폴더의 부모) 기준이다.

### 지원하는 스키마 부분집합

| 키 | 대상 |
|---|---|
| `type` (`object` `array` `string` `integer` `number` `boolean` `null` 중 하나. 목록 불가) | 전부 |
| `properties`, `required`, `additionalProperties: false` | object |
| `items`, `minItems`, `maxItems` | array |
| `minLength`, `maxLength`, `pattern` | string |
| `minimum`, `maximum`, `exclusiveMinimum`, `exclusiveMaximum` | integer, number |
| `enum`, `const` | 전부 |
| `title`, `description`, `$comment`, `default`, `examples` | 무시 (설명용) |

그 밖의 키(`oneOf`, `format`, `$ref` 등)가 있으면 조용히 무시하지 않고 `ge plan` / `ge run` 이 명세를 거부한다.

## 실행기

| `--executor` | 필요한 것 | 실행 방식 |
|---|---|---|
| `claude` (기본) | `claude` CLI | `claude -p` + `--json-schema`. 편집·셸·웹 도구 금지 |
| `codex` | `codex` CLI | `codex exec --sandbox read-only --output-schema` |
| `cursor` | `cursor-agent` CLI | `-p --mode ask`(읽기 전용). 스키마는 검증으로 강제 |
| `grok` | `grok` CLI | 헤드리스, 행동 도구 금지. 노드에 `tools: "read"` 면 파일을 읽는다 |
| `stub` | 없음 | 스키마를 채우거나 `stub_output` 을 돌려준다. 시험·예시용 |

각 CLI 는 `PATH`(또는 `GE_CLAUDE`, `GE_CODEX`, `GE_CURSOR`, `GE_GROK`)에서 찾는다. `ge` 는 각 CLI 의 로그인을
그대로 쓰고 API 키를 저장하지 않는다. `claude` 는 워커 환경에서 `ANTHROPIC_API_KEY` 를 빼서 CLI 로그인을 쓰게 한다.
노드의 `executor` 가 `--executor` 보다 우선하고, `--executor stub` 이면 모든 노드가 stub 이다.
테스트는 가짜 CLI 로 출력 해석만 확인한다. 현재 실제 CLI 와의 호환은 아래 표로 따로 적는다.

| CLI | 확인한 버전 | 날짜 | 결과 |
|---|---|---|---|
| claude | 2.1.289 | 2026-10-30 | 확인 못 함 (로컬 로그인 만료) |
| codex | codex-cli 0.148.0 | 2026-10-30 | `examples/summarize.json` 끝까지 실행. 검증 노드가 3개 항목을 불합격시켜 게이트가 멈춤(의도한 동작) |
| cursor-agent | — | — | 확인 못 함 (시험 PC 에 없음) |
| grok | 1.0.46 | 2026-10-30 | `examples/summarize.json` 통과 (`ok: true`) |

## 구조 규칙은 Alloy 로 확인한다

검사를 AI 검사자에게만 맡기지 않는다. `formal/ge_invariants.als` 는 의도한 안전 실행 정책의 작은 Alloy 6 모형이다.
정해진 범위 안에서 네 성질의 반례가 없음을 확인한다: apply 는 통과한(계약이 있으면 승인까지 된) verify 뒤에만 돈다 /
원본 폴더에 쓰는 노드가 없다 / preflight 에 실패한 노드는 불리지 않고 그 아래 노드도 돌지 않는다 / 실행은 비순환이고
verify 는 검사 대상 노드 뒤에 온다. 음성 대조로 apply 게이트를 빼면 반례가 나온다. 모형은 규칙을 확인하고, 코드가
규칙을 따르는지는 테스트가 확인한다. 모형 밖: 계약 없는 명세, `required: false` 노드, 임의의 script 노드
(`.als` 파일 주석 참고).

```
ALLOY_JAR=/path/to/org.alloytools.alloy.dist.jar ./ge check-model   # java 나 jar 가 없으면 종료코드 2
```

## 그 밖의 명령

`ge plan`, `ge new`, `ge workflows`, `ge stats --topologies`, `ge fabric`, `ge ledger`, `ge lesson`,
`ge contract-sha`, `ge approve`, `ge trace`, `ge note`, `ge report`, `ge check-model`.

`ge fabric` 은 명세를 쓸 때 [Fabric](https://github.com/danielmiessler/fabric) 프롬프트 패턴(MIT, Daniel Miessler)을
찾아 준다. 코퍼스는 번들하지 않고 `ge fabric --sync` 로 한 번 받는다. 구조(단계·검토 축·출력 모양)만 빌리고,
전체 점수 하나 대신 항목별 판정과 근거를 남긴다.

## 언어

CLI 메시지, 오류 문구, `ge new` 템플릿은 한국어다. 명령·플래그·JSON 키는 영어다.

## 한계

- 개인 도구다. 주로 내 일에서 썼고 인터페이스가 바뀔 수 있다.
- 검사하는 모델에도 사각이 있다. probe 와 게이트가 줄여 주지만 없애지는 못한다.
- 코디네이터는 판정의 item_id 와 rubric 항목을 대조하지 않는다. 빠진 항목은 프롬프트와 mechanical 항목으로만 잡는다.
- `ge` 가 1,900줄 한 파일이다. 실행기·보고 분리는 후속 과제다.
- 구현은 AI 코딩 에이전트와 함께 했다. 설계와 판단은 내가 했다.

## 배경

공개 전 내 기록에서 나온 숫자다(원자료 비공개, 표본 작음, 벤치마크 아님): 글로만 쓴 규칙은 4번 중 2번 지켜졌고,
자동 검사가 붙은 규칙은 13번 중 11번 지켜졌다. verify 노드가 있던 런 32개 중 11개에서 "실행 성공"과 검사 판정이
서로 맞지 않았다.

## 라이선스

MIT © 2026 Adriftnote
