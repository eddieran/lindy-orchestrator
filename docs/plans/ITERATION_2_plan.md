# Iteration 2: Observability, Analytics & Web Dashboard

## 背景

v0.13.0 已发布，包含 lifecycle hooks、per-module concurrency、config hot-reload 和 prompt templates。下一轮迭代聚焦于生产级可观测性：让编排会话可量化、可导出、可视化。

三个特性组合为 4 个递进式 PR：

```
PR 1: Async Hooks + MetricsCollector（基础层）
  │
  ├── PR 2: `stats` CLI 命令（本地文件分析，零外部依赖）
  │
  ├── PR 3: OTel metrics exporter（可选依赖，桥接到 Prometheus/Grafana）
  │
  └── PR 4: Web Dashboard + SSE（实时浏览器 UI）
```

---

## PR 1: Async Hooks + MetricsCollector

### 目标
扩展 `HookRegistry` 支持 async handler（保持 sync handler 完全向后兼容），引入 `MetricsCollector` 作为所有下游消费者（stats CLI、OTel、web dashboard）的统一聚合点。

### 为什么需要
当前 `HookRegistry.emit()` 是同步的——async 导出器（OTel、SSE）会阻塞 dispatch 线程。`MetricsCollector` 提供轻量级的运行时指标聚合，无需外部依赖。

### `src/lindy_orchestrator/hooks.py` — 修改
- 新增 `AsyncEventHandler = Callable[[Event], Awaitable[None]]`
- 新增 `on_async(event_type, handler)` 和 `on_any_async(handler)` 方法
- `emit()` 中通过 `inspect.iscoroutinefunction()` 检测 async handler，在懒创建的后台事件循环线程上执行
- 新增 `_async_loop`、`_async_thread` 实例变量（首次 async emit 时创建）
- 新增 `shutdown()` 方法优雅停止后台循环
- 后台循环线程为 daemon thread，进程退出时自动清理
- 完全向后兼容：现有 sync handler 行为不变

### `src/lindy_orchestrator/metrics.py` — 新建
```python
@dataclass TaskMetrics:            # 单任务: duration, cost, status, qa counts, retry count
@dataclass ModuleMetrics:          # 单模块聚合: cost, task/completed/failed counts, qa rates
@dataclass SessionMetricsSnapshot: # 时间点快照: 总计 + per_module + per_task

class MetricsCollector:
    def attach(self, hooks: HookRegistry) -> None   # 通过 on_any 订阅
    def detach(self) -> None
    def snapshot(self) -> SessionMetricsSnapshot     # 冻结副本，线程安全
    # 内部 handler: _on_task_started, _on_task_completed, _on_task_failed,
    #   _on_task_skipped, _on_qa_passed, _on_qa_failed, _on_stall_*, _on_session_*
```

设计要点：
- 使用 `on_any` 单一 handler 接收所有事件，内部按类型分发
- `threading.Lock()` 保证线程安全（与 HookRegistry 一致）
- `snapshot()` 返回冻结的 dataclass 副本，可安全跨线程传递

### `src/lindy_orchestrator/scheduler.py` — 修改
- 在 `execute_plan()` 中：SESSION_START 前创建 `MetricsCollector` 并 `attach(hooks)`
- SESSION_END 后：`detach()`，将 snapshot 写入 action logger
- finally 块中调用 `hooks.shutdown()`

### `src/lindy_orchestrator/cli.py` — 修改
- `run` 和 `resume` 命令中，在 dashboard.stop() 后调用 `hooks.shutdown()`

### 测试
- `tests/test_async_hooks.py`:
  - async handler 正常触发
  - sync + async handler 共存
  - async handler 异常不阻塞其他 handler
  - shutdown 停止后台循环
  - shutdown 后 sync handler 仍工作
  - 向后兼容（纯 sync 使用不变）

- `tests/test_metrics.py`:
  - attach/detach
  - task 生命周期跟踪（started → completed → duration）
  - cost 累加
  - per-module 聚合
  - snapshot 独立性（修改 snapshot 不影响 collector）
  - 多线程并发安全

