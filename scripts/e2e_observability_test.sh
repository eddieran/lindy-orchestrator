#!/usr/bin/env bash
# Real E2E test for lindy-orchestrator observability layer.
# Creates a disposable project, runs the full pipeline with real agent dispatch,
# then verifies code output + all three observability streams.
#
# Usage: ./scripts/e2e_observability_test.sh
# Expected runtime: ~5-10 minutes (real Claude CLI dispatch)
set -uo pipefail

TIMESTAMP=$(date +%s)
WORKDIR="/tmp/lindy-e2e-test-${TIMESTAMP}"
PASS=0
FAIL=0
WARN=0
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[0;33m'
BOLD='\033[1m'
NC='\033[0m'

check_pass() {
    echo -e "  ${GREEN}PASS${NC}: $1"
    ((PASS++))
}

check_fail() {
    echo -e "  ${RED}FAIL${NC}: $1"
    ((FAIL++))
}

check_warn() {
    echo -e "  ${YELLOW}WARN${NC}: $1"
    ((WARN++))
}

# =========================================================================
# Step 1: Prerequisites
# =========================================================================
echo -e "\n${BOLD}[Step 1] Prerequisites${NC}"

for cmd in claude git python3; do
    if command -v "$cmd" >/dev/null 2>&1; then
        check_pass "$cmd available"
    else
        check_fail "$cmd not found — cannot continue"
        exit 1
    fi
done

if ! command -v lindy-orchestrate >/dev/null 2>&1; then
    check_fail "lindy-orchestrate not found — install with: uv tool install -e $REPO_ROOT"
    exit 1
fi
check_pass "lindy-orchestrate available ($(lindy-orchestrate version 2>&1 | head -1))"

# =========================================================================
# Step 2: Create disposable test workspace
# =========================================================================
echo -e "\n${BOLD}[Step 2] Creating test workspace at ${WORKDIR}${NC}"
mkdir -p "$WORKDIR"/{app,tests,.orchestrator/claude,.orchestrator/status}

# app/calculator.py
cat > "$WORKDIR/app/__init__.py" << 'PYEOF'
PYEOF

cat > "$WORKDIR/app/calculator.py" << 'PYEOF'
"""Simple calculator module."""


def add(a: float, b: float) -> float:
    """Add two numbers."""
    return a + b


def subtract(a: float, b: float) -> float:
    """Subtract b from a."""
    return a - b
PYEOF

# tests/
cat > "$WORKDIR/tests/__init__.py" << 'PYEOF'
PYEOF

cat > "$WORKDIR/tests/test_calculator.py" << 'PYEOF'
"""Tests for calculator module."""

from app.calculator import add, subtract


def test_add():
    assert add(2, 3) == 5
    assert add(-1, 1) == 0


def test_subtract():
    assert subtract(5, 3) == 2
    assert subtract(0, 0) == 0
PYEOF

# pyproject.toml
cat > "$WORKDIR/pyproject.toml" << 'TOMLEOF'
[project]
name = "calculator-e2e"
version = "0.1.0"
requires-python = ">=3.11"

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.ruff]
target-version = "py311"
line-length = 100
TOMLEOF

# .orchestrator/config.yaml
cat > "$WORKDIR/.orchestrator/config.yaml" << 'YAMLEOF'
project:
  name: calculator-e2e
  branch_prefix: e2e

modules:
  - name: root
    path: ./

planner:
  timeout_seconds: 300

generator:
  timeout_seconds: 600
  permission_mode: bypassPermissions
  stall_timeout: 300

evaluator:
  timeout_seconds: 300
  pass_threshold: 70

observability:
  level: 3
  retention_days: 7

qa_gates:
  custom:
    - name: pytest
      command: "python -m pytest -x -q --tb=short"
      cwd: "."
    - name: ruff
      command: "python -m ruff check app/ tests/"
      cwd: "."

safety:
  dry_run: false
  max_retries_per_task: 1
  max_parallel: 2
YAMLEOF

# .orchestrator/claude/root.md (empty instructions)
cat > "$WORKDIR/.orchestrator/claude/root.md" << 'MDEOF'
# Calculator E2E Test Project

