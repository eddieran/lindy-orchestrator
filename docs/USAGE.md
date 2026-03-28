# lindy-orchestrator v0.15.0 使用指南

> 轻量级、git 原生的多 Agent 编排框架。
> 辅助功能文档参见 [USAGE_helpers.md](USAGE_helpers.md)。

---

## 目录

1. [前置条件](#前置条件)
2. [安装](#安装)
3. [接入项目（onboard / init）](#接入项目)
4. [配置详解（orchestrator.yaml）](#配置详解)
5. [CLI 核心命令](#cli-核心命令)
   - [run — 执行目标](#run--执行目标)
   - [plan — 规划任务](#plan--规划任务)
   - [resume — 恢复会话](#resume--恢复会话)
   - [status — 查看状态](#status--查看状态)
   - [logs — 查看日志](#logs--查看日志)
   - [validate — 校验配置](#validate--校验配置)
   - [version — 版本信息](#version--版本信息)
6. [Provider 系统](#provider-系统)
7. [目标描述最佳实践](#目标描述最佳实践)
8. [项目结构示例](#项目结构示例)

辅助功能：gc、scan、DAG 仪表盘、Hook 事件系统、QA 门禁、执行报告、会话管理、常见问题排查 → 参见 [USAGE_helpers.md](USAGE_helpers.md)

---

## 前置条件

- Python 3.11+
- Git
- 至少安装以下一种 Agent CLI：
  - [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code)（默认 provider）
  - [Codex CLI](https://github.com/openai/codex)（可选 provider）
- GitHub CLI (`gh`)（如使用 CI check QA 门禁）

---

## 安装

```bash
# 使用 uv（推荐）
uvx lindy-orchestrate --version          # 直接运行，无需安装
# 或从源码：
git clone https://github.com/eddieran/lindy-orchestrator.git && cd lindy-orchestrator
uv sync --extra dev                      # 创建 .venv + 安装依赖
uv run lindy-orchestrate --version

# 使用 pip
pip install lindy-orchestrator
pip install -e ".[dev]"                  # 从源码安装
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
lindy-orchestrate onboard                              # 已有代码的项目
lindy-orchestrate onboard "一个基于 FastAPI 的后端服务"  # 空项目
lindy-orchestrate onboard --file description.md         # 从文件读取描述
cat desc.txt | lindy-orchestrate onboard --file -       # 从 stdin
lindy-orchestrate onboard --non-interactive             # 全自动
lindy-orchestrate onboard --force                       # 覆盖已有文件
lindy-orchestrate onboard --depth 2                     # 调整扫描深度
```

| 选项 | 短选项 | 说明 |
|------|--------|------|
| `DESCRIPTION` | — | 位置参数，项目描述（scaffold 模式需要） |
| `--file PATH` | `-f` | 从文件读取描述（`-` 表示 stdin） |
| `--depth N` | — | 目录扫描深度，默认 1 |
| `--non-interactive` | `-y` | 跳过确认提示，使用默认值 |
| `--force` | — | 覆盖已有文件 |

**三阶段流程（init+onboard 模式）：**

**阶段 1 — 静态分析**（自动）：扫描 marker 文件（`pyproject.toml`、`package.json`、`Cargo.toml`、`go.mod` 等），检测模块、技术栈、依赖、CI 配置、架构模式。

**阶段 2 — 交互式问答**：根据扫描结果问 7 个问题（已检测到的跳过）：项目用途、模块职责、跨模块依赖、QA 要求、敏感路径、耦合程度、分支前缀。

**阶段 3 — 生成文件**：

| 文件 | 作用 |
|------|------|
| `orchestrator.yaml` | 编排配置 |
| `CLAUDE.md`（根/模块） | Agent 上下文指令 |
| `ARCHITECTURE.md` | 架构文档 |
| `<module>/STATUS.md` | 模块状态追踪 |
| `CONTRACTS.md` | 跨模块接口契约（耦合度 >= 2） |
| `docs/agents/*.md` | 协调协议、编码规范、约束边界 |
| `.orchestrator/` | 日志、会话、邮箱目录 |

### 方式 B：`init`（快速）

只生成 `orchestrator.yaml` + `STATUS.md` + `.orchestrator/`。

```bash
lindy-orchestrate init                            # 自动检测
lindy-orchestrate init --modules "backend,frontend"  # 手动指定模块
lindy-orchestrate init --depth 2                  # 扫描深度
lindy-orchestrate init --no-status                # 不生成 STATUS.md
lindy-orchestrate init --force                    # 覆盖已有文件
```

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

# ─── 规划器 ───
planner:
  provider: claude_cli         # claude_cli（默认）或 codex_cli
  timeout_seconds: 120         # 规划器超时
  prompt: ""                   # 自定义规划 prompt（空 = 使用默认）

# ─── 生成器 ───
generator:
  provider: claude_cli         # claude_cli（默认）或 codex_cli
  timeout_seconds: 1800        # 单任务硬超时（30 分钟）
  stall_timeout: 600           # 600 秒无输出 → 终止进程
  permission_mode: bypassPermissions
  max_output_chars: 50000      # 输出截断阈值
  prompt_prefix: ""            # 注入到每个生成器 prompt 前

# ─── 评估器 ───
evaluator:
  provider: claude_cli         # claude_cli（默认）或 codex_cli
  timeout_seconds: 300         # 评估器超时
  pass_threshold: 80           # 低于此分数（0-100）触发重试
  prompt_prefix: ""            # 注入到每个评估器 prompt 前

# ─── QA 门禁 ───
qa_gates:
  ci_check:                    # CI 检查参数
    timeout_seconds: 900
    poll_interval: 30
  structural_check:            # 结构化检查
    max_file_lines: 500
    sensitive_patterns: [".env", "*.key", "*.pem"]
  custom:                      # 自定义 QA 门禁
    - name: backend-pytest
      command: "pytest --tb=short -q"
      cwd: "{module_path}"    # {module_path} 替换为模块路径
      timeout: 600
      diff_only: false
      modules: []              # 空 = 所有模块

# ─── 安全设置 ───
safety:
  dry_run: false
  max_retries_per_task: 2
  max_parallel: 3

# ─── 日志设置 ───
logging:
  dir: ".orchestrator/logs"
  session_dir: ".orchestrator/sessions"
  log_file: "actions.jsonl"

```

**qa_gates 模块级快捷语法：** 支持按模块名分组的写法（如 `backend:` 列表），会被自动规范化为 `custom` 列表并添加 `modules` 字段。

**向后兼容：** 旧格式 YAML 使用 `dispatcher:` 而非 `generator:` 仍可正常加载，会输出弃用警告。

---

## CLI 核心命令

### run — 执行目标

完整编排流程：读取 STATUS.md → LLM 分解 → 按依赖并行派发 → QA 门禁 → 报告。

```bash
lindy-orchestrate run "在 backend 模块添加 /api/users CRUD 端点"
lindy-orchestrate run --file goal.md                    # 从文件读取目标
echo "重构数据层" | lindy-orchestrate run --file -      # 从 stdin
lindy-orchestrate run --plan .orchestrator/plans/latest.json  # 执行已保存计划
lindy-orchestrate run "Add tests" --dry-run             # 模拟运行
lindy-orchestrate run "Add tests" --provider codex_cli  # 指定 provider
lindy-orchestrate run "Add auth" -v                     # 详细输出
lindy-orchestrate run "Fix bugs" -c path/to/config.yaml # 指定配置
```

| 选项 | 短选项 | 说明 |
|------|--------|------|
| `GOAL` | — | 位置参数，自然语言目标 |
| `--file PATH` | `-f` | 从文件读取目标（`-` 表示 stdin） |
| `--plan PATH` | `-p` | 执行已保存的计划 JSON（跳过 LLM 规划） |
| `--config PATH` | `-c` | 指定配置文件路径 |
| `--dry-run` | — | 模拟运行，不实际派发 agent |
| `--verbose` | `-v` | 显示详细输出 |
| `--provider NAME` | — | 指定 provider：`claude_cli` 或 `codex_cli` |

### plan — 规划任务

只生成任务计划，不执行。

```bash
lindy-orchestrate plan "Add user authentication with JWT"
lindy-orchestrate plan --file goal.md
lindy-orchestrate plan "Add auth" -o plan.json          # 另存为 JSON
lindy-orchestrate plan "Add auth" -c config.yaml
```

| 选项 | 短选项 | 说明 |
|------|--------|------|
| `GOAL` | — | 位置参数，自然语言目标 |
| `--file PATH` | `-f` | 从文件读取目标 |
| `--output PATH` | `-o` | 另存为 JSON 文件 |
| `--config PATH` | `-c` | 指定配置文件路径 |

计划自动保存到 `.orchestrator/plans/`。推荐工作流：先 `plan` 审阅，再 `run --plan`。

```bash
lindy-orchestrate plan "Refactor data layer" -o plan.json
# 审阅 plan.json
lindy-orchestrate run --plan plan.json
```

### resume — 恢复会话

从上次检查点恢复执行。跳过已完成任务，重置失败任务为 pending 状态。

```bash
lindy-orchestrate resume                 # 恢复最近一次
lindy-orchestrate resume abc123          # 恢复指定会话
lindy-orchestrate resume -v              # 详细输出
lindy-orchestrate resume -c config.yaml
```

| 选项 | 短选项 | 说明 |
|------|--------|------|
| `SESSION_ID` | — | 位置参数，会话 ID（留空恢复最近） |
| `--config PATH` | `-c` | 指定配置文件路径 |
| `--verbose` | `-v` | 详细输出 |

恢复逻辑：已完成保持不变；失败重置为 pending（重试计数归零）；被跳过的任务在依赖不再失败时也重置。

### status — 查看状态

展示模块健康度概览和最近日志。

```bash
lindy-orchestrate status                 # 状态表 + 最近 10 条日志
lindy-orchestrate status --json          # JSON 输出
lindy-orchestrate status --status-only   # 仅状态表
lindy-orchestrate status --logs-only     # 仅日志
lindy-orchestrate status -n 20           # 指定日志条数
```

| 选项 | 短选项 | 说明 |
|------|--------|------|
| `--config PATH` | `-c` | 指定配置文件路径 |
| `--json` | — | JSON 输出（含 modules、logs） |
| `--last N` | `-n` | 最近 N 条日志，默认 10 |
| `--logs-only` | — | 仅显示日志 |
| `--status-only` | — | 仅显示状态表 |

输出内容：模块健康度（GREEN/YELLOW/RED）、活跃任务数、待处理请求数、阻塞项数、最近日志。

### logs — 查看日志

`status --logs-only` 的快捷方式。

```bash
lindy-orchestrate logs            # 最近 20 条
lindy-orchestrate logs -n 50      # 最近 50 条
lindy-orchestrate logs --json     # 原始 JSONL
```

| 选项 | 短选项 | 说明 |
|------|--------|------|
| `--last N` | `-n` | 最近 N 条，默认 20 |
| `--config PATH` | `-c` | 指定配置文件路径 |
| `--json` | — | 原始 JSONL 输出 |

### validate — 校验配置

检查配置语法、模块路径、STATUS.md 可解析性、CLI 可用性。

```bash
lindy-orchestrate validate
lindy-orchestrate validate -c path/to/config.yaml
```

| 选项 | 短选项 | 说明 |
|------|--------|------|
| `--config PATH` | `-c` | 指定配置文件路径 |

### version — 版本信息

```bash
lindy-orchestrate version          # 文本格式
lindy-orchestrate version --json   # JSON 格式
lindy-orchestrate --version        # 全局选项
lindy-orchestrate -V               # 短选项
```

---

## Provider 系统

每个角色（planner/generator/evaluator）可独立配置不同的 provider，默认都是 `claude_cli`。

| Provider | 值 | CLI 工具 | 说明 |
|----------|----|----------|------|
| Claude CLI | `claude_cli` | `claude` | 默认 provider |
| Codex CLI | `codex_cli` | `codex` | OpenAI Codex CLI |

配置方式：

```yaml
generator:
  provider: claude_cli  # 或 codex_cli
evaluator:
  provider: codex_cli   # 可以用不同的 provider
```

```bash
lindy-orchestrate run "Add tests" --provider codex_cli  # CLI 参数覆盖
```

---

## 目标描述最佳实践

编排器效果直接取决于目标描述的质量。

```bash
# 好：具体、可验证、明确模块范围
lindy-orchestrate run "在 backend 模块添加 /api/users CRUD 端点，使用 SQLAlchemy ORM，包含 pytest 测试"
lindy-orchestrate run "dashboard 模块：用 Recharts 添加每日 PnL 折线图，数据从 /api/portfolio 获取"

# 差：太模糊、太大、缺少上下文
lindy-orchestrate run "改进系统"
lindy-orchestrate run "搭建完整的交易系统"
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
│   ├── logs/actions.jsonl
│   ├── sessions/
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
├── CONTRACTS.md
├── docs/agents/
│   ├── protocol.md
│   ├── conventions.md
│   └── boundaries.md
├── .orchestrator/
│   ├── logs/actions.jsonl
│   ├── sessions/
│   ├── plans/latest.json
│   └── reports/{session_id}_summary.md
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