### 验证
```bash
pytest tests/test_hooks.py tests/test_async_hooks.py tests/test_metrics.py -v
pytest tests/ -x -q --tb=short
ruff check src/ tests/ && ruff format --check src/ tests/
```

---

## PR 2: `stats` CLI 命令

### 目标
新增 `lindy-orchestrate stats` 命令，跨会话聚合成本、时长、QA 通过率和失败趋势。完全从本地文件读取，零外部依赖。

### 为什么需要
`cost_usd` 在每个 task 上有追踪，但从未跨会话聚合。用户需要快速查看：总花费、各模块成本、QA 通过率、失败趋势。

### 数据源
- 会话 JSON 文件（`.orchestrator/sessions/*.json`）— 包含 `plan_json`（各 task 的 `cost_usd`、`status`、`started_at`、`completed_at`）
- JSONL 日志（`.orchestrator/logs/actions.jsonl`）— 包含 dispatch 和 QA 条目

### `src/lindy_orchestrator/analytics.py` — 新建
```python
@dataclass SessionSummary:  # session_id, goal, status, task counts, cost, duration, modules
@dataclass ModuleStats:     # name, cost, tasks, completed, failed, qa rates, avg duration
@dataclass AggregateStats:  # 总计 + per_module dict + per_session list

def load_session_summaries(sessions_dir: Path) -> list[SessionSummary]
    # 读取会话 JSON，从 plan_json 提取 task 成本
    # 防御性 .get() 处理老格式文件

def parse_log_entries(log_path: Path) -> list[dict]
    # 解析 JSONL，跳过格式错误行

def aggregate_log_metrics(entries) -> dict
    # 从日志条目统计 dispatch 次数、QA pass/fail

def compute_aggregate_stats(sessions_dir, log_path, *, limit=None, module_filter=None) -> AggregateStats
    # 主聚合函数，合并会话 + 日志数据
```

### `src/lindy_orchestrator/cli_stats.py` — 新建
```python
def register_stats_command(app, console, load_cfg) -> None:
    @app.command()
    def stats(config, limit, module, as_json, cost_only) -> None:
```

参数：
- `-n/--limit`: 限制最近 N 个会话
- `--module`: 按模块过滤
- `--json`: JSON 输出
- `--cost-only`: 仅显示成本明细

输出格式（Rich tables）：
```
Aggregate Statistics (12 sessions)
──────────────────────────────────
  Total cost:        $47.23
  Total tasks:       84  (71 completed, 8 failed, 5 skipped)
  QA pass rate:      89.2%
  Avg task duration:  2m15s
  Failure rate:      9.5%

Per-Module Breakdown
┌──────────┬───────┬────────┬────────┬─────────┬────────┐
│ Module   │ Tasks │ Cost   │ Failed │ QA Pass │ Avg    │
├──────────┼───────┼────────┼────────┼─────────┼────────┤
│ backend  │    42 │ $28.10 │      3 │  95.2%  │ 2m30s  │
│ frontend │    31 │ $15.20 │      4 │  82.1%  │ 1m55s  │
│ docs     │    11 │  $3.93 │      1 │  90.9%  │ 1m10s  │
└──────────┴───────┴────────┴────────┴─────────┴────────┘

Recent Sessions
┌─────────┬─────────────────────┬────────┬───────┬───────┐
│ Session │ Goal                │ Status │ Tasks │ Cost  │
├─────────┼─────────────────────┼────────┼───────┼───────┤
│ abc1234 │ Build user auth...  │ done   │     8 │ $5.20 │
│ def5678 │ Add payment flow... │ paused │     6 │ $4.10 │
└─────────┴─────────────────────┴────────┴───────┴───────┘
```

### `src/lindy_orchestrator/cli.py` — 修改
- 底部新增：`from .cli_stats import register_stats_command; register_stats_command(app, console, load_cfg)`

### 测试
- `tests/test_analytics.py`:
  - 空目录返回空列表
  - 单个完成的会话正确解析
  - 从 plan_json 提取 task cost
  - 格式错误的会话文件被跳过
  - limit 参数生效
  - module 过滤生效
  - QA 通过率和失败率计算正确

- `tests/test_cli_stats.py`:
  - 无会话时显示 "No sessions found"
  - 有会话时输出格式化表格
  - `--json` 输出有效 JSON
  - `--cost-only` 仅显示成本表
  - `--module` 过滤正确

