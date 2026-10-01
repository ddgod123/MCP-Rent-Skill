#!/usr/bin/env python3
"""Install RentPro MCP + Skill for supported AI hosts.

The installer configures a remote MCP endpoint. It never downloads a server
binary, writes credentials to the repository, or prints the bearer token.
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
    "https://api.github.com/repos/ddgod123/MCP-Rent-Skill/contents/"
    "rentpro_skill_release.json?ref=main"
)
SERVER_NAME = "rentpro"


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
            "install",
            "--manifest-url",
            manifest_url,
        ],
        check=True,
    )


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
    }
    old = backup(path)
    write_private_json(path, config)
    if old:
        print(f"已备份旧配置：{old}")
    print(f"已写入 WorkBuddy MCP 配置：{path}")
    if install_skill:
        skill_update(DEFAULT_MANIFEST)
        print("已安装/更新 RentPro Skill")


def print_generic_config(endpoint: str) -> None:
    print(
        json.dumps(
            {
                "mcpServers": {
                    SERVER_NAME: {
                        "url": endpoint,
                        "headers": {"Authorization": "Bearer <MCP_AUTH_TOKEN>"},
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
        description="一键安装 RentPro 公网 MCP + Skill（不安装本地数据库或服务端）。"
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
        help="MCP Bearer Token；不传时安全地交互式输入，不会写入仓库",
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

    if not args.endpoint.startswith("https://"):
        raise RuntimeError("生产 MCP endpoint 必须使用 HTTPS")
    token = args.token or getpass.getpass("MCP Bearer Token（不会显示）：").strip()
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
            print("已安装/更新 RentPro Skill")
    print("安装完成。请重启或刷新宿主的 MCP/Skill 配置。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        raise SystemExit(1)
