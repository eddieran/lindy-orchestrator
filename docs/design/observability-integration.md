# 概念设计: 可观测性集成

> 让 Agent 从"盲"到"有眼睛"

## 1. 问题陈述

当前被 dispatch 的 agent 对运行时状态完全盲目。Agent 可以写代码、运行测试、检查文件，但不能:

- 验证部署后的应用是否健康
- 查询应用日志中是否有异常
- 检查指标是否在正常范围
- 看到浏览器中页面的渲染结果

当 agent 完成一个 "部署认证服务" 的任务后，它只能通过 pytest 验证逻辑正确性，无法验证服务在运行时是否真的能响应请求。

OpenAI 的做法:
- **OpenTelemetry**: Agent 可查询 traces/metrics/logs
- **Chrome DevTools Protocol**: Agent 可获取 DOM 快照、截图、导航
- **Per-worktree 可观测性栈**: 每个工作区自带 Prometheus + Grafana

**我们的策略**: 不做重量级的 per-worktree 监控栈，而是从两个轻量方向切入:
1. **Observability QA Gate** — 运行时验证作为 QA 检查
2. **Agent Tool Scaffolding** — 为 agent 提供可调用的自定义工具

## 2. 方案 A: Observability QA Gate

### 2.1 三种验证模式

```yaml
# orchestrator.yaml
qa_gates:
  observability:
    health_checks:
      - url: "http://localhost:8000/health"
        expected_status: 200
        timeout: 10
    log_checks:
      - file: "logs/app.log"
        error_pattern: "ERROR|CRITICAL|Traceback"
        max_errors: 0
    metric_checks:
      - command: "curl -s localhost:9090/api/v1/query?query=http_requests_total"
        threshold: "> 0"
```

### 2.2 Health Endpoint 检查

```python
def _check_health(checks: list[HealthCheck]) -> list[Finding]:
    """轮询 health endpoint。"""
    findings = []
    for check in checks:
        try:
            resp = subprocess.run(
                ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                 "--max-time", str(check.timeout), check.url],
                capture_output=True, text=True, timeout=check.timeout + 5,
            )
            status = int(resp.stdout.strip())
            if status != check.expected_status:
                findings.append(Finding(
                    "health_check",
                    f"{check.url} returned {status} (expected {check.expected_status})",
                    f"Check service logs. Ensure the service is running and healthy."
                ))
        except (subprocess.TimeoutExpired, ValueError):
            findings.append(Finding(
                "health_check",
                f"{check.url} timed out after {check.timeout}s",
                "Service may not be running. Check `docker ps` or process list."
            ))
    return findings
```

### 2.3 日志模式扫描

```python
def _check_logs(checks: list[LogCheck]) -> list[Finding]:
    """扫描日志文件中的错误模式。"""
    findings = []
    for check in checks:
        log_path = Path(check.file)
        if not log_path.exists():
            continue

        # 只检查最近 N 行 (避免扫描巨大日志)
        tail = subprocess.run(
            ["tail", "-n", "500", str(log_path)],
            capture_output=True, text=True, timeout=10,
        )

        matches = re.findall(check.error_pattern, tail.stdout)
        if len(matches) > check.max_errors:
            findings.append(Finding(
                "log_check",
                f"Found {len(matches)} error(s) matching `{check.error_pattern}` "
                f"in `{check.file}` (limit: {check.max_errors})",
                f"Review last errors:\n" + "\n".join(
                    f"  {line}" for line in tail.stdout.splitlines()
                    if re.search(check.error_pattern, line)
                )[:500]
            ))
    return findings
```

### 2.4 指标阈值检查

```python
def _check_metrics(checks: list[MetricCheck]) -> list[Finding]:
    """执行指标查询命令并检查阈值。"""
    findings = []
    for check in checks:
        try:
            result = subprocess.run(
                check.command, shell=True,
                capture_output=True, text=True, timeout=30,
            )
            # 简单阈值判断: "> N", "< N", "== N"
            if not _eval_threshold(result.stdout.strip(), check.threshold):
                findings.append(Finding(
                    "metric_check",
                    f"Metric check failed: `{check.command}` "
                    f"result={result.stdout.strip()}, expected {check.threshold}",
                    "Investigate the metric source and verify the value is expected."
                ))
        except subprocess.TimeoutExpired:
            findings.append(Finding(
                "metric_check",
                f"Metric command timed out: `{check.command}`",
                "Check if the metrics endpoint is reachable."
            ))
    return findings
```

### 2.5 Gate 注册

```python
@register("observability_check")
class ObservabilityCheckGate:
    def check(self, params, project_root, module_name, task_output, **kwargs):
        findings = []

        if "health_checks" in params:
            findings.extend(_check_health(params["health_checks"]))
        if "log_checks" in params:
            findings.extend(_check_logs(params["log_checks"]))
        if "metric_checks" in params:
            findings.extend(_check_metrics(params["metric_checks"]))

        passed = len(findings) == 0
        return QAResult(
            gate="observability_check",
            passed=passed,
            output=_format_findings(findings),
        )
```

### 2.6 优雅降级

- 如果没有配置 observability，gate 不注入（区别于 structural_check 总是注入）
- 如果配置了但 endpoint 不可达，返回 warning 而非 failure
- 支持 `optional: true` 参数，failure 只产出 warning 不阻塞任务

