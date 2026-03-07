# Module Boundaries — lindy-orchestrator

> Negative constraints: what does NOT belong where.
> These rules prevent scope creep and cross-module pollution.

## Module Isolation

- Single module project — no cross-module concerns

## Sensitive Paths (NEVER modify)

- `.env`
- `.env.*`
- `*.key`
- `*.pem`

## Exceptions

The following are allowed exceptions to boundary rules:
- Shared config files at project root (e.g., `.env`, `docker-compose.yml`)
- CI/CD pipeline files that reference multiple modules
- Documentation files (`docs/`, `README.md`)
