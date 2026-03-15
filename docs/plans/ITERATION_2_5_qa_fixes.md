# Iteration 2.5: QA Gate 修复 — 来自 3 个项目失败的自我迭代

## 背景
3 个真实项目通过 lindy-orchestrator 编排全部失败。根因分析发现 8 个问题，按优先级修复。

## P0 — 阻塞性 bug

### #8 修复 max_workers=0 bug
- 现象：skip_qa=true 的任务，空 gate 列表传给 ThreadPoolExecutor(max_workers=0) 报错
- 修复：scheduler.py `_run_qa_gates()` 开头检查 gate_count==0 直接返回 True

### #3 skip_qa 跳过 delivery_check
- 现象：skip_qa=true 的非代码任务仍执行 delivery_check，报 "no new commits"
- 修复：scheduler.py `_dispatch_loop()` 中 delivery_check 和 QA gates 都受 skip_qa 控制

## P1 — 高影响

### #1 structural_check diff-awareness
- 现象：agent 修改前文件就超行数限制，structural_check 误报
- 修复：只检查 git diff 中变更的文件，跳过未修改文件

### #4a 区分 retryable/non-retryable
- 现象：pre-existing violation 重试 3 次都失败，浪费 token
- 修复：QAResult 新增 retryable 字段；全部 non-retryable 时不重试

### #5 onboard 生成的 max_parallel 默认值
- 现象：onboard 生成 max_parallel=1，串行执行
- 修复：onboard 模板默认 max_parallel=3

## P2 — 改进

### #2 command_check diff_only 模式
- 新增 diff_only 配置项和 {changed_files} 模板变量

### #7 QA gate required 字段
- required=false 的 gate 失败只警告，不触发重试

### #6 task 级别 gate 排除
- skip_gates 字段排除特定内置 gate