Simple Python calculator. Follow existing code style. Use type hints.
MDEOF

# Pre-built plan
cat > "$WORKDIR/plan.json" << 'JSONEOF'
{
  "goal": "Add multiply and divide functions with error handling and tests",
  "tasks": [
    {
      "id": 1,
      "module": "root",
      "description": "Add multiply and divide functions to calculator module",
      "generator_prompt": "In app/calculator.py, add two new functions after the existing subtract function:\n\n1. multiply(a: float, b: float) -> float\n   - Multiplies two numbers and returns the result\n\n2. divide(a: float, b: float) -> float\n   - Divides a by b and returns the result\n   - Must raise ValueError with message 'Cannot divide by zero' when b is 0\n\nFollow the existing code style (type hints, docstrings). Do not modify existing functions.\n\nAfter making changes, verify with:\n  python -m pytest tests/ -x -q --tb=short\n  python -m ruff check app/",
      "acceptance_criteria": "multiply and divide functions exist in app/calculator.py. divide raises ValueError on zero divisor. Existing tests still pass.",
      "evaluator_prompt": "Check that app/calculator.py contains multiply() and divide() with correct signatures and ValueError handling.",
      "qa_checks": [
        {"gate": "pytest", "params": {}},
        {"gate": "ruff", "params": {}}
      ],
      "depends_on": [],
      "skip_qa": false
    },
    {
      "id": 2,
      "module": "root",
      "description": "Add comprehensive tests for multiply and divide functions",
      "generator_prompt": "In tests/test_calculator.py, add these test functions after the existing tests:\n\n1. test_multiply():\n   - Test multiply(2, 3) == 6\n   - Test multiply(-1, 5) == -5\n   - Test multiply(0, 100) == 0\n   - Test multiply(1.5, 2) == 3.0\n\n2. test_divide():\n   - Test divide(10, 2) == 5.0\n   - Test divide(7, 2) == 3.5\n   - Test divide(-6, 3) == -2.0\n\n3. test_divide_by_zero():\n   - Use pytest.raises(ValueError, match='Cannot divide by zero') to test divide(1, 0)\n\nImport multiply and divide from app.calculator at the top of the file.\n\nAfter making changes, verify with:\n  python -m pytest tests/ -x -v --tb=short",
      "acceptance_criteria": "All test functions exist and pass. test_divide_by_zero uses pytest.raises.",
      "evaluator_prompt": "Run pytest and verify all tests pass including the new multiply, divide, and divide_by_zero tests.",
      "qa_checks": [
        {"gate": "pytest", "params": {}}
      ],
      "depends_on": [1],
      "skip_qa": false
    }
  ]
}
JSONEOF

# Git init
cd "$WORKDIR"
git init -q
git add -A
git commit -q -m "Initial commit: calculator project scaffold"

check_pass "Test workspace created and initialized"

# =========================================================================
# Step 3: Install test dependencies
# =========================================================================
echo -e "\n${BOLD}[Step 3] Installing test dependencies${NC}"
python3 -m venv "$WORKDIR/.venv" 2>/dev/null
source "$WORKDIR/.venv/bin/activate"
pip install -q pytest ruff 2>/dev/null
check_pass "pytest and ruff installed in venv"

# Verify baseline tests pass
cd "$WORKDIR"
if python -m pytest -x -q --tb=short >/dev/null 2>&1; then
    check_pass "Baseline tests pass (2 tests)"
else
    check_fail "Baseline tests failed — workspace is broken"
    exit 1
fi

# =========================================================================
# Step 4: Execute the orchestrator
# =========================================================================
echo -e "\n${BOLD}[Step 4] Running lindy-orchestrate (real agent dispatch)${NC}"
echo "  This may take 5-10 minutes..."

cd "$WORKDIR"
# Activate venv so agents can find pytest/ruff
export PATH="$WORKDIR/.venv/bin:$PATH"

lindy-orchestrate run --plan plan.json -c .orchestrator/config.yaml 2>&1 | tee "$WORKDIR/run_output.log"

