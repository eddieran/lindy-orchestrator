# lindy-orchestrator 使用指南

## 前置条件

- Python 3.11+
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) 已安装且在 PATH 中
- Git
- GitHub CLI (`gh`)（如果使用 CI check QA gate）

```bash
# 安装
pip install lindy-orchestrator

# 如需 Anthropic API 模式
pip install lindy-orchestrator[api]

# 验证
lindy-orchestrate --version
claude --version
```

---

## 第一步：接入项目

有两种方式，选一种。

### 方式 A：`onboard`（推荐）

```bash
cd your-project
lindy-orchestrate onboard
```

这是一个三阶段流程：

**阶段 1 — 静态分析**（自动）

扫描项目目录，检测 marker 文件（`pyproject.toml`、`package.json`、`Cargo.toml`、`go.mod` 等）。检测结果包括：

- 项目模块（子目录中有 marker 文件的即为模块）
- 每个模块的技术栈（Python/Node.js/Rust/Go/...）
- 依赖清单（从 manifest 中解析）
- 入口文件、测试/构建/lint 命令
- CI 配置（GitHub Actions/GitLab CI/Jenkins/CircleCI）
- 目录树结构
- 已有文档（README.md, CLAUDE.md）
- 架构模式（REST API、数据库 ORM、前端 SPA、容器化等）

如果项目根目录本身就有 marker 文件且没有子模块，会被识别为**单模块项目**。不需要手动指定模块数量。

**阶段 2 — 交互式问答**

根据扫描结果，问 7 个问题（已检测到的会跳过或提供默认值）：

| # | 问题 | 说明 |
|---|------|------|
| Q1 | 项目主要用途 | 如果 README 中有描述会自动提取，确认即可 |
| Q2 | 模块职责 | 展示检测到的模块，未识别到模式的会问你 |
| Q3 | 跨模块依赖 | 只在多模块时问。格式：`frontend -> backend : REST API` |
| Q4 | QA 要求 | 展示检测到的测试/lint 命令，确认或修改 |
| Q5 | 敏感路径 | 默认 `.env`, `.env.*`, `*.key`, `*.pem`，可追加 |
| Q6 | 耦合程度 | 只在多模块时问。1=松散 2=适中 3=紧密 |
| Q7 | 分支前缀 | 默认 `af`，可改为任意值如 `feat`, `roxy` |

不想回答可以用 `--non-interactive` 全自动：

```bash
lindy-orchestrate onboard --non-interactive
```

**阶段 3 — 生成文件**

| 文件 | 生成条件 | 作用 |
|------|----------|------|
| `orchestrator.yaml` | 始终 | 编排配置：模块定义、超时、QA 门禁、安全参数 |
| `CLAUDE.md`（根目录） | 始终 | 项目级 Agent 上下文指令 |
| `<module>/CLAUDE.md` | 每个模块 | 模块级 Agent 上下文（技术栈、目录结构、命令、边界） |
| `CONTRACTS.md` | 耦合程度 ≥ 2 | 跨模块接口契约、STATUS.md schema、Task ID 约定 |
| `<module>/STATUS.md` | 每个模块 | 模块状态追踪文件 |
| `.orchestrator/` | 始终 | 日志和会话目录 |
| `.gitignore` | 更新 | 忽略日志和会话文件 |

### 方式 B：`init`（快速）

```bash
cd your-project

# 自动检测
lindy-orchestrate init

# 或手动指定
lindy-orchestrate init --modules "backend,frontend,worker"
```

只生成 `orchestrator.yaml` + `STATUS.md` + `.orchestrator/`。不生成 CLAUDE.md 和 CONTRACTS.md。适合你已经有这些文件或不需要的场景。

**选项：**
- `--depth 2` — 扫描深度（默认 1 层）
- `--no-status` — 不生成 STATUS.md
- `--force` — 覆盖已有文件

---

## 第二步：审阅和调整配置

### orchestrator.yaml

```yaml
project:
  name: "my-project"
  branch_prefix: "af"         # 任务分支前缀：af/task-1

modules:
  - name: backend             # 模块名
    path: backend/            # 相对路径
    repo: myorg/my-backend    # GitHub repo（CI check 需要）
    ci_workflow: ci.yml        # CI workflow 文件名
    # role: qa                # 标记为 QA 模块（agent_check 会派发到这里）

planner:
  mode: cli                   # cli = claude -p（无需 API key）
  # mode: api                 # api = Anthropic SDK（需要 ANTHROPIC_API_KEY）
  # model: claude-sonnet-4-20250514

dispatcher:
  timeout_seconds: 1800       # 单任务硬超时（30 分钟）
  stall_timeout_seconds: 600  # 无输出判定停滞（10 分钟）
  permission_mode: bypassPermissions
  # max_output_chars: 50000   # 输出截断阈值

qa_gates:
  custom:                     # 自定义 QA 门禁（YAML 定义，不需要写 Python）
    - name: backend-pytest
      command: "pytest --tb=short -q"
      cwd: "{module_path}"    # {module_path} 会被替换为模块实际路径
    - name: frontend-lint
      command: "npm run lint"
      cwd: "{module_path}"

safety:
  dry_run: false              # true = 模拟全流程但不派发 agent
  max_retries_per_task: 2     # QA 失败后自动重试次数
  max_parallel: 3             # 最大并行 agent 数
```

