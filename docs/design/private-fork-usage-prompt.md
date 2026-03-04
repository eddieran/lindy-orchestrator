# Prompt 模板: 使用 lindy-orchestrator 管理新项目

> 将此 prompt 发给 Claude Code，让它为你的目标项目接入 lindy-orchestrator 编排框架。

---

## 使用方法

1. 在**目标项目**的根目录打开 Claude Code
2. 将下面的 prompt 复制粘贴，替换 `{{占位符}}`
3. Claude 会自动完成 onboard + 配置调优

---

## Prompt 模板

```
你是一个项目编排工程师。我需要你使用 lindy-orchestrator 框架来管理当前项目。

## 背景

lindy-orchestrator 是一个轻量级 git-native 多 agent 编排框架，它通过以下机制管理多模块项目：
- 自动分析项目结构（模块、技术栈、依赖关系）
- 生成 CLAUDE.md（索引式 agent 指令）、ARCHITECTURE.md（结构地图 + 负向边界）、STATUS.md（模块状态消息总线）
- 将高层目标分解为 DAG 任务图，dispatch 给各模块 agent 并行执行
- 通过 QA gates（structural_check, layer_check, ci_check, command_check）机械化强制架构约束
- QA 失败时带结构化反馈自动重试

## 前置条件

确保以下工具已安装：
- Python 3.11+
- Claude Code CLI（`claude` 命令在 PATH 中）
- Git
- GitHub CLI（`gh`，可选，CI check 需要）

## 第一步：安装

```bash
pip install lindy-orchestrator
```

如果需要 Anthropic API 直接调用（非 Claude CLI）：
```bash
pip install lindy-orchestrator[api]
```

## 第二步：Onboard 项目

在项目根目录执行：

```bash
lindy-orchestrate onboard
```

这会启动三阶段流程：
1. **静态分析** — 扫描目录结构、marker 文件、依赖、CI 配置
2. **交互问答** — 确认模块角色、跨模块依赖、QA 需求、敏感路径
3. **生成产物** — orchestrator.yaml、CLAUDE.md、ARCHITECTURE.md、STATUS.md、docs/agents/

如果想跳过交互直接使用默认值：
```bash
lindy-orchestrate onboard --non-interactive
```

## 第三步：调优配置

Onboard 完成后，检查并调整 `orchestrator.yaml`：

```yaml
project:
  name: "{{项目名}}"
  branch_prefix: "af"

modules:
  - name: {{模块1名称}}
    path: {{模块1路径}}/
    repo: {{GitHub org/repo}}        # CI check 需要
    ci_workflow: ci.yml

dispatcher:
  timeout_seconds: 1800              # 按需调整
  stall_timeout_seconds: 600
  permission_mode: bypassPermissions

qa_gates:
  custom:
    - name: {{模块1名称}}-test
      command: "{{测试命令，如 pytest --tb=short -q}}"
      cwd: "{module_path}"
    - name: {{模块1名称}}-lint
      command: "{{lint命令，如 ruff check}}"
      cwd: "{module_path}"

safety:
  max_retries_per_task: 2
  max_parallel: 3
```

重点调优项：
- `qa_gates.custom` — 添加项目实际使用的测试和 lint 命令
- `dispatcher.timeout_seconds` — 大型项目可能需要更长超时
- `safety.max_parallel` — 根据机器性能调整并发度

## 第四步：填充 STATUS.md

为每个模块的 `STATUS.md` 填入当前状态：
- `overall_health`: GREEN / YELLOW / RED
- `Active Work`: 当前进行中的工作
- `Blockers`: 已知阻塞项

## 第五步：验证配置

```bash
lindy-orchestrate validate
```

确保配置语法正确、模块路径存在、Claude CLI 可用。

## 第六步：试运行

先用 plan 命令预览任务分解：

```bash
lindy-orchestrate plan "{{你的目标描述}}"
```

确认任务分解合理后执行：

```bash
lindy-orchestrate run "{{你的目标描述}}"
```

## 关键文件说明

| 文件 | 作用 | 谁读 |
|------|------|------|
| `orchestrator.yaml` | 运行时配置 | CLI |
| `CLAUDE.md` (root) | 索引式 orchestrator 指令 (~40行) | Orchestrator agent |
| `ARCHITECTURE.md` | 模块拓扑 + 负向边界 + 层级结构 | Planner + Agent |
| `CONTRACTS.md` | 跨模块接口合约 | 需要跨模块通信时 |
| `docs/agents/protocol.md` | 完整协调协议 | 需要时引用 |
| `docs/agents/conventions.md` | 编码约定 | 需要时引用 |
| `docs/agents/boundaries.md` | 边界规则 + 例外 | 需要时引用 |
| `{module}/CLAUDE.md` | 模块 agent 指令 (8段, ~80行) | Module agent |
| `{module}/STATUS.md` | 模块状态消息总线 | 所有 agent |

## 目标描述最佳实践

好的目标：
- "在 backend 模块添加 /api/users CRUD 接口，使用 SQLAlchemy ORM，包含 pytest 测试"
- "前端 Dashboard 页面添加日线 PnL 图表，使用 Recharts，从 /api/portfolio 获取数据"
- "将 data 模块重构为 async/await，保持 API 兼容性"

不好的目标：
- "改进系统" — 太模糊
- "构建整个交易系统" — 太大
- "修 bug" — 无上下文

经验法则：如果你不能用 3 句话描述验收标准，目标需要拆分。

## 常用命令速查

```bash
# Onboard 和配置
lindy-orchestrate onboard              # 完整 onboard（推荐）
lindy-orchestrate init --modules "a,b" # 快速初始化
lindy-orchestrate validate             # 检查配置

# 执行目标
lindy-orchestrate plan "目标"           # 预览任务分解
lindy-orchestrate run "目标"            # 执行
lindy-orchestrate run "目标" --dry-run  # 模拟执行
lindy-orchestrate resume               # 恢复中断的 session

# 监控和维护
lindy-orchestrate status               # 模块健康状态
lindy-orchestrate logs                  # 操作日志
lindy-orchestrate scan                  # 熵扫描（架构漂移、质量评级）
lindy-orchestrate gc --apply            # 清理过期分支和 session
```
```

---

## 快速启动版（最小 Prompt）

如果你只需要最简洁的指令：

```
在当前项目安装并配置 lindy-orchestrator 编排框架：

1. pip install lindy-orchestrator
2. lindy-orchestrate onboard --non-interactive
3. 检查 orchestrator.yaml，为每个模块添加实际的测试命令到 qa_gates.custom
4. 为每个模块的 STATUS.md 填入当前健康状态
5. lindy-orchestrate validate
6. lindy-orchestrate plan "{{目标}}"
7. 确认后 lindy-orchestrate run "{{目标}}"
```

---

## 高级：源码级定制

如果需要从源码安装（开发/定制场景）：

```bash
# Clone lindy-orchestrator 源码
git clone https://github.com/eddieran/lindy-orchestrator.git
cd lindy-orchestrator
pip install -e ".[dev]"

# 回到目标项目
cd /path/to/your/project
lindy-orchestrate onboard
```

关键定制点：
- `src/lindy_orchestrator/providers/` — 添加新的 dispatch provider（实现 `DispatchProvider` Protocol）
- `src/lindy_orchestrator/qa/` — 添加自定义 QA gate（使用 `@register("gate_name")` 装饰器）
- `src/lindy_orchestrator/discovery/templates/` — 定制生成的 CLAUDE.md / ARCHITECTURE.md 模板
- `src/lindy_orchestrator/entropy/scanner.py` — 扩展熵扫描检查项
