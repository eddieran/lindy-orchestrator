# 设计文档: Agent 运行时调度与生命周期

> v0.3.0 实现 — Provider 抽象 + 流式调度 + 粗粒度 Checkpoint + DAG 调度器

## 1. 设计目标

Agent 运行时管理覆盖从任务分解到执行完成的全生命周期：

```
Goal → Plan → DAG Schedule → Dispatch → Monitor → QA Verify → Retry/Complete
                                 ↑                                    |
                                 └────── Feedback Loop ───────────────┘
```

核心约束：
- 每次 dispatch 是**无状态的全新进程**（区别于 Codex 的长会话 + compaction）
- 通过 QA 反馈循环补偿无状态带来的信息丢失
- 粗粒度 checkpoint（session 级，非 turn 级）

---

## 2. 架构总览

### 2.1 组件关系

```
┌─────────────┐
│   CLI/Goal  │
└──────┬──────┘
       │
┌──────▼──────┐     ┌──────────────┐
│   Planner   │────▶│  Provider    │
│ (planner.py)│     │ (providers/) │
└──────┬──────┘     └──────┬───────┘
       │                   │
┌──────▼──────┐     ┌──────▼───────┐
│  Scheduler  │────▶│  Dispatcher  │
│(scheduler.py│     │(dispatcher.py│
└──────┬──────┘     └──────────────┘
       │
┌──────▼──────┐
│   Session   │
│ (session.py)│
└─────────────┘
```

### 2.2 数据流

```
Goal (str)
  → Planner: generate_plan(goal, config) → TaskPlan
    → Scheduler: execute_plan(plan, config) → TaskPlan (updated)
      → for each TaskItem:
        → Provider.dispatch(module, working_dir, prompt) → DispatchResult
        → _check_delivery(branch) → (ok, msg)
        → QA gates → list[QAResult]
        → if failed: augment prompt + retry
    → Session: save(state) → JSON file
```

---

## 3. Provider 抽象层

### 3.1 Protocol 定义

**实现**: `providers/base.py`

```python
@runtime_checkable
class DispatchProvider(Protocol):
    def dispatch(
        self,
        module: str,
        working_dir: Path,
        prompt: str,
        on_event: Callable[[dict], None] | None = None,
    ) -> DispatchResult: ...

    def dispatch_simple(
        self,
        module: str,
        working_dir: Path,
        prompt: str,
    ) -> DispatchResult: ...
```

两个方法覆盖两种调度模式：
- `dispatch()` — 流式执行，支持心跳回调，用于正式任务
- `dispatch_simple()` — 阻塞执行，用于规划和快速查询

### 3.2 工厂函数

**实现**: `providers/__init__.py`

```python
def create_provider(config: DispatcherConfig) -> DispatchProvider:
    if config.provider == "claude_cli":
        return ClaudeCLIProvider(config)
    raise ValueError(f"Unknown provider: {config.provider}")
```

### 3.3 ClaudeCLIProvider

**实现**: `providers/claude_cli.py`

薄包装层，将 Provider 接口委托给 `dispatcher.py` 的函数：

```python
class ClaudeCLIProvider:
    def __init__(self, config: DispatcherConfig):
        self.config = config

    def dispatch(self, module, working_dir, prompt, on_event=None):
        return dispatch_agent(module, working_dir, prompt, self.config, on_event)

    def dispatch_simple(self, module, working_dir, prompt):
        return dispatch_agent_simple(module, working_dir, prompt, self.config)
```

### 3.4 Config

```python
class DispatcherConfig(BaseModel):
    provider: str = "claude_cli"        # 扩展点
    timeout_seconds: int = 1800         # 硬超时 30 分钟
    stall_timeout_seconds: int = 600    # 卡死超时 10 分钟
    permission_mode: str = "bypassPermissions"
    max_output_chars: int = 50_000      # 输出截断阈值
```

### 3.5 扩展性设计

Provider 抽象使得未来可替换 dispatch 后端：
- `claude_cli` — 当前实现，调用 `claude` CLI
- `anthropic_api` — 直接调用 Anthropic API（planner 已支持此模式）
- `mock` — 测试用 mock provider
- 自定义 — 用户可实现 DispatchProvider Protocol

---

## 4. 两种调度模式

### 4.1 流式调度 (Streaming)

**实现**: `dispatcher.py:dispatch_agent()`

用于正式任务执行，支持实时监控：

```
claude -p <prompt> --output-format stream-json --verbose
         --permission-mode <mode>
```

**进程模型**: `subprocess.Popen()` + 后台读取线程 + Queue

**执行流程**:

```
1. 启动 claude 子进程 (Popen)
2. 后台线程持续读取 stdout → Queue
3. 主线程循环:
   a. 从 Queue 取一行 (5秒超时)
   b. 解析 JSONL → event dict
   c. 提取 tool_use 信息
   d. 调用 on_event 回调 (error-safe)
   e. 累加 event_count
   f. 重置 last_activity 时间戳
   g. 检查硬超时和卡死超时
4. 进程结束 → 提取结果
```

