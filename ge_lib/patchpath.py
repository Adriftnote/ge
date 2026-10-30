"""patchpath — patches[].file 을 SRC 안의 실제 파일로 바꾼다. 원본을 덮지 않기 위한 유일한 관문.

실제로 있었던 두 건:
  · 워커가 절대경로를 냈다 -> os.path.join 이 그대로 돌려줘 **사본이 원본이 됐다**(copyfile 이 막아 줬다).
  · 워커가 레포 루트 기준으로 냈다 -> 경로가 겹쳐 파일을 못 찾았다.
둘 다 여기서 막는다. 적용 스크립트가 갈라지지 않게 이 모듈 하나만 쓴다.
"""
import os
import re


def rel_to_src(f, src_root, must_exist=False):
    f = f.replace("\\", "/").strip()
    root = os.path.abspath(src_root).replace("\\", "/").rstrip("/")
    if re.match(r"^[A-Za-z]:/", f) or f.startswith("/"):
        a = os.path.abspath(f).replace("\\", "/")
        if not (a.lower() == root.lower() or a.lower().startswith(root.lower() + "/")):
            raise ValueError("SRC 밖: " + f)
        f = a[len(root) + 1:]
    while f.startswith("./"):
        f = f[2:]
    f = f.lstrip("/")
    if not f or ".." in f.split("/"):
        raise ValueError("허용되지 않는 경로: " + f)
    if not must_exist or os.path.isfile(os.path.join(root, f)):
        return f
    parts, tried = f.split("/"), [f]
    for i in range(1, len(parts)):
        cand = "/".join(parts[i:])
        tried.append(cand)
        if os.path.isfile(os.path.join(root, cand)):
            return cand
    raise ValueError("SRC 안에서 못 찾음. 시도: " + ", ".join(tried))
