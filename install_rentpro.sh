#!/bin/sh
set -eu

# Discover the current release, then verify both executable scripts before use.
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT HUP INT TERM

python3 - "$tmp" "$@" <<'PY'
import hashlib
import base64
import json
import pathlib
import re
import subprocess
import sys
from urllib.request import Request, urlopen

target = pathlib.Path(sys.argv[1])
install_url = "https://api.smartrent.xin/api/public/rentpro-install"
github_base = "https://api.github.com/repos/ddgod123/MCP-Rent-Skill/contents"
cloud_base = "https://smartrent.xin/skill/rentpro-rent"


def fetch(url):
    request = Request(url, headers={
        "User-Agent": "rentpro-skill-bootstrap/2.0",
        "Accept": "application/json" if url == install_url else "application/vnd.github.raw+json",
    })
    with urlopen(request, timeout=20) as response:
        return response.read()


def fetch_manifest(urls):
    for url in urls:
        try:
            content = fetch(url)
        except Exception:
            continue
        # A malformed successful response is an error, not permission to switch versions.
        payload = json.loads(content)
        if isinstance(payload, dict) and payload.get("content"):
            payload = json.loads(base64.b64decode(payload["content"]))
        if not isinstance(payload, dict):
            raise SystemExit("发布清单必须是 JSON 对象")
        return url, payload
    raise SystemExit("无法下载 Skill 发布清单")


try:
    descriptor_raw = fetch(install_url)
except Exception:
    manifest_url, manifest = fetch_manifest([f"{github_base}/rentpro_skill_release.json?ref=main"])
    expected_version = None
else:
    descriptor = json.loads(descriptor_raw)
    if not isinstance(descriptor, dict):
        raise SystemExit("安装入口必须是 JSON 对象")
    version = descriptor.get("version")
    if descriptor.get("skill_name") != "rentpro-rent" or not isinstance(version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise SystemExit("安装入口的 Skill 标识或版本号无效")
    pinned_url = f"{cloud_base}/v{version}/rentpro_skill_release.json"
    if descriptor.get("manifest_url") != pinned_url:
        raise SystemExit("安装入口必须指向同一版本的固定云端清单")
    manifest_url, manifest = fetch_manifest([
        pinned_url,
        f"{github_base}/rentpro_skill_release.json?ref=rentpro-rent-v{version}",
    ])
    expected_version = version

version = manifest.get("latest_version")
source_ref = manifest.get("source_ref", "")
if manifest.get("skill_name") != "rentpro-rent" or not isinstance(version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
    raise SystemExit("发布清单的 Skill 标识或版本号无效")
if expected_version and expected_version != version:
    raise SystemExit("下载的清单版本与所选版本不一致")
if not isinstance(source_ref, str) or not (re.fullmatch(r"[0-9a-f]{40}", source_ref) or source_ref == f"rentpro-rent-v{version}"):
    raise SystemExit("发布清单必须指向不可变的同版 tag 或 commit")
if manifest_url.endswith("?ref=main"):
    manifest_url = f"{github_base}/rentpro_skill_release.json?ref={source_ref}"

staged = {}
for name in ("install_rentpro.py", "rentpro_skill_update.py"):
    entries = [item for item in manifest.get("files", []) if item.get("path") == name]
    if len(entries) != 1 or not re.fullmatch(r"[0-9a-fA-F]{64}", entries[0].get("sha256", "")):
        raise SystemExit(f"发布清单缺少文件校验信息：{name}")
    item = entries[0]
    allowed = {
        f"{cloud_base}/v{version}/{name}",
        f"{github_base}/{name}?ref={source_ref}",
        f"https://raw.githubusercontent.com/ddgod123/MCP-Rent-Skill/{source_ref}/{name}",
    }
    urls = [item.get("download_url"), item.get("fallback_download_url")]
    if any(url not in allowed for url in urls if url is not None):
        raise SystemExit(f"安装文件下载地址无效：{name}")
    content = None
    for url in urls:
        if not url:
            continue
        try:
            content = fetch(url)
        except Exception:
            continue
        if hashlib.sha256(content).hexdigest() != item["sha256"].lower():
            raise SystemExit(f"安装文件 SHA-256 校验失败：{name}")
        break
    if content is None:
        raise SystemExit(f"无法下载 Skill 安装文件：{name}")
    staged[name] = content

# Do not execute any downloaded code until both files have passed verification.
for name, content in staged.items():
    path = target / name
    path.write_bytes(content)
    path.chmod(0o700)
result = subprocess.run([
    sys.executable, str(target / "install_rentpro.py"),
    "--manifest-url", manifest_url, *sys.argv[2:],
], capture_output=True, text=True, check=False)
if result.returncode != 0:
    failure = next(
        (line.strip() for line in (result.stderr or "").splitlines() + (result.stdout or "").splitlines()
         if line.strip().startswith("[FAIL] ")),
        None,
    )
    print(failure or f"[FAIL] 安装器执行失败（退出码 {result.returncode}）", file=sys.stderr)
    raise SystemExit(result.returncode)
if result.stdout:
    print(result.stdout, end="")
if result.stderr:
    print(result.stderr, end="", file=sys.stderr)
PY