**结果提取优先级**:
1. JSONL 中的 `type == "result"` 事件
2. 拼接所有 assistant text blocks
3. 读取 stderr (fallback)

**输出截断**:
- 超过 `max_output_chars` 时，取头尾各一半 + 省略号
- `DispatchResult.truncated = True` 标记

### 4.2 阻塞调度 (Blocking)

**实现**: `dispatcher.py:dispatch_agent_simple()`

用于规划、报告等快速任务：

```
claude -p <prompt> --permission-mode <mode> --output-format json
```

**进程模型**: `subprocess.run()` 一次性等待完成

**结果提取**:
- 解析 stdout 为 JSON，提取 `parsed["result"]`
- 失败时直接使用 stdout 文本

---

## 5. 卡死检测 (Stall Detection)

**实现**: `dispatcher.py:dispatch_agent()` 主循环

### 5.1 超时层级

```
┌─────────────────────────────────────────────┐
│ 硬超时 (config.timeout_seconds = 1800s)     │  ← 无条件杀死进程
│  ┌────────────────────────────────────────┐  │
│  │ 卡死超时 (动态计算)                     │  │  ← stdout 无输出时杀死
│  │  ┌─────────────────────────────────┐   │  │
│  │  │ 心跳间隔 (5s Queue poll)        │   │  │  ← 检测存活
│  │  └─────────────────────────────────┘   │  │
│  └────────────────────────────────────────┘  │
└─────────────────────────────────────────────┘
```

### 5.2 动态卡死超时

```python
if event_count == 0:
    # 首个事件前的宽限期 — agent 可能在读取大量 context
    effective_stall = max(config.stall_timeout_seconds * 2, 600)
else:
    # 已有事件后 — 最低10分钟 floor，保护长时间 tool 执行 (如 pytest)
    effective_stall = max(config.stall_timeout_seconds, 600)
```

**设计理由**:
- 首事件宽限：agent 启动时需要读取 CLAUDE.md + STATUS.md + 项目文件，可能需要较长时间
- 10分钟 floor：保护 `pytest` / `npm test` 等长时间运行的 tool call
- 任何 stdout 输出都重置卡死计时器

### 5.3 诊断信息

卡死触发时，`DispatchResult` 包含：
- `error = "stall"` 或 `"timeout"`
- `last_tool_use` — 最后调用的工具名
- `event_count` — 已接收事件数
- `duration_seconds` — 实际运行时间

---

## 6. Scheduler — DAG 调度器

### 6.1 执行模型

**实现**: `scheduler.py:execute_plan(plan, config)`

```python
while not plan.all_terminal():
    ready = plan.next_ready()       # 依赖已满足的任务
    with ThreadPoolExecutor(max_workers=config.safety.max_parallel) as pool:
        futures = {pool.submit(_execute_single_task, t): t for t in ready}
        for future in as_completed(futures):
            result = future.result()
```

**DAG 语义**:
- `next_ready()` — 返回所有 depends_on 中的任务全部 COMPLETED 的任务
- 自动跳过 — 依赖 FAILED/SKIPPED 的任务自动标记 SKIPPED
- `all_terminal()` — 所有任务处于 {COMPLETED, FAILED, SKIPPED}

**并行度**: `config.safety.max_parallel` 控制最大并发任务数

### 6.2 单任务执行流程

**实现**: `scheduler.py:_execute_single_task(task, config)`

```
1. QA Gate 自动注入
   ├── structural_check (始终注入)
   ├── layer_check (ARCHITECTURE.md 存在且 enabled 时)
   └── command_check (config.qa_gates.custom 中的每个命令)

2. Dispatch 循环 (while True):
   a. 构建 prompt (含 QA 反馈，如果是重试)
   b. provider.dispatch(module, working_dir, prompt, on_event=heartbeat)
   c. _check_delivery(branch) — 检查分支是否有新 commit
   d. 逐个运行 QA gate
   e. 全部通过 → COMPLETED, return
   f. 任一失败:
      - retries++
      - retries > max_retries → FAILED, return
      - 收集失败 gate 的 output → format_qa_feedback()
      - 增强 prompt → 回到 step a
```

### 6.3 Delivery Check

**实现**: `scheduler.py:_check_delivery()`

验证 agent 是否真正交付了代码：

```python
# 1. 检查分支是否存在 (本地 + remote)
git branch --list {branch}
git branch -r --list */{branch}

# 2. 找到分叉点
git merge-base HEAD {branch}

# 3. 计算新 commit 数
git rev-list --count {fork_point}..{branch}
```

返回 `(ok=True, msg)` 当 commit_count > 0，否则返回警告（非硬失败）。

### 6.4 心跳回调

Scheduler 每 30 秒发射一次心跳事件：

