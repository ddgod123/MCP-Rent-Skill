# Mike的房源库 Skill（北京租房）

`rentpro-rent` 是 RentPro 的宿主侧租房顾问行为层，负责需求收集、MCP 工具路由与客户回复规范。
真实挂牌由 RentPro MCP 提供；公开 Skill 包不包含数据库、生产数据或密钥。

## 安装与更新

将以下地址发送给当前使用的、支持 Skill 导入的 AI 宿主：

```text
https://api.smartrent.xin/api/public/rentpro-install
```

宿主读取最新安装描述，首次安装时使用自身的 Skill 管理能力；已安装时更新本宿主原来的目录。
逐文件校验 SHA-256，完成后刷新 Skill 和 MCP 工具列表。公开 MCP 无需 Bearer Token：

```text
https://mcp.smartrent.xin/public-mcp
```

每个宿主拥有独立安装副本。DSH 更新不会更新 WorkBuddy，反之亦然；不要因机器上装有某个应用就选择它。
不支持文件安装或自动配置的宿主，应通过其原生管理界面导入，不能宣称已安装或修改其他应用配置。

## 管理员脚本：选择当前宿主

以下命令供具备本地执行能力的宿主或管理员使用，不要求普通租客执行。
首次从下载目录、开发仓库或临时引导脚本安装时，必须传入目标宿主或其实际 Skill 目录。

```bash
python3 install_rentpro.py --host workbuddy
python3 install_rentpro.py --host dsh
python3 install_rentpro.py --host cursor
python3 install_rentpro.py --host generic --skill-dir /absolute/path/to/skills/rentpro-rent
```

上述命令安装 Skill。公开 MCP 连接由当前宿主通过自身的配置能力添加；脚本不会猜测其他宿主的 MCP 配置结构。
`--configure-mcp` 保留给管理员受保护端点配置，不是普通用户安装公开服务的必要步骤。

| 宿主参数 | 用户级 Skill 根目录 |
|---|---|
| `workbuddy` | `~/.workbuddy/skills` |
| `dsh`（别名 `deepseek`、`deepseekharness`、`deepseek-harness`） | `~/.dsh/skills` |
| `codex` | 优先复用已有 `~/.codex/skills` 或 `~/.agents/skills` 副本；设置 `CODEX_HOME` 时也识别其 `skills` 目录 |
| `cursor` | `~/.cursor/skills` |
| `qoder` | `~/.qoder/skills` |
| `trae` | `~/.trae/skills` |
| `claude`（别名 `claude-code`） | `~/.claude/skills` |
| `generic` | 必须通过 `--skill-dir` 或 `--local-skill` 指定原生宿主实际目录 |

以上根目录下使用 `rentpro-rent` 子目录。不同版本、项目级目录或自定义安装位置，使用明确路径。
千问等尚未确认目录的宿主使用原生导入能力或 `generic`，不凭品牌名称猜测文件位置。

目标判定顺序：明确的 `--local-skill/--skill-dir` → 明确的 `--host` → 已安装脚本所在目录 →
宿主显式提供的 `RENTPRO_SKILL_DIR/RENTPRO_HOST`。无法判定时停止；同一宿主存在多个候选副本时要求指定路径。
`CODEX_HOME` 只影响已选择 Codex 时的目录，不用于猜测当前宿主。

## 检查更新

每个新会话首次加载时，宿主调用只读工具 `check_rentpro_skill_update` 比较已加载版本。
发现新版后通过统一安装地址更新当前副本，不在每轮租房咨询中重复检查。

安装目录中的新版更新器自动检查、更新自身同目录的 Skill，例如：

```bash
python3 ~/.dsh/skills/rentpro-rent/rentpro_skill_update.py check --json
python3 ~/.dsh/skills/rentpro-rent/rentpro_skill_update.py update --yes
```

也可从开发仓库明确选择目标；全局参数应放在子命令之前：

```bash
python3 scripts/rentpro_skill_update.py --host dsh check --json
python3 scripts/rentpro_skill_update.py --local-skill /absolute/path/to/rentpro-rent/SKILL.md update --yes
```

`0.18.9` 及更早的更新器默认指向 WorkBuddy。第一次迁移时必须明确指定当前宿主的 `--local-skill`，
或由宿主按统一安装地址原位替换新版包，不能只在 DSH 目录裸跑旧更新器。
`0.18.6` 及更早版本的更新器还固定旧发布清单，应通过统一安装地址重新导入。

更新会备份旧文件，逐文件校验并原子替换，失败时尽量回滚。不能自动降级；更新成功后仍需宿主刷新已加载内容。
Skill、references 和更新器始终属于同一个目标目录，不能单独把更新器安装到另一款宿主。

## 文件与发布规则

安装包包括 `SKILL.md`、`references/`、`rentpro_skill_update.py` 和安装脚本。
`rentpro_skill_release.json` 是远端发布清单，网络更新不保证将其复制到本地；项目安装可能包含该文件。
不能依靠本地残留清单发现最新版，每次检查从统一安装入口重新获取。

开发仓库 `skills/rentpro-rent/` 是文档来源，发布脚本同步 README、CHANGELOG 和安装包。
当前版本区块由发布脚本生成；发布校验同时检查版本、最低 MCP、CHANGELOG 条目和各文件 SHA-256。
稳定文件下载固定到不可变版本 tag 或 commit。文件下载依次使用云主源和同版本 GitHub API；
发布清单发现阶段在云端不可用时使用公开 GitHub API。raw.githubusercontent.com 不属于当前自动回退链路。
GitHub Release 使用对应版本的 CHANGELOG 条目。

## 当前版本

<!-- release-version:start -->
```text
Skill: rentpro-rent 0.18.12
最低 MCP: 0.19.3
更新通道: stable
```
<!-- release-version:end -->
