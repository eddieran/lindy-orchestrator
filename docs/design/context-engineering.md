# 设计文档: 上下文工程

> v0.3.0 实现 — CLAUDE.md 索引化 + docs/agents/ 结构化 + 分层 Context 投递

## 1. 设计目标

借鉴 OpenAI Harness Engineering 的核心洞察：**AGENTS.md 是目录，不是百科全书**。Agent 读索引获得全局地图，需要深入时再读具体文件。

lindy-orchestrator 的 context engineering 实现了完整的五阶段生命周期：

```
Scan → Interview → Generation → Runtime Delivery → Feedback Loop
```

---

## 2. 架构总览

### 2.1 Context 层次结构

```
项目根/
├── CLAUDE.md              ← L0 索引层 (~40行): 角色 + 模块表 + 指针
├── ARCHITECTURE.md        ← L1 结构层: 拓扑 + 负向边界 + 层级
├── CONTRACTS.md           ← L2 接口层: 跨模块合约 (complexity≥2 时生成)
├── docs/
│   └── agents/            ← L3 详情层: agent-facing 深层文档
│       ├── protocol.md    ← 完整协调协议 + 会话生命周期
│       ├── conventions.md ← 基于技术栈的编码约定
│       └── boundaries.md  ← 负向边界 + 例外 + 允许接口
├── {module}/
│   ├── CLAUDE.md          ← 模块指令 (8段结构, ~80行)
│   └── STATUS.md          ← 模块状态 (消息总线)
└── orchestrator.yaml      ← 运行时配置
```

### 2.2 信息分层策略

```
层级              内容                 加载时机         位置
─────────────────────────────────────────────────────────────
L0 (Always)      角色 + 模块索引      每次会话启动      CLAUDE.md (root)
L1 (Planning)    架构 + 边界          每次规划任务      ARCHITECTURE.md
L2 (Execution)   合约 + 接口          跨模块任务时      CONTRACTS.md
L3 (Deep Dive)   协议 + 约定 + 边界   需要时引用       docs/agents/
L4 (Module)      模块角色 + 技术栈     agent dispatch   {module}/CLAUDE.md
L5 (Runtime)     健康度 + 任务状态     每次 session     {module}/STATUS.md
```

Agent 的 context 加载路径：

```
Orchestrator 启动 → 读 CLAUDE.md (L0) → 读所有 STATUS.md (L5)
规划阶段 → 读 ARCHITECTURE.md (L1) → 如需跨模块则读 CONTRACTS.md (L2)
Module Agent 启动 → 读 {module}/CLAUDE.md (L4) → 读 STATUS.md (L5)
遇到边界问题 → 读 docs/agents/boundaries.md (L3)
```

---

## 3. 阶段 1: Scan — 静态项目分析

**实现**: `discovery/analyzer.py:analyze_project(root, max_depth=1) -> ProjectProfile`

纯文件系统遍历，无 LLM 调用：

| 检测项 | 方法 | 产出 |
|-------|------|------|
| 模块边界 | 扫描顶层目录的 marker 文件 (pyproject.toml, package.json 等) | `list[ModuleProfile]` |
| 技术栈 | 解析依赖文件 → 推断框架 | `tech_stack: list[str]` |
| 命令集 | 检测 test/build/lint 命令来源 | `test_commands`, `build_commands`, `lint_commands` |
| 目录树 | 3层深度，最多40项 | `dir_tree: str` |
| 模式检测 | 文件名/依赖匹配 | `detected_patterns: list[str]` (REST API, ORM, SPA 等) |
| 敏感路径 | .env, *.key, *.pem 等 | `sensitive_paths: list[str]` |
| CI 配置 | GitHub Actions / GitLab CI / Jenkins | `detected_ci: str` |
| Git 信息 | remote URL, 默认分支 | `git_remote`, `default_branch` |

**依赖解析器**（无第三方库依赖）：
- `_parse_pyproject_deps()` — regex 解析 TOML
- `_parse_package_json_deps()` — JSON 解析
- `_parse_cargo_deps()` — regex 解析
- `_parse_gomod_deps()` — 行解析

**技术栈推断**：
- Python: fastapi→FastAPI, sqlalchemy→SQLAlchemy, django→Django
- Node: react→React, next→Next.js, express→Express

---

## 4. 阶段 2: Interview — 交互式补充

**实现**: `discovery/interview.py:run_interview(profile, non_interactive=False) -> DiscoveryContext`

交互式问答补充自动扫描无法获取的信息：

1. 项目描述
2. 模块角色与技术栈精炼
3. 跨模块依赖关系（多模块时）
4. 每模块 QA 需求
5. 敏感路径
6. 协调复杂度（1=松散, 2=中等, 3=紧密）
7. 分支前缀（默认 `af`）

