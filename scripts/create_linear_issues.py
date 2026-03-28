#!/usr/bin/env python3
"""Create Linear issues for all pipeline refactor tasks."""

import json
import os
import re
import sys
import urllib.request
from pathlib import Path

API_KEY = os.environ["LINEAR_API_KEY"]
TEAM_ID = "a2e30578-e8d1-4f2b-9617-f99bf5c49f25"  # LINDY
PROJECT_ID = "9903697c-bbb0-43e5-a190-ceee7b742ab9"  # lindy-orchestrator
STATE_ID = "629c18be-0ec3-415a-8bdc-d9c6d5b7b15d"  # Todo

TASKS_DIR = Path(__file__).parent.parent / "docs" / "superpowers" / "specs" / "tasks"

# Task definitions with metadata
TASKS = [
    {"file": "T01-data-models.md", "id": "T1", "title": "T1: Data Models + Serialization", "depends": [], "priority": 1},
    {"file": "T02-config-schema.md", "id": "T2", "title": "T2: Configuration Schema", "depends": ["T1"], "priority": 1},
    {"file": "T02b-provider-factory.md", "id": "T2b", "title": "T2b: Provider Factory Refactor", "depends": ["T2"], "priority": 1},
    {"file": "T03-soft-deprecation.md", "id": "T3", "title": "T3: Soft Feature Deprecation", "depends": ["T2"], "priority": 2},
    {"file": "T04-planner-runner.md", "id": "T4", "title": "T4: Planner Runner", "depends": ["T2b"], "priority": 2},
    {"file": "T05-generator-runner.md", "id": "T5", "title": "T5: Generator Runner", "depends": ["T2b"], "priority": 2},
    {"file": "T06-evaluator-runner.md", "id": "T6", "title": "T6: Evaluator Runner", "depends": ["T2b"], "priority": 2},
    {"file": "T07-orchestrator.md", "id": "T7", "title": "T7: Orchestrator", "depends": ["T3", "T4", "T5", "T6"], "priority": 1},
    {"file": "T08-visualization.md", "id": "T8", "title": "T8: Visualization Update", "depends": ["T7"], "priority": 2},
    {"file": "T09-cli-wiring.md", "id": "T9", "title": "T9: CLI Wiring + Non-Runtime Consumers", "depends": ["T7"], "priority": 2},
    {"file": "T10-integration-tests.md", "id": "T10", "title": "T10: Integration Tests", "depends": ["T8", "T9"], "priority": 1},
    {"file": "T11-e2e-tests.md", "id": "T11", "title": "T11: End-to-End Tests", "depends": ["T10"], "priority": 1},
    {"file": "T12-cleanup-pr.md", "id": "T12", "title": "T12: Hard Delete + Cleanup + PR", "depends": ["T11"], "priority": 1},
]


def graphql(query, variables=None):
    payload = {"query": query}
    if variables:
        payload["variables"] = variables
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        "https://api.linear.app/graphql",
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": API_KEY,
        },
    )
    with urllib.request.urlopen(req) as resp:
        result = json.loads(resp.read())
    if "errors" in result:
        print(f"GraphQL errors: {result['errors']}", file=sys.stderr)
        sys.exit(1)
    return result


def read_task_file(filename: str) -> str:
    path = TASKS_DIR / filename
    content = path.read_text()
    # Strip YAML frontmatter
    if content.startswith("---"):
        end = content.index("---", 3)
        content = content[end + 3:].strip()
    return content


def create_issue(title: str, description: str, priority: int) -> str:
    """Create a Linear issue and return its ID."""
    mutation = """
    mutation CreateIssue($input: IssueCreateInput!) {
        issueCreate(input: $input) {
            success
            issue {
                id
                identifier
                url
            }
        }
    }
    """
    variables = {
        "input": {
            "teamId": TEAM_ID,
            "projectId": PROJECT_ID,
            "stateId": STATE_ID,
            "title": title,
            "description": description,
            "priority": priority,
        }
    }
    result = graphql(mutation, variables)
    issue = result["data"]["issueCreate"]["issue"]
    print(f"  Created {issue['identifier']}: {title} → {issue['url']}")
    return issue["id"]


def add_relation(issue_id: str, related_id: str):
    """Create a 'blocks' relation: related_id blocks issue_id."""
    mutation = """
    mutation CreateRelation($input: IssueRelationCreateInput!) {
        issueRelationCreate(input: $input) {
            success
        }
    }
    """
    variables = {
        "input": {
            "issueId": issue_id,
            "relatedIssueId": related_id,
            "type": "blocks",
        }
    }
    graphql(mutation, variables)


def main():
    print("Creating Linear issues for pipeline refactor DAG...\n")

    # Phase 1: Create all issues
    issue_ids: dict[str, str] = {}
    for task in TASKS:
        content = read_task_file(task["file"])
        issue_id = create_issue(task["title"], content, task["priority"])
        issue_ids[task["id"]] = issue_id

    # Phase 2: Create dependency relations
    print("\nCreating dependency relations...")
    for task in TASKS:
        if not task["depends"]:
            continue
        issue_id = issue_ids[task["id"]]
        for dep_id in task["depends"]:
            dep_issue_id = issue_ids[dep_id]
            add_relation(issue_id, dep_issue_id)
            print(f"  {dep_id} blocks {task['id']}")

    print(f"\nDone! Created {len(TASKS)} issues with {sum(len(t['depends']) for t in TASKS)} dependency relations.")
    print(f"Project: https://linear.app/lindymoneyera/project/lindy-orchestrator-e01c9328908a/overview")


if __name__ == "__main__":
    main()
