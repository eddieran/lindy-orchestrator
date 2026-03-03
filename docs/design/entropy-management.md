# 概念设计: 熵管理体系

> 从 "响应式清理" 升级到 "主动扫描 + 质量评级 + 自动修复"

## 1. 问题陈述

Agent 生成的代码会随时间积累熵: 文档与代码脱节、架构边界被侵蚀、未使用的代码堆积、quality 逐步退化。

当前 `gc.py` 处理时间维度的清理:
- 过期分支 (>14天)
- 旧会话归档 (>30天)
- 日志轮转 (>10MB)
- STATUS.md 修改时间漂移 (>7天)
- 孤儿 Plan 文件

但这些都是**时间检查**，不是**语义检查**。STATUS.md 可能昨天才改但内容完全过时；ARCHITECTURE.md 可能描述了不存在的目录；CONTRACTS.md 定义的接口可能已经变了。

OpenAI 的做法: **周期性 agent 扫描**项目，检测不一致，更新质量评级，开 refactoring PR。

**目标**: 构建 Entropy Scanner，从语义级检测架构漂移、合约合规、质量退化，并输出可操作的报告。

## 2. 现状 → 目标

```
                 gc.py (现有)              entropy/scanner.py (新增)
              ─────────────              ──────────────────────────
维度:          时间维度                    语义维度
模式:          响应式清理                  主动检测 + 评级
检查:          mtime, age, size           content, structure, compliance
输出:          cleanup actions            quality report + fix suggestions
触发:          CLI: lindy-orchestrate gc  CLI: lindy-orchestrate scan
```

两者互补: Scanner 检测问题，GC 清理过期产物。

## 3. Scanner 检查项设计

### 3.1 架构漂移检测

**什么**: ARCHITECTURE.md 中描述的结构与实际文件系统不匹配。

```python
class ArchitectureDrift:
    """检测 ARCHITECTURE.md 与实际目录结构的偏差。"""

    def check(self, project_root, architecture_md_content):
        findings = []

        # 1. 模块拓扑检查: ARCHITECTURE.md 中列的模块是否都存在
        declared_modules = parse_module_topology(architecture_md_content)
        actual_dirs = scan_directories(project_root)

        for mod in declared_modules:
            if mod.path not in actual_dirs:
                findings.append(Finding(
                    severity="error",
                    category="architecture_drift",
                    message=f"Module `{mod.name}` declared in ARCHITECTURE.md "
                            f"but directory `{mod.path}/` does not exist",
                    fix="Remove from ARCHITECTURE.md or create the directory"
                ))

        for dir in actual_dirs:
            if dir not in [m.path for m in declared_modules]:
                findings.append(Finding(
                    severity="warning",
                    category="architecture_drift",
                    message=f"Directory `{dir}/` exists but is not declared "
                            f"in ARCHITECTURE.md",
                    fix="Add to ARCHITECTURE.md or remove if unused"
                ))

        # 2. 层级结构检查: 声明的层级目录是否存在
        # 3. 负向边界检查: 声明 "does NOT import" 的是否真的没有 import

        return findings
```

### 3.2 合约合规检测

**什么**: CONTRACTS.md 定义的接口与实际实现不匹配。

```python
class ContractCompliance:
    """检测 CONTRACTS.md 接口定义与实际代码的偏差。"""

    def check(self, project_root, contracts_md_content):
        findings = []

        # 1. API 端点合规: CONTRACTS.md 声明 GET /api/users,
        #    检查路由文件中是否真的定义了
        api_contracts = parse_api_contracts(contracts_md_content)
        actual_routes = scan_route_files(project_root)

        for contract in api_contracts:
            if contract not in actual_routes:
                findings.append(Finding(
                    severity="warning",
                    category="contract_compliance",
                    message=f"API contract `{contract.method} {contract.path}` "
                            f"defined in CONTRACTS.md but not found in code",
                    fix="Implement the endpoint or update CONTRACTS.md"
                ))

        # 2. 环境变量合规: 声明的 env vars 是否在代码中使用
        # 3. 数据格式合规: 声明的 schema 与实际 model 是否匹配

        return findings
```

### 3.3 质量指标收集

**什么**: per-module 的代码质量综合评估。

```python
class QualityMetrics:
    """收集每个模块的质量指标。"""

    def collect(self, project_root, module_path):
        return ModuleMetrics(
            # 文件规模
            total_files=count_files(module_path),
            total_lines=count_lines(module_path),
            avg_file_size=avg_lines_per_file(module_path),
            oversized_files=count_files_over(module_path, limit=500),

            # Import 健康度
            cross_module_imports=count_cross_imports(project_root, module_path),
            circular_imports=detect_circular(module_path),

            # 文档新鲜度
            status_md_age_days=file_age(module_path / "STATUS.md"),
            claude_md_exists=file_exists(module_path / "CLAUDE.md"),

            # 测试信号 (如果可检测)
            has_test_dir=dir_exists(module_path, "tests"),
            test_file_count=count_test_files(module_path),
        )
```

### 3.4 STATUS.md 内容漂移

**什么**: STATUS.md 的内容与 git 状态不匹配（区别于 gc.py 只检查 mtime）。

```python
class StatusContentDrift:
    """检测 STATUS.md 内容与实际 git 状态的偏差。"""

    def check(self, project_root, module_name, status_content):
        findings = []

        # 1. "Active Work" 中标记 IN_PROGRESS 的任务，
        #    对应分支是否还存在
        active_tasks = parse_active_tasks(status_content)
        for task in active_tasks:
            if task.branch and not branch_exists(project_root, task.branch):
                findings.append(Finding(
                    severity="info",
                    category="status_drift",
                    message=f"Task '{task.description}' marked IN_PROGRESS "
                            f"but branch `{task.branch}` not found",
                    fix="Update STATUS.md: mark task as COMPLETED or remove"
                ))

        # 2. "Completed" 中的任务，分支是否已合并
        # 3. "Cross-Module Requests" 是否有过期未响应的

        return findings
```

