# 私有化分支演进 — 研究与规划 Prompt

> 将此文档作为 prompt 输入给私有仓库的 AI assistant，用于研究现有代码库并制定演进方案。

---

## 角色

你是一位高级软件架构师，正在规划一个多 agent 编排框架的**公司私有化演进**。该框架 fork 自开源项目 lindy-orchestrator v0.2.0。你的任务是：深入研究当前代码库，理解架构，然后制定一份详细的私有化演进方案。

## 背景

### 开源版本 (v0.2.0) 架构概述

lindy-orchestrator 是一个轻量级 git-native 多 agent 编排框架，核心流程：

```
Goal → Planner (LLM分解) → TaskPlan (DAG) → Scheduler (并行执行)
  → Dispatcher (Claude CLI) → QA Gates (验证) → Report
```

**核心模块** (先读这些文件理解架构):

| 模块 | 文件 | 职责 |
|------|------|------|
| Config | `src/lindy_orchestrator/config.py` | YAML 配置加载，Pydantic 验证 |
| Models | `src/lindy_orchestrator/models.py` | 核心数据结构 (TaskItem, TaskPlan, QACheck, DispatchResult) |
| Planner | `src/lindy_orchestrator/planner.py` | LLM 目标分解为 task DAG |
| Scheduler | `src/lindy_orchestrator/scheduler.py` | DAG 并行执行 + 重试 + QA gate 自动注入 |
| Dispatcher | `src/lindy_orchestrator/dispatcher.py` | Claude CLI 调用，流式监控，stall 检测 |
| QA Gates | `src/lindy_orchestrator/qa/` | 可插拔验证门 (ci_check, command_check, structural_check, agent_check, feedback) |
| Discovery | `src/lindy_orchestrator/discovery/` | 项目自动分析 + 组织 artifact 生成 |
| Session | `src/lindy_orchestrator/session.py` | 会话持久化与恢复 |
| GC | `src/lindy_orchestrator/gc.py` | 工作区垃圾回收 |
| CLI | `src/lindy_orchestrator/cli.py` | Typer CLI 入口 |

**设计原则** (已验证的，应保留):
1. **Git-native delivery**: 每个任务 → branch → commit → merge-base 验证
2. **QA gate 自动注入**: structural_check 透明强制，无需 planner 指定
3. **结构化修复反馈**: QA 失败 → 解析错误 → 教 agent 怎么修 (不只告诉什么错)
4. **负向边界约束**: ARCHITECTURE.md 声明 "does NOT" 规则
5. **模块隔离**: 跨模块通过 CONTRACTS.md 接口通信，import 边界机械化强制
6. **Stateless dispatch**: 每次 dispatch 是全新 claude 进程，天然无 context bloat

### Harness Engineering 对照分析

我们已完成与 OpenAI Harness Engineering 方法论的深度对比，结论和设计文档在 `docs/design/`:

| 文档 | 核心结论 |
|------|---------|
| `harness-engineering-comparison.md` | 五原则/三支柱完整映射，识别 10 个差距 |
| `layer-enforcement.md` | 模块内层级强制 QA gate 设计 (P1) |
| `entropy-management.md` | 语义级扫描 + A-F 质量评级 (P2) |
| `observability-integration.md` | 运行时 QA gate + agent 工具脚手架 (P2) |
| `context-engineering.md` | CLAUDE.md 索引化 + docs/agents/ 分层 (P1) |

**请先阅读这些文档作为基础**，私有化演进应在此基础上进行。

---

## 任务

请按以下步骤执行：

### Phase 1: 研究（只读）

1. **通读核心源码**，建立对架构的完整理解：
   - `config.py` → `models.py` → `planner.py` → `scheduler.py` → `dispatcher.py`
   - `qa/` 目录全部文件（理解 gate 注册和执行机制）
   - `discovery/` 目录（理解 onboard 流程和 artifact 生成）
   - `cli.py`（理解所有命令和入口点）

2. **通读设计文档** `docs/design/*.md`，理解已规划但未实现的方向

3. **标注扩展点**: 在代码中找到所有可以被私有化定制的注入点和接口

### Phase 2: 分析私有化需求

基于对代码的理解，分析以下私有化方向的可行性和实现路径：

#### 方向 A: 企业级集成

| 需求 | 说明 |
|------|------|
| **内部 CI/CD** | 对接公司 CI 系统（不限于 GitHub Actions），支持 Jenkins / GitLab CI / 内部构建系统 |
| **消息通知** | 任务状态变更推送到 Slack/飞书/钉钉/企业微信 |
| **项目管理** | 与 Jira/Linear/内部项目管理工具双向同步（任务创建→Jira ticket，Jira 状态→任务更新） |
| **内部文档** | 从 Confluence/Notion/语雀 拉取上下文注入 agent prompt |