## 3. 方案 B: Agent Tool Scaffolding

### 3.1 概念

在 `lindy-orchestrate onboard` 时，根据检测到的技术栈，自动生成 agent 可调用的 shell 脚本到 `.orchestrator/tools/`。Dispatch 时将此目录加入 PATH。

### 3.2 工具检测与生成

```python
# discovery/analyzer.py 新增检测逻辑
def detect_tooling_needs(profile: ProjectProfile) -> list[ToolSpec]:
    tools = []

    for mod in profile.modules:
        tech = [t.lower() for t in mod.tech_stack]

        # 数据库工具
        if any(marker in tech for marker in ["postgresql", "mysql", "sqlite"]):
            tools.append(ToolSpec(
                name="db-query",
                description="Query the database",
                template="db_query.sh",
            ))

        # API 健康检查
        if any(marker in tech for marker in ["fastapi", "express", "flask", "django"]):
            tools.append(ToolSpec(
                name="api-health",
                description="Check API health endpoints",
                template="api_health.sh",
            ))

        # 日志搜索
        tools.append(ToolSpec(
            name="log-search",
            description="Search application logs for patterns",
            template="log_search.sh",
        ))

    return tools
```

### 3.3 工具模板示例

```bash
#!/usr/bin/env bash
# .orchestrator/tools/api-health
# Auto-generated by lindy-orchestrate onboard
set -euo pipefail

URL="${1:-http://localhost:8000/health}"
TIMEOUT="${2:-5}"

echo "Checking health: $URL"
STATUS=$(curl -s -o /dev/null -w "%{http_code}" --max-time "$TIMEOUT" "$URL" 2>/dev/null || echo "000")

if [ "$STATUS" = "200" ]; then
    echo "HEALTHY (HTTP $STATUS)"
    exit 0
else
    echo "UNHEALTHY (HTTP $STATUS)"
    exit 1
fi
```

```bash
#!/usr/bin/env bash
# .orchestrator/tools/log-search
# Auto-generated by lindy-orchestrate onboard
set -euo pipefail

PATTERN="${1:-ERROR}"
LOG_DIR="${2:-logs}"
LINES="${3:-50}"

echo "Searching for '$PATTERN' in $LOG_DIR (last $LINES lines per file)"
find "$LOG_DIR" -name "*.log" -exec sh -c '
    echo "--- $1 ---"
    tail -n '"$LINES"' "$1" | grep -n "$0" || echo "(no matches)"
' "$PATTERN" {} \;
```

### 3.4 PATH 注入

```python
# dispatcher.py 修改 (概念)
def dispatch_agent(prompt, *, config, ...):
    env = os.environ.copy()

    # 注入 .orchestrator/tools/ 到 PATH
    tools_dir = config.root / ".orchestrator" / "tools"
    if tools_dir.is_dir():
        env["PATH"] = f"{tools_dir}:{env.get('PATH', '')}"

    # ... 现有 dispatch 逻辑
```

### 3.5 在 CLAUDE.md 中声明工具

```python
# root_claude_md.py 新增段落
if tools_dir.exists():
    sections.append("## Available Tools\n")
    sections.append("The following tools are available in your PATH:\n")
    for tool in sorted(tools_dir.iterdir()):
        if tool.is_file() and os.access(tool, os.X_OK):
            desc = _extract_description(tool)  # 从注释中提取
            sections.append(f"- `{tool.name}` — {desc}")
```

## 4. 配置 Schema

```yaml
# orchestrator.yaml 新增
qa_gates:
  observability:
    health_checks:
      - url: "http://localhost:8000/health"
        expected_status: 200
        timeout: 10
    log_checks:
      - file: "logs/app.log"
        error_pattern: "ERROR|CRITICAL"
        max_errors: 0
    metric_checks: []
    optional: false  # true = warnings only, false = blocks task

tools:
  scaffold: true  # onboard 时自动生成工具
  custom:
    - name: "check-migrations"
      command: "python manage.py showmigrations --plan"
```

```python
# config.py 新增
class ObservabilityConfig(BaseModel):
    health_checks: list[HealthCheckConfig] = Field(default_factory=list)
    log_checks: list[LogCheckConfig] = Field(default_factory=list)
    metric_checks: list[MetricCheckConfig] = Field(default_factory=list)
    optional: bool = False
```

## 5. 远期: Per-Worktree 可观测性栈

如果项目有 `docker-compose.yml`，可以在任务 dispatch 前启动一个轻量监控栈:

```yaml
# .orchestrator/observability/docker-compose.yml
services:
  app:
    build: .
    ports: ["8000:8000"]
  prometheus:
    image: prom/prometheus:latest
    volumes: ["./prometheus.yml:/etc/prometheus/prometheus.yml"]
    ports: ["9090:9090"]
```

这是 P3 远期目标，需要:
- Docker Compose 检测 (`analyzer.py`)
- 启动/停止生命周期管理
- 端口分配（避免冲突）

## 6. 验证方式

- **Observability gate**: 用 mock HTTP server 测试 health check; 用临时文件测试 log check
- **Tool scaffolding**: 验证 onboard 后 `.orchestrator/tools/` 生成正确; 验证 dispatch 时 PATH 包含工具目录
- **端到端**: 在带 FastAPI 的项目上运行完整 goal，验证 observability gate 在部署任务后执行
