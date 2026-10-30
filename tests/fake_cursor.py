"""ge cursor 실행기 시험용. 모델 호출 없음. GE_CURSOR 로 이 파일을 가리킨다. 프롬프트는 stdin."""
import json, re, sys

prompt = sys.stdin.read()
schema = {"type": "object"}
m = re.search(r"No prose, no code fences, no keys outside the schema\.\n(\{.*\})\s*$", prompt, re.S)
if m:
    schema = json.loads(m.group(1))


def fill(s):
    t = s.get("type")
    if t == "object":
        props = s.get("properties", {})
        return {k: fill(props[k]) for k in s.get("required", list(props)) if k in props}
    if t == "array":
        n = max(s.get("minItems", 1), 1)
        if "maxItems" in s:
            n = min(n, s["maxItems"])
        return [fill(s.get("items", {})) for _ in range(n)]
    if t == "string":
        return s["enum"][0] if "enum" in s else ("stub-claim-" * 4)[: max(s.get("minLength", 10), 10)]
    if t in ("number", "integer"):
        return 0
    if t == "boolean":
        return True
    return None


payload = fill(schema)
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "duration_ms": 5,
                  "result": "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```",
                  "session_id": "fake-cursor-session",
                  "usage": {"inputTokens": 12, "outputTokens": 5, "cacheReadTokens": 1, "cacheWriteTokens": 0}}))
