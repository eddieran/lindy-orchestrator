 零侵入重构：所有脚手架文件迁入 .orchestrator/

 Context

 当前 onboard 会在项目根目录和模块目录散落 8-13 个文件（orchestrator.yaml, CLAUDE.md, ARCHITECTURE.md, STATUS.md, docs/agents/* 等），对宿主项目侵入性强。目标：所有脚手架文件统一收纳到 .orchestrator/ 下，通过
 .gitignore 忽略，彻底零侵入。同时新增 clear 命令一键清除所有脚手架。

 ---
 新目录结构

 .orchestrator/
 ├── config.yaml              # was: orchestrator.yaml
 ├── architecture.md           # was: ARCHITECTURE.md
 ├── contracts.md              # was: CONTRACTS.md (conditional)
 ├── claude/
 │   ├── root.md               # was: CLAUDE.md (root)
 │   └── {module-name}.md      # was: {module}/CLAUDE.md
 ├── status/
 │   └── {module-name}.md      # was: {module}/STATUS.md
 ├── docs/
 │   ├── protocol.md           # was: docs/agents/protocol.md
 │   ├── conventions.md        # was: docs/agents/conventions.md
 │   └── boundaries.md         # was: docs/agents/boundaries.md
 ├── plans/                    # 已有
 ├── logs/                     # 已有
 ├── sessions/                 # 已有
 └── mailbox/                  # 已有

 .gitignore 只需一行：.orchestrator/

 ---
 关键约束：CLAUDE.md 自动发现

 Claude Code CLI 从工作目录向上查找 CLAUDE.md。迁入 .orchestrator/claude/ 后不会被自动发现。

 解决方案：在 dispatch 时将 CLAUDE.md 内容注入到 task prompt 中。好处：
 - 适用所有 provider（Claude、Codex）
 - 比自动发现更可靠
 - 无需 symlink

 ---
 实施计划

 Phase 1：路径中心化 (config.py)

 所有路径定义集中在 OrchestratorConfig，其他模块通过方法访问，不硬编码路径。

 文件：src/lindy_orchestrator/config.py

 - CONFIG_FILENAME → ".orchestrator/config.yaml"
 - find_config() → 先查 .orchestrator/config.yaml，再 fallback orchestrator.yaml（向后兼容）
 - 新增路径方法：
   - architecture_path() → .orchestrator/architecture.md
   - contracts_path() → .orchestrator/contracts.md
   - claude_md_path(module=None) → .orchestrator/claude/root.md 或 .orchestrator/claude/{module}.md
   - status_path(module) → .orchestrator/status/{module}.md（更新已有方法）
   - docs_path(filename) → .orchestrator/docs/{filename}
 - root property → 返回 .orchestrator/ 的父目录（项目根）

 Phase 2：CLAUDE.md + STATUS.md 注入 (scheduler_helpers.py, scheduler.py)

 新增 inject_claude_md(task, config, progress)：
 - 读取 .orchestrator/claude/root.md + .orchestrator/claude/{module}.md
 - Prepend 到 task.prompt

 新增 inject_status_content(task, config, progress)：
 - 读取 .orchestrator/status/{module}.md
 - Prepend 到 task.prompt

 调用点：scheduler.py 的 _dispatch_loop() 中，在 inject_mailbox_messages() 之前（仅首次 dispatch）

 同步更新：
 - prompts.py："Read your STATUS.md first" → "Read the STATUS.md content provided above"
 - qa/agent_check.py：同上

 Phase 3：Generator 输出路径迁移 (generator.py)

 generate_artifacts() 所有输出路径改为 .orchestrator/ 下：

 ┌───────────────────┬────────────────────────────────────┐
 │      原路径       │               新路径               │
 ├───────────────────┼────────────────────────────────────┤
 │ orchestrator.yaml │ .orchestrator/config.yaml          │
 ├───────────────────┼────────────────────────────────────┤
 │ CLAUDE.md         │ .orchestrator/claude/root.md       │
 ├───────────────────┼────────────────────────────────────┤
 │ {mod}/CLAUDE.md   │ .orchestrator/claude/{mod.name}.md │
 ├───────────────────┼────────────────────────────────────┤
 │ ARCHITECTURE.md   │ .orchestrator/architecture.md      │
 ├───────────────────┼────────────────────────────────────┤
 │ CONTRACTS.md      │ .orchestrator/contracts.md         │
 ├───────────────────┼────────────────────────────────────┤
 │ docs/agents/*.md  │ .orchestrator/docs/*.md            │
 ├───────────────────┼────────────────────────────────────┤
 │ {mod}/STATUS.md   │ .orchestrator/status/{mod.name}.md │
 └───────────────────┴────────────────────────────────────┘

 _update_gitignore() → 只写 .orchestrator/

 Phase 4：CLI 显示更新

 文件：cli_onboard.py, cli_onboard_helpers.py, cli_init.py, cli_scaffold.py, cli_config.py

 - 所有 files_to_create 列表更新路径显示
 - _has_config() 兼容新旧路径
 - cli_config.py 读写 .orchestrator/config.yaml

 Phase 5：所有读取方路径更新

 ┌───────────────────────┬───────────────────────────────────────────┬────────────────────────────────────────────────────┐
 │         文件          │                当前硬编码                 │                        改为                        │
 ├───────────────────────┼───────────────────────────────────────────┼────────────────────────────────────────────────────┤
 │ planner.py            │ config.root / "ARCHITECTURE.md"           │ config.architecture_path()                         │
 ├───────────────────────┼───────────────────────────────────────────┼────────────────────────────────────────────────────┤
 │ scheduler_helpers.py  │ config.root / "ARCHITECTURE.md"           │ config.architecture_path()                         │
 ├───────────────────────┼───────────────────────────────────────────┼────────────────────────────────────────────────────┤
 │ qa/layer_check.py     │ project_root / "ARCHITECTURE.md"          │ project_root / ".orchestrator" / "architecture.md" │
 ├───────────────────────┼───────────────────────────────────────────┼────────────────────────────────────────────────────┤
 │ entropy/scanner.py    │ 硬编码 ARCHITECTURE/CONTRACTS/STATUS 路径 │ 使用 config 方法                                   │
 ├───────────────────────┼───────────────────────────────────────────┼────────────────────────────────────────────────────┤
 │ discovery/analyzer.py │ 读取 {mod}/CLAUDE.md                      │ 改查 .orchestrator/claude/{mod}.md                 │
 └───────────────────────┴───────────────────────────────────────────┴────────────────────────────────────────────────────┘

 Phase 6：模板内容路径引用更新

 discovery/templates/ 下所有模板生成的 markdown 内容中，引用路径需从根目录改为 .orchestrator/ 下：
 - root_claude_md.py：ARCHITECTURE.md、docs/agents/*、CONTRACTS.md 引用
 - module_claude_md.py：STATUS.md 引用
 - architecture_md.py：CONTRACTS.md 引用
 - agent_docs.py：所有交叉引用
 - contracts_md.py：STATUS.md 引用

 Phase 7：新增 clear 命令

 新文件：src/lindy_orchestrator/cli_clear.py

 @app.command()
 def clear(
     force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
 ):
     """Remove all orchestrator scaffold files from the project."""
     orch_dir = Path.cwd() / ".orchestrator"

     # 同时检查旧格式文件
     legacy_files = [
         "orchestrator.yaml", "CLAUDE.md", "ARCHITECTURE.md",
         "CONTRACTS.md", "docs/agents"
     ]

     if not orch_dir.exists() and not any(...legacy...):
         console.print("No orchestrator files found.")
         raise typer.Exit()

     if not force:
         # 显示将要删除的内容，确认
         confirm = typer.confirm("Remove all orchestrator files?")

     # 删除 .orchestrator/
     shutil.rmtree(orch_dir)

     # 清理旧格式残留（向后兼容）
     for f in legacy_files: ...

     # 清理 .gitignore 中的 orchestrator 条目
     _clean_gitignore()

     console.print("[green]All orchestrator files removed.[/]")

 注册：cli.py 中 register_clear_command(app, console)

 Phase 8：向后兼容 + .gitignore

 - find_config() 兼容新旧路径（Phase 1 已含）
 - clear 命令同时清理旧格式残留文件
 - .gitignore 简化为 .orchestrator/

 Phase 9：测试更新

 ~17 个测试文件需更新 fixture 路径：
 - conftest.py：orchestrator.yaml → .orchestrator/config.yaml，STATUS.md → .orchestrator/status/{mod}.md
 - 所有引用 scaffold 文件路径的 test 文件同步更新
 - 新增 test_cli_clear.py：测试 clear 命令
 - 新增 test_inject_claude_md.py：测试 CLAUDE.md 注入

 ---
 关键文件清单

 ┌───────────────────────────────────────────────┬──────────────────────────────────────────────┐
 │                     文件                      │                   变更类型                   │
 ├───────────────────────────────────────────────┼──────────────────────────────────────────────┤
 │ src/lindy_orchestrator/config.py              │ 路径中心化（基础）                           │
 ├───────────────────────────────────────────────┼──────────────────────────────────────────────┤
 │ src/lindy_orchestrator/scheduler_helpers.py   │ 新增 inject_claude_md, inject_status_content │
 ├───────────────────────────────────────────────┼──────────────────────────────────────────────┤
 │ src/lindy_orchestrator/scheduler.py           │ 调用注入函数                                 │
 ├───────────────────────────────────────────────┼──────────────────────────────────────────────┤
 │ src/lindy_orchestrator/discovery/generator.py │ 所有输出路径                                 │
 ├───────────────────────────────────────────────┼──────────────────────────────────────────────┤
 │ src/lindy_orchestrator/cli_clear.py           │ 新文件 - clear 命令                          │
 ├───────────────────────────────────────────────┼──────────────────────────────────────────────┤
 │ src/lindy_orchestrator/cli.py                 │ 注册 clear 命令                              │
 ├───────────────────────────────────────────────┼──────────────────────────────────────────────┤
 │ src/lindy_orchestrator/cli_onboard.py         │ 显示路径                                     │
 ├───────────────────────────────────────────────┼──────────────────────────────────────────────┤
 │ src/lindy_orchestrator/cli_onboard_helpers.py │ 配置检测                                     │
 ├───────────────────────────────────────────────┼──────────────────────────────────────────────┤
 │ src/lindy_orchestrator/cli_init.py            │ 创建路径                                     │
 ├───────────────────────────────────────────────┼──────────────────────────────────────────────┤
 │ src/lindy_orchestrator/cli_scaffold.py        │ 显示路径                                     │
 ├───────────────────────────────────────────────┼──────────────────────────────────────────────┤
 │ src/lindy_orchestrator/cli_config.py          │ 配置读写路径                                 │
 ├───────────────────────────────────────────────┼──────────────────────────────────────────────┤
 │ src/lindy_orchestrator/planner.py             │ 使用 config 方法                             │
 ├───────────────────────────────────────────────┼──────────────────────────────────────────────┤
 │ src/lindy_orchestrator/qa/layer_check.py      │ ARCHITECTURE 路径                            │
 ├───────────────────────────────────────────────┼──────────────────────────────────────────────┤
 │ src/lindy_orchestrator/entropy/scanner.py     │ 多个路径                                     │
 ├───────────────────────────────────────────────┼──────────────────────────────────────────────┤
 │ src/lindy_orchestrator/discovery/analyzer.py  │ CLAUDE.md 读取                               │
 ├───────────────────────────────────────────────┼──────────────────────────────────────────────┤
 │ src/lindy_orchestrator/prompts.py             │ STATUS.md 引用文案                           │
 ├───────────────────────────────────────────────┼──────────────────────────────────────────────┤
 │ src/lindy_orchestrator/qa/agent_check.py      │ STATUS.md 引用文案                           │
 ├───────────────────────────────────────────────┼──────────────────────────────────────────────┤
 │ discovery/templates/*.py (5 files)            │ 内容中路径引用                               │
 ├───────────────────────────────────────────────┼──────────────────────────────────────────────┤
 │ tests/conftest.py + ~16 test files            │ fixture 路径                                 │
 └───────────────────────────────────────────────┴──────────────────────────────────────────────┘

 验证

 1. pytest tests/ -x -q — 全量测试通过
 2. ruff check src/ tests/ — lint 通过
 3. 手动验证：在空目录运行 onboard，确认所有文件在 .orchestrator/ 下
 4. 手动验证：clear 命令删除所有脚手架文件
 5. 手动验证：旧格式项目运行 onboard --force 后迁移到新结构
 6. 手动验证：plan --dry-run 确认 CLAUDE.md 和 STATUS.md 内容正确注入到 prompt
