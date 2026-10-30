"""ge codex 실행기 시험용. 모델 호출 없음. GE_CODEX 로 이 파일을 가리킨다."""
import json, os, sys, time

args = sys.argv[1:]
prompt = sys.stdin.read()
mode = os.environ.get("FAKE_CODEX_MODE", "success")
record = os.environ.get("FAKE_CODEX_RECORD")

if record:
    with open(record, "w", encoding="utf-8") as f:
        json.dump({"args": args, "cwd": os.getcwd(), "stdin": prompt, "env_cwd": os.environ.get("GE_CWD")}, f, ensure_ascii=False, indent=1)

last = None
schema_path = None
model = None
for i, a in enumerate(args):
    if a == "--output-last-message" and i + 1 < len(args):
        last = args[i + 1]
    if a == "--output-schema" and i + 1 < len(args):
        schema_path = args[i + 1]
    if a == "--model" and i + 1 < len(args):
        model = args[i + 1]

print(json.dumps({"type": "thread.started", "thread_id": "fake-codex-thread"}))
print(json.dumps({"type": "turn.started"}))

if mode == "timeout":
    time.sleep(30)
    sys.exit(0)
if mode == "nonzero_valid_output":
    if last:
        open(last, "w", encoding="utf-8").write('{"ok": true}')
    print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 3, "output_tokens": 2}}))
    sys.exit(7)
if mode == "turn_failed":
    print(json.dumps({"type": "turn.failed", "error": {"message": "fake failure"}}))
    sys.exit(0)
if mode == "missing":
    print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 3, "output_tokens": 2}}))
    sys.exit(0)
if mode == "malformed":
    if last:
        open(last, "w", encoding="utf-8").write('not json')
    print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 3, "output_tokens": 2}, "session_id": "fake-codex-session"}))
    sys.exit(0)

schema = {"type": "object", "properties": {}}
if schema_path:
    schema = json.load(open(schema_path, encoding="utf-8"))

def fill(s):
    t = s.get("type")
    if t == "object":
        props = s.get("properties", {})
        return {k: fill(v) for k, v in props.items() if k in s.get("required", props.keys())}
    if t == "array":
        return [fill(s.get("items", {})) for _ in range(max(s.get("minItems", 1), 1))]
    if t == "string":
        return s["enum"][0] if "enum" in s else ("codex-stub" * 3)[:max(s.get("minLength", 4), 4)]
    if t in ("number", "integer"):
        return 0
    if t == "boolean":
        return True
    return None

payload = fill(schema)
if last:
    open(last, "w", encoding="utf-8").write(json.dumps(payload, ensure_ascii=False))
print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 13, "output_tokens": 8}, "session_id": "fake-codex-session"}))
sys.exit(0)
