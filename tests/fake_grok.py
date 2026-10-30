"""ge grok 실행기 시험용. 모델 호출 없음. GE_GROK 로 이 파일을 가리킨다."""
import json, sys

schema = {"type": "object"}
args = sys.argv[1:]
for i, a in enumerate(args):
    if a == "--json-schema" and i + 1 < len(args):
        schema = json.loads(args[i + 1])
        break


def fill(s):
    t = s.get("type")
    if t == "object":
        props = s.get("properties", {})
        req = s.get("required", list(props))
        return {k: fill(props[k]) for k in req if k in props}
    if t == "array":
        n = max(s.get("minItems", 1), 1)
        if "maxItems" in s:
            n = min(n, s["maxItems"])
        return [fill(s.get("items", {})) for _ in range(n)]
    if t == "string":
        if "enum" in s:
            return s["enum"][0]
        return ("stub-claim-" * 4)[: max(s.get("minLength", 10), 10)]
    if t in ("number", "integer"):
        return 0
    if t == "boolean":
        return True
    return None


payload = fill(schema) if schema else {"ok": True}
print(json.dumps({
    "text": json.dumps(payload, ensure_ascii=False),
    "structuredOutput": payload,
    "usage": {"input_tokens": 11, "output_tokens": 7},
    "total_cost_usd": 0.0,
    "modelUsage": {"fake-grok": {"inputTokens": 11, "outputTokens": 7}},
    "sessionId": "fake-session",
}))