`non_interactive=True` 时使用合理默认值，适合 CI/脚本场景。

**产出**: `DiscoveryContext` — 完整的项目理解数据结构：

```python
@dataclass
class DiscoveryContext:
    project_name: str
    project_description: str
    root: str
    modules: list[ModuleProfile]
    cross_deps: list[CrossModuleDep]
    coordination_complexity: int  # 1-3
    branch_prefix: str = "af"
    sensitive_paths: list[str]
    qa_requirements: dict[str, list[str]]
    git_remote: str
    monorepo: bool
```

---

## 5. 阶段 3: Generation — 模板渲染

**实现**: `discovery/generator.py:generate_artifacts(ctx, output_dir, force=False) -> list[Path]`

### 5.1 Root CLAUDE.md — 索引版

**实现**: `discovery/templates/root_claude_md.py:render_root_claude_md(ctx)`

**产出示例**（~40行）：

```markdown
# my-project — Project Orchestrator

> You coordinate modules, you do NOT implement.
> Read STATUS.md files, decompose goals, dispatch tasks, verify quality.

## Modules

| Module | Path | Stack | Patterns |
|--------|------|-------|----------|
| backend | backend/ | Python, FastAPI | REST API, ORM |
| frontend | frontend/ | TypeScript, React | SPA |

## Key Files

- `ARCHITECTURE.md` — Module boundaries and layer structure. Read before planning.
- `docs/agents/protocol.md` — Full coordination protocol and session rules.
- `docs/agents/conventions.md` — Coding standards per tech stack.
- `CONTRACTS.md` — Cross-module API/data/env contracts.

## Quick Rules

1. Each task → branch `af/task-{id}`. Agents commit and push.
2. STATUS.md is the message bus. Cross-module requests go there.
3. Honor CONTRACTS.md interfaces for cross-module communication.
4. Every task is verified by QA gates before completion.

## Session Start

1. Read all module STATUS.md files
2. Check blockers and cross-module requests
3. Read `ARCHITECTURE.md`, then plan and verify through QA gates
```

**设计决策**：
- CONTRACTS.md 仅在 `coordination_complexity >= 2` 时出现在 Key Files
- Quick Rules 第3条在无 CONTRACTS.md 时替换为 "Agents must NOT modify files outside their module"
- 模块表包含 patterns 列（最多3个），帮助 orchestrator 快速理解项目结构

### 5.2 docs/agents/ — 详情层

**实现**: `discovery/templates/agent_docs.py:render_agent_docs(ctx) -> dict[str, str]`

#### protocol.md

STATUS.md 消息总线的完整规则：
- 7 个追踪段：Meta, Active Work, Completed, Requests, Deliverables, Metrics, Blockers
- 跨模块请求流程：A→B 创建请求 → orchestrator 拾取 → B 执行 → B 记录交付 → A 标记 DONE
- 分支交付：`{branch_prefix}/task-{id}`，commit + push 后由 QA gate 验证
- 5 种 QA gate：structural_check, layer_check, ci_check, command_check, agent_check
- QA 失败自动重试（默认2次），带结构化反馈
- 跨模块请求优先级：P0 (blocker), P1 (important), P2 (nice-to-have)

#### conventions.md

基于检测到的技术栈自动生成编码约定：
- **Python**: type hints, PEP 8, pathlib, Pydantic models, SQLAlchemy 2.0 style, FastAPI async
- **TypeScript/Node**: strict types, functional React, App Router (Next.js), named exports
- **Rust**: `Result<T, E>`, no unwrap(), prefer `&str`
- **Go**: return errors (no panic), `context.Context` first param

通用规则：文件 ≤500行（structural_check 强制）、无敏感文件、使用模块边界接口

#### boundaries.md

- 模块隔离原则 + 例外说明
- 敏感路径列表（never modify）
- 允许接口表（from_module → to_module，interface_type）
- 跨模块通信规则：仅通过 STATUS.md + CONTRACTS.md

### 5.3 Module CLAUDE.md — 8 段结构

**实现**: `discovery/templates/module_claude_md.py:render_module_claude_md(ctx, module)`

每个模块 agent 收到的上下文（~80行）：

```
1. Header       — 模块名 + 技术栈
2. Boot         — "FIRST ACTION: Read STATUS.md"
3. Role         — 职责 + 检测到的模式
4. Tech Stack   — 框架列表
5. Directory    — 3层目录树（最多40项）
6. Commands     — test/build/lint 命令
7. Conventions  — 技术栈特定规则
8. Boundaries   — 负向约束 ("own ONLY files under {path}/")
```

**Cross-Module 段**（多模块项目）：
- Consumes: 从其他模块消费的接口
- Produces: 向其他模块提供的接口
- 包含 interface_type（api, file, database, env_var, message_queue）