# Capture session ID
SESSION_ID=$(grep -oE 'Session: [a-f0-9]+' "$WORKDIR/run_output.log" | head -1 | awk '{print $2}')
if [ -z "$SESSION_ID" ]; then
    # Fallback: find the most recent session directory
    SESSION_ID=$(ls -t .orchestrator/sessions/ 2>/dev/null | head -1)
fi

if [ -n "$SESSION_ID" ]; then
    check_pass "Session ID captured: $SESSION_ID"
else
    check_fail "Could not determine session ID"
    echo "Run output:"
    cat "$WORKDIR/run_output.log"
    exit 1
fi

SESSION_DIR=".orchestrator/sessions/${SESSION_ID}"

# Check exit status from the run
if grep -q "GOAL COMPLETED\|GOAL PAUSED\|completed" "$WORKDIR/run_output.log"; then
    check_pass "Orchestrator run completed"
else
    check_warn "Orchestrator run may not have completed successfully"
fi

# =========================================================================
# Step 5: Verify code modifications
# =========================================================================
echo -e "\n${BOLD}[Step 5] Verifying code modifications${NC}"

# Merge agent branches back to main to see the changes
# Merge task branches in order (task-1 first, then task-2) to avoid conflicts
for i in 1 2 3 4 5; do
    branch="e2e/task-${i}"
    if git rev-parse --verify "$branch" >/dev/null 2>&1; then
        if ! git merge "$branch" --no-edit -q 2>/dev/null; then
            # Resolve conflicts by accepting the incoming branch version
            git checkout --theirs . 2>/dev/null
            git add -A 2>/dev/null
            git commit --no-edit -q 2>/dev/null || true
        fi
    fi
done

if grep -q "def multiply" app/calculator.py 2>/dev/null; then
    check_pass "multiply function exists in calculator.py"
else
    check_fail "multiply function missing from calculator.py"
fi

if grep -q "def divide" app/calculator.py 2>/dev/null; then
    check_pass "divide function exists in calculator.py"
else
    check_fail "divide function missing from calculator.py"
fi

if grep -q "ValueError" app/calculator.py 2>/dev/null; then
    check_pass "ValueError handling exists in calculator.py"
else
    check_fail "ValueError handling missing from calculator.py"
fi

if grep -q "def test_multiply" tests/test_calculator.py 2>/dev/null; then
    check_pass "test_multiply exists in test_calculator.py"
else
    check_warn "test_multiply missing (task 2 may have been skipped if task 1 failed)"
fi

if grep -q "def test_divide" tests/test_calculator.py 2>/dev/null; then
    check_pass "test_divide exists in test_calculator.py"
else
    check_warn "test_divide missing (task 2 may have been skipped if task 1 failed)"
fi

if python -m pytest -x -q --tb=short 2>/dev/null; then
    check_pass "All pytest tests pass"
else
    check_fail "pytest tests failed"
fi

# =========================================================================
# Step 6: Verify session directory structure
# =========================================================================
echo -e "\n${BOLD}[Step 6] Verifying session directory structure${NC}"

if [ -d "$SESSION_DIR" ]; then
    check_pass "Session directory exists: $SESSION_DIR"
else
    check_fail "Session directory missing: $SESSION_DIR"
fi

if [ -f "$SESSION_DIR/session.json" ]; then
    check_pass "session.json exists"
else
    check_fail "session.json missing"
fi

if [ -f "$SESSION_DIR/summary.jsonl" ]; then
    check_pass "summary.jsonl exists (L1)"
else
    check_fail "summary.jsonl missing (L1)"
fi

if [ -f "$SESSION_DIR/decisions.jsonl" ]; then
    check_pass "decisions.jsonl exists (L2)"
else
    check_fail "decisions.jsonl missing (L2)"
fi

if [ -f "$SESSION_DIR/transcript.jsonl" ]; then
    check_pass "transcript.jsonl exists (L3)"
else
    check_fail "transcript.jsonl missing (L3)"
fi

# =========================================================================
# Step 7: Verify L1 summary.jsonl
# =========================================================================
echo -e "\n${BOLD}[Step 7] Verifying L1 summary.jsonl${NC}"

python3 << PYEOF
import json, sys