#### 方向 B: 多 LLM 后端

| 需求 | 说明 |
|------|------|
| **Dispatcher 抽象** | 当前 dispatcher 硬编码 `claude` CLI。需要抽象为 Provider 接口，支持多种后端 |
| **Claude API 直连** | 跳过 CLI，直接用 Anthropic API（已有 `planner.py` 的 api mode 可参考） |
| **OpenAI / 私有模型** | 支持 GPT、DeepSeek、自部署 LLM 作为 agent 后端 |
| **Model routing** | 按任务复杂度/模块类型路由到不同模型（简单任务→小模型，复杂架构→大模型） |
| **成本追踪** | 每次 dispatch 记录 token 用量和成本，生成报告 |

#### 方向 C: 安全与合规

| 需求 | 说明 |
|------|------|
| **权限控制** | 哪些 agent 可以访问哪些模块，基于角色的 dispatch 权限 |
| **Audit trail** | 增强 JSONL 日志，记录谁触发、什么 prompt、什么输出、什么文件被改 |
| **敏感数据防护** | Agent prompt 中脱敏处理，输出中的密钥/token 自动过滤 |
| **Sandbox 强化** | dispatch 时限制文件系统访问范围、网络访问、环境变量暴露 |
| **代码审查门** | 强制 PR review 流程，agent 不能直接 merge |

#### 方向 D: 团队协作

| 需求 | 说明 |
|------|------|
| **多用户会话** | 多个工程师同时运行 goal，会话不冲突 |
| **任务认领** | 人工介入：某些任务标记为需要人工处理，agent 跳过 |
| **Dashboard** | Web UI 展示：任务进度、QA 结果、模块健康度、历史趋势 |
| **角色分工** | 不同角色看到不同视图（架构师看全局，开发者看自己模块） |

#### 方向 E: 实现 harness engineering 设计文档

| 文档 | 优先级 | 说明 |
|------|--------|------|
| `layer-enforcement.md` | P1 | 实现 `qa/layer_check.py`，模块内层级强制 |
| `context-engineering.md` | P1 | CLAUDE.md 索引化，docs/agents/ 生成 |
| `observability-integration.md` | P2 | `qa/observability_check.py` + 工具脚手架 |
| `entropy-management.md` | P2 | `entropy/scanner.py` + 质量评级 |

### Phase 3: 制定演进方案

产出一份详细的演进计划，包含:

1. **架构改造图**: 当前架构 → 目标架构的变化图（标注新增组件、修改的接口、废弃的部分）

2. **分层优先级**:
   - **Layer 0 (基座)**: 必须先做的架构改造（Dispatcher 抽象、配置扩展、日志增强）——后续所有功能都依赖这些
   - **Layer 1 (核心价值)**: 最直接解决痛点的功能（多 LLM 后端、通知集成、harness P1 设计）
   - **Layer 2 (企业级)**: 安全合规、团队协作、Dashboard
   - **Layer 3 (进阶)**: harness P2 设计、高级 routing、自动化 PR review

3. **关键设计决策**: 对每个重要的技术选型（如 Dispatcher 抽象用 ABC/Protocol/Plugin、通知用 webhook/SDK、Dashboard 用什么框架），给出 2-3 个选项的对比分析和推荐

4. **具体改造方案**: 对 Layer 0 和 Layer 1 的每个改造项，给出:
   - 需要修改/新增的文件列表
   - 接口设计（函数签名或 class 定义）
   - 配置 schema 变更
   - 对现有功能的兼容性影响
   - 测试策略

5. **风险评估**: 每个改造项的风险点和缓解措施

### 输出格式

产出以下文档：

```
docs/
└── evolution/
    ├── overview.md          # 总体演进概述 + 架构变化图
    ├── layer-0-foundation.md # 基座改造详细设计
    ├── layer-1-core.md       # 核心价值功能设计
    ├── layer-2-enterprise.md # 企业级功能设计
    └── decisions.md          # 关键技术决策记录 (ADR 格式)
```

---

## 约束

1. **保持开源版本兼容性**: 私有化改造应通过扩展（新文件/新配置段）而非修改核心逻辑实现。方便将来从上游 merge 更新。
2. **渐进式改造**: 每个 Layer 可以独立交付和验证，不依赖后续 Layer。
3. **不过度设计**: 只设计确定要用的功能。Dashboard 等可以先用 CLI 报告替代，验证需求后再做 Web UI。
4. **复用现有模式**: QA gate 用 `@register()` 模式、配置用 Pydantic BaseModel、日志用 JSONL append-only。不引入新范式。
5. **中文输出**: 所有文档用中文撰写。
