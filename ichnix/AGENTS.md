# Working on the workspace backend

- Keep the same mapped report fields across manual upload, CLI and signed provider intake. Verify program and catalog scope before processing provider metadata.
- In `server.py`, check the identity and role at the requested operation. MCP workers may read a case through their active lease. Keep worker outcomes, owner votes and named reviewers’ assessments of signed failed observations separate. Only named reviewers need observation references for writing assessments.
- In `graph.py`, bind query parameters and keep service, component and revision together. Link a human assessment to the specific observation. Preserve the original result when a later run passes or a reviewer changes their decision.
- Use a disposable HydraDB graph for integration checks and a temporary SQLite directory for identities and leases. The parent [instructions](../AGENTS.md) link the test entry points.
