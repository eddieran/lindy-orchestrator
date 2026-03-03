# Harness Engineering x Lindy-Orchestrator: 对比分析

> 基于 OpenAI 2026年2月发布的 Harness Engineering 系列文章，对照 lindy-orchestrator 现有架构的深度分析。

## 背景

OpenAI 在 "Harness Engineering: leveraging Codex in an agent-first world" 系列中，揭示了他们用 ~40 人团队在 5 个月内通过 Codex agent 生成 100 万行生产代码的工程方法论。核心转变：**工程师从"写代码"变为"设计 harness"** —— 设计环境、声明意图、构建反馈回路。

系列文章包括：
- **Harness Engineering** — 总体方法论与五原则
- **Unlocking the Codex Harness** — App Server 架构（JSON-RPC、Item/Turn/Thread 原语）
- **Unrolling the Codex Agent Loop** — Agent 执行循环与 context window 管理

Martin Fowler 在其分析中提炼出三支柱框架：**Context Engineering + Architectural Constraints + Entropy Management**。

---

## 一、五原则逐一对照

### 原则 1: "Agent 看不到的等于不存在"

> 所有决策推入 repo 为 markdown/schema/ExecPlans。Repo 是唯一真相来源。

| 维度 | OpenAI | Lindy-Orchestrator |
|------|--------|--------------------|
| 状态追踪 | ExecPlans 提交到 repo | `STATUS.md` per-module（健康度、当前任务、阻塞、跨模块请求） |
| 架构文档 | ARCHITECTURE.md with negative boundaries | `architecture_md.py` 生成 ARCHITECTURE.md，含模块拓扑、负向边界、层级结构 |
| 接口合约 | 结构化 docs/ 目录 | `CONTRACTS.md`（API/数据库/环境变量/消息队列合约） |
| Agent 指令 | AGENTS.md ~100行索引 → docs/ | `CLAUDE.md` root + per-module（角色、启动协议、技术栈、命令、边界） |
| 执行计划 | 提交到 repo 作为真相 | `.orchestrator/plans/` 被 gitignore |

**契合度: 强**

lindy-orchestrator 在 context 物化方面走得很远：STATUS.md 作为消息总线、ARCHITECTURE.md 用负向边界约束、CONTRACTS.md 定义跨模块接口、CLAUDE.md 定义 agent 角色。Agent 启动时首先读取 STATUS.md (`module_claude_md.py:33 — "FIRST ACTION ON EVERY SESSION: Read STATUS.md"`)。

**差距:**
- Plans 持久化在 `.orchestrator/plans/` 但被 gitignore，不是 repo 真相的一部分
- 缺少 `docs/` 结构化目录作为深层知识的存放处

---

### 原则 2: "问缺什么能力，别问 agent 为何失败"

> 不调 prompt，而是给 agent 构建环境级工具和反馈。

| 维度 | OpenAI | Lindy-Orchestrator |
|------|--------|--------------------|
| 环境感知 | OpenTelemetry + 自定义 CLI 工具 | `discovery/analyzer.py` 自动检测技术栈、命令、CI 配置 |
| 失败反馈 | 自定义 helpers 解析错误 | `qa/feedback.py` — pytest/ruff/tsc 结构化修复解析器 |
| 自动注入 | 环境能力透明提供 | `scheduler.py:122-148` 自动注入 structural_check + custom gates |
| 可观测性 | OTel traces + Prometheus metrics | 无 — agent 仅能看到文件和命令输出 |

**契合度: 中**

`qa/feedback.py` 是这一原则的直接体现：不告诉 agent "你的 pytest 失败了"，而是解析出具体哪个测试、哪个 assertion，然后教它怎么修。同样，`structural_check.py` 不只说"文件太大"，而是建议 "Split into {stem}_core and {stem}_helpers"。

**差距:**
- 没有为 agent 提供自定义 CLI 工具（DB 查询、API 健康检查、日志搜索）
- 缺少运行时可观测性集成（agent 无法查询 metrics/traces/logs）
- 没有 per-worktree 可观测性栈的概念

---

### 原则 3: "机械化强制优于文档"

> 自定义 linter + 结构测试，在 CI 中强制依赖层级（Types → Config → Repo → Service → Runtime → UI）。

| 维度 | OpenAI | Lindy-Orchestrator |
|------|--------|--------------------|
| 跨模块边界 | 自定义 linter CI 拦截 | `structural_check.py:117-179` — 检测跨模块 import |
| 模块内层级 | 依赖方向 linter 强制 | `architecture_md.py:143` 生成层级文档（models→schemas→services→routes），**仅文档化** |
| 文件约束 | 结构测试 | `structural_check.py:73-95` — 文件大小限制 + 修复建议 |
| 敏感文件 | CI 拦截 | `structural_check.py:98-114` — .env/.key/.pem 检测 |
| 自动强制 | CI pipeline | `scheduler.py:122` — QA gate 自动注入，无需 planner 指定 |