**你需要确认的关键项：**
1. `modules` — 路径是否正确，`repo` 填 GitHub slug（如果用 CI check）
2. `qa_gates.custom` — 测试/lint 命令是否正确
3. `safety.max_parallel` — 根据你的 Claude API 并发限制调整

### STATUS.md

每个模块的 STATUS.md 是 Agent 了解模块现状的入口。生成后你应该填入实际状态：

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

## 第三步：使用

### 规划（不执行）

```bash
lindy-orchestrate plan "Add user authentication with JWT"
```

LLM 会读取所有模块的 STATUS.md，分解目标为带依赖的任务 DAG。输出每个任务的：
- 目标模块
- 任务描述
- 依赖关系
- QA 检查项
- 具体 prompt（agent 会收到的指令）

```bash
# 保存为 JSON 文件
lindy-orchestrate plan "Add user authentication" -o plan.json
```

**建议：先 plan，审阅后再 run。**

### 执行

```bash
lindy-orchestrate run "Add user authentication with JWT"
```

完整流程：

```
读取所有 STATUS.md → LLM 分解目标 → 按依赖顺序并行派发
                                        ↓
                              Agent 在模块目录中工作
                              （写代码 → commit → push）
                                        ↓
                              运行 QA 门禁（CI / 命令 / Agent 检查）
                                        ↓
                            通过 → 标记完成     失败 → 带反馈重试
                                                  ↓
                                        超过重试次数 → 标记失败，暂停
```

**选项：**
- `--dry-run` — 完整流程但不实际派发 agent
- `-v` / `--verbose` — 显示 agent 的每次工具调用
- `-c path/to/config.yaml` — 指定配置文件
- `-f` / `--file goal.md` — 从文件读取目标（使用 `-f -` 从 stdin 读取）
- `-p` / `--plan plan.json` — 执行已保存的任务计划（跳过 LLM 规划步骤）

### 模拟运行

```bash
lindy-orchestrate run "Refactor the data layer" --dry-run
```

会显示完整的任务分解和执行顺序，但不会真的启动 agent。用来验证目标描述是否清晰。

### 查看状态

```bash
lindy-orchestrate status          # 表格形式
lindy-orchestrate status --json   # JSON 形式
```

展示每个模块的：健康度、活跃任务数、待处理请求数、阻塞项数。

### 查看日志

```bash
lindy-orchestrate logs            # 最近 20 条
lindy-orchestrate logs -n 50      # 最近 50 条
lindy-orchestrate logs --json     # 原始 JSONL
```

日志存储在 `.orchestrator/logs/actions.jsonl`，追加写入，不会覆盖。

### 恢复会话

```bash
lindy-orchestrate resume          # 恢复最近一次
lindy-orchestrate resume abc123   # 恢复指定 session
```

### 校验配置

```bash
lindy-orchestrate validate
```

检查：配置语法、模块路径是否存在、STATUS.md 是否存在且可解析、Claude CLI 是否可用。

### 清理工作区

```bash
lindy-orchestrate gc              # 默认 dry run，显示待清理项
lindy-orchestrate gc --apply      # 实际执行清理
```

清理过期的任务分支、旧会话文件、超大日志文件和孤立的计划文件。

**选项：**
- `--apply` — 实际执行清理（默认只显示）
- `--branch-age 14` — 任务分支最大保留天数（默认 14）
- `--session-age 30` — 会话文件最大保留天数（默认 30）
- `--log-size 10` — 日志文件最大大小 MB（默认 10）
- `--status-stale 7` — STATUS.md 过期阈值天数（默认 7）

### 熵扫描

```bash
lindy-orchestrate scan                  # 扫描所有模块
lindy-orchestrate scan --module backend # 扫描指定模块
lindy-orchestrate scan --grade-only     # 只显示评分
```

检测架构漂移、契约违规、代码质量衰减等问题。

### 问题追踪

需要在 `orchestrator.yaml` 中配置 `tracker`：

```yaml
tracker:
  enabled: true
  provider: github         # github | linear
  repo: myorg/my-project
  labels: ["orchestrator"]
  sync_on_complete: true
```

```bash
# 列出问题
lindy-orchestrate issues                     # 列出 open 状态的问题
lindy-orchestrate issues --label bug         # 按标签过滤
lindy-orchestrate issues --status closed     # 按状态过滤
lindy-orchestrate issues --json              # JSON 格式输出

# 从问题执行
lindy-orchestrate run-issue 42               # 获取 issue #42 并作为目标执行
lindy-orchestrate run-issue 42 --dry-run     # 模拟运行
```

`run-issue` 会自动获取 issue 内容作为目标，执行完成后可自动在 issue 上添加评论并关闭（需配置 `sync_on_complete: true`）。

### 模块间通信（Mailbox）

需要在 `orchestrator.yaml` 中配置 `mailbox`：