## 4. 质量评级算法

### 评分维度

| 维度 | 权重 | A (优) | F (差) |
|------|------|--------|--------|
| 架构合规 | 30% | 无漂移, 层级正确 | 多处漂移, 边界被侵蚀 |
| 文件健康 | 20% | 均<500行, 无敏感文件 | 多个超大文件, 含 .env |
| Import 纪律 | 20% | 无跨模块 import, 无循环 | 多处跨模块/循环 import |
| 文档新鲜度 | 15% | STATUS.md <3天, 内容准确 | STATUS.md >14天, 内容过时 |
| 测试信号 | 15% | 有 tests/, 测试文件多 | 无 tests/ |

### 评级公式

```python
def grade_module(metrics: ModuleMetrics, findings: list[Finding]) -> str:
    """A-F 评级。"""
    score = 100

    # 架构合规 (-30 max)
    arch_errors = count_by(findings, "architecture_drift", "error")
    arch_warnings = count_by(findings, "architecture_drift", "warning")
    score -= min(30, arch_errors * 15 + arch_warnings * 5)

    # 文件健康 (-20 max)
    score -= min(20, metrics.oversized_files * 5)

    # Import 纪律 (-20 max)
    score -= min(20, metrics.cross_module_imports * 10 + metrics.circular_imports * 15)

    # 文档新鲜度 (-15 max)
    if metrics.status_md_age_days > 14:
        score -= 15
    elif metrics.status_md_age_days > 7:
        score -= 8

    # 测试信号 (-15 max)
    if not metrics.has_test_dir:
        score -= 15
    elif metrics.test_file_count == 0:
        score -= 10

    if score >= 90: return "A"
    if score >= 75: return "B"
    if score >= 60: return "C"
    if score >= 40: return "D"
    return "F"
```

## 5. 输出模式

### 5.1 Report-Only (默认)

```bash
lindy-orchestrate scan
```

输出:

```
Entropy Scan Report — my-project

## Module Grades
  backend:  B (score: 78)
  frontend: A (score: 92)
  shared:   D (score: 45)

## Findings (12 total)

### Architecture Drift (3)
  [error] Module `analytics` in ARCHITECTURE.md but dir missing
  [warn]  Directory `scripts/` exists but not in ARCHITECTURE.md
  [warn]  Layer `middleware/` declared for backend but dir not found

### Contract Compliance (2)
  [warn] API `POST /api/analytics/event` in CONTRACTS.md but not in routes
  [warn] Env var `REDIS_URL` in CONTRACTS.md but unused in code

### Status Drift (4)
  [info] backend: Task "Add auth" IN_PROGRESS but branch not found
  ...

### Quality (3)
  [warn] backend/routes.py: 680 lines (limit: 500)
  [warn] shared/__init__.py: imports from backend/
  ...
```

### 5.2 Auto-Fix

```bash
lindy-orchestrate scan --fix
```

对确定性问题自动修复:
- 从 ARCHITECTURE.md 移除不存在的模块
- 将超大文件标记为 TODO（不自动拆分）
- 更新 STATUS.md 中已完成任务的状态

### 5.3 PR Creation (远期)

```bash
lindy-orchestrate scan --pr
```

将 fix 结果提交到分支并创建 PR，由人类审查。

## 6. 与现有组件的关系

```
                    ┌──────────────┐
                    │  CLI: scan   │
                    └──────┬───────┘
                           │
              ┌────────────┼───────────┐
              ▼            ▼           ▼
     ┌────────────┐ ┌───────────┐ ┌──────────┐
     │ Scanner    │ │  Grader   │ │ Reporter │
     │ (检测问题) │ │ (评估质量) │ │ (输出报告)│
     └──────┬─────┘ └─────┬─────┘ └──────────┘
            │             │
            │  ┌──────────┘
            ▼  ▼
     ┌─────────────┐
     │ ARCHITECTURE │  读取
     │ CONTRACTS    │  作为
     │ STATUS.md    │  基准
     └─────────────┘

     已有组件复用:
     - _get_staged_files() from structural_check.py
     - _check_import_boundary() 逻辑 from structural_check.py
     - GCAction/GCReport 模式 from gc.py
     - format_gc_report() 输出风格 from gc.py
```

## 7. CLI 集成

```python
# cli.py 新增
@app.command()
def scan(
    fix: bool = typer.Option(False, help="Auto-fix deterministic issues"),
    module: str = typer.Option("", help="Scan specific module only"),
    grade_only: bool = typer.Option(False, help="Only show grades, skip findings"),
):
    """Scan project for entropy: architecture drift, contract violations, quality decay."""
    config = load_config()
    report = run_entropy_scan(config, auto_fix=fix, target_module=module)
    print(format_scan_report(report, grades_only=grade_only))
```

## 8. 调度策略

| 触发方式 | 场景 |
|---------|------|
| 手动 | `lindy-orchestrate scan` |
| Goal 完成后 | scheduler 在所有任务完成后自动运行 scan |
| CI 集成 | GitHub Action 在 PR 上运行 `scan --grade-only` |
| 定期 | cron 每周运行，输出到 `.orchestrator/entropy/` |

## 9. 验证方式

- 单元测试: 用 mock 项目结构测试各 checker
- 集成测试: 故意制造漂移（删目录、改 import）验证检测
- 评级测试: 用已知质量的模块验证评分符合预期
