# lindy-orchestrator v0.8.0 辅助功能指南

> 核心用法参见 [USAGE.md](USAGE.md)。本文档覆盖辅助 CLI 命令、QA 门禁、事件系统、执行报告等。

---

## 目录

1. [gc — 垃圾回收](#gc--垃圾回收)
2. [scan — 熵扫描](#scan--熵扫描)
3. [mailbox — 邮箱系统](#mailbox--邮箱系统)
4. [issues — 查看 Issue](#issues--查看-issue)
5. [run-issue — 执行 Issue](#run-issue--执行-issue)
6. [实时 DAG 仪表盘](#实时-dag-仪表盘)
7. [Hook / 事件系统](#hook--事件系统)
8. [QA 门禁详解](#qa-门禁详解)
9. [结构化 QA 反馈与渐进式重试](#结构化-qa-反馈与渐进式重试)
10. [Dispatch 模式](#dispatch-模式)
11. [执行总结报告](#执行总结报告)
12. [会话管理与检查点](#会话管理与检查点)
13. [STATUS.md 格式](#statusmd-格式)
14. [常见问题排查](#常见问题排查)

---

## gc — 垃圾回收

清理 Agent 生成的工件残留。**默认 dry run，不执行任何删除操作。**

```bash
lindy-orchestrate gc                     # 预览要清理的内容
lindy-orchestrate gc --apply             # 实际执行清理
lindy-orchestrate gc --branch-age 7 --session-age 14 --log-size 5 --status-stale 3
```

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
| stale_branch | 删除超龄的 `{prefix}/task-*` 本地分支 |
| old_session | 归档超龄会话文件到 `sessions/archive/` |
| log_rotation | 超大日志重命名（加时间戳），创建空日志 |
| status_drift | 提示长期未更新的 STATUS.md（仅报告，不修改） |
| orphan_plan | 报告超过 30 天且无会话引用的计划文件 |

---

## scan — 熵扫描

扫描项目中的架构漂移、契约违规和质量衰退。

```bash
lindy-orchestrate scan                   # 全量扫描
lindy-orchestrate scan --module backend  # 只扫描特定模块
lindy-orchestrate scan --grade-only      # 只看评分
```

| 选项 | 短选项 | 说明 |
|------|--------|------|
| `--config PATH` | `-c` | 指定配置文件路径 |
| `--module NAME` | — | 只扫描指定模块 |
| `--grade-only` | — | 只显示评分（A-F） |

扫描维度：

| 检查项 | 检测内容 |
|--------|----------|
| architecture_drift | ARCHITECTURE.md 声明与实际文件系统的不一致 |
| contract_violation | CONTRACTS.md 缺失必要章节、未覆盖模块 |
| status_drift | 健康度无效、长期未更新、IN_PROGRESS 任务但分支不存在 |
| quality | 超大文件（>500 行）、缺少测试目录 |

每个发现项包含严重级别（error/warning/info）和修复建议。每个模块获得 A-F 评分。

---

## mailbox — 邮箱系统

查看或发送跨模块消息。Agent 执行时可通过邮箱进行异步通信。

```bash
lindy-orchestrate mailbox                    # 所有模块邮箱汇总
lindy-orchestrate mailbox frontend           # 特定模块待处理消息
lindy-orchestrate mailbox frontend --json    # JSON 输出
lindy-orchestrate mailbox --send-to backend --send-from frontend -m "Need REST API"
lindy-orchestrate mailbox --send-to backend -m "Urgent: API broken" -p urgent
```

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
- `inject_on_dispatch: true` 时，派发前自动注入待处理消息到 prompt
- 纯文件系统实现，无外部依赖

---

## issues — 查看 Issue

从配置的 tracker（GitHub Issues）拉取 issue 列表。

```bash
lindy-orchestrate issues                 # 基本用法
lindy-orchestrate issues --label bug     # 按标签过滤
lindy-orchestrate issues --status closed # 过滤状态
lindy-orchestrate issues -n 50           # 限制数量
lindy-orchestrate issues --json          # JSON 输出
```

| 选项 | 短选项 | 说明 |
|------|--------|------|
| `--config PATH` | `-c` | 指定配置文件路径 |
| `--label NAME` | — | 按标签过滤 |
| `--status STATE` | — | Issue 状态过滤，默认 `open` |
| `--limit N` | `-n` | 最大获取数，默认 20 |
| `--json` | — | JSON 输出 |

**前提：** 配置 `tracker.enabled: true` 和 `tracker.repo`，并安装配置好 `gh` CLI。

---

## run-issue — 执行 Issue

从 tracker 拉取 issue 并作为目标执行。

```bash
lindy-orchestrate run-issue 42           # 执行 issue #42
lindy-orchestrate run-issue 42 --dry-run # 模拟运行
lindy-orchestrate run-issue 42 -v        # 详细输出
```

| 选项 | 短选项 | 说明 |
|------|--------|------|
| `ISSUE_ID` | — | 位置参数（必填），Issue ID |
| `--config PATH` | `-c` | 指定配置文件路径 |
| `--dry-run` | — | 只规划，不执行 |
| `--verbose` | `-v` | 详细输出 |

完成后行为（`tracker.sync_on_complete: true`）：所有任务成功 → 自动评论并关闭 issue；有失败 → 只添加评论，不关闭。

---

## 实时 DAG 仪表盘

执行任务时，终端显示实时更新的 DAG 仪表盘。

- **ASCII 任务 DAG 树**：展示任务依赖关系和当前状态
- **状态图标**：completed/failed/running/pending/skipped
- **摘要栏**：各状态任务数量和已用时间
- **注解气泡**（verbose 模式）：每个任务正在使用的工具或最新状态

| 环境 | 行为 |
|------|------|
| TTY 终端 | Rich Live 面板，每秒刷新 4 次，完成后打印最终 DAG |
| 非 TTY（CI、管道） | 回退到文本进度输出 |

Dashboard 通过订阅 Hook 事件驱动：`task_started` / `task_completed` / `task_failed` / `task_retrying` / `task_skipped` / `task_heartbeat` / `qa_passed` / `qa_failed` / `checkpoint_saved` / `stall_warning`。

```bash
lindy-orchestrate run "My goal" -v   # 启用 verbose 查看注解
```

---

## Hook / 事件系统

编排器使用事件驱动架构。`HookRegistry` 是中央事件总线。

### 事件类型

| 事件 | 触发时机 |
|------|----------|
| `task_started` | 任务开始执行 |
| `task_completed` | 任务执行成功 |
| `task_failed` | 任务执行失败 |
| `task_retrying` | QA 失败后重试 |
| `task_skipped` | 依赖失败导致跳过 |
| `qa_passed` / `qa_failed` | QA 门禁结果 |
| `stall_warning` / `stall_killed` | Agent 停滞检测 |
| `task_heartbeat` | Agent 产生事件（携带工具名） |
| `checkpoint_saved` | 检查点保存 |
| `mailbox_message` | 邮箱消息 |
| `session_start` / `session_end` | 会话生命周期 |

### Event 数据结构

```python
@dataclass
class Event:
    type: EventType          # 事件类型
    timestamp: str           # ISO 时间戳
    data: dict[str, Any]     # 事件附带数据
    task_id: int | None      # 关联的任务 ID
    module: str              # 关联的模块名
```

### HookRegistry API

- `on(event_type, handler)` — 注册特定事件处理函数
- `on_any(handler)` — 注册全局处理函数
- `emit(event)` — 发出事件
- `remove(event_type, handler)` / `remove_any(handler)` — 移除处理函数
- `clear()` — 移除所有处理函数

---

## QA 门禁详解

QA 门禁在每个任务完成后自动运行。支持五种类型：

### 1. CI Check

轮询 GitHub Actions CI 状态。需要 `gh` CLI 和模块配置 `repo` + `ci_workflow`。

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

派发独立 QA Agent 验证。需要 `role: qa` 模块。QA Agent 输出 `QA_RESULT: PASS` 或 `QA_RESULT: FAIL` + `FAILURE_REASON: ...`。

### 4. Structural Check（内置）

自动运行，不需额外配置：

| 检查 | 说明 |
|------|------|
| file_size | 文件超过 `max_file_lines` 行 → 建议拆分 |
| sensitive_file | 匹配 `.env`、`*.key`、`*.pem` → 建议加 .gitignore |
| import_boundary | 跨模块直接导入 → 建议使用 CONTRACTS.md 接口 |

### 5. Layer Check（内置）

基于 ARCHITECTURE.md 层级声明验证模块内部导入方向。

```markdown
# ARCHITECTURE.md 示例
- **backend/**: models → schemas → services → routes → main
```

规则：`layer[i]` 只能导入 `layer[j]`（j <= i）。共享目录（utils/、shared/、common/）免检。

---

## 结构化 QA 反馈与渐进式重试

### 结构化反馈

QA 失败时，编排器会：分类（test_failure / lint_error / type_error / build_error / boundary_violation / timeout）→ 解析（针对 pytest / ruff / tsc 提取结构化错误）→ 定位（提取文件路径和行号）→ 指导（附加修复建议）。

### 渐进式重试

| 重试次数 | Prompt 策略 |
|----------|-------------|
| 第 1 次 | 完整原始 prompt + 结构化反馈（错误列表 + 文件路径 + 修复步骤） |
| 第 2 次+ | 精简 prompt，只包含失败错误和文件，指示直接修复 |

最大重试次数由 `safety.max_retries_per_task` 控制。

---

## Dispatch 模式

| 模式 | 函数 | 适用场景 |
|------|------|----------|
| 流式 | `dispatch_agent()` | 长任务 — 实时心跳监控、stall 升级、事件回调 |
| 阻塞 | `dispatch_agent_simple()` | 短任务 — 规划、报告生成，无线程开销 |

**流式 Dispatch：** 使用 `--output-format stream-json` 获取 JSONL 事件流。后台线程读取 stdout，每行输出重置 stall 计时器。提取 `tool_use` 和 `result` 事件。超过 `max_output_chars` 自动截断（保留首尾各半）。

**阻塞 Dispatch：** 使用 `--output-format json` 获取完整 JSON。`subprocess.run()` 同步等待。解析 `result` 字段。同样支持输出截断。

---

## 执行总结报告

每次执行（`run` / `resume` / `run-issue`）完成后：

**终端输出：**
- Header Panel — 完成/暂停状态、session ID、任务统计、总时长
- Task Details 表 — 模块、描述、状态、时长、重试次数、QA 结果、输出预览
- Execution Metrics 表 — 总任务数、完成/失败/跳过数、总时长、预估成本

**Markdown 报告：** 保存到 `.orchestrator/reports/{session_id}_summary.md`，包含完整任务明细和 QA 结果。

---

## 会话管理与检查点

### 会话状态

每次 `run` / `run-issue` 创建一个会话，持久化到 `.orchestrator/sessions/{session_id}.json`。

字段：`session_id`（8 位 UUID）、`goal`、`status`（in_progress / completed / paused / failed）、`plan_json`（TaskPlan 快照）、`checkpoint_count`、`started_at` / `completed_at`。

### 检查点

每个任务完成后自动保存检查点。中断后可用 `resume` 恢复。

### 会话安全

- 会话 ID 正则校验（仅字母数字、下划线、横线），防路径穿越
- 文件加载校验路径在 sessions 目录内

---

## STATUS.md 格式

每个模块的 STATUS.md 是 Agent 了解模块现状的入口：

```markdown
## Meta
| Key | Value |
|-----|-------|
| module | backend |
| last_updated | 2026-03-02 10:00 UTC |
| overall_health | GREEN |

## Active Work
| ID | Task | Status | BlockedBy | Started | Notes |
|----|------|--------|-----------|---------|-------|
| BE-001 | 用户认证 API | IN_PROGRESS | — | 2026-03-01 | JWT 方案 |

## Completed (Recent)
| ID | Task | Completed | Outcome |
|----|------|-----------|---------|

## Backlog
- [ ] 接入 OAuth2

## Cross-Module Requests / Deliverables
（跨模块请求和交付物表格）

## Blockers
- (none)
```

健康度：`GREEN`=正常 / `YELLOW`=有风险 / `RED`=被阻塞。

---

## 常见问题排查

### CLI 未找到

```
Error: Claude CLI not found in PATH.   → 安装 Claude Code CLI
Error: Codex CLI not found in PATH.    → 安装 Codex CLI
```

### 没检测到模块

用 `lindy-orchestrate init --modules "myapp"` 手动指定，或 `onboard` 自动识别根目录为单模块。

### STATUS.md 解析错误

解析器容错，不会崩溃。表格格式不对时字段可能为空。用 `lindy-orchestrate validate` 检查。

### Agent 超时 / 停滞

调整配置：

```yaml
dispatcher:
  timeout_seconds: 3600
  stall_escalation:
    warn_after_seconds: 600
    kill_after_seconds: 1200
```

### 想跳过 QA 快速迭代

去掉 `qa_gates.custom` 配置项。CI check 和 Agent check 只在 LLM 规划时分配，不配置 repo 就不会触发。

### Tracker 未启用

```yaml
tracker:
  enabled: true
  provider: github
  repo: yourorg/yourrepo
```

### Mailbox 未启用

```yaml
mailbox:
  enabled: true
```

### 配置文件找不到

编排器从当前目录向上搜索最多 10 层父目录。确保在项目目录内运行，或用 `-c` 指定路径。
