#!/usr/bin/env python3
"""Check and update the RentPro WorkBuddy Skill from the fixed GitHub repository."""
from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent if SCRIPT_DIR.name == "scripts" else SCRIPT_DIR
REPO_OWNER = "ddgod123"
REPO_NAME = "MCP-Rent-Skill-"
SKILL_NAME = "rentpro-rent"
REMOTE_SKILL_RELATIVE_PATH = "SKILL.md"
PROJECT_SKILL_RELATIVE_PATH = "skills/rentpro-rent/SKILL.md"
DEFAULT_MANIFEST_URL = (
    f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/contents/"
    "rentpro_skill_release.json?ref=main"
)
DEFAULT_LOCAL_SKILL = (
    Path.home() / ".workbuddy" / "skills" / SKILL_NAME / "SKILL.md"
)
PROJECT_SKILL_CANDIDATES = (
    ROOT / PROJECT_SKILL_RELATIVE_PATH,
    ROOT / REMOTE_SKILL_RELATIVE_PATH,
)
DEFAULT_UPDATE_SCRIPT = (
    Path.home() / ".workbuddy" / "skills" / SKILL_NAME / "rentpro_skill_update.py"
)
GITHUB_HOSTS = {"api.github.com", "raw.githubusercontent.com"}
VERSION_RE = re.compile(r"^[vV]?([0-9]+)\.([0-9]+)\.([0-9]+)$")
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
UPDATE_AVAILABLE_EXIT = 2


class UpdateError(RuntimeError):
    """An expected, user-actionable update failure."""


@dataclass(frozen=True)
class SkillMetadata:
    name: str | None
    version: str | None


def parse_version(value: str) -> tuple[int, int, int]:
    match = VERSION_RE.fullmatch(value.strip())
    if not match:
        raise UpdateError(f"版本号格式无效：{value!r}，应为 X.Y.Z")
    return tuple(int(part) for part in match.groups())


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def read_skill_metadata(content: bytes) -> SkillMetadata:
    text = content.decode("utf-8-sig")
    if not text.startswith("---"):
        return SkillMetadata(None, None)
    parts = text.split("---", 2)
    if len(parts) < 3:
        return SkillMetadata(None, None)
    frontmatter = parts[1]

    def value_for(key: str) -> str | None:
        match = re.search(rf"(?m)^{re.escape(key)}:\s*(\S.*?)\s*$", frontmatter)
        return match.group(1).strip() if match else None

    name = value_for("name")
    version = value_for("version")
    if version is None:
        metadata = re.search(
            r"(?ms)^metadata:\s*\n(?P<body>(?:^[ \t]+.*(?:\n|$))*)",
            frontmatter,
        )
        if metadata:
            version_match = re.search(
                r"(?m)^[ \t]+version:\s*(\S.*?)\s*$",
                metadata.group("body"),
            )
            if version_match:
                version = version_match.group(1).strip()
    return SkillMetadata(name, version)


def local_metadata(path: Path) -> tuple[SkillMetadata | None, str | None]:
    if not path.exists():
        return None, None
    content = path.read_bytes()
    return read_skill_metadata(content), sha256_bytes(content)