### 验证
```bash
pytest tests/test_analytics.py tests/test_cli_stats.py -v
lindy-orchestrate stats --help
ruff check src/ tests/ && ruff format --check src/ tests/
```

---

## PR 3: OpenTelemetry Metrics Exporter

### 目标
可选的 OTel metrics 导出器，将 hook 事件桥接到 OTel SDK，输出到 Prometheus/Grafana。安装方式：`pip install lindy-orchestrator[otel]`。

### 为什么需要
生产部署中，团队希望在 Prometheus/Grafana 中看到指标。OTel exporter 将 hook 驱动的事件桥接到标准可观测性工具链——作为可选依赖。

### `pyproject.toml` — 修改
```toml
[project.optional-dependencies]
otel = [
    "opentelemetry-api>=1.20",
    "opentelemetry-sdk>=1.20",
    "opentelemetry-exporter-otlp-proto-grpc>=1.20",
]
dev = [... 现有 ..., "opentelemetry-sdk>=1.20"]
```

### `src/lindy_orchestrator/config.py` — 修改
```python
class OTelConfig(BaseModel):
    enabled: bool = False
    exporter: str = "console"  # "console" | "otlp"
    endpoint: str = ""
    service_name: str = "lindy-orchestrator"

# OrchestratorConfig 新增：
otel: OTelConfig = Field(default_factory=OTelConfig)
```

### `src/lindy_orchestrator/otel.py` — 新建
- 受保护导入：`try: from opentelemetry import metrics; except ImportError: _otel_available = False`
- `is_otel_available() -> bool`
- `OTelMetricsExporter`:
  - 通过 `on_any()` 订阅 HookRegistry
  - OTel instruments:
    - `lindy.task.duration`（histogram, seconds）
    - `lindy.task.cost`（histogram, USD）
    - `lindy.task.completed`（counter）
    - `lindy.task.failed`（counter）
    - `lindy.task.skipped`（counter）
    - `lindy.dispatch.count`（counter）
    - `lindy.qa.passed`（counter）
    - `lindy.qa.failed`（counter）
    - `lindy.stall.warning`（counter）
  - 所有指标带 `module` 属性标签
- `create_otel_exporter(endpoint, exporter_type)` — 工厂方法
- `setup_otel_from_config(config_dict) -> OTelMetricsExporter | None`

### `src/lindy_orchestrator/scheduler.py` — 修改
- MetricsCollector attach 之后：若 `config.otel.enabled`，懒加载并 attach OTel exporter
- 清理阶段：detach + shutdown OTel exporter
- 捕获 `ImportError`，OTel 启用但未安装时发出 warning

### 测试
- `tests/test_otel.py`:
  - import guard（未安装时优雅处理）
  - attach/detach
  - counter 和 histogram 记录
  - module 属性标签
  - config disabled 返回 None
  - 未安装 SDK 返回 None
  - 使用 `pytest.importorskip("opentelemetry")` 条件跳过

### 验证
```bash
pip install -e ".[otel,dev]"
pytest tests/test_otel.py -v
pytest tests/test_config.py -v
ruff check src/ tests/ && ruff format --check src/ tests/
```

---

## PR 4: Web Dashboard + SSE

### 目标
SSE（Server-Sent Events）驱动的浏览器实时 dashboard，替代/补充 ASCII DAG。使用纯 stdlib（`http.server`、`threading`、`json`），零外部 web 框架依赖。

### 为什么需要
ASCII DAG dashboard 在终端中工作良好，但无法分享、收藏或在 CI 中使用。浏览器 dashboard 通过 SSE 提供实时可见性，且不引入额外依赖。

### 选择 SSE 而非 WebSocket 的原因
- 更简单：单向（server→client），stdlib 即可实现
- 无需握手协议升级
- 浏览器原生支持 EventSource API，自动重连

### `src/lindy_orchestrator/web/__init__.py` — 新建（空 package）

