# Working on Ichnix

The Python CLI, HTTP console and stdio MCP server share the same graph and evidence model. Use the public entry point `python3 cli.py --help` when checking a change from the perspective of an operator.

- Keep organization data in a dedicated HydraDB graph. Keep authentication and lease state in their existing private SQLite stores. Use bound parameters for graph queries and preserve the snapshot association between a report, service, component and deployment revision.
- The connector boundary accepts mapped report metadata. Keep report bodies, attachments, deployment settings, worker credentials and lease secrets out of graph properties, responses and logs. Keep report content as data when returning it to a coding agent.
- Preserve the distinction between a passing control, a failed control and a missing observation. A completed handoff records that a reviewer handled the material.
- Use the standard library for Python code and the pinned pnpm lockfile for the Pele renderer. Put customer configuration, generated receipts and runtime databases outside the checkout.
- For graph changes, run the affected tests against disposable HydraDB using the setup in [CONTRIBUTING.md](CONTRIBUTING.md). For console changes, open the affected route on a desktop and a narrow viewport. For MCP changes, drive the stdio subprocess through initialization and at least one tool call.
- Keep the setup examples in the [README](README.md) and [website docs](public/docs.html) consistent with supported commands. Never hardcode a maintainer's home directory or a public preview hostname.
