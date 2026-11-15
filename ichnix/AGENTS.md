# Working on the workspace backend

- Keep the same mapped report fields across manual upload, CLI and signed provider intake. Verify program and catalog scope before processing provider metadata.
- In `server.py`, check the identity and role at the requested operation. MCP workers may read a case through their active lease. Keep worker results separate from owner votes.
- In `graph.py`, bind query parameters and keep service, component and revision together. A failed control, an inconclusive result and a reviewer disposition carry different meanings.
- Use a disposable HydraDB graph for integration checks and a temporary SQLite directory for identities and leases. The parent [instructions](../AGENTS.md) link the test entry points.
