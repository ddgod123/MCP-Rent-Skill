# RentPro 租房顾问 Skill

`rentpro-rent` 是 RentPro 的宿主侧行为层，面向 WorkBuddy、Codex、Claude 等支持
Skill/系统指令的 MCP 宿主。它负责租房意图识别、首轮菜单、需求收集、需求上下文维护、
MCP 工具选择和客户侧回复边界。

真实挂牌数据由 RentPro MCP 服务提供。本仓库不包含数据库、后端服务、生产数据、图片
密钥或内部治理文档。

## 一键安装 MCP + Skill

安装器连接 RentPro 公网 MCP，并将 Skill 安装到 WorkBuddy。命令不会包含 Token；
运行后会安全地交互式读取 Token、检查 HTTPS 健康状态，再写入宿主配置。

```bash
set -eu; d="$HOME/.rentpro/mcp-client"; r="https://github.com/ddgod123/MCP-Rent-Skill.git"; t="rentpro-rent-v0.18.2"; if [ -d "$d/.git" ]; then git -C "$d" remote set-url origin "$r"; git -C "$d" fetch --depth 1 origin "refs/tags/$t" && git -C "$d" checkout --detach FETCH_HEAD; else git clone --depth 1 --branch "$t" "$r" "$d"; fi; python3 "$d/install_rentpro.py" --host workbuddy
```

其他支持 MCP/Skill 的宿主可以使用同一命令，将最后的 `--host workbuddy` 改为
`--host generic`；安装器会输出不含真实 Token 的配置模板。

MCP endpoint：

```text
https://mcp.smartrent.xin/mcp
```

安装位置：

```text
~/.workbuddy/skills/rentpro-rent/SKILL.md
~/.workbuddy/skills/rentpro-rent/references/tool-routing.md
~/.workbuddy/skills/rentpro-rent/references/response-format.md
~/.workbuddy/skills/rentpro-rent/rentpro_skill_update.py
```

安装完成后刷新 Skill 或重启 WorkBuddy。

从旧版单文件更新器迁移到 schema v2 时，先执行一次上述项目安装；
旧更新器不能直接解析 v2 manifest。

## 检查更新

更新器只访问固定的公开仓库，并校验 Skill 名称、版本和 SHA-256：

```bash
python3 ~/.workbuddy/skills/rentpro-rent/rentpro_skill_update.py check
```

如果发现更新，用户明确同意后执行：

```bash
python3 ~/.workbuddy/skills/rentpro-rent/rentpro_skill_update.py update --yes
```

更新器会先备份旧文件，再原子替换新 Skill。完成后刷新或重启 WorkBuddy。

宿主也可以把下面的逻辑接入启动或首次加载流程：

```text
检查一次版本
    ↓
有新版本时提示管理员：
“RentPro 顾问 Skill 有新版本，请对我说：更新到最新版本”
    ↓
用户确认
    ↓
执行 update --yes
    ↓
刷新 Skill
```

不要在每轮租客咨询中重复检查版本，也不要把版本信息展示给普通租客。

## 文件说明

```text
SKILL.md                    宿主行为指令
references/                 按需读取的工具路由与客户回复规范
rentpro_skill_update.py     检查、安装和更新脚本
rentpro_skill_release.json  当前发布版本和 SHA-256 manifest
```

## 版本规则

- `SKILL.md` 使用 `X.Y.Z` 版本号。
- `mcp_min_version` 表示兼容所需的最低 MCP 版本。
- schema v2 manifest 的 `files[*].sha256` 必须分别与公开仓库中的每个文件完全一致；
  `SKILL.md` 的兼容字段 `sha256` 仍保留。
- 正式稳定发布时，应将 `source_ref` 和下载地址切换到不可变 Git tag 或 Release。
- WorkBuddy 需要安装完整多文件包才能使用 references；只复制 `SKILL.md` 的旧安装
  仍可运行主流程，但不会具备 references 中的完整细则。

## 当前版本

```text
Skill: rentpro-rent 0.18.2
最低 MCP: 0.17.0
更新通道: stable
```

正式发布要求：先在本仓库创建 `rentpro-rent-vX.Y.Z` 不可变 tag，发布脚本的
`--source-ref` 必须传该 tag 或 40 位 commit SHA。稳定通道不会从 `main` 等可变分支下载。