**契合度: 强**

这是 lindy-orchestrator 与 harness engineering 最深的契合点。`structural_check.py` 本质上就是 OpenAI 说的 "structural test"。QA gate 的自动注入机制意味着强制是透明的——planner 不需要记得添加检查，scheduler 自动加上。

**差距:**
- **模块内层级方向未强制**。`_build_layer_structure()` 输出 "models → schemas → services → routes → main"，但没有 linter 检查 routes 是否错误 import 了 main。这是 OpenAI 依赖层级强制的核心，也是最大的差距。

---

### 原则 4: "给 Agent 眼睛"

> Chrome DevTools Protocol 提供 DOM/截图/导航；OTel 提供 traces/metrics/logs 查询；per-worktree 可观测性栈。

| 维度 | OpenAI | Lindy-Orchestrator |
|------|--------|--------------------|
| 执行监控 | 全方位 | `dispatcher.py` 心跳检测 + JSONL 事件流 + tool 使用追踪 |
| 浏览器测试 | CDP (DOM + 截图 + 导航) | 无 |
| 运行时指标 | OTel + Prometheus | 无 |
| 日志分析 | Agent 可查询 logs/metrics | 无 — 仅 JSONL 动作日志（面向编排器，非 agent） |

**契合度: 弱**

lindy-orchestrator 的 dispatcher 有不错的执行监控（心跳、stall 检测、事件流），但这些是编排器自己的可见性，不是 agent 的可见性。被 dispatch 的 agent 对运行时状态是"盲的"——无法查询应用是否健康、metrics 是否正常、logs 中是否有异常。

**差距:**
- 缺少 observability QA gate（health endpoint / log pattern / metric threshold）
- 缺少 agent 可调用的自定义工具
- 无浏览器/UI 验证能力
- 无 per-worktree 可观测性栈

---

### 原则 5: "地图而非手册"

> AGENTS.md ~100 行，纯索引指向 docs/。ARCHITECTURE.md 强调负向边界。

| 维度 | OpenAI | Lindy-Orchestrator |
|------|--------|--------------------|
| Agent 入口 | AGENTS.md ~100行 TOC → docs/ | `CLAUDE.md` root ~80行（混合索引与协议详情） |
| 架构地图 | ARCHITECTURE.md with boundaries | `architecture_md.py:18` — "This is a **map**, not a manual" |
| 负向边界 | "does NOT" 约束 | `_infer_boundaries()` 生成 "does NOT import/call/contain" |
| 详情存放 | 结构化 docs/ 目录 | 无 agent-facing 的 docs/ 目录 |

**契合度: 强**

`architecture_md.py` 的 docstring 直接写 "a map, not a manual"，模板第 18 行也是原文。负向边界推断 (`_infer_boundaries()`) 也是 OpenAI 方法的直接体现。

**差距:**
- CLAUDE.md 混合了索引角色和详细协议，信噪比不如纯索引高
- 没有 agent-facing 的 `docs/agents/` 目录存放详细文档

---

## 二、三支柱映射分析

### 支柱 1: Context Engineering

```
OpenAI:  AGENTS.md(TOC) → docs/(详情) → OTel(动态) → CDP(浏览器)
         ↓
         "结构化知识 + 动态可观测性"

Lindy:   CLAUDE.md → STATUS.md → ARCHITECTURE.md → CONTRACTS.md
         ↓
         "结构化知识 (仅静态)"
```

**现状**: 静态 context 覆盖全面（角色/状态/架构/合约）。
**差距**: 无动态 context（运行时指标、应用日志、浏览器状态）。

### 支柱 2: Architectural Constraints

```
OpenAI:  自定义 linter → CI 拦截 → 依赖层级强制
         ↓
         "跨模块 + 模块内 全面机械化"

Lindy:   structural_check → command_check → ci_check → auto-injection
         ↓
         "跨模块强制 ✓ | 模块内层级 ✗ (仅文档化)"
```

**现状**: 跨模块边界机械化强制，QA gate 自动注入。
**差距**: 模块内依赖层级仅在 ARCHITECTURE.md 中文档化，无 linter 强制。

### 支柱 3: Entropy Management

```
OpenAI:  周期性 agent → 不一致扫描 → 质量评级 → 自动 refactoring PR
         ↓
         "主动检测 + 自动修复"

Lindy:   gc.py → 过期分支/会话/日志轮转/STATUS漂移/孤儿计划
         ↓
         "响应式清理 (时间维度, 非语义维度)"
```

**现状**: GC 做时间维度的清理（文件修改时间）。
**差距**: 缺少语义级检测（架构漂移、合约合规、代码质量评级）。

---

## 三、Agent Loop 架构对比

