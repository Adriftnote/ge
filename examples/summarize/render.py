"""Script node: write the verified summary as Markdown. Counting and formatting are code, not a model."""
import json
import os
import sys


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


run_dir = sys.argv[1]
art = os.path.join(run_dir, "artifacts")
s = json.loads(read(os.path.join(art, "summarize.json")))
v = json.loads(read(os.path.join(art, "verify.json")))
bad = [x["item_id"] for x in v["verdicts"] if x["verdict"] != "pass"]
if bad:
    print(json.dumps({"ok": False, "not_passed": bad}))
    sys.exit(1)
lines = ["# Summary", "", s["summary"], "", "## Key points", ""] + ["- " + p for p in s["key_points"]]
out = os.path.join(art, "summary.md")
with open(out, "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")
print(json.dumps({"ok": True, "written": out, "points": len(s["key_points"])}))
