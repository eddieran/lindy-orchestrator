# lindy-orchestrator 使用指南 — 辅助功能

> 本文件是 [USAGE.md](USAGE.md) 的补充，涵盖工具命令、QA 门禁详解和 Dispatch 模式。

---

## 工具命令

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
