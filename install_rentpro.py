#!/usr/bin/env python3
"""Install the RentPro Skill and optionally configure MCP for trusted hosts.

Skill-only installation never needs a Bearer Token. MCP configuration is an
explicit administrator action and reads the token from a protected file or
environment variable; it never prints the token or writes it to this repo.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from urllib.request import Request, urlopen


DEFAULT_ENDPOINT = "https://mcp.smartrent.xin/mcp"
DEFAULT_MANIFEST = (
    "https://smartrent.xin/skill/rentpro-rent/v0.18.4/"
    "rentpro_skill_release.json"
)
SERVER_NAME = "rentpro"
DISPLAY_NAME = "Mike的房源库 Skill（北京租房）"


def backup(path: Path) -> Path | None:
    if not path.exists():
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = path.with_name(f"{path.name}.bak.{stamp}")
    shutil.copy2(path, destination)
    return destination


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"配置文件不是有效 JSON：{path}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"配置文件根节点必须是 JSON 对象：{path}")
    return value


def write_private_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        temporary = Path(handle.name)
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
    os.replace(temporary, path)
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def workbuddy_config() -> Path:
    return Path.home() / ".workbuddy" / "connectors" / "default" / "mcp.json"


def skill_update(manifest_url: str) -> None:
    script = Path(__file__).with_name("rentpro_skill_update.py")
    if not script.exists():
        raise RuntimeError(f"找不到 Skill 更新器：{script}")
    subprocess.run(
        [
            sys.executable,
            str(script),
            "--manifest-url",
            manifest_url,
            "update",
            "--yes",
            "--force",
        ],
        check=True,
    )


def read_token_file(path: Path) -> str:
    try:
        value = path.expanduser().read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError(f"无法读取 MCP Token 文件：{path}") from exc
    if not value:
        raise RuntimeError(f"MCP Token 文件为空：{path}")
    return value


def resolve_token(args: argparse.Namespace) -> str:
    sources = sum(
        value is not None
        for value in (args.token, args.token_file, args.token_env)
    )
    if sources > 1:
        raise RuntimeError("--token、--token-file、--token-env 只能选择一个")

    if args.token_file:
        return read_token_file(Path(args.token_file))
    if args.token_env:
        value = os.environ.get(args.token_env, "").strip()
        if not value:
            raise RuntimeError(f"环境变量 {args.token_env} 未设置或为空")
        return value
    if args.token:
        return args.token.strip()
    if args.no_prompt:
        raise RuntimeError(
            "非交互式 MCP 配置必须传 --token-file 或 --token-env；"
            "普通用户请使用 --skip-mcp"
        )
    return getpass.getpass("MCP Bearer Token（不会显示）：").strip()


def healthcheck(endpoint: str) -> None:
    health = endpoint.rsplit("/", 1)[0] + "/healthz"
    request = Request(health, headers={"Accept": "application/json"})
    with urlopen(request, timeout=10) as response:
        if response.status != 200:
            raise RuntimeError(f"MCP 健康检查失败：HTTP {response.status}")
    print(f"健康检查通过：{health}")


def install_workbuddy(endpoint: str, token: str, install_skill: bool) -> None:
    path = workbuddy_config()
    config = read_json(path)
    servers = config.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise RuntimeError(f"{path} 中的 mcpServers 不是对象")
    servers[SERVER_NAME] = {
        "type": "streamableHttp",
        "url": endpoint,
        "headers": {"Authorization": f"Bearer {token}"},
        "disabled": False,
        "displayName": DISPLAY_NAME,
    }
    old = backup(path)
    write_private_json(path, config)
    if old:
        print(f"已备份旧配置：{old}")
    print(f"已写入 WorkBuddy MCP 配置：{path}")
    if install_skill:
        skill_update(DEFAULT_MANIFEST)
        print(f"已安装/更新 Skill：{DISPLAY_NAME}")


def print_generic_config(endpoint: str) -> None:
    print(
        json.dumps(
            {
                "mcpServers": {
                    SERVER_NAME: {
                        "url": endpoint,
                        "headers": {"Authorization": "Bearer <MCP_AUTH_TOKEN>"},
                        "displayName": DISPLAY_NAME,
                    }
                }
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    print("\n请把 <MCP_AUTH_TOKEN> 替换为管理员提供的 Token，不要提交到 Git。")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="安装 RentPro Skill；MCP 配置仅在显式 --configure-mcp 时执行。"
    )
    parser.add_argument(
        "--host",
        choices=("workbuddy", "generic"),
        default="workbuddy",
        help="目标宿主；未知宿主使用 generic 输出配置模板",
    )
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument(
        "--token",
        help="MCP Bearer Token（不推荐，会出现在 shell 历史中）",
    )
    parser.add_argument(
        "--token-file",
        help="从受保护文件读取 MCP Token；不会打印或写入仓库",
    )
    parser.add_argument(
        "--token-env",
        help="从指定环境变量读取 MCP Token；不会打印",
    )
    parser.add_argument(
        "--no-prompt",
        action="store_true",
        help="没有 Token 时直接失败，不打开交互式输入",
    )
    parser.add_argument(
        "--configure-mcp",
        action="store_true",
        help="管理员显式开启 MCP 配置；需要 --token-file、--token-env 或交互式 Token",
    )
    parser.add_argument(
        "--skip-mcp",
        action="store_true",
        help="兼容参数：只安装 Skill，不修改 WorkBuddy MCP 配置，也不需要 Token",
    )
    parser.add_argument(
        "--skip-skill",
        action="store_true",
        help="只配置 MCP，不安装/更新 Skill",
    )
    parser.add_argument(
        "--skip-healthcheck",
        action="store_true",
        help="跳过公网健康检查",
    )
    args = parser.parse_args()

    if args.configure_mcp and args.skip_mcp:
        raise RuntimeError("--configure-mcp 与 --skip-mcp 不能同时使用")

    if not args.configure_mcp:
        if args.skip_skill:
            raise RuntimeError("只配置 MCP 时必须显式传 --configure-mcp")
        if args.token or args.token_file or args.token_env:
            raise RuntimeError("如需配置 MCP，请显式传 --configure-mcp")
        skill_update(DEFAULT_MANIFEST)
        print(f"已安装 Skill：{DISPLAY_NAME}（未配置 MCP）")
    else:
        if not args.endpoint.startswith("https://"):
            raise RuntimeError("生产 MCP endpoint 必须使用 HTTPS")
        token = resolve_token(args)
        if len(token) < 32:
            raise RuntimeError("MCP Token 长度不足，拒绝写入宿主配置")

        if not args.skip_healthcheck:
            healthcheck(args.endpoint)
        if args.host == "workbuddy":
            install_workbuddy(args.endpoint, token, not args.skip_skill)
        else:
            print_generic_config(args.endpoint)
            if not args.skip_skill:
                skill_update(DEFAULT_MANIFEST)
                print(f"已安装/更新 Skill：{DISPLAY_NAME}")
    print("安装完成。请重启或刷新宿主的 MCP/Skill 配置。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        raise SystemExit(1)
