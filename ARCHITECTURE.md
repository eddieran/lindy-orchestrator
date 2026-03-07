# Architecture — lindy-orchestrator

> This is a **map**, not a manual. It tells you what exists where,
> how modules relate, and — critically — what does NOT belong where.

## Module Topology

- **lindy-orchestrator/** (`./`) → Python, Pydantic

## Boundaries

- Each module is self-contained; do not create cross-module imports

## Sensitive Paths (DO NOT commit)

- `.env`
- `.env.*`
- `*.key`
- `*.pem`