### 5.4 ARCHITECTURE.md — 结构地图

**实现**: `discovery/templates/architecture_md.py:render_architecture_md(ctx)`

核心理念："**A map, not a manual.** It tells you what exists where, how modules relate, and — critically — what does NOT belong where."

| 段 | 内容 |
|----|------|
| Module Topology | `**module/** (path/) → tech stack — patterns` |
| Dependency Direction | from → to + interface_type + description |
| Boundaries | 负向约束（`_infer_boundaries()` 生成） |
| Layer Structure | 基于框架自动推断 |
| Sensitive Paths | DO NOT commit 标记 |

**层级推断规则**（`_build_layer_structure()`）：

| 框架 | 层级 (低 → 高) |
|------|----------------|
| FastAPI/Flask | models → schemas → services → routes → main |
| Django | models → serializers → views → urls → wsgi |
| Express | models → middleware → routes → controllers → app |
| React/Next.js | types → hooks → components → pages → app |
| Vue | types → composables → components → views → router |
| Spring | entities → repositories → services → controllers → application |

### 5.5 完整生成流水线

`generator.py` 的产物顺序：

```
1. orchestrator.yaml     — 运行时配置（模块、CI、QA gates、安全设置）
2. CLAUDE.md (root)      — 索引版 (~40行)
3. {module}/CLAUDE.md    — 每模块指令 (8段, ~80行)
4. CONTRACTS.md          — 跨模块合约 (complexity≥2)
5. ARCHITECTURE.md       — 结构地图 + 负向边界 + 层级
6. docs/agents/          — protocol.md, conventions.md, boundaries.md
7. {module}/STATUS.md    — 消息总线模板
8. .orchestrator/        — logs/, sessions/ 目录
9. .gitignore            — 追加 orchestrator 条目
```

---

## 6. 阶段 4: Runtime Delivery — 运行时 Context 投递

### 6.1 Orchestrator 视角

Orchestrator（planner.py）构建 prompt 时注入：

```
[Root CLAUDE.md] + [所有模块 STATUS.md 摘要] + [ARCHITECTURE.md] + [Goal 描述]
```

`planner.py:_read_all_statuses()` 使用 `status/parser.py:parse_status_md()` 解析每个模块的 STATUS.md，提取：
- overall_health (GREEN/YELLOW/RED)
- active_tasks（IN_PROGRESS 任务列表）
- completed_tasks（最近完成任务）
- blockers（阻塞项）
- cross_module_requests（待处理请求）

### 6.2 Module Agent 视角

每个 agent dispatch 收到的 prompt：

```
[Module CLAUDE.md] + [具体任务 prompt] + [QA 反馈（重试时）]
```

任务 prompt 支持两种格式：

**结构化格式**（planner 输出）：
```json
{
  "objective": "Add user authentication endpoint",
  "context_files": ["backend/routes/auth.py", "backend/models/user.py"],
  "constraints": ["Use JWT tokens", "Hash passwords with bcrypt"],
  "verification": ["Run pytest", "Check /auth/login returns 200"]
}
```

渲染为：
```markdown
## Objective
Add user authentication endpoint

## Context Files (read these first)
- backend/routes/auth.py
- backend/models/user.py

## Constraints
- Use JWT tokens
- Hash passwords with bcrypt

## Before committing, verify
- Run pytest
- Check /auth/login returns 200
```

**纯文本格式**：直接作为 prompt 传递。

### 6.3 STATUS.md 消息总线

**实现**: `status/parser.py:parse_status_md(path) -> ModuleStatus`

设计哲学：**容错解析**——提取能解析的，跳过不能解析的，永不崩溃。

支持解析的 section：

| Section | 产出 | 关键字段 |
|---------|------|---------|
| Meta | `ModuleMeta` | module, last_updated, overall_health |
| Active Work | `list[ActiveTask]` | id, task, status, blocked_by |
| Completed | `list[CompletedTask]` | id, task, completed, outcome |
| Cross-Module Requests | `list[CrossModuleRequest]` | from_module, to_module, priority, status |
| Cross-Module Deliverables | `list[CrossModuleDeliverable]` | from_module, to_module, status, path |
| Key Metrics | `dict[str, str]` | 指标 → 值 |
| Blockers | `list[str]` | 阻塞项列表 |

---

## 7. 阶段 5: Feedback Loop — QA 反馈闭环

### 7.1 反馈链路

```
Agent Output → Delivery Check → QA Gates → Violations with Remediation → Re-prompt
```

### 7.2 QA Gates