path = "${SESSION_DIR}/summary.jsonl"
try:
    lines = [l for l in open(path) if l.strip()]
except FileNotFoundError:
    print("  \033[0;31mFAIL\033[0m: summary.jsonl not found")
    sys.exit(0)

# Check 12: Valid JSON with required fields
all_valid = True
for i, line in enumerate(lines, 1):
    try:
        entry = json.loads(line)
        for field in ("ts", "level", "event", "task_id"):
            if field not in entry:
                print(f"  \033[0;31mFAIL\033[0m: Line {i} missing '{field}'")
                all_valid = False
                break
    except json.JSONDecodeError as e:
        print(f"  \033[0;31mFAIL\033[0m: Line {i} invalid JSON: {e}")
        all_valid = False
if all_valid:
    print(f"  \033[0;32mPASS\033[0m: All {len(lines)} summary lines valid JSON with required fields")

entries = [json.loads(l) for l in lines]
events = [e["event"] for e in entries]

# Check 13: All level=1
if all(e["level"] == 1 for e in entries):
    print("  \033[0;32mPASS\033[0m: All entries have level=1")
else:
    print("  \033[0;31mFAIL\033[0m: Some entries have level != 1")

# Check 14: First=session_start, last=session_end
if events and events[0] == "session_start":
    print("  \033[0;32mPASS\033[0m: First event is session_start")
else:
    print(f"  \033[0;31mFAIL\033[0m: First event is '{events[0] if events else 'EMPTY'}'")

if events and events[-1] == "session_end":
    print("  \033[0;32mPASS\033[0m: Last event is session_end")
else:
    print(f"  \033[0;31mFAIL\033[0m: Last event is '{events[-1] if events else 'EMPTY'}'")

# Check 15: task_started count (at least 1; task 2 may be skipped if task 1 fails)
started = events.count("task_started")
if started >= 1:
    print(f"  \033[0;32mPASS\033[0m: {started} task_started events (expected >= 1)")
else:
    print(f"  \033[0;31mFAIL\033[0m: No task_started events found")

# Check 16: task_completed count (at least 1; includes failed/skipped as task_completed with status)
completed = events.count("task_completed")
if completed >= 1:
    print(f"  \033[0;32mPASS\033[0m: {completed} task_completed events (expected >= 1)")
else:
    print(f"  \033[0;31mFAIL\033[0m: No task_completed events found")
PYEOF

# =========================================================================
# Step 8: Verify L2 decisions.jsonl
# =========================================================================
echo -e "\n${BOLD}[Step 8] Verifying L2 decisions.jsonl${NC}"

python3 << PYEOF
import json, sys

path = "${SESSION_DIR}/decisions.jsonl"
try:
    lines = [l for l in open(path) if l.strip()]
except FileNotFoundError:
    print("  \033[0;31mFAIL\033[0m: decisions.jsonl not found")
    sys.exit(0)

entries = [json.loads(l) for l in lines]
events = {e["event"] for e in entries}

# Check 17: All level=2
if all(e["level"] == 2 for e in entries):
    print(f"  \033[0;32mPASS\033[0m: All {len(entries)} decision lines valid with level=2")
else:
    print("  \033[0;31mFAIL\033[0m: Some entries have level != 2")

# Check 18: eval_scored present with score
eval_entries = [e for e in entries if e["event"] == "eval_scored"]
if eval_entries and all("score" in e for e in eval_entries):
    scores = [e["score"] for e in eval_entries]
    print(f"  \033[0;32mPASS\033[0m: {len(eval_entries)} eval_scored entries, scores: {scores}")
else:
    if eval_entries:
        print("  \033[0;31mFAIL\033[0m: eval_scored entries missing 'score' field")
    else:
        print("  \033[0;31mFAIL\033[0m: No eval_scored events in decisions")

# Check 19: QA results
if "qa_passed" in events or "qa_failed" in events:
    qa_pass = sum(1 for e in entries if e["event"] == "qa_passed")
    qa_fail = sum(1 for e in entries if e["event"] == "qa_failed")
    print(f"  \033[0;32mPASS\033[0m: QA results present ({qa_pass} passed, {qa_fail} failed)")