def _github_token() -> str | None:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        return token.strip()
    gh = shutil.which("gh")
    if not gh:
        return None
    result = subprocess.run(
        [gh, "auth", "token"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return None


def validate_github_url(value: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.hostname not in GITHUB_HOSTS:
        raise UpdateError(
            "更新地址必须是 HTTPS GitHub API/raw 地址，"
            f"当前为：{value}"
        )
    if parsed.hostname == "api.github.com":
        expected = f"/repos/{REPO_OWNER}/{REPO_NAME}/"
        if not parsed.path.startswith(expected):
            raise UpdateError(f"GitHub API 地址不属于固定 RentPro 仓库：{value}")
    if parsed.hostname == "raw.githubusercontent.com":
        expected = f"/{REPO_OWNER}/{REPO_NAME}/"
        if not parsed.path.startswith(expected):
            raise UpdateError(f"GitHub raw 地址不属于固定 RentPro 仓库：{value}")


def fetch_url(url: str, token: str | None, timeout: float) -> bytes:
    validate_github_url(url)
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github.raw+json",
            "User-Agent": "rentpro-skill-updater/1.0",
        },
    )
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.read()
    except HTTPError as exc:
        if exc.code in {401, 403, 404}:
            raise UpdateError(
                "无法读取 GitHub Skill manifest。仓库可能是私有仓库，"
                "请先配置 GITHUB_TOKEN 或执行 `gh auth login`。"
            ) from exc
        raise UpdateError(f"GitHub 请求失败：HTTP {exc.code}") from exc
    except URLError as exc:
        raise UpdateError(f"GitHub 请求失败：{exc.reason}") from exc
    except TimeoutError as exc:
        raise UpdateError("GitHub 请求超时") from exc


def decode_github_content(content: bytes) -> bytes:
    """Handle both raw GitHub responses and JSON content API responses."""
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return content
    encoded = payload.get("content") if isinstance(payload, dict) else None
    if not encoded:
        return content
    try:
        return base64.b64decode("".join(encoded.split()))
    except (ValueError, TypeError) as exc:
        raise UpdateError("GitHub 内容编码无效") from exc


def load_manifest(url: str, timeout: float) -> dict:
    token = _github_token()
    raw = decode_github_content(fetch_url(url, token, timeout))
    try:
        manifest = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise UpdateError("GitHub manifest 不是有效 JSON") from exc
    if not isinstance(manifest, dict):
        raise UpdateError("GitHub manifest 顶层必须是对象")

    if manifest.get("skill_name") != SKILL_NAME:
        raise UpdateError(
            f"manifest 的 skill_name 必须是 {SKILL_NAME!r}，"
            f"实际为 {manifest.get('skill_name')!r}"
        )
    latest_version = manifest.get("latest_version")
    if not isinstance(latest_version, str):
        raise UpdateError("manifest 缺少 latest_version")
    parse_version(latest_version)
    if manifest.get("skill_path") != REMOTE_SKILL_RELATIVE_PATH:
        raise UpdateError(
            f"manifest 的 skill_path 必须是 {REMOTE_SKILL_RELATIVE_PATH!r}"
        )
    digest = manifest.get("sha256")
    if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
        raise UpdateError("manifest 缺少有效的 64 位 SHA-256")
    skill_url = manifest.get("download_url")
    if not isinstance(skill_url, str) or not skill_url:
        source_ref = manifest.get("source_ref", "main")
        skill_url = (
            f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/contents/"
            f"{REMOTE_SKILL_RELATIVE_PATH}?ref={source_ref}"
        )
        manifest["download_url"] = skill_url
    validate_github_url(skill_url)
    manifest["latest_version"] = latest_version.lstrip("vV")
    manifest["sha256"] = digest.lower()
    return manifest


def build_status(local_path: Path, manifest_url: str, timeout: float) -> dict:
    manifest = load_manifest(manifest_url, timeout)
    metadata, local_digest = local_metadata(local_path)
    local_version = metadata.version if metadata else None
    local_name = metadata.name if metadata else None
    latest_version = manifest["latest_version"]
    latest_tuple = parse_version(latest_version)

    if not local_path.exists():
        update_available = True
        reason = "本地尚未安装"
    elif local_digest == manifest["sha256"]:
        update_available = False
        reason = "本地文件与远端 SHA-256 一致"
    elif not local_version:
        update_available = True
        reason = "本地 Skill 没有版本号或版本号无法读取"
    else:
        local_tuple = parse_version(local_version)
        if latest_tuple > local_tuple:
            update_available = True
            reason = "远端版本更高"
        elif latest_tuple == local_tuple:
            update_available = True
            reason = "版本号相同但文件内容不同，需要重新同步"
        else:
            update_available = False
            reason = "本地版本高于远端 manifest，未自动降级"

    return {
        "skill_name": SKILL_NAME,
        "local_path": str(local_path),
        "local_exists": local_path.exists(),
        "local_name": local_name,
        "local_version": local_version,
        "local_sha256": local_digest,
        "latest_version": latest_version,
        "latest_sha256": manifest["sha256"],
        "mcp_min_version": manifest.get("mcp_min_version"),
        "update_channel": manifest.get("update_channel", "stable"),
        "update_available": update_available,
        "reason": reason,
        "manifest_url": manifest_url,
        "download_url": manifest["download_url"],
        "release_notes": manifest.get("release_notes", ""),
        "_manifest": manifest,
    }


def print_status(status: dict, as_json: bool) -> None:
    output = {key: value for key, value in status.items() if not key.startswith("_")}
    if as_json:
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return

    print("RentPro Skill 更新检查")
    print(f"本地版本：{status['local_version'] or '未安装/未知'}")
    print(f"远端版本：{status['latest_version']}")
    print(f"检查结果：{'发现更新' if status['update_available'] else '已是最新或本地更高'}")
    print(f"原因：{status['reason']}")
    if status["release_notes"]:
        print(f"更新说明：{status['release_notes']}")
    if status["update_available"]:
        print("RentPro 顾问 Skill 有新版本，请对我说：更新到最新版本")
        print("管理员可执行：python3 scripts/rentpro_skill_update.py update")


def atomic_write(path: Path, content: bytes, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fchmod(handle.fileno(), mode)
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def backup_path(path: Path) -> Path | None:
    if not path.exists():
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = path.with_name(f"{path.name}.backup-{stamp}")
    shutil.copy2(path, destination)
    return destination


def update_installed_skill(
    local_path: Path,
    manifest: dict,
    timeout: float,
) -> Path | None:
    token = _github_token()
    content = decode_github_content(fetch_url(manifest["download_url"], token, timeout))
    actual_digest = sha256_bytes(content)
    if actual_digest != manifest["sha256"]:
        raise UpdateError(
            "下载文件 SHA-256 校验失败，已中止更新；"
            f"期望 {manifest['sha256']}，实际 {actual_digest}"
        )
    metadata = read_skill_metadata(content)
    if metadata.name != SKILL_NAME:
        raise UpdateError(
            f"下载的 Skill name 不正确：{metadata.name!r}，期望 {SKILL_NAME!r}"
        )
    if metadata.version is None:
        raise UpdateError("下载的 Skill 缺少 version 元数据")
    if parse_version(metadata.version) != parse_version(manifest["latest_version"]):
        raise UpdateError(
            f"下载的 Skill 版本 {metadata.version} 与 manifest "
            f"{manifest['latest_version']} 不一致"
        )

    mode = stat.S_IMODE(local_path.stat().st_mode) if local_path.exists() else 0o644
    previous = backup_path(local_path)
    atomic_write(local_path, content, mode)
    return previous


def install_from_project(local_path: Path, update_script_path: Path) -> None:
    source = next(
        (candidate for candidate in PROJECT_SKILL_CANDIDATES if candidate.exists()),
        None,
    )
    if source is None:
        candidates = "、".join(str(candidate) for candidate in PROJECT_SKILL_CANDIDATES)
        raise UpdateError(f"项目 Skill 不存在，已检查：{candidates}")
    content = source.read_bytes()
    metadata = read_skill_metadata(content)
    if metadata.name != SKILL_NAME or not metadata.version:
        raise UpdateError("项目 Skill 缺少正确的 name/version 元数据")
    previous = backup_path(local_path)
    mode = stat.S_IMODE(local_path.stat().st_mode) if local_path.exists() else 0o644
    atomic_write(local_path, content, mode)
    atomic_write(update_script_path, Path(__file__).read_bytes(), 0o755)
    print(f"已安装 Skill：{local_path}")
    print(f"已安装更新器：{update_script_path}")
    if previous:
        print(f"旧 Skill 已备份：{previous}")
    print("请刷新或重启 WorkBuddy 以加载新 Skill。")


def command_check(args: argparse.Namespace) -> int:
    status = build_status(args.local_skill, args.manifest_url, args.timeout)
    print_status(status, args.json)
    return UPDATE_AVAILABLE_EXIT if status["update_available"] else 0


def command_update(args: argparse.Namespace) -> int:
    status = build_status(args.local_skill, args.manifest_url, args.timeout)
    if not status["update_available"] and not args.force:
        print_status(status, args.json)
        return 0
    if not args.yes:
        if not sys.stdin.isatty():
            raise UpdateError("非交互模式更新需要显式传入 --yes")
        answer = input(
            f"确认将 {args.local_skill} 更新到 {status['latest_version']}？[y/N] "
        ).strip().lower()
        if answer not in {"y", "yes"}:
            print("已取消更新。")
            return 0

    previous = update_installed_skill(
        args.local_skill,
        status["_manifest"],
        args.timeout,
    )
    print(f"已更新 RentPro Skill 到 {status['latest_version']}：{args.local_skill}")
    if previous:
        print(f"旧 Skill 已备份：{previous}")
    print("请刷新或重启 WorkBuddy 以加载新 Skill。")
    return 0


def command_install(args: argparse.Namespace) -> int:
    install_from_project(args.local_skill, args.update_script)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="检查并更新 RentPro WorkBuddy Skill。",
    )
    parser.add_argument(
        "--manifest-url",
        default=DEFAULT_MANIFEST_URL,
        help="固定 RentPro GitHub manifest 地址",
    )
    parser.add_argument(
        "--local-skill",
        type=Path,
        default=DEFAULT_LOCAL_SKILL,
        help="WorkBuddy 本地 Skill 路径",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=15,
        help="网络请求超时秒数",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    check = subparsers.add_parser("check", help="检查远端 Skill 版本")
    check.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    check.set_defaults(func=command_check)

    update = subparsers.add_parser("update", help="用户确认后更新本地 Skill")
    update.add_argument("--yes", action="store_true", help="已获得用户确认，跳过二次询问")
    update.add_argument("--force", action="store_true", help="即使当前看起来是最新也重新下载")
    update.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    update.set_defaults(func=command_update)

    install = subparsers.add_parser(
        "install",
        help="从当前项目安装 Skill 和更新器到 WorkBuddy",
    )
    install.add_argument(
        "--update-script",
        type=Path,
        default=DEFAULT_UPDATE_SCRIPT,
        help="更新器安装路径",
    )
    install.set_defaults(func=command_install)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    args.local_skill = args.local_skill.expanduser()
    if hasattr(args, "update_script"):
        args.update_script = args.update_script.expanduser()
    return args.func(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except UpdateError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        raise SystemExit(1)