```
⋯ 42 events, 3m 15s, last tool: Write
```

追踪 `event_count` 和 `last_tool`，用于卡死时的诊断。

### 6.5 QA 反馈增强

QA 失败后的 prompt 增强格式：

```markdown
## IMPORTANT: Previous attempt failed QA verification

### [structural_check]
VIOLATION [import_boundary]: backend/routes/api.py imports frontend/...
FIX: Use CONTRACTS.md interface instead...

### [layer_check]
VIOLATION [layer_violation]: models imports from routes (higher).
FIX: Move shared logic to models/ or lower...

Fix these issues. Specific instructions:
- Actually RUN all scripts and commands
- Ensure output files are generated before declaring success
- Verify changes by running relevant test/build commands
- If a CI check failed, check branch was pushed and CI triggered
```

---

## 7. Planner — 目标分解

### 7.1 两种规划模式

**实现**: `planner.py:generate_plan(goal, config)`

| 模式 | 路径 | 使用场景 |
|------|------|---------|
| CLI | `provider.dispatch()` 流式调用 | 默认，使用本地 claude CLI |
| API | `anthropic.Anthropic().messages.create()` | 需要 ANTHROPIC_API_KEY |

### 7.2 规划 Prompt 构建

```
Input:
  - Goal 描述
  - 所有模块 STATUS.md 摘要 (parse_status_md 解析)
  - 模块信息 (名称、路径、技术栈)
  - 可用 QA gates
  - ARCHITECTURE.md 内容

Output:
  - JSON: { tasks: [{ id, module, description, prompt, depends_on, qa_checks }] }
```

### 7.3 依赖推断

如果 planner 输出的所有任务都没有 `depends_on`，scheduler 自动创建顺序链：

```python
if not any(t.depends_on for t in tasks):
    for i in range(1, len(tasks)):
        tasks[i].depends_on = [tasks[i-1].id]
```

### 7.4 结构化 Prompt 渲染

Planner 输出的 `prompt` 字段支持 dict 结构：

```json
{
  "objective": "Add user auth endpoint",
  "context_files": ["backend/routes/auth.py"],
  "constraints": ["Use JWT", "Hash with bcrypt"],
  "verification": ["Run pytest", "Check /auth/login returns 200"]
}
```

渲染为 4 段 Markdown（Objective → Context Files → Constraints → Verification）。

---

## 8. Checkpoint 与 Resume

### 8.1 Session 持久化

**实现**: `session.py:SessionManager`

```python
@dataclass
class SessionState:
    session_id: str          # uuid[:8]
    started_at: str          # ISO-8601
    completed_at: str | None
    goal: str
    status: str              # in_progress, completed, paused, failed
    actions_taken: list[dict]     # 历史操作日志
    pending_tasks: list[dict]     # 未执行任务快照
    completed_tasks: list[dict]   # 已完成任务快照
    plan_json: dict | None        # 完整 TaskPlan 序列化
```

**存储格式**: `{sessions_dir}/{session_id}.json`

**操作**:
- `create(goal)` — 创建新 session
- `save(state)` — 持久化到 JSON
- `load_latest()` — 按 mtime 加载最新 session
- `load(session_id)` — 按 ID 加载
- `complete(state)` — 标记 completed + 设置 completed_at
- `list_sessions(limit)` — 按 mtime 排序列表

### 8.2 Checkpoint 粒度

**当前实现: 粗粒度（Session 级）**

```
                    Checkpoint
                       ↓
[Goal] → [Plan] → [Save Session] → [Execute Task 1] → [Execute Task 2] → ... → [Save Session]
                                         ↑                                           ↑
                                    无中间 checkpoint                          完成后保存
```

- Session 在执行**开始前**和**完成后**保存
- 单个任务执行**过程中没有 checkpoint**
- 崩溃在任务执行中 → 该任务进度完全丢失

### 8.3 Resume 流程

```
1. load_latest() 或 load(session_id)
2. 检查 state.status == "in_progress" 或 "paused"
3. 从 plan_json 重建 TaskPlan
4. 跳过 status == COMPLETED 的任务
5. 从第一个 PENDING 任务继续执行
```

### 8.4 已知限制

| 限制 | 影响 | 缓解策略 |
|------|------|---------|
| 无 turn 级 checkpoint | 任务中崩溃丢失全部进度 | 任务粒度尽量小 |
| plan_json 是快照 | Resume 不感知运行时变化 | Resume 前重新读取 STATUS.md |
| 无 rollback 机制 | 失败任务的 git 变更残留 | 手动 git revert |
| Session 按 mtime 排序 | 并发执行可能混淆最新 session | 使用 session_id 精确指定 |
| 无部分任务恢复 | 多 commit 任务中断后只能重做 | 依赖 git 已 push 的 commit |

---


---

> 进阶主题（Codex 对比、已知缺陷、未来方向）见 [dispatch-runtime-advanced.md](./dispatch-runtime-advanced.md)