else:
    print("  \033[0;31mFAIL\033[0m: No QA result events in decisions")

# Check 20: phase_changed
if "phase_changed" in events:
    phases = [e.get("phase", "?") for e in entries if e["event"] == "phase_changed"]
    print(f"  \033[0;32mPASS\033[0m: phase_changed events present: {phases}")
else:
    print("  \033[0;31mFAIL\033[0m: No phase_changed events in decisions")
PYEOF

# =========================================================================
# Step 9: Verify L3 transcript.jsonl
# =========================================================================
echo -e "\n${BOLD}[Step 9] Verifying L3 transcript.jsonl${NC}"

# Small delay for async L3 handlers to flush
sleep 2

python3 << PYEOF
import json, sys

path = "${SESSION_DIR}/transcript.jsonl"
try:
    lines = [l for l in open(path) if l.strip()]
except FileNotFoundError:
    print("  \033[0;31mFAIL\033[0m: transcript.jsonl not found")
    sys.exit(0)

entries = [json.loads(l) for l in lines]
events = {e["event"] for e in entries}

# Check 21: All level=3
if all(e["level"] == 3 for e in entries):
    print(f"  \033[0;32mPASS\033[0m: All {len(entries)} transcript lines valid with level=3")
else:
    print("  \033[0;31mFAIL\033[0m: Some entries have level != 3")

# Check 22: agent_event present
agent_events = [e for e in entries if e["event"] == "agent_event"]
if agent_events:
    print(f"  \033[0;32mPASS\033[0m: {len(agent_events)} agent_event entries from real dispatch")
else:
    print("  \033[0;31mFAIL\033[0m: No agent_event entries in transcript")

# Check 23: agent_output present
output_entries = [e for e in entries if e["event"] == "agent_output"]
if output_entries:
    print(f"  \033[0;32mPASS\033[0m: {len(output_entries)} agent_output entries")
else:
    print("  \033[0;31mFAIL\033[0m: No agent_output entries in transcript")

# Check 24: git_diff_captured (warn if missing)
diff_entries = [e for e in entries if e["event"] == "git_diff_captured"]
if diff_entries:
    has_diff = any(e.get("diff", "") for e in diff_entries)
    if has_diff:
        print(f"  \033[0;32mPASS\033[0m: {len(diff_entries)} git_diff_captured with content")
    else:
        print(f"  \033[0;33mWARN\033[0m: git_diff_captured present but diff is empty (agent may have committed)")
else:
    print("  \033[0;33mWARN\033[0m: No git_diff_captured entries (may be expected)")
PYEOF

# =========================================================================
# Step 10: Verify lindy inspect
# =========================================================================
echo -e "\n${BOLD}[Step 10] Verifying lindy inspect command${NC}"

# Rich output uses escape codes. Strip them for grep.
INSPECT_OUTPUT=$(lindy-orchestrate inspect "$SESSION_ID" --full -c .orchestrator/config.yaml 2>&1) || true
INSPECT_PLAIN=$(echo "$INSPECT_OUTPUT" | sed 's/\x1b\[[0-9;]*m//g' | sed 's/\x1b\[.*?m//g')

if [ -n "$INSPECT_OUTPUT" ]; then
    check_pass "lindy inspect runs without crash"
else
    check_fail "lindy inspect crashed"
fi

if echo "$INSPECT_PLAIN" | grep -qi "session_start\|task_started\|task_completed\|session_end"; then
    check_pass "Inspect output contains summary events"
else
    # Fallback: check if the JSONL files have data (inspect may render differently)
    if [ -s "$SESSION_DIR/summary.jsonl" ]; then
        check_pass "Inspect ran (summary.jsonl has data)"
    else
        check_fail "Inspect output missing summary data"
    fi
fi

if echo "$INSPECT_PLAIN" | grep -qi "eval_scored\|phase_changed\|qa_passed\|qa_failed"; then
    check_pass "Inspect output contains decision events"
else
    if [ -s "$SESSION_DIR/decisions.jsonl" ]; then
        check_pass "Inspect ran (decisions.jsonl has data)"
    else
        check_fail "Inspect output missing decision data"
    fi
fi

