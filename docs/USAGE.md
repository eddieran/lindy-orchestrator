# lindy-orchestrator v0.8.0 使用指南

> 轻量级、git 原生的多 Agent 编排框架。

---

## 目录

1. [前置条件](#前置条件)
2. [安装](#安装)
3. [接入项目（onboard / init）](#接入项目)
4. [配置详解（orchestrator.yaml）](#配置详解)
5. [CLI 命令参考](#cli-命令参考)
   - [run — 执行目标](#run--执行目标)
   - [plan — 规划任务](#plan--规划任务)
   - [resume — 恢复会话](#resume--恢复会话)
   - [status — 查看状态](#status--查看状态)
   - [logs — 查看日志](#logs--查看日志)
   - [validate — 校验配置](#validate--校验配置)
   - [gc — 垃圾回收](#gc--垃圾回收)
   - [scan — 熵扫描](#scan--熵扫描)
   - [mailbox — 邮箱系统](#mailbox--邮箱系统)
   - [issues — 查看 Issue](#issues--查看-issue)
   - [run-issue — 执行 Issue](#run-issue--执行-issue)
   - [version — 版本信息](#version--版本信息)
6. [Provider 系统](#provider-系统)
7. [实时 DAG 仪表盘](#实时-dag-仪表盘)
8. [Hook / 事件系统](#hook--事件系统)
9. [QA 门禁详解](#qa-门禁详解)
10. [结构化 QA 反馈与渐进式重试](#结构化-qa-反馈与渐进式重试)
11. [Dispatch 模式](#dispatch-模式)
12. [执行总结报告](#执行总结报告)
13. [会话管理与检查点](#会话管理与检查点)
14. [目标描述最佳实践](#目标描述最佳实践)
15. [项目结构示例](#项目结构示例)
16. [常见问题排查](#常见问题排查)

---

## 前置条件

- Python 3.11+
- Git
- 至少安装以下一种 Agent CLI：
  - [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code)（默认 provider）
  - [Codex CLI](https://github.com/openai/codex)（可选 provider）
- GitHub CLI (`gh`)（如使用 tracker 集成或 CI check QA 门禁）

---

## 安装

```bash
# 基础安装
pip install lindy-orchestrator

# 如需 Anthropic API 模式（planner.mode: api）
pip install lindy-orchestrator[api]

# 验证安装
lindy-orchestrate --version
```

---

## 接入项目

有两种方式接入项目，选一种即可。

### 方式 A：`onboard`（推荐）

`onboard` 命令会根据项目状态智能选择模式：

| 项目状态 | 触发模式 | 行为 |
|----------|----------|------|
| 空项目（无源文件） | scaffold 模式 | LLM 驱动，需要项目描述 |
| 有代码但无 `orchestrator.yaml` | init+onboard 模式 | 扫描、问答、生成 |
| 已有 `orchestrator.yaml` | re-onboard 模式 | 更新配置和文件 |

```bash
cd your-project

# 已有代码的项目 — 自动检测模块和技术栈
lindy-orchestrate onboard

# 空项目 — 用描述引导 LLM 搭建骨架
lindy-orchestrate onboard "一个基于 FastAPI 的后端服务"

# 从文件读取描述
lindy-orchestrate onboard --file description.md

# 从 stdin 读取
cat desc.txt | lindy-orchestrate onboard --file -

# 全自动（跳过交互确认）
lindy-orchestrate onboard --non-interactive

# 覆盖已有文件
lindy-orchestrate onboard --force

# 调整扫描深度（默认 1 层）
lindy-orchestrate onboard --depth 2
```

**选项：**

| 选项 | 短选项 | 说明 |
|------|--------|------|
| `DESCRIPTION` | — | 位置参数，项目描述（scaffold 模式需要） |
| `--file PATH` | `-f` | 从文件读取描述（`-` 表示 stdin） |
| `--depth N` | — | 目录扫描深度，默认 1 |
| `--non-interactive` | `-y` | 跳过确认提示，使用默认值 |
| `--force` | — | 覆盖已有文件 |

**三阶段流程（init+onboard 模式）：**

**阶段 1 — 静态分析**（自动）

扫描项目目录，检测 marker 文件（`pyproject.toml`、`package.json`、`Cargo.toml`、`go.mod` 等）。检测内容：

- 项目模块（含 marker 文件的子目录）
- 技术栈（Python/Node.js/Rust/Go/Java/C++）
- 依赖清单、入口文件、测试/构建/lint 命令
- CI 配置（GitHub Actions/GitLab CI/Jenkins/CircleCI）
- 目录树结构、已有文档（README.md, CLAUDE.md）
- 架构模式（REST API、数据库 ORM、前端 SPA、容器化等）

如果根目录本身有 marker 文件且无子模块，自动识别为**单模块项目**。

**阶段 2 — 交互式问答**

根据扫描结果，问 7 个问题（已检测到的跳过或提供默认值）：

| # | 问题 | 说明 |
|---|------|------|
| Q1 | 项目主要用途 | 如果 README 中有描述会自动提取 |
| Q2 | 模块职责 | 展示检测到的模块，未识别的会问你 |
| Q3 | 跨模块依赖 | 仅多模块项目。格式：`frontend -> backend : REST API` |
| Q4 | QA 要求 | 展示检测到的测试/lint 命令，确认或修改 |
| Q5 | 敏感路径 | 默认 `.env`, `.env.*`, `*.key`, `*.pem`，可追加 |
| Q6 | 耦合程度 | 仅多模块项目。1=松散 2=适中 3=紧密 |
| Q7 | 分支前缀 | 默认 `af`，可改为任意值 |

**阶段 3 — 生成文件**

| 文件 | 生成条件 | 作用 |
|------|----------|------|
| `orchestrator.yaml` | 始终 | 编排配置 |
| `CLAUDE.md`（根目录） | 始终 | 项目级 Agent 上下文指令 |
| `ARCHITECTURE.md` | 始终 | 架构文档 |
| `<module>/CLAUDE.md` | 每个模块 | 模块级 Agent 上下文 |
| `<module>/STATUS.md` | 每个模块 | 模块状态追踪 |
| `CONTRACTS.md` | 耦合程度 >= 2 | 跨模块接口契约 |
| `docs/agents/protocol.md` | 始终 | 协调协议 |
| `docs/agents/conventions.md` | 始终 | 编码规范 |
| `docs/agents/boundaries.md` | 始终 | 约束边界 |
| `.orchestrator/` | 始终 | 日志、会话、邮箱目录 |
| `.gitignore` | 更新 | 忽略日志和会话文件 |

### 方式 B：`init`（快速）

只生成 `orchestrator.yaml` + `STATUS.md` + `.orchestrator/`。不生成 CLAUDE.md 和 CONTRACTS.md。

```bash
cd your-project

# 自动检测
lindy-orchestrate init

# 手动指定模块
lindy-orchestrate init --modules "backend,frontend,worker"

# 调整扫描深度
lindy-orchestrate init --depth 2

# 不生成 STATUS.md
lindy-orchestrate init --no-status

# 覆盖已有文件
lindy-orchestrate init --force
```

**选项：**

| 选项 | 短选项 | 说明 |
|------|--------|------|
| `--modules LIST` | `-m` | 逗号分隔的模块名（跳过自动检测） |
| `--depth N` | — | 扫描深度，默认 1 |
| `--no-status` | — | 不生成 STATUS.md |
| `--force` | — | 覆盖已有文件 |

---

## 配置详解

### orchestrator.yaml 完整 schema

```yaml
# ─── 项目基本信息 ───
project:
  name: "my-project"           # 项目名
  branch_prefix: "af"          # 任务分支前缀：af/task-1, af/task-2

# ─── 模块定义 ───
modules:
  - name: backend              # 模块名
    path: backend/             # 相对路径
    status_md: STATUS.md       # 默认 STATUS.md
    claude_md: CLAUDE.md       # 默认 CLAUDE.md
    repo: myorg/my-backend     # GitHub repo slug（CI check 需要）
    ci_workflow: ci.yml        # CI workflow 文件名
    role: ""                   # "qa" 标记为 QA 模块

  - name: frontend
    path: frontend/
    repo: myorg/my-frontend

  - name: qa
    path: qa/
    role: qa                   # Agent check 门禁会派发到此模块

# ─── 规划器 ───
planner:
  mode: cli                    # cli = claude -p（无需 API key）
  # mode: api                  # api = Anthropic SDK（需 ANTHROPIC_API_KEY）
  model: claude-sonnet-4-20250514  # api 模式使用的模型
  max_tokens: 4096             # 规划器最大输出 token
  timeout_seconds: 120         # 规划器超时
  # prompt_template: path/to/template.j2  # 自定义 Jinja2 模板路径

# ─── 派发器 ───
dispatcher:
  provider: claude_cli         # claude_cli（默认）或 codex_cli
  timeout_seconds: 1800        # 单任务硬超时（30 分钟）
  stall_timeout_seconds: 600   # 向后兼容的 stall 超时
  stall_escalation:            # 两阶段 stall 升级
    warn_after_seconds: 300    # 300 秒无输出 → 发出警告事件
    kill_after_seconds: 600    # 600 秒无输出 → 终止进程
  permission_mode: bypassPermissions  # Agent 权限模式
  max_output_chars: 50000      # 输出截断阈值

# ─── QA 门禁 ───
qa_gates:
  ci_check:                    # CI 检查参数
    timeout_seconds: 900       # CI 轮询超时
    poll_interval: 30          # 轮询间隔

  structural:                  # 结构化检查
    max_file_lines: 500        # 单文件最大行数
    enforce_module_boundary: true  # 是否强制模块边界
    sensitive_patterns:        # 敏感文件匹配模式
      - ".env"
      - "*.key"
      - "*.pem"

  layer_check:                 # 层级检查
    enabled: true              # 是否启用
    unknown_file_policy: skip  # skip | warn

  custom:                      # 自定义 QA 门禁
    - name: backend-pytest
      command: "pytest --tb=short -q"
      cwd: "{module_path}"    # {module_path} 替换为模块实际路径
      timeout: 600             # 命令超时
      modules: []              # 空 = 所有模块；指定则只对特定模块生效
    - name: frontend-lint
      command: "npm run lint"
      cwd: "{module_path}"

  # 模块级快捷语法（会被规范化为 custom 列表）：
  # backend:
  #   - name: pytest
  #     command: "cd backend && pytest"
  # frontend:
  #   - name: playwright
  #     command: "npx playwright test"

# ─── 安全设置 ───
safety:
  dry_run: false               # true = 模拟全流程但不派发 agent
  max_retries_per_task: 2      # QA 失败后自动重试次数
  max_parallel: 3              # 最大并行 agent 数

# ─── 日志设置 ───
logging:
  dir: ".orchestrator/logs"
  session_dir: ".orchestrator/sessions"
  log_file: "actions.jsonl"

# ─── 邮箱系统 ───
mailbox:
  enabled: true                # 启用邮箱（默认 true）
  dir: ".orchestrator/mailbox" # 邮箱存储目录
  inject_on_dispatch: true     # 派发时自动注入待处理消息

# ─── Tracker 集成 ───
tracker:
  enabled: false               # 启用 tracker
  provider: github             # github | linear
  repo: ""                     # GitHub repo slug
  labels:                      # 过滤标签
    - orchestrator
  sync_on_complete: true       # 完成时自动评论并关闭 issue
```

### 关键配置说明

**qa_gates 模块级快捷语法**

除了标准的 `custom` 列表，还支持按模块名分组的快捷写法：

```yaml
qa_gates:
  backend:
    - name: pytest
      command: "cd backend && pytest"
  frontend:
    - name: playwright
      command: "npx playwright test"
```

这会被自动规范化为：

```yaml
qa_gates:
  custom:
    - name: pytest
      command: "cd backend && pytest"
      modules: ["backend"]
      cwd: "."
    - name: playwright
      command: "npx playwright test"
      modules: ["frontend"]
      cwd: "."
```

**stall_escalation 两阶段停滞升级**

v0.8.0 引入了两阶段 stall 升级机制：

1. **Stage 1 — 警告**：`warn_after_seconds` 秒无输出后，发出 `stall_warning` 事件
2. **Stage 2 — 终止**：`kill_after_seconds` 秒无输出后，终止进程

特殊处理：
- 首次事件有宽限期：阈值翻倍（最低 300s 警告 / 600s 终止）
- Bash 工具感知：当最后一个工具是 Bash 时，阈值增加 50%（适应长时间构建/测试）
- 向后兼容：`stall_timeout_seconds` 仍然有效，未配置 `stall_escalation` 时回退到旧逻辑

### STATUS.md

每个模块的 STATUS.md 是 Agent 了解模块现状的入口。生成后应填入实际状态：

```markdown
## Meta
| Key | Value |
|-----|-------|
| module | backend |
| last_updated | 2026-03-02 10:00 UTC |
| overall_health | GREEN |
| agent_session | — |

## Active Work
| ID | Task | Status | BlockedBy | Started | Notes |
|----|------|--------|-----------|---------|-------|
| BE-001 | 用户认证 API | IN_PROGRESS | — | 2026-03-01 | JWT 方案 |

## Completed (Recent)
| ID | Task | Completed | Outcome |
|----|------|-----------|---------|
| BE-000 | 项目初始化 | 2026-02-28 | 骨架搭建完成 |

## Backlog
- [ ] 接入 OAuth2
- [ ] 添加 rate limiting

## Cross-Module Requests
| ID | From | To | Request | Priority | Status |
|----|------|----|---------|----------|--------|

## Cross-Module Deliverables
| ID | From | To | Deliverable | Status | Path |
|----|------|----|-------------|--------|------|

## Key Metrics
| Metric | Value |
|--------|-------|
| test_coverage | 45% |
| api_endpoints | 12 |

## Blockers
- (none)
```

**健康度含义：**
- `GREEN` — 正常推进
- `YELLOW` — 有风险，需要关注
- `RED` — 被阻塞，无法推进

---

## CLI 命令参考

### run — 执行目标

执行完整的编排流程：读取 STATUS.md → LLM 分解目标 → 按依赖顺序并行派发 → QA 门禁 → 报告。

```bash
# 基本用法
lindy-orchestrate run "在 backend 模块添加 /api/users CRUD 端点"

# 从文件读取目标
lindy-orchestrate run --file goal.md

# 从 stdin 读取
echo "重构数据层" | lindy-orchestrate run --file -

# 执行已保存的计划文件（跳过规划步骤）
lindy-orchestrate run --plan .orchestrator/plans/latest.json

# 模拟运行（不实际派发 agent）
lindy-orchestrate run "Refactor the data layer" --dry-run

# 指定 provider
lindy-orchestrate run "Add tests" --provider codex_cli

# 详细输出
lindy-orchestrate run "Add auth" -v

# 指定配置文件
lindy-orchestrate run "Fix bugs" -c path/to/config.yaml
```

**选项：**

| 选项 | 短选项 | 说明 |
|------|--------|------|
| `GOAL` | — | 位置参数，自然语言目标 |
| `--file PATH` | `-f` | 从文件读取目标（`-` 表示 stdin） |
| `--plan PATH` | `-p` | 执行已保存的计划 JSON（跳过 LLM 规划） |
| `--config PATH` | `-c` | 指定配置文件路径 |
| `--dry-run` | — | 模拟运行，不实际派发 agent |
| `--verbose` | `-v` | 显示详细输出 |
| `--provider NAME` | — | 指定 provider：`claude_cli` 或 `codex_cli` |

**执行流程：**

```
读取所有 STATUS.md → LLM 分解目标 → 按依赖顺序并行派发
                                       ↓
                             Agent 在模块目录中工作
                             （写代码 → commit → push）
                                       ↓
                             运行 QA 门禁
                                       ↓
                           通过 → 标记完成     失败 → 带反馈重试
                                                 ↓
                                       超过重试次数 → 标记失败
```

### plan — 规划任务

只生成任务计划，不执行。

```bash
# 基本用法
lindy-orchestrate plan "Add user authentication with JWT"

# 从文件读取目标
lindy-orchestrate plan --file goal.md

# 保存为 JSON
lindy-orchestrate plan "Add user authentication" -o plan.json

# 指定配置
lindy-orchestrate plan "Add auth" -c config.yaml
```

**选项：**

| 选项 | 短选项 | 说明 |
|------|--------|------|
| `GOAL` | — | 位置参数，自然语言目标 |
| `--file PATH` | `-f` | 从文件读取目标 |
| `--output PATH` | `-o` | 另存为 JSON 文件 |
| `--config PATH` | `-c` | 指定配置文件路径 |

计划会自动保存到 `.orchestrator/plans/` 目录。输出包含每个任务的目标模块、描述、依赖关系、QA 检查项和 prompt。

**先 plan，审阅后再 run** 是推荐的工作流：

```bash
lindy-orchestrate plan "Refactor data layer" -o plan.json
# 审阅 plan.json，确认任务分解合理
lindy-orchestrate run --plan plan.json
```

### resume — 恢复会话

从上次检查点恢复执行。跳过已完成的任务，重置失败任务为 pending 状态重试。

```bash
# 恢复最近一次会话
lindy-orchestrate resume

# 恢复指定会话
lindy-orchestrate resume abc123

# 详细输出
lindy-orchestrate resume -v

# 指定配置
lindy-orchestrate resume -c config.yaml
```

**选项：**

| 选项 | 短选项 | 说明 |
|------|--------|------|
| `SESSION_ID` | — | 位置参数，会话 ID（留空恢复最近） |
| `--config PATH` | `-c` | 指定配置文件路径 |
| `--verbose` | `-v` | 详细输出 |

恢复逻辑：
- 已完成的任务保持不变
- 失败的任务重置为 pending，重试计数归零
- 被跳过的任务（因依赖失败而跳过）在依赖不再失败时也重置为 pending

### status — 查看状态

展示模块健康度概览和最近日志。

```bash
# 默认：状态表 + 最近 10 条日志
lindy-orchestrate status

# JSON 输出
lindy-orchestrate status --json

# 仅看状态表
lindy-orchestrate status --status-only

# 仅看日志
lindy-orchestrate status --logs-only

# 指定日志条数
lindy-orchestrate status -n 20

# 指定配置
lindy-orchestrate status -c config.yaml
```

**选项：**

| 选项 | 短选项 | 说明 |
|------|--------|------|
| `--config PATH` | `-c` | 指定配置文件路径 |
| `--json` | — | JSON 输出（包含 modules、logs、mailbox） |
| `--last N` | `-n` | 最近 N 条日志，默认 10 |
| `--logs-only` | — | 仅显示日志 |
| `--status-only` | — | 仅显示状态表 |

输出内容：
- 每个模块的健康度（GREEN/YELLOW/RED）、活跃任务数、待处理请求数、阻塞项数
- 邮箱待处理消息汇总（如果启用了 mailbox）
- 最近重要日志条目

### logs — 查看日志

`status --logs-only` 的快捷方式。

```bash
lindy-orchestrate logs            # 最近 20 条
lindy-orchestrate logs -n 50      # 最近 50 条
lindy-orchestrate logs --json     # 原始 JSONL
```

**选项：**

| 选项 | 短选项 | 说明 |
|------|--------|------|
| `--last N` | `-n` | 最近 N 条，默认 20 |
| `--config PATH` | `-c` | 指定配置文件路径 |
| `--json` | — | 原始 JSONL 输出 |

日志存储在 `.orchestrator/logs/actions.jsonl`，追加写入。

### validate — 校验配置

检查配置语法、模块路径、STATUS.md 可解析性、CLI 可用性。

```bash
lindy-orchestrate validate
lindy-orchestrate validate -c path/to/config.yaml
```

**选项：**

| 选项 | 短选项 | 说明 |
|------|--------|------|
| `--config PATH` | `-c` | 指定配置文件路径 |

检查项：
- 配置文件语法合法性
- 每个模块路径是否存在
- STATUS.md 是否存在且可解析（输出 health、active work 数、blockers 数）
- Claude CLI 是否在 PATH 中

### gc — 垃圾回收

清理 Agent 生成的工件残留。**默认 dry run，不执行任何删除操作。**

```bash
# 预览要清理的内容
lindy-orchestrate gc

# 实际执行清理
lindy-orchestrate gc --apply

# 自定义阈值
lindy-orchestrate gc --branch-age 7 --session-age 14 --log-size 5 --status-stale 3

# 指定配置
lindy-orchestrate gc -c config.yaml
```

**选项：**

| 选项 | 短选项 | 说明 |
|------|--------|------|
| `--config PATH` | `-c` | 指定配置文件路径 |
| `--apply` | — | 实际执行清理（默认 dry run） |
| `--branch-age N` | — | 任务分支最大存活天数，默认 14 |
| `--session-age N` | — | 会话文件最大存活天数，默认 30 |
| `--log-size N` | — | 日志文件最大体积（MB），默认 10 |
| `--status-stale N` | — | STATUS.md 过期阈值（天），默认 7 |

清理范围：

| 类别 | 行为 |
|------|------|
| **stale_branch** | 删除超龄的 `{prefix}/task-*` 本地分支 |
| **old_session** | 将超龄的会话文件归档到 `sessions/archive/` |
| **log_rotation** | 超大日志文件重命名（加时间戳），创建空日志 |
| **status_drift** | 提示长期未更新的 STATUS.md（仅报告，不修改） |
| **orphan_plan** | 报告超过 30 天且无会话引用的计划文件 |

### scan — 熵扫描

扫描项目中的架构漂移、契约违规和质量衰退。

```bash
# 全量扫描
lindy-orchestrate scan

# 只扫描特定模块
lindy-orchestrate scan --module backend

# 只看评分
lindy-orchestrate scan --grade-only

# 指定配置
lindy-orchestrate scan -c config.yaml
```

**选项：**

| 选项 | 短选项 | 说明 |
|------|--------|------|
| `--config PATH` | `-c` | 指定配置文件路径 |
| `--module NAME` | — | 只扫描指定模块 |
| `--grade-only` | — | 只显示评分（A-F） |

扫描维度：

| 检查项 | 检测内容 |
|--------|----------|
| **architecture_drift** | ARCHITECTURE.md 声明与实际文件系统的不一致 |
| **contract_violation** | CONTRACTS.md 缺失必要章节、未覆盖模块 |
| **status_drift** | STATUS.md 健康度无效、长期未更新、IN_PROGRESS 任务但分支不存在 |
| **quality** | 超大文件（>500 行）、缺少测试目录 |

每个发现项包含严重级别（error/warning/info）和修复建议。每个模块会获得 A-F 评分。

### mailbox — 邮箱系统

查看或发送跨模块消息。Agent 在执行时可以通过邮箱进行异步通信。

```bash
# 查看所有模块的邮箱汇总
lindy-orchestrate mailbox

# 查看特定模块的待处理消息
lindy-orchestrate mailbox frontend

# JSON 输出
lindy-orchestrate mailbox frontend --json

# 发送消息
lindy-orchestrate mailbox --send-to backend --send-from frontend -m "Need REST API for /users"

# 带优先级发送
lindy-orchestrate mailbox --send-to backend --send-from frontend -m "Urgent: API broken" -p urgent
```

**选项：**

| 选项 | 短选项 | 说明 |
|------|--------|------|
| `MODULE` | — | 位置参数，查看特定模块消息（留空看汇总） |
| `--send-to NAME` | — | 收件模块 |
| `--send-from NAME` | — | 发件模块（默认 `cli`） |
| `--message TEXT` | `-m` | 消息内容 |
| `--priority LEVEL` | `-p` | 优先级：low / normal / high / urgent |
| `--config PATH` | `-c` | 指定配置文件路径 |
| `--json` | — | JSON 输出 |

**邮箱系统原理：**

- 存储：`.orchestrator/mailbox/{module}.jsonl`，每个模块一个 JSONL 文件
- 线程安全：支持并发读写
- 消息类型：request / response / notification
- 状态：pending → read → acknowledged
- `inject_on_dispatch: true` 时，Agent 派发前会自动注入该模块的待处理消息到 prompt 中
- 无需外部依赖，纯文件系统实现

### issues — 查看 Issue

从配置的 tracker（GitHub Issues）拉取 issue 列表。

```bash
# 基本用法
lindy-orchestrate issues

# 按标签过滤
lindy-orchestrate issues --label bug

# 过滤状态
lindy-orchestrate issues --status closed

# 限制数量
lindy-orchestrate issues -n 50

# JSON 输出
lindy-orchestrate issues --json
```

**选项：**

| 选项 | 短选项 | 说明 |
|------|--------|------|
| `--config PATH` | `-c` | 指定配置文件路径 |
| `--label NAME` | — | 按标签过滤 |
| `--status STATE` | — | Issue 状态过滤，默认 `open` |
| `--limit N` | `-n` | 最大获取数，默认 20 |
| `--json` | — | JSON 输出 |

**前提：** 配置 `tracker.enabled: true` 和 `tracker.repo`，并安装配置好 `gh` CLI。

### run-issue — 执行 Issue

从 tracker 拉取 issue 并作为目标执行。

```bash
# 执行 issue #42
lindy-orchestrate run-issue 42

# 模拟运行
lindy-orchestrate run-issue 42 --dry-run

# 详细输出
lindy-orchestrate run-issue 42 -v
```

**选项：**

| 选项 | 短选项 | 说明 |
|------|--------|------|
| `ISSUE_ID` | — | 位置参数（必填），Issue ID |
| `--config PATH` | `-c` | 指定配置文件路径 |
| `--dry-run` | — | 只规划，不执行 |
| `--verbose` | `-v` | 详细输出 |

完成后的行为（当 `tracker.sync_on_complete: true`）：
- 自动在 issue 上添加执行摘要评论
- 所有任务成功 → 自动关闭 issue
- 有任务失败 → 只添加评论，不关闭

### version — 版本信息

```bash
lindy-orchestrate version          # 文本格式
lindy-orchestrate version --json   # JSON 格式
lindy-orchestrate --version        # 全局选项
lindy-orchestrate -V               # 短选项
```

---

## Provider 系统

v0.8.0 引入了 provider 抽象层，支持多种 Agent CLI 后端。

### 可用 Provider

| Provider | 值 | CLI 工具 | 说明 |
|----------|----|----------|------|
| Claude CLI | `claude_cli` | `claude` | 默认 provider，使用 Claude Code CLI |
| Codex CLI | `codex_cli` | `codex` | OpenAI Codex CLI |

### 配置方式

```yaml
# 在 orchestrator.yaml 中设置默认 provider
dispatcher:
  provider: claude_cli  # 或 codex_cli
```

```bash
# 通过 CLI 参数覆盖（仅 run 命令）
lindy-orchestrate run "Add tests" --provider codex_cli
```

### Provider 行为差异

两种 provider 共享相同的 dispatch 抽象，都支持：
- 流式 dispatch（heartbeat/stall 检测）
- 阻塞 dispatch（规划、报告生成）
- 两阶段 stall 升级（warn → kill）
- 输出截断保护

区别在于底层 CLI 命令：

| 特性 | claude_cli | codex_cli |
|------|-----------|-----------|
| 流式命令 | `claude -p <prompt> --output-format stream-json --verbose` | `codex --prompt <prompt> --output-format stream-json` |
| 阻塞命令 | `claude -p <prompt> --output-format json` | `codex --prompt <prompt> --output-format json` |
| 权限参数 | `--permission-mode bypassPermissions` | `--approval-mode full-auto` |

---

## 实时 DAG 仪表盘

执行任务时，终端会显示一个实时更新的 DAG 仪表盘，展示任务执行状态。

### 功能

- **ASCII 任务 DAG 树**：展示任务依赖关系和当前状态
- **状态图标**：每个任务的状态用图标标识（completed/failed/running/pending/skipped）
- **摘要栏**：显示各状态任务数量和已用时间
- **注解气泡**（verbose 模式）：显示每个任务正在使用的工具或最新状态

### TTY vs 非 TTY

| 环境 | 行为 |
|------|------|
| **TTY 终端** | Rich Live 面板，每秒刷新 4 次，执行完成后打印最终 DAG 快照 |
| **非 TTY**（CI、管道） | 回退到文本进度输出，不使用 Live 面板 |

Dashboard 通过订阅 Hook 事件驱动刷新，响应以下事件：
- `task_started` / `task_completed` / `task_failed` / `task_retrying` / `task_skipped`
- `task_heartbeat`（verbose 模式下显示当前工具名）
- `qa_passed` / `qa_failed`
- `checkpoint_saved` / `stall_warning`

使用 `-v` 启用 verbose 模式查看更详细的注解：

```bash
lindy-orchestrate run "My goal" -v
```

---

## Hook / 事件系统

编排器内部使用事件驱动架构。`HookRegistry` 是中央事件总线，所有组件通过它通信。

### 事件类型

| 事件 | 触发时机 |
|------|----------|
| `task_started` | 任务开始执行 |
| `task_completed` | 任务执行成功 |
| `task_failed` | 任务执行失败 |
| `task_retrying` | QA 失败后重试 |
| `task_skipped` | 依赖失败导致跳过 |
| `qa_passed` | QA 门禁通过 |
| `qa_failed` | QA 门禁失败 |
| `stall_warning` | Agent 无输出达到警告阈值 |
| `stall_killed` | Agent 无输出达到终止阈值 |
| `task_heartbeat` | Agent 产生事件（携带工具名） |
| `checkpoint_saved` | 会话检查点保存 |
| `mailbox_message` | 邮箱消息事件 |
| `session_start` | 会话开始 |
| `session_end` | 会话结束 |

### 使用方式

Dashboard 是 Hook 系统的主要消费者。它在 `start()` 时注册事件处理函数，根据事件更新 DAG 面板显示。

事件数据结构：

```python
@dataclass
class Event:
    type: EventType          # 事件类型
    timestamp: str           # ISO 时间戳
    data: dict[str, Any]     # 事件附带数据
    task_id: int | None      # 关联的任务 ID
    module: str              # 关联的模块名
```

HookRegistry API：
- `on(event_type, handler)` — 注册特定事件处理函数
- `on_any(handler)` — 注册全局处理函数（所有事件都触发）
- `emit(event)` — 发出事件
- `remove(event_type, handler)` / `remove_any(handler)` — 移除处理函数
- `clear()` — 移除所有处理函数

---

## QA 门禁详解

QA 门禁在每个任务执行完成后自动运行。支持四种类型：

### 1. CI Check

轮询 GitHub Actions CI 状态。需要 `gh` CLI 和模块配置 `repo` + `ci_workflow`。

```yaml
# 由 LLM 在 plan 中自动分配
qa_checks:
  - gate: ci_check
    params:
      repo: myorg/my-backend
      branch: af/task-1
      workflow: ci.yml
      timeout_seconds: 600
      poll_interval: 30
```

### 2. Command Check

运行 shell 命令，exit code 0 = 通过。

```yaml
qa_gates:
  custom:
    - name: backend-pytest
      command: "pytest --tb=short -q"
      cwd: "{module_path}"
      timeout: 600
```

### 3. Agent Check

派发独立的 QA Agent 验证。需要一个 `role: qa` 的模块。

```yaml
modules:
  - name: qa
    path: qa/
    role: qa
```

QA Agent 收到前序任务输出作为上下文，输出 `QA_RESULT: PASS` 或 `QA_RESULT: FAIL` + `FAILURE_REASON: ...`。

### 4. Structural Check（内置）

自动运行的结构化检查，不需要额外配置：

| 检查 | 说明 |
|------|------|
| **file_size** | 文件超过 `max_file_lines` 行 → 建议拆分 |
| **sensitive_file** | 匹配 `.env`、`*.key`、`*.pem` 等 → 建议加 .gitignore |
| **import_boundary** | 跨模块直接导入 → 建议使用 CONTRACTS.md 接口 |

### 5. Layer Check（内置）

基于 ARCHITECTURE.md 中的层级声明，验证模块内部的导入方向：

```markdown
# ARCHITECTURE.md 中的声明
- **backend/**: models → schemas → services → routes → main
```

规则：`layer[i]` 只能导入 `layer[j]`（j <= i）。共享目录（utils/、shared/、common/）免检。

配置：

```yaml
qa_gates:
  layer_check:
    enabled: true
    unknown_file_policy: skip  # skip = 跳过未知文件，warn = 报告
```

---

## 结构化 QA 反馈与渐进式重试

### 结构化反馈

QA 失败时，编排器不是简单地将原始输出塞回 prompt，而是：

1. **分类**：将失败归类为 test_failure / lint_error / type_error / build_error / boundary_violation / timeout
2. **解析**：针对不同工具（pytest / ruff / tsc）提取结构化的错误信息
3. **定位**：提取涉及的文件路径和行号
4. **指导**：附加针对性的修复建议

支持的解析器：

| 工具 | 提取内容 |
|------|----------|
| pytest | FAILED 行、断言内容、short test summary |
| ruff / eslint / flake8 | 文件:行:列、规则 ID、消息 |
| tsc | 文件(行,列)、TS 错误码、类型不匹配描述 |
| 通用 | 截断输出 + 通用修复指南 |

### 渐进式重试

重试 prompt 根据重试次数逐步聚焦：

| 重试次数 | Prompt 策略 |
|----------|-------------|
| 第 1 次 | 完整原始 prompt + 结构化反馈（错误列表 + 文件路径 + 修复步骤） |
| 第 2 次及以后 | 精简 prompt，只包含失败的具体错误和文件，指示 "不要重新阅读整个代码库，直接去修" |

最大重试次数由 `safety.max_retries_per_task` 控制。

---

## Dispatch 模式

| 模式 | 函数 | 适用场景 |
|------|------|----------|
| **流式** | `dispatch_agent()` | 长任务 — 实时心跳监控、stall 两阶段升级、事件回调 |
| **阻塞** | `dispatch_agent_simple()` | 短任务 — 规划、报告生成，无线程开销 |

**流式 Dispatch 特性：**

- 使用 `--output-format stream-json` 获取 JSONL 事件流
- 后台线程读取 stdout，解析事件
- 每行输出重置 stall 计时器
- 提取工具使用信息（`tool_use` 事件）
- 提取最终结果（`result` 事件）
- 输出超过 `max_output_chars` 时自动截断（保留首尾各半）

**阻塞 Dispatch 特性：**

- 使用 `--output-format json` 获取完整 JSON 响应
- `subprocess.run()` 同步等待
- 解析 JSON 中的 `result` 字段
- 同样支持输出截断

---

## 执行总结报告

每次执行（`run` / `resume` / `run-issue`）完成后，编排器会：

1. **终端输出**：
   - Header Panel — 目标完成/暂停状态、session ID、任务统计、总时长
   - Task Details 表 — 每个任务的模块、描述、状态、时长、重试次数、QA 结果、输出预览
   - Execution Metrics 表 — 总任务数、完成/失败/跳过数、总时长、预估成本

2. **Markdown 报告**：
   - 保存到 `.orchestrator/reports/{session_id}_summary.md`
   - 包含完整的任务明细表和每个任务的 QA 结果及输出预览

---

## 会话管理与检查点

### 会话状态

每次 `run` / `run-issue` 创建一个会话，状态持久化到 `.orchestrator/sessions/{session_id}.json`。

会话字段：
- `session_id` — 8 位 UUID
- `goal` — 目标描述
- `status` — in_progress / completed / paused / failed
- `plan_json` — 完整的 TaskPlan 快照（用于 resume）
- `checkpoint_count` — 检查点计数
- `started_at` / `completed_at` — 时间戳

### 检查点

执行过程中，每个任务完成后会自动保存检查点（更新 `plan_json`）。如果执行中断，可以用 `resume` 从检查点恢复。

### 会话安全

- 会话 ID 经过正则校验（仅允许字母数字、下划线、横线），防止路径穿越攻击
- 文件加载时校验路径是否在 sessions 目录内

---

## 目标描述最佳实践

编排器效果直接取决于目标描述的质量。

**好的目标：**

```bash
# 具体、可验证
lindy-orchestrate run "在 backend 模块添加 /api/users CRUD 端点，使用 SQLAlchemy ORM，包含 pytest 测试"

# 明确模块范围
lindy-orchestrate run "dashboard 模块：用 Recharts 添加每日 PnL 折线图，数据从 /api/portfolio 获取"

# 有约束条件
lindy-orchestrate run "重构 data 模块的数据获取逻辑，改用 async/await，保持 API 接口不变"
```

**差的目标：**

```bash
lindy-orchestrate run "改进系统"         # 太模糊
lindy-orchestrate run "搭建完整的交易系统"  # 太大
lindy-orchestrate run "修 bug"           # 没有上下文
```

**经验法则：** 如果无法在 3 句话内描述验收标准，目标需要拆分。

---

## 项目结构示例

### 单模块项目

```
my-app/
├── orchestrator.yaml
├── CLAUDE.md
├── ARCHITECTURE.md
├── STATUS.md
├── .orchestrator/
│   ├── logs/
│   │   └── actions.jsonl
│   ├── sessions/
│   ├── mailbox/
│   ├── plans/
│   └── reports/
├── pyproject.toml
├── src/
└── tests/
```

### 多模块项目

```
my-platform/
├── orchestrator.yaml
├── CLAUDE.md
├── ARCHITECTURE.md
├── CONTRACTS.md                   # 耦合度 >= 2 才生成
├── docs/
│   └── agents/
│       ├── protocol.md
│       ├── conventions.md
│       └── boundaries.md
├── .orchestrator/
│   ├── logs/
│   │   └── actions.jsonl
│   ├── sessions/
│   ├── mailbox/
│   │   ├── backend.jsonl
│   │   └── frontend.jsonl
│   ├── plans/
│   │   └── latest.json
│   └── reports/
│       └── abc123_summary.md
├── backend/
│   ├── CLAUDE.md
│   ├── STATUS.md
│   └── ...
├── frontend/
│   ├── CLAUDE.md
│   ├── STATUS.md
│   └── ...
└── qa/
    ├── CLAUDE.md
    ├── STATUS.md
    └── ...
```

---

## 常见问题排查

### Claude CLI 未找到

```
Error: Claude CLI not found in PATH.
```

安装 Claude Code CLI：https://docs.anthropic.com/en/docs/claude-code

### Codex CLI 未找到

```
Error: Codex CLI not found in PATH.
```

安装 Codex CLI：https://github.com/openai/codex

### 没检测到模块

```
No modules detected.
```

项目根目录下没有含 marker 文件的子目录。解决方案：
- `lindy-orchestrate init --modules "myapp"` 手动指定
- 如果根目录本身就是项目（有 `pyproject.toml` 等），`onboard` 会自动将根目录识别为单模块

### STATUS.md 解析错误

解析器是容错的，不会崩溃。但表格格式不对时字段可能为空。用 `validate` 检查：

```bash
lindy-orchestrate validate
```

### Agent 超时 / 停滞

调整 `orchestrator.yaml`：

```yaml
dispatcher:
  timeout_seconds: 3600          # 延长硬超时到 1 小时
  stall_escalation:
    warn_after_seconds: 600      # 延长警告到 10 分钟
    kill_after_seconds: 1200     # 延长终止到 20 分钟
```

### 想跳过 QA 快速迭代

去掉 `qa_gates.custom` 配置项即可。CI check 和 Agent check 只在 LLM 规划时自动分配，不配置 repo 就不会触发。

### Tracker 未启用

```
Tracker is disabled. Set tracker.enabled: true in config.
```

在 `orchestrator.yaml` 中启用：

```yaml
tracker:
  enabled: true
  provider: github
  repo: yourorg/yourrepo
```

### Mailbox 未启用

```
Mailbox is disabled. Set mailbox.enabled: true in config.
```

在 `orchestrator.yaml` 中启用：

```yaml
mailbox:
  enabled: true
```

### 配置文件找不到

```
No orchestrator.yaml found. Run `lindy-orchestrate init` first.
```

编排器从当前目录向上搜索最多 10 层父目录来查找 `orchestrator.yaml`。确保在项目目录内运行命令，或用 `-c` 指定路径：

```bash
lindy-orchestrate status -c /path/to/orchestrator.yaml
```
