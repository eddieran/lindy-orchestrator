# 概念设计: 上下文工程

> CLAUDE.md 索引化 + docs/ 结构化 + ExecPlan 持久化

## 1. 问题陈述

OpenAI 的 context engineering 有一个核心洞察: **AGENTS.md 是目录，不是百科全书**。~100 行纯索引，指向 `docs/` 中的详细文档。Agent 读 AGENTS.md 获得全局地图，需要深入时再读具体文件。

当前 lindy-orchestrator 的 CLAUDE.md 混合了多种关注:

```
root CLAUDE.md (~80行):
  - 角色定义 (orchestrator 身份)
  - 项目概述
  - 模块表格
  - 数据流
  - 协调协议 (5条规则)
  - 敏感路径
  - 会话协议 (5步)
```

问题:
1. **信噪比**: 所有信息平铺，agent 每次都要处理全部内容
2. **扩展性差**: 随着项目复杂度增长，CLAUDE.md 会膨胀
3. **没有分层**: 高频参考信息和低频参考信息混在一起
4. **Plans 不持久**: 执行计划存在 `.orchestrator/plans/` 被 gitignore，不是 repo 真相

## 2. 设计方案

### 2.1 新的 Context 层次结构

```
项目根/
├── CLAUDE.md              ← 索引层 (~60-80行): 角色 + 模块表 + 指针
├── ARCHITECTURE.md        ← 结构层 (已有): 地图 + 边界
├── CONTRACTS.md           ← 接口层 (已有): 跨模块合约
├── docs/
│   └── agents/            ← 详情层 (新增): agent-facing 深层文档
│       ├── protocol.md    ← 完整协调协议
│       ├── conventions.md ← 编码约定 + 命名规范
│       ├── boundaries.md  ← 详细边界规则 + 例外
│       └── tools.md       ← 可用工具说明
├── docs/
│   └── plans/             ← 计划层 (新增): 持久化的执行计划
│       └── 2026-03-03-add-auth.md
└── {module}/
    ├── CLAUDE.md          ← 模块指令 (已有)
    └── STATUS.md          ← 模块状态 (已有)
```

### 2.2 索引化的 CLAUDE.md

**现状** (`root_claude_md.py` 生成):

```markdown
# my-project — Project Orchestrator

> You are the Project Orchestrator. You coordinate modules...
> Your job is to read all module STATUS.md files...

## Project Overview
(项目描述)

## Modules
| Module | Path | Tech Stack | Patterns |
(完整表格)

## Data Flow
(依赖关系)

## Coordination Protocol
1. STATUS.md as message bus...
2. Scope isolation...
3. Branch-based delivery...
4. QA gates...
5. ARCHITECTURE.md...

## Sensitive Paths
(路径列表)

## Session Protocol
1. Read all module STATUS.md files
2. Check for open cross-module requests
3. Plan and dispatch tasks
4. Verify results through QA gates
5. Generate a completion report
```

**改造后**:

```markdown
# my-project — Project Orchestrator

> You coordinate modules, you do NOT implement.
> Read STATUS.md files, decompose goals, dispatch tasks, verify quality.

## Modules

| Module | Path | Stack |
|--------|------|-------|
| backend | backend/ | Python, FastAPI |
| frontend | frontend/ | TypeScript, React |

## Key Files

- `ARCHITECTURE.md` — Module boundaries and layer structure. Read before planning.
- `CONTRACTS.md` — Cross-module API/data/env contracts.
- `docs/agents/protocol.md` — Full coordination protocol and session rules.
- `docs/agents/conventions.md` — Coding standards and naming rules.

## Quick Rules

1. Each task → branch `af/task-{id}`. Agents commit and push.
2. STATUS.md is the message bus. Cross-module requests go there.
3. Agents must NOT modify files outside their module.
4. Every task is verified by QA gates before completion.

## Session Start

1. Read all module STATUS.md files
2. Check blockers and cross-module requests
3. Read `ARCHITECTURE.md` for current boundaries
```

**变化**:
- 从 ~80 行减到 ~40 行
- 协调协议的详细规则移到 `docs/agents/protocol.md`
- 约定和边界详情移到各自文件
- "Key Files" 段作为指针，替代内联所有内容

### 2.3 docs/agents/ 文件设计

#### protocol.md

```markdown
# Coordination Protocol

## STATUS.md as Message Bus
STATUS.md 是模块间通信的核心机制...
(展开现有 CLAUDE.md 中的协调协议，增加详细说明和示例)

## Branch-Based Delivery
每个任务产出一个分支: `{branch_prefix}/task-{id}`
(分支命名规则、commit 规范、push 要求)

## QA Gates
每个任务通过 QA gate 验证后标记完成...
(gate 类型说明、失败处理流程)

## Cross-Module Requests
当 agent 需要其他模块的工作时...
(请求格式、响应流程、超时处理)

## Session Lifecycle
1. Boot: Read STATUS.md
2. Plan: Decompose goal
3. Execute: Dispatch tasks
4. Verify: Run QA gates
5. Report: Generate summary
```

#### conventions.md

```markdown
# Coding Conventions

## General
- File size limit: 500 lines (enforced by structural_check)
- No sensitive files (.env, *.key, *.pem)
- Use module boundary interfaces, not direct imports

## Python Modules
- Follow layer structure: models → schemas → services → routes → main
- Use type hints on all public functions
- Tests in tests/ directory

## TypeScript Modules
- Follow layer structure: types → hooks → components → pages → app
- Use strict TypeScript
- Components in PascalCase, utilities in camelCase
```

