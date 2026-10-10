#!/bin/sh
set -eu

# Small, verified bootstrap used by the public website. The Python installer
# performs the full manifest and per-file SHA-256 verification afterwards.
base='https://smartrent.xin/skill/rentpro-rent/v0.18.4'
fallback='https://raw.githubusercontent.com/ddgod123/MCP-Rent-Skill/rentpro-rent-v0.18.4'
tmp="${TMPDIR:-/tmp}/rentpro-skill-bootstrap.$$"
trap 'rm -rf "$tmp"' EXIT HUP INT TERM
mkdir -p "$tmp"

python3 - "$tmp" "$base" <<'PY'
import pathlib
import sys
from urllib.request import Request, urlopen

target = pathlib.Path(sys.argv[1])
base = sys.argv[2]
fallback = "https://raw.githubusercontent.com/ddgod123/MCP-Rent-Skill/rentpro-rent-v0.18.4"
for name in ("install_rentpro.py", "rentpro_skill_update.py"):
    content = None
    for url in (f"{base}/{name}", f"{fallback}/{name}"):
        try:
            request = Request(url, headers={"User-Agent": "rentpro-skill-bootstrap/1.0"})
            with urlopen(request, timeout=20) as response:
                content = response.read()
            break
        except Exception:
            continue
    if content is None:
        raise SystemExit(f"无法下载 Skill 安装文件：{name}")
    path = target / name
    path.write_bytes(content)
    path.chmod(0o700 if name == "rentpro_skill_update.py" else 0o600)
PY

exec python3 "$tmp/install_rentpro.py" "$@"