```
Codex Agent Loop:
  assemble inputs → inference → tool execution → feed results → repeat
  ├─ Context window compaction (auto when threshold exceeded)
  ├─ Stateless API (full conversation each request)
  ├─ OS-level sandbox (Seatbelt/seccomp)
  └─ Network isolation (disabled by default)

Lindy Dispatch Cycle:
  build prompt → dispatch agent (claude -p) → stream JSONL events → collect result
  ├─ No compaction needed (each dispatch is fresh invocation)
  ├─ Stateless (each task = new process)
  ├─ Permission mode configurable (bypassPermissions default)
  └─ No network isolation (inherits host env)
```

**关键区别**: Codex 在单次长会话中执行多轮 tool call，需要 compaction 管理 context window。lindy-orchestrator 的每次 dispatch 是全新进程，天然无 context bloat，但也无法跨 turn 保留中间状态。

**App Server 架构对比**:

| 维度 | Codex App Server | Lindy Dispatcher |
|------|-----------------|-----------------|
| 协议 | JSON-RPC over JSONL/stdio | claude CLI stream-json |
| 原语 | Item → Turn → Thread | DispatchResult (单次) |
| 会话持久性 | Thread 支持创建/恢复/fork/归档 | SessionManager 支持保存/恢复 |
| 审批流 | 双向 — server 请求 client allow/deny | permission_mode 预设 |
| 部署 | 本地嵌入 / 解耦发布 / Web 容器 | 本地 CLI subprocess |

---

## 四、优先级路线图

### P1: 高影响力 + 高可行性

| 项目 | 描述 | 关键文件 | 设计文档 |
|------|------|---------|---------|
| 层级强制 linter | 模块内依赖方向 QA gate | `qa/layer_check.py`(新) | [layer-enforcement.md](./layer-enforcement.md) |
| Context 索引化 | CLAUDE.md 精简为 TOC, docs/agents/ 存详情 | `root_claude_md.py` | [context-engineering.md](./context-engineering.md) |
| Plan 持久化 | Plans 提交到 repo (可选) | `cli.py` | [context-engineering.md](./context-engineering.md) |

### P2: 中影响力

| 项目 | 描述 | 关键文件 | 设计文档 |
|------|------|---------|---------|
| Observability gate | 运行时验证（health/log/metric） | `qa/observability_check.py`(新) | [observability-integration.md](./observability-integration.md) |
| Agent 工具脚手架 | onboard 时生成自定义 CLI 工具 | `dispatcher.py`, `analyzer.py` | [observability-integration.md](./observability-integration.md) |
| 熵扫描器 | 语义级架构漂移 + 质量评级 | `entropy/scanner.py`(新) | [entropy-management.md](./entropy-management.md) |

### P3: 远期 / 按需

| 项目 | 描述 |
|------|------|
| Browser testing gate | Playwright 集成 + 截图验证 |
| PR Review agent | 自动代码审查 + 标准强制 |
| Sub-agent dispatch | 单任务内并行子 agent |

---

## 五、决策日志

| 决策 | 理由 |
|------|------|
| **采纳** 层级强制 | 最大差距，已有基础设施 (`_build_layer_structure` + `structural_check` 模式) |
| **采纳** Context 索引化 | 低成本高回报，遵循 "map not manual" 原则已有认同 |
| **改编** Observability | 不做 per-worktree OTel 栈（过重），而是轻量 QA gate |
| **改编** Entropy | 不做全自动 PR（风险高），而是 report + 可选 fix |
| **延迟** CDP 浏览器 | 多数后端项目不需要，按需启用 |
| **延迟** Sub-agent | 当前 task-level 并行已满足大多数场景 |
| **排除** MCP 协议 | OpenAI 自己放弃了 MCP，lindy 的 CLI dispatch 模型更简洁 |

---

## 参考来源

- [Harness engineering: leveraging Codex in an agent-first world — OpenAI](https://openai.com/index/harness-engineering/)
- [Unlocking the Codex harness: how we built the App Server — OpenAI](https://openai.com/index/unlocking-the-codex-harness/)
- [Unrolling the Codex agent loop — OpenAI](https://openai.com/index/unrolling-the-codex-agent-loop/)
- [Harness Engineering — Martin Fowler](https://martinfowler.com/articles/exploring-gen-ai/harness-engineering.html)
- [How OpenAI's Codex Team Works — Eng Leadership Newsletter](https://newsletter.eng-leadership.com/p/how-openais-codex-team-works-and)
- [5 Harness Engineering Principles — Tony Lee](https://tonylee.im/en/blog/openai-harness-engineering-five-principles-codex)
- [How I think about Codex — Simon Willison](https://simonwillison.net/2026/Feb/22/how-i-think-about-codex/)
- [OpenAI Harness Engineering — InfoQ](https://www.infoq.com/news/2026/02/openai-harness-engineering-codex/)