#### boundaries.md

```markdown
# Module Boundaries

## Principles
- Modules are isolated. No cross-module imports.
- Communication via CONTRACTS.md interfaces only.
- If you need data from another module, create a Cross-Module Request.

## Negative Constraints
(从 ARCHITECTURE.md 的 Boundaries 段落链接或复制)

## Exceptions
(允许的例外情况，如 shared/ 库)
```

### 2.4 ExecPlan 持久化

**现状**: Plans 写入 `.orchestrator/plans/` 被 gitignore。

**改造**:

```python
# cli.py 修改
@app.command()
def goal(
    ...,
    commit_plan: bool = typer.Option(
        False, "--commit-plan",
        help="Commit plan to docs/plans/ for traceability"
    ),
):
    # ... plan generation ...

    if commit_plan:
        plan_dir = config.root / "docs" / "plans"
        plan_dir.mkdir(parents=True, exist_ok=True)

        slug = slugify(goal_text)[:50]
        date = datetime.now().strftime("%Y-%m-%d")
        plan_path = plan_dir / f"{date}-{slug}.md"

        plan_path.write_text(format_plan_as_markdown(plan), encoding="utf-8")

        # Git add + commit
        subprocess.run(["git", "add", str(plan_path)], cwd=config.root)
        subprocess.run(
            ["git", "commit", "-m", f"plan: {goal_text[:70]}"],
            cwd=config.root
        )
```

Plan 格式:

```markdown
# Plan: Add user authentication

Date: 2026-03-03
Goal: Add JWT-based user authentication to the backend
Status: in_progress

## Tasks

### Task 1: Create auth models (backend)
- Branch: af/task-1
- Dependencies: none
- QA: structural_check, pytest
- Status: completed

### Task 2: Add auth middleware (backend)
- Branch: af/task-2
- Dependencies: task-1
- QA: structural_check, pytest
- Status: in_progress

### Task 3: Add login page (frontend)
- Branch: af/task-3
- Dependencies: task-2
- QA: structural_check
- Status: pending
```

## 3. 对 root_claude_md.py 的改造

### 现状

```python
# root_claude_md.py:8-79
def render_root_claude_md(ctx: DiscoveryContext) -> str:
    # 内联所有内容: 角色 + 概述 + 模块表 + 数据流 + 协议 + 敏感路径 + 会话协议
```

### 改造

```python
def render_root_claude_md(ctx: DiscoveryContext) -> str:
    """Render concise index-style CLAUDE.md."""
    # 保留: 角色定义 (精简), 模块表, Quick Rules, Session Start
    # 移除: 详细协调协议, 完整数据流
    # 新增: Key Files 指针段

def render_agent_docs(ctx: DiscoveryContext) -> dict[str, str]:
    """Render docs/agents/ files."""
    return {
        "protocol.md": _render_protocol(ctx),
        "conventions.md": _render_conventions(ctx),
        "boundaries.md": _render_boundaries(ctx),
    }
```

### generator.py 改造

```python
# discovery/generator.py 新增步骤
def generate_artifacts(ctx, output_dir, ...):
    # ... 现有步骤 ...

    # Step N: Generate docs/agents/
    agent_docs = render_agent_docs(ctx)
    agents_dir = output_dir / "docs" / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)

    for filename, content in agent_docs.items():
        (agents_dir / filename).write_text(content, encoding="utf-8")
```

## 4. 信息分层策略

```
层级              内容                 频率          位置
─────────────────────────────────────────────────────────────
L0 (Always)      角色 + 模块索引      每次会话       CLAUDE.md
L1 (Planning)    架构 + 边界          每次规划       ARCHITECTURE.md
L2 (Execution)   合约 + 接口          跨模块任务时    CONTRACTS.md
L3 (Deep Dive)   协议 + 约定 + 边界   需要时引用     docs/agents/
L4 (History)     执行计划             回顾时         docs/plans/
```

Agent 的 context 加载路径:

```
启动 → 读 CLAUDE.md (L0) → 读 STATUS.md
规划 → 读 ARCHITECTURE.md (L1) → 如需跨模块则读 CONTRACTS.md (L2)
执行 → 如遇边界问题则读 docs/agents/boundaries.md (L3)
回顾 → 如需理解历史决策则读 docs/plans/ (L4)
```

## 5. 迁移策略

### 向后兼容

- 旧项目不受影响（CLAUDE.md 保持完整，`docs/agents/` 不存在时不报错）
- `onboard` 命令新增 `--index-style` flag（默认 true for new projects）
- 已有项目通过 `lindy-orchestrate migrate-context` 迁移

### 迁移命令

```python
@app.command()
def migrate_context():
    """Migrate CLAUDE.md from monolithic to index style."""
    config = load_config()

    # 1. 读现有 CLAUDE.md
    claude_md = (config.root / "CLAUDE.md").read_text()

    # 2. 提取各段落到 docs/agents/
    protocol = extract_section(claude_md, "Coordination Protocol")
    # ...

    # 3. 重写 CLAUDE.md 为索引版
    # 4. 生成 docs/agents/ 文件
    # 5. 提示用户 review 和 commit
```

## 6. 验证方式

- 检查生成的 CLAUDE.md 行数 < 80
- 检查 CLAUDE.md 包含 "Key Files" 段
- 检查 `docs/agents/` 包含 protocol.md, conventions.md, boundaries.md
- 检查 `docs/agents/protocol.md` 包含从 CLAUDE.md 迁移的协调协议内容
- 端到端: onboard 新项目，验证生成的 context 结构
