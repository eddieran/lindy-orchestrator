# Agent 运行时调度 — 进阶主题

> 本文档是 [dispatch-runtime.md](./dispatch-runtime.md) 的延续。

## 9. 与 Codex 模型对比

| 维度 | Codex Agent Loop | Lindy Dispatch |
|------|-----------------|---------------|
| 进程模型 | 单进程长会话，多轮 tool call | 每任务新进程，单轮执行 |
| Context 管理 | 对话累积 → compaction 压缩 | 无累积，每次全新 prompt |
| 协议 | JSON-RPC over stdio (Item → Turn → Thread) | claude CLI stream-json |
| 会话持久性 | Thread 支持 create/resume/fork/archive | Session JSON (coarse-grained) |
| Sandbox | OS 级 (Seatbelt/seccomp) + 网络隔离 | 无 sandbox，permission_mode 预设 |
| 审批流 | 双向 — server 请求 client allow/deny | 单向 — 预设 permission_mode |
| Checkpoint | 每个 Turn 持久化 | 仅 Session 级 |
| 状态恢复 | Thread resume 从任意点继续 | 仅跳过已完成任务 |

**关键差异**: Codex 在单次长会话中通过 compaction 管理 context window，天然支持 Turn 级 checkpoint。lindy-orchestrator 的每次 dispatch 是全新进程，天然无 context bloat，但也无法跨 turn 保留中间状态——通过 QA 反馈循环和结构化 prompt 来补偿。

---

## 10. DispatchResult 数据模型

```python
@dataclass
class DispatchResult:
    module: str
    success: bool             # exit_code == 0
    output: str               # agent 输出文本
    exit_code: int = 0
    duration_seconds: float = 0.0
    truncated: bool = False   # 输出是否被截断
    error: str | None = None  # "timeout", "stall", "cli_not_found", "dispatcher_error"
    event_count: int = 0      # 接收到的 JSONL 事件数
    last_tool_use: str = ""   # 最后调用的工具名
```

**诊断价值**:
- `error` — 区分超时、卡死、CLI 缺失、内部错误
- `event_count` — 0 意味着 agent 可能未启动成功
- `last_tool_use` — 卡死时帮助定位是哪个 tool 执行过长
- `duration_seconds` — 性能基线

---

## 11. 调用方集成

### 11.1 Scheduler

```python
# scheduler.py
provider = create_provider(config.dispatcher)
result = provider.dispatch(
    module=task.module,
    working_dir=working_dir,
    prompt=augmented_prompt,
    on_event=heartbeat_callback,
)
```

### 11.2 Planner

```python
# planner.py
provider = create_provider(config.dispatcher)
result = provider.dispatch(
    module="planner",
    working_dir=config.root,
    prompt=plan_prompt,
    on_event=heartbeat_callback,
)
```

### 11.3 Agent Check (QA)

```python
# qa/agent_check.py
provider = create_provider(dispatcher_config)
result = provider.dispatch(
    module=module_name,
    working_dir=project_root,
    prompt=review_prompt,
)
```

所有调用方通过 Provider 接口统一，不直接依赖 dispatcher.py 函数。

---

## 12. 已知缺陷与未来方向

### 当前缺陷

1. **无 Turn 级 Checkpoint**: 单任务执行中崩溃丢失全部进度
2. **无 Rollback**: 失败任务的 git 变更残留在分支上
3. **Session 排序脆弱**: 依赖 mtime，并发执行时可能混淆
4. **Delivery Check 非原子**: 分支存在但 push 未完成时误判
5. **无 Sandbox**: Agent 继承 host 环境所有权限，依赖 permission_mode 预设
6. **串行 QA**: QA gates 逐个运行，无并行优化
7. **无网络隔离**: Agent 可访问任何网络资源
8. **Stall 检测假阳性**: 极长 tool call（如大型 monorepo 的 git clone）可能误触发

### 未来方向

| 方向 | 描述 | 优先级 |
|------|------|--------|
| Turn 级 Checkpoint | 在 tool call 间保存中间状态 | P1 |
| Git Rollback | 任务失败时自动 revert 分支 | P1 |
| Async Provider | 支持 async/await 的异步调度 | P2 |
| Sandbox 模式 | Per-task 目录隔离 + 网络限制 | P2 |
| Parallel QA | 独立 QA gates 并行执行 | P3 |
| Sub-agent Dispatch | 单任务内并行子 agent | P3 |
| Custom Provider SDK | 文档化 Provider Protocol，支持第三方实现 | P3 |
