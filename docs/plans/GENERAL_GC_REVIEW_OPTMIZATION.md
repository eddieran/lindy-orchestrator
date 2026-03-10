Act as a senior Python Tech Lead and perform a comprehensive, safe, incremental, and non-disruptive self-audit and optimization of the current Python project.

Goals:
- Do not change existing business semantics
- Do not break external interfaces or configuration compatibility
- Make small, low-risk changes first, then move to higher-risk areas
- After each change, add or update tests and verify the project still runs correctly

Focus areas:
1. Whether the code design is reasonable (responsibility boundaries, coupling, duplication, overly long functions/classes)
2. Whether there is outdated code, deprecated APIs, or legacy compatibility layers
3. Whether there are unused functions/classes/imports/configs/dead code
4. Whether duplicated logic can be merged or reused
5. Gaps in unit test and integration test coverage
6. Whether end-to-end tests cover the core business flows
7. Type hints, exception handling, logging, configuration, performance, dependencies, security, and concurrency issues
8. Risks in API contracts, database/migrations, CI/CD, and documentation

Working approach:
- First scan the project structure and produce a risk map
- List findings by high / medium / low risk
- Provide a low-risk-first improvement plan
- Implement changes in small batches, and for each batch explain:
  - what was changed
  - the risk level
  - how it was validated
- Add or update tests alongside each change
- Produce a final optimization report and follow-up roadmap

Strict requirements:
- Do not do aggressive refactoring
- Do not remove or rename public interfaces casually
- If you cannot confirm something is truly unused, mark it as a candidate instead of deleting it
- Do not only give suggestions; provide concrete, actionable modifications whenever possible
- Tests must cover boundaries, error paths, and regression scenarios
- Keep the project runnable at all times