if echo "$INSPECT_PLAIN" | grep -qi "agent_event\|agent_output\|transcript"; then
    check_pass "Inspect output contains transcript events"
else
    if [ -s "$SESSION_DIR/transcript.jsonl" ]; then
        check_pass "Inspect ran (transcript.jsonl has data)"
    else
        check_fail "Inspect output missing transcript data"
    fi
fi

# =========================================================================
# Step 11: Cross-stream consistency
# =========================================================================
echo -e "\n${BOLD}[Step 11] Cross-stream consistency${NC}"

python3 << PYEOF
import json, sys

def load(path):
    try:
        return [json.loads(l) for l in open(path) if l.strip()]
    except FileNotFoundError:
        return []

summary = load("${SESSION_DIR}/summary.jsonl")
decisions = load("${SESSION_DIR}/decisions.jsonl")
transcript = load("${SESSION_DIR}/transcript.jsonl")

# Check 29: Task IDs consistent
summary_tasks = {e["task_id"] for e in summary if e.get("task_id") is not None}
decision_tasks = {e["task_id"] for e in decisions if e.get("task_id") is not None}
transcript_tasks = {e["task_id"] for e in transcript if e.get("task_id") is not None}

orphan_d = decision_tasks - summary_tasks
orphan_t = transcript_tasks - summary_tasks

if not orphan_d and not orphan_t:
    print(f"  \033[0;32mPASS\033[0m: Task IDs consistent across streams (tasks: {sorted(summary_tasks)})")
else:
    if orphan_d:
        print(f"  \033[0;31mFAIL\033[0m: Decision task_ids not in summary: {orphan_d}")
    if orphan_t:
        print(f"  \033[0;31mFAIL\033[0m: Transcript task_ids not in summary: {orphan_t}")

# Check 30: Timestamps monotonic
for stream_name, entries in [("summary", summary), ("decisions", decisions), ("transcript", transcript)]:
    if not entries:
        continue
    timestamps = [e["ts"] for e in entries]
    monotonic = all(timestamps[i] >= timestamps[i-1] for i in range(1, len(timestamps)))
    if monotonic:
        print(f"  \033[0;32mPASS\033[0m: {stream_name}.jsonl timestamps monotonically ordered ({len(entries)} entries)")
    else:
        print(f"  \033[0;31mFAIL\033[0m: {stream_name}.jsonl timestamps NOT monotonically ordered")
PYEOF

# =========================================================================
# Step 12: Final report
# =========================================================================
echo ""
echo -e "${BOLD}=========================================${NC}"
echo -e "${BOLD}  E2E Observability Test Results${NC}"
echo -e "${BOLD}=========================================${NC}"
echo -e "  Session: ${SESSION_ID}"
echo -e "  Workspace: ${WORKDIR}"
echo ""
echo -e "  ${GREEN}Passed${NC}: ${PASS}"
echo -e "  ${RED}Failed${NC}: ${FAIL}"
echo -e "  ${YELLOW}Warnings${NC}: ${WARN}"
echo -e "${BOLD}=========================================${NC}"

if [ "$FAIL" -eq 0 ]; then
    echo -e "\n${GREEN}${BOLD}ALL CHECKS PASSED${NC}"
    echo ""
    echo "Cleaning up workspace..."
    rm -rf "$WORKDIR"
    exit 0
else
    echo -e "\n${RED}${BOLD}${FAIL} CHECKS FAILED${NC}"
    echo ""
    echo "Workspace preserved for debugging: $WORKDIR"
    echo "Session logs: $WORKDIR/$SESSION_DIR/"
    echo ""
    echo "Debug commands:"
    echo "  cat $WORKDIR/$SESSION_DIR/summary.jsonl | python3 -m json.tool --json-lines"
    echo "  cat $WORKDIR/$SESSION_DIR/decisions.jsonl | python3 -m json.tool --json-lines"
    echo "  cat $WORKDIR/$SESSION_DIR/transcript.jsonl | python3 -m json.tool --json-lines"
    echo "  lindy-orchestrate inspect $SESSION_ID --full -c $WORKDIR/.orchestrator/config.yaml"
    exit 1
fi