| Gate | 范围 | 检测内容 | 实现 |
|------|------|---------|------|
| structural_check | 跨模块边界 | 文件大小、敏感文件、跨模块 import | `qa/structural_check.py` |
| layer_check | 模块内层级 | `layer[i]` 不得 import `layer[j]` (j>i) | `qa/layer_check.py` |
| ci_check | CI 流水线 | GitHub Actions 检查结果 | `qa/ci_check.py` |
| command_check | 自定义命令 | pytest, npm test 等命令退出码 | `qa/command_check.py` |
| agent_check | LLM 审查 | Agent 审查任务输出质量 | `qa/agent_check.py` |

### 7.3 Remediation-rich 反馈

每个 QA gate 的 `Violation` 包含修复建议，直接注入重试 prompt：

```python
@dataclass
class Violation:
    rule: str       # "layer_violation", "file_size", "import_boundary"
    file: str       # 违规文件路径
    message: str    # 人可读描述
    remediation: str  # 具体修复建议
```

示例：
- **file_size**: "Split into `user_core.py` + `user_helpers.py`"
- **import_boundary**: "Use CONTRACTS.md interface instead, create Cross-Module Request"
- **layer_violation**: "Move shared logic to `models/` or lower, or use dependency injection"

### 7.4 重试 Prompt 增强

QA 失败后，`scheduler.py` 将反馈注入下次 prompt（`format_qa_feedback()`）：

```markdown
## IMPORTANT: Previous attempt failed QA verification

### [structural_check]
[结构化反馈 — 哪个文件、什么问题、如何修复]

### [layer_check]
[层级违规详情 + 修复建议]

Fix these issues. Specific instructions:
- Actually RUN all scripts and commands
- Ensure output files are generated before declaring success
- Verify changes by running relevant test/build commands
```

---

## 8. 核心设计哲学

### 8.1 负向边界优先

约束用 "does NOT" 声明，在三处重复出现并由两层机械化强制：

| 声明位置 | 示例 |
|---------|------|
| module CLAUDE.md (Boundaries 段) | "own ONLY files under `backend/`" |
| ARCHITECTURE.md (Boundaries 段) | "`frontend/` does NOT contain server-side logic" |
| docs/agents/boundaries.md | 完整例外清单 + 允许接口表 |

| 强制机制 | 范围 |
|---------|------|
| `structural_check` | 跨模块 import 检测 |
| `layer_check` | 模块内层级方向检测 |

### 8.2 Context 分级加载

不同角色看到不同粒度的 context：

| 角色 | 看到的 Context | 行数 |
|------|---------------|------|
| Orchestrator | 索引版 CLAUDE.md + 所有 STATUS.md 摘要 | ~40 + N×摘要 |
| Module Agent | 模块 CLAUDE.md + 任务 prompt | ~80 + prompt |
| Planner | Root CLAUDE.md + STATUS.md + ARCHITECTURE.md + Goal | ~40 + ~200 |

### 8.3 静态优先，动态补充

所有 context 以 Markdown 文件形式持久化在 repo 中（原则 1: "Agent 看不到的等于不存在"）。运行时状态通过 STATUS.md 投递——一种结构化纯文本的消息总线，agent 可读可写。

---

## 9. 与 v0.2.0 对比

| 维度 | v0.2.0 | v0.3.0 |
|------|--------|--------|
| Root CLAUDE.md | ~80行，内联所有协议 | ~40行索引，指向 docs/agents/ |
| 详细文档 | 无 agent-facing docs/ | docs/agents/ (protocol, conventions, boundaries) |
| 层级强制 | 仅文档化 | layer_check QA gate 机械化强制 |
| 编码约定 | 散落在模块 CLAUDE.md | 集中在 conventions.md + 模块 CLAUDE.md |
| 负向边界 | ARCHITECTURE.md 中声明 | 三处声明 + 两层强制 |

---

## 10. 已知局限与未来方向

### 当前局限

1. **无动态 Context**: Agent 无法查询运行时指标、应用日志、浏览器状态
2. **ExecPlan 未持久化**: Plans 在 `.orchestrator/plans/` 被 gitignore，不是 repo 真相
3. **Context 新鲜度**: ARCHITECTURE.md / CONTRACTS.md 可能与代码脱节（entropy scanner 可检测但不自动修复）
4. **单语言约定**: conventions.md 基于依赖检测生成，混合语言项目中的约定优先级未明确
5. **无增量更新**: `onboard` 是全量生成，缺少 `migrate-context` 增量迁移命令

### 未来方向

| 方向 | 描述 | 优先级 |
|------|------|--------|
| ExecPlan 持久化 | `--commit-plan` 将计划提交到 `docs/plans/` | P1 |
| Observability Context | 运行时指标/日志注入 agent prompt | P2 |
| 增量迁移 | `migrate-context` 从旧格式升级 | P2 |
| Agent Tool Scaffolding | onboard 时生成 CLI 工具到 PATH | P3 |
| 浏览器 Context | CDP/Playwright 截图注入 | P3 |
