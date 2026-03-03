# 概念设计: 依赖层级机械化强制

> 从 "文档化层级" 升级到 "机械化强制层级"

## 1. 问题陈述

当前 `architecture_md.py:_build_layer_structure()` 为每个模块生成层级文档：

```
- **backend/**: models → schemas → services → routes → main
- **frontend/**: types → hooks → components → pages → app
```

但这只是文档。Agent 完全可以在 `routes/` 中 import `main` 的内容，或在 `models/` 中调用 `services/` 的函数。ARCHITECTURE.md 告诉 agent "应该"怎么做，但不阻止它"不这么做"。

OpenAI 的做法是：依赖方向由自定义 linter 在 CI 中强制。如果 Runtime 层 import 了 UI 层，构建失败。

**目标**: 新增 `layer_check` QA gate，将模块内依赖方向变为机械化检查。

## 2. 现状分析

### 已有基础

| 组件 | 位置 | 作用 |
|------|------|------|
| `_build_layer_structure()` | `architecture_md.py:143` | 推断每个模块的层级顺序 |
| `structural_check.py` | `qa/structural_check.py` | 跨模块 import 边界检查 (可复用模式) |
| `Violation` dataclass | `structural_check.py:22` | 统一的违规 + 修复建议格式 |
| `@register()` decorator | `qa/__init__.py` | QA gate 注册机制 |
| Auto-injection | `scheduler.py:122-148` | 自动注入 structural_check |

### 当前检测范围

```
structural_check.py 检测:
  ✓ 跨模块 import (backend/ imports frontend/)
  ✗ 模块内层级违反 (routes/ imports main)
  ✓ 文件大小
  ✓ 敏感文件
```

## 3. 设计方案

### 3.1 层级定义格式

从 ARCHITECTURE.md 的 "Layer Structure" 段解析：

```markdown
## Layer Structure

- **backend/**: models → schemas → services → routes → main
```

解析为有序层级图：

```python
@dataclass
class LayerDef:
    module: str
    layers: list[str]  # ordered: lower index = lower layer

# Example: backend/
# layers = ["models", "schemas", "services", "routes", "main"]
# models(0) → schemas(1) → services(2) → routes(3) → main(4)
# 规则: layer[i] 可以 import layer[j] 仅当 j <= i (同层或更低层)
```

### 3.2 文件到层级映射

通过目录名和文件名模式匹配：

```python
def _resolve_layer(filepath: str, layers: list[str]) -> int | None:
    """将文件路径解析为层级索引。"""
    parts = Path(filepath).parts
    for part in parts:
        # 精确匹配目录名
        for i, layer in enumerate(layers):
            if part == layer or part.rstrip("s") == layer.rstrip("s"):
                return i

    # 文件名匹配 (e.g., user_model.py → models)
    stem = Path(filepath).stem.lower()
    for i, layer in enumerate(layers):
        singular = layer.rstrip("s")
        if stem.endswith(f"_{singular}") or stem.startswith(f"{singular}_"):
            return i

    return None  # 无法确定层级，跳过检查
```

### 3.3 Import 分析

```python
def _check_layer_violations(
    project_root: Path,
    module_name: str,
    layer_def: LayerDef,
    staged_files: list[str],
) -> list[Violation]:
    """检测违反层级方向的 import。"""
    violations = []

    for filepath in staged_files:
        source_layer = _resolve_layer(filepath, layer_def.layers)
        if source_layer is None:
            continue

        imports = _extract_imports(project_root / filepath)

        for imp in imports:
            target_layer = _resolve_import_layer(imp, layer_def.layers)
            if target_layer is None:
                continue

            if target_layer > source_layer:
                violations.append(Violation(
                    rule="layer_violation",
                    file=filepath,
                    message=(
                        f"`{filepath}` (layer: {layer_def.layers[source_layer]}) "
                        f"imports from `{imp}` (layer: {layer_def.layers[target_layer]}). "
                        f"Higher layers must not be imported by lower layers."
                    ),
                    remediation=(
                        f"Move the shared logic to `{layer_def.layers[source_layer]}/` or lower, "
                        f"or create an interface in `{layer_def.layers[min(source_layer, target_layer)]}/`. "
                        f"Dependency direction: {' → '.join(layer_def.layers)}"
                    ),
                ))

    return violations
```

### 3.4 配置 Schema

```yaml
# orchestrator.yaml
qa_gates:
  layer_check:
    enabled: true
    # 可覆盖自动推断的层级
    overrides:
      backend:
        layers: ["models", "schemas", "services", "routes", "main"]
        exceptions:
          - "main can import routes"  # 启动入口允许
    # 无法确定层级的文件: warn | skip | error
    unknown_file_policy: skip
```

```python
# config.py 新增
class LayerCheckConfig(BaseModel):
    enabled: bool = True
    overrides: dict[str, dict] = Field(default_factory=dict)
    unknown_file_policy: str = "skip"  # warn | skip | error
```

### 3.5 Gate 注册

```python
# qa/layer_check.py

@register("layer_check")
class LayerCheckGate:
    """QA gate for intra-module dependency layer enforcement."""

    def check(self, params, project_root, module_name, task_output, **kwargs):
        # 1. 解析 ARCHITECTURE.md 获取层级定义
        layer_def = _parse_architecture_layers(project_root, module_name)

        if layer_def is None:
            return QAResult(gate="layer_check", passed=True,
                          output="No layer structure defined for this module.")

        # 2. 获取 staged 文件
        staged_files = _get_staged_files(project_root, module_name)

        # 3. 检查违规
        violations = _check_layer_violations(
            project_root, module_name, layer_def, staged_files
        )

        # 4. 格式化结果
        return QAResult(
            gate="layer_check",
            passed=len(violations) == 0,
            output=_format_violations(violations),
            details={"violation_count": len(violations)},
        )
```

## 4. 集成点

### 自动注入

在 `scheduler.py` 的 gate 自动注入逻辑中，与 `structural_check` 并列：

```python
# scheduler.py (概念)
# 现有: 自动注入 structural_check
# 新增: 如果 ARCHITECTURE.md 存在层级定义，自动注入 layer_check
if not has_layer_check:
    task.qa_checks.append(QACheck(gate="layer_check", params=layer_config))
```

### 与 structural_check 的关系

```
structural_check:  跨模块边界   (backend/ ↛ frontend/)
layer_check:       模块内层级   (routes/ ↛ main)
```

两者互补，不重叠。structural_check 保证模块间隔离，layer_check 保证模块内分层纪律。

## 5. 支持的框架层级

基于 `_build_layer_structure()` 已有推断:

| 框架 | 层级 (低 → 高) |
|------|----------------|
| FastAPI/Flask | models → schemas → services → routes → main |
| Django | models → serializers → views → urls → wsgi |
| Express | models → middleware → routes → controllers → app |
| React/Next.js | types → hooks → components → pages → app |
| Vue | types → composables → components → views → router |
| Spring | entities → repositories → services → controllers → application |

## 6. 边界情况

- **文件不在任何层级目录**: 由 `unknown_file_policy` 控制（默认 skip）
- **utils/ 或 shared/ 目录**: 视为最低层（任何层都可以 import）
- **__init__.py re-exports**: 允许 re-export，不视为违规
- **测试文件**: tests/ 目录默认排除，测试可以 import 任何层
- **main/app 入口**: 最高层允许 import 所有层（这是其定义）

## 7. 验证方式

- 单元测试: 参照 `tests/test_structural_check.py` 模式
- 集成测试: 用 mock 项目结构验证层级检测
- 端到端: 在实际项目上运行 `lindy-orchestrate goal` 观察 layer_check 输出