### `src/lindy_orchestrator/web/server.py` — 新建
```python
class SSEManager:
    # 管理客户端队列，broadcast(event_type, data)，add/remove clients
    # 捕获 BrokenPipeError/ConnectionResetError 自动清理断开的客户端

class DashboardRequestHandler(BaseHTTPRequestHandler):
    # 路由:
    #   GET /           → index.html
    #   GET /static/*   → 静态资源 (JS, CSS)
    #   GET /api/events → SSE 事件流
    #   GET /api/state  → 当前 plan 状态 JSON
    #   GET /api/metrics → MetricsCollector snapshot JSON
    # 静默请求日志（覆盖 log_message）

class WebDashboard:
    def __init__(self, plan, hooks, metrics=None, host="127.0.0.1", port=8420)
    def start(self) -> str          # 启动 server 线程，返回 URL
    def stop(self) -> None          # 停止 server，取消订阅 hooks
    def _on_event(self, event) -> None  # Event → SSE broadcast
```

### `src/lindy_orchestrator/web/static/index.html` — 新建
- 单页应用，内嵌 CSS/JS
- Task DAG 可视化（CSS box model，按状态着色）
- 成本累计侧边栏、QA 结果日志
- EventSource 自动重连 SSE
- 初始状态从 `/api/state` 获取，定期从 `/api/metrics` 轮询指标

### `src/lindy_orchestrator/cli.py` — 修改
- `run` 和 `resume` 命令新增 `--web` 和 `--web-port` 参数
- `--web` 启用时：创建 WebDashboard，执行前启动，打印 URL，执行后停止
- 可与 ASCII dashboard 并行运行或替代运行

端口策略：默认 8420，端口被占用时尝试 8421-8430，全部失败时清晰报错

### 测试
- `tests/test_web_server.py`:
  - SSEManager: add/remove client, broadcast, 无 client 不报错, 断开客户端清理
  - DashboardRequestHandler: 路由 200/404, index.html 返回 HTML
  - WebDashboard: start/stop, hook 事件广播到 SSE 客户端

- `tests/test_web_integration.py`:
  - 完整生命周期: 启动 → HTTP 请求 → emit 事件 → SSE 接收 → 停止
  - `--web` flag 验证

### 走查
通过Agent Browser来对你能想到的所有页面功能进行走查。
查到问题，反工修复
全部走查成功后，反手沉淀下来走查的路径，沉淀成行为测试或者 E2E 测试。

### 验证
```bash
pytest tests/test_web_server.py tests/test_web_integration.py -v
pytest tests/ -x -q --tb=short
lindy-orchestrate run --help  # 验证 --web flag
ruff check src/ tests/ && ruff format --check src/ tests/
```

---

## 关键设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 新增必要依赖 | 无 | OTel 是可选 extras；web 用纯 stdlib |
| MetricsCollector 是否需要配置 | 否 | 轻量级，始终激活，无配置开销 |
| Async hooks 实现 | 独立 daemon thread + event loop | 不阻塞 dispatch 线程，进程退出自动清理 |
| `stats` 数据源 | 本地文件 | 离线工作，不依赖 OTel/Prometheus |
| Web 实时协议 | SSE（非 WebSocket） | 单向，stdlib 即可，EventSource 自动重连 |
| Web dashboard 端口 | 8420（自动递增） | 不太可能冲突，冲突时自动尝试下一个 |

## 依赖关系

```
PR 1 → PR 2（使用 MetricsCollector 的 dataclass 保持类型一致）
PR 1 → PR 3（OTel 使用 async hooks）
PR 1 → PR 4（WebDashboard 使用 HookRegistry + MetricsCollector）
PR 2 ← 独立数据源（本地文件），但复用 PR 1 的数据结构
```

PR 2 和 PR 3 之间没有硬依赖，可以并行开发。PR 4 最后，整合所有能力。

## 全局验证（每个 PR 都要跑）
```bash
python -m pytest tests/ -x -q --tb=short && python -m ruff check src/ tests/ && python -m ruff format --check src/ tests/
```

全部完成后，针对每一个功能，进行完整的走查，保证无论是PR的流水线，还是e2e测试，都能完整通过，包括codex和claude的功能。
并且补全/更新缺失的测试。

## 版本规划
全部完成后，打一个0.14.0的版本，统一4个pr，一起releaes