```yaml
mailbox:
  enabled: true
  inject_on_dispatch: true   # 自动将待处理消息注入 agent prompt
```

```bash
# 查看消息
lindy-orchestrate mailbox                    # 查看所有模块的消息概览
lindy-orchestrate mailbox frontend           # 查看 frontend 模块的待处理消息
lindy-orchestrate mailbox frontend --json    # JSON 格式输出

# 发送消息
lindy-orchestrate mailbox --send-to backend --send-from frontend -m "Need API endpoint for /users"
```

---

## QA 门禁详解

QA 门禁在每个任务 dispatch 完成后自动运行。支持四种类型：

### 1. CI Check

轮询 GitHub Actions CI 状态。需要 `gh` CLI 和模块配置了 `repo` + `ci_workflow`。

```yaml
# 在 task plan 中由 LLM 自动分配
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

运行任意 shell 命令，exit code 0 = 通过。

```yaml
qa_gates:
  custom:
    - name: backend-pytest
      command: "pytest --tb=short -q"
      cwd: "{module_path}"
      timeout: 600
```

### 3. Agent Check

派发一个独立的 QA agent 来验证。需要配置一个 `role: qa` 的模块。

```yaml
modules:
  - name: qa
    path: qa/
    role: qa
```

QA agent 会收到前序任务的输出作为上下文，输出 `QA_RESULT: PASS` 或 `QA_RESULT: FAIL` + `FAILURE_REASON: ...`。

### 4. 自定义 YAML 门禁

在 `orchestrator.yaml` 中定义，无需写 Python：

```yaml
qa_gates:
  custom:
    - name: type-check
      command: "npx tsc --noEmit"
      cwd: "{module_path}"
    - name: integration-test
      command: "pytest tests/integration/ -x"
      cwd: "{module_path}"
```

### 失败重试

QA 失败后，编排器会：
1. 将失败信息追加到原始 prompt
2. 附加可操作性指令（"实际运行脚本"、"确认输出文件已生成"等）
3. 重新派发 agent（最多 `max_retries_per_task` 次）

---

## 两种 Dispatch 模式

| 模式 | 函数 | 适用场景 |
|------|------|----------|
| **流式** | `dispatch_agent()` | 长任务 — 实时心跳监控，stall 检测，事件回调 |
| **阻塞** | `dispatch_agent_simple()` | 短任务 — 规划、报告生成，无线程开销 |

**Stall 检测机制：**
- Agent 每产生一行输出就重置 stall 计时器
- 首次事件有宽限期：`max(stall_timeout * 2, 600s)`（处理模型启动慢的情况）
- 后续事件最低保障：`max(stall_timeout, 600s)`（保护长时间运行的 pytest 等工具）
- 超过 stall 时间 → kill 进程，错误信息包含 stderr 和最后使用的工具名
- 硬超时 `timeout_seconds` 是最终安全网

---

## 目标描述的最佳实践

编排器的效果直接取决于目标描述的质量。

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
# 太模糊
lindy-orchestrate run "改进系统"

# 太大
lindy-orchestrate run "搭建完整的交易系统"

# 没有上下文
lindy-orchestrate run "修 bug"
```

**经验法则：** 如果你无法在 3 句话内描述验收标准，这个目标需要拆分。

---

## 项目结构示例

### 单模块项目

```
my-app/
├── orchestrator.yaml
├── CLAUDE.md
├── STATUS.md              # 根目录的 STATUS.md
├── .orchestrator/
│   ├── logs/
│   └── sessions/
├── pyproject.toml
├── src/
└── tests/
```

### 多模块项目

```
my-platform/
├── orchestrator.yaml
├── CLAUDE.md              # 项目级
├── CONTRACTS.md           # 跨模块契约（耦合度 ≥ 2 才生成）
├── .orchestrator/
│   ├── logs/
│   └── sessions/
├── backend/
│   ├── CLAUDE.md          # 模块级
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

## 常见问题

### Claude CLI 未找到

```
Error: Claude CLI not found in PATH.
```

安装 Claude Code CLI：https://docs.anthropic.com/en/docs/claude-code

### 没检测到模块

```
No modules detected.
```

你的项目根目录下没有含 marker 文件的子目录。两种解决方案：
- `lindy-orchestrate init --modules "myapp"` 手动指定
- 如果根目录本身就是项目（有 `pyproject.toml` 等），`onboard` 会自动将根目录识别为单模块

### STATUS.md 解析错误

解析器是容错的，不会崩溃。但如果表格格式不对（缺列、多列），字段可能为空。用 `validate` 检查：

```bash
lindy-orchestrate validate
```

### Agent 超时/停滞

调整 `orchestrator.yaml`：

```yaml
dispatcher:
  timeout_seconds: 3600       # 延长硬超时到 1 小时
  stall_timeout_seconds: 900  # 延长 stall 检测到 15 分钟
```

### 想跳过 QA 快速迭代

去掉 `qa_gates.custom` 配置项即可。CI check 和 agent check 只在 LLM 规划时自动分配，不配置 repo 就不会触发。
