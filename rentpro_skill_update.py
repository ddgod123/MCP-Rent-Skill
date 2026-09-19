#!/usr/bin/env python3
"""Check, install and update the RentPro WorkBuddy Skill package."""
from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent if SCRIPT_DIR.name == "scripts" else SCRIPT_DIR
REPO_OWNER = "ddgod123"
REPO_NAME = "MCP-Rent-Skill"
SKILL_NAME = "rentpro-rent"
REMOTE_SKILL_RELATIVE_PATH = "SKILL.md"
PROJECT_SKILL_RELATIVE_PATH = "skills/rentpro-rent/SKILL.md"
DEFAULT_MANIFEST_URL = (
    f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/contents/"
    "rentpro_skill_release.json?ref=main"
)
DEFAULT_LOCAL_SKILL = Path.home() / ".workbuddy" / "skills" / SKILL_NAME / "SKILL.md"
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


@dataclass(frozen=True)
class ManifestFile:
    path: str
    role: str
    download_url: str
    sha256: str


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
            "User-Agent": "rentpro-skill-updater/2.0",
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
                "无法读取 GitHub manifest 或 Skill 文件。仓库可能是私有仓库，"
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


def safe_relative_path(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise UpdateError("manifest 文件路径必须是非空字符串")
    if "\\" in value or value.startswith("/"):
        raise UpdateError(f"manifest 文件路径不安全：{value!r}")
    path = PurePosixPath(value)
    if any(part in {"", ".", ".."} for part in path.parts):
        raise UpdateError(f"manifest 文件路径不安全：{value!r}")
    normalized = str(path)
    if normalized != value:
        raise UpdateError(f"manifest 文件路径必须使用规范相对路径：{value!r}")
    return normalized


def github_file_url(path: str, source_ref: str) -> str:
    encoded_path = quote(path, safe="/")
    return (
        f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/contents/"
        f"{encoded_path}?ref={quote(source_ref, safe='')}"
    )


def _manifest_file(
    raw: dict,
    *,
    source_ref: str,
    default_role: str,
) -> ManifestFile:
    path = safe_relative_path(raw.get("path"))
    digest = raw.get("sha256")
    if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
        raise UpdateError(f"manifest 文件 {path!r} 缺少有效的 64 位 SHA-256")
    url = raw.get("download_url") or github_file_url(path, source_ref)
    if not isinstance(url, str) or not url:
        raise UpdateError(f"manifest 文件 {path!r} 缺少 download_url")
    validate_github_url(url)
    role = raw.get("role", default_role)
    if not isinstance(role, str) or not role:
        role = default_role
    return ManifestFile(path, role, url, digest.lower())


def manifest_files(manifest: dict) -> list[ManifestFile]:
    source_ref = manifest.get("source_ref", "main")
    if not isinstance(source_ref, str) or not source_ref:
        raise UpdateError("manifest 的 source_ref 无效")

    schema_version = manifest.get("schema_version", 1)
    if schema_version == 1:
        path = manifest.get("skill_path", REMOTE_SKILL_RELATIVE_PATH)
        digest = manifest.get("sha256")
        url = manifest.get("download_url")
        if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            raise UpdateError("schema v1 manifest 缺少有效的 64 位 SHA-256")
        if not isinstance(url, str) or not url:
            url = github_file_url(path, source_ref)
        return [
            _manifest_file(
                {
                    "path": path,
                    "role": "skill",
                    "download_url": url,
                    "sha256": digest,
                },
                source_ref=source_ref,
                default_role="skill",
            )
        ]

    if schema_version != 2:
        raise UpdateError(f"不支持的 manifest schema_version：{schema_version!r}")
    raw_files = manifest.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        raise UpdateError("schema v2 manifest 必须包含非空 files 数组")

    result: list[ManifestFile] = []
    seen: set[str] = set()
    for raw in raw_files:
        if not isinstance(raw, dict):
            raise UpdateError("manifest files 数组中的每一项必须是对象")
        item = _manifest_file(
            raw,
            source_ref=source_ref,
            default_role="resource",
        )
        if item.path in seen:
            raise UpdateError(f"manifest files 存在重复路径：{item.path}")
        seen.add(item.path)
        result.append(item)

    skill_paths = [
        item.path for item in result if item.role == "skill" or item.path == "SKILL.md"
    ]
    if skill_paths != ["SKILL.md"]:
        raise UpdateError("schema v2 manifest 必须且只能包含路径为 SKILL.md 的 skill 文件")
    return result


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
    files = manifest_files(manifest)
    manifest["_files"] = files
    manifest["latest_version"] = latest_version.lstrip("vV")
    return manifest


def destination_for(local_skill: Path, relative_path: str) -> Path:
    if relative_path == REMOTE_SKILL_RELATIVE_PATH:
        return local_skill
    return local_skill.parent / Path(*PurePosixPath(relative_path).parts)


def build_status(local_path: Path, manifest_url: str, timeout: float) -> dict:
    manifest = load_manifest(manifest_url, timeout)
    metadata, local_digest = local_metadata(local_path)
    local_version = metadata.version if metadata else None
    local_name = metadata.name if metadata else None
    latest_version = manifest["latest_version"]
    latest_tuple = parse_version(latest_version)

    file_statuses = []
    all_match = True
    for item in manifest["_files"]:
        path = destination_for(local_path, item.path)
        digest = sha256_bytes(path.read_bytes()) if path.exists() else None
        matches = digest == item.sha256
        all_match = all_match and matches
        file_statuses.append(
            {
                "path": item.path,
                "local_path": str(path),
                "exists": path.exists(),
                "local_sha256": digest,
                "latest_sha256": item.sha256,
                "matches": matches,
                "role": item.role,
            }
        )

    if all_match:
        update_available = False
        reason = "本地 Skill 包所有文件与远端 SHA-256 一致"
    elif not local_path.exists():
        update_available = True
        reason = "本地尚未安装"
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
            reason = "版本号相同但 Skill 包内容不同，需要重新同步"
        else:
            update_available = False
            reason = "本地版本高于远端 manifest，未自动降级"

    skill_item = next(item for item in manifest["_files"] if item.path == "SKILL.md")
    return {
        "skill_name": SKILL_NAME,
        "local_path": str(local_path),
        "local_exists": local_path.exists(),
        "local_name": local_name,
        "local_version": local_version,
        "local_sha256": local_digest,
        "latest_version": latest_version,
        "latest_sha256": skill_item.sha256,
        "manifest_schema_version": manifest.get("schema_version", 1),
        "files": file_statuses,
        "mcp_min_version": manifest.get("mcp_min_version"),
        "update_channel": manifest.get("update_channel", "stable"),
        "update_available": update_available,
        "reason": reason,
        "manifest_url": manifest_url,
        "download_url": skill_item.download_url,
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
    print(f"manifest schema：{status['manifest_schema_version']}")
    print(f"检查结果：{'发现更新' if status['update_available'] else '已是最新或本地更高'}")
    print(f"原因：{status['reason']}")
    mismatches = [item["path"] for item in status["files"] if not item["matches"]]
    if mismatches:
        print(f"待同步文件：{', '.join(mismatches)}")
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
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination = path.with_name(f"{path.name}.backup-{stamp}")
    shutil.copy2(path, destination)
    return destination


def _file_mode(path: Path, role: str) -> int:
    if path.exists():
        return stat.S_IMODE(path.stat().st_mode)
    return 0o755 if role == "updater" else 0o644


def _backup_label(backups: list[Path]) -> Path | list[Path] | None:
    if not backups:
        return None
    return backups[0] if len(backups) == 1 else backups


def _restore_written(written: list[Path], backups: list[Path]) -> None:
    for destination in reversed(written):
        matching = [
            backup
            for backup in backups
            if backup.name.startswith(f"{destination.name}.backup-")
        ]
        if matching:
            try:
                atomic_write(
                    destination,
                    matching[-1].read_bytes(),
                    stat.S_IMODE(matching[-1].stat().st_mode),
                )
            except OSError:
                pass
        elif destination.exists():
            try:
                destination.unlink()
            except OSError:
                pass


def update_installed_skill(
    local_path: Path,
    manifest: dict,
    timeout: float,
) -> Path | list[Path] | None:
    token = _github_token()
    downloaded: dict[str, tuple[ManifestFile, bytes]] = {}

    # Download and validate every file before changing the local installation.
    for item in manifest.get("_files") or manifest_files(manifest):
        content = decode_github_content(fetch_url(item.download_url, token, timeout))
        actual_digest = sha256_bytes(content)
        if actual_digest != item.sha256:
            raise UpdateError(
                f"下载文件 {item.path} SHA-256 校验失败，已中止更新；"
                f"期望 {item.sha256}，实际 {actual_digest}"
            )
        downloaded[item.path] = (item, content)

    skill_content = downloaded["SKILL.md"][1]
    metadata = read_skill_metadata(skill_content)
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

    backups: list[Path] = []
    written: list[Path] = []
    try:
        for relative_path, (item, content) in downloaded.items():
            destination = destination_for(local_path, relative_path)
            previous = backup_path(destination)
            if previous:
                backups.append(previous)
            atomic_write(destination, content, _file_mode(destination, item.role))
            written.append(destination)
    except (OSError, UpdateError) as exc:
        _restore_written(written, backups)
        raise UpdateError(f"本地 Skill 包写入失败，已尽量回滚：{exc}") from exc
    return _backup_label(backups)


def _project_payload(source_dir: Path) -> list[tuple[str, Path, str]]:
    payload: list[tuple[str, Path, str]] = [
        ("SKILL.md", source_dir / "SKILL.md", "skill"),
    ]
    references = source_dir / "references"
    if references.is_dir():
        for source in sorted(references.glob("*.md")):
            if source.name == "README.md":
                continue
            payload.append((f"references/{source.name}", source, "reference"))
    manifest = source_dir / "rentpro_skill_release.json"
    if manifest.exists():
        payload.append(("rentpro_skill_release.json", manifest, "manifest"))
    return payload


def install_from_project(local_path: Path, update_script_path: Path) -> list[Path]:
    source = next(
        (candidate for candidate in PROJECT_SKILL_CANDIDATES if candidate.exists()),
        None,
    )
    if source is None:
        candidates = "、".join(str(candidate) for candidate in PROJECT_SKILL_CANDIDATES)
        raise UpdateError(f"项目 Skill 不存在，已检查：{candidates}")
    source_dir = source.parent
    metadata = read_skill_metadata(source.read_bytes())
    if metadata.name != SKILL_NAME or not metadata.version:
        raise UpdateError("项目 Skill 缺少正确的 name/version 元数据")

    targets: list[tuple[Path, Path, str]] = []
    for relative_path, project_file, role in _project_payload(source_dir):
        if not project_file.exists():
            continue
        destination = (
            local_path
            if relative_path == "SKILL.md"
            else local_path.parent / Path(*PurePosixPath(relative_path).parts)
        )
        targets.append((project_file, destination, role))
    targets.append((Path(__file__), update_script_path, "updater"))

    backups: list[Path] = []
    written: list[Path] = []
    try:
        for source_file, destination, role in targets:
            previous = backup_path(destination)
            if previous:
                backups.append(previous)
            atomic_write(
                destination,
                source_file.read_bytes(),
                _file_mode(destination, role),
            )
            written.append(destination)
    except (OSError, UpdateError) as exc:
        _restore_written(written, backups)
        raise UpdateError(f"项目 Skill 安装失败，已尽量回滚：{exc}") from exc

    print(f"已安装 RentPro Skill 包到：{local_path.parent}")
    for _, destination, _ in targets:
        print(f"  - {destination}")
    if backups:
        print("旧文件已备份：")
        for backup in backups:
            print(f"  - {backup}")
    print("请刷新或重启 WorkBuddy 以加载新 Skill。")
    return backups


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
    print(f"已更新 RentPro Skill 包到 {status['latest_version']}：{args.local_skill.parent}")
    if previous:
        print(f"旧文件已备份：{previous}")
    print("请刷新或重启 WorkBuddy 以加载新 Skill。")
    return 0


def command_install(args: argparse.Namespace) -> int:
    install_from_project(args.local_skill, args.update_script)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="检查、安装并更新 RentPro WorkBuddy Skill 包。",
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

    check = subparsers.add_parser("check", help="检查远端 Skill 版本和文件")
    check.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    check.set_defaults(func=command_check)

    update = subparsers.add_parser("update", help="用户确认后更新本地 Skill 包")
    update.add_argument("--yes", action="store_true", help="已获得用户确认，跳过二次询问")
    update.add_argument("--force", action="store_true", help="即使当前看起来是最新也重新下载")
    update.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    update.set_defaults(func=command_update)

    install = subparsers.add_parser(
        "install",
        help="从当前项目安装 Skill、references 和更新器到 WorkBuddy",
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
