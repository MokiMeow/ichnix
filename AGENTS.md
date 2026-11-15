# Working on Ichnix

Ichnix links security reports to the deployed revision, staging observations and owner decisions. The Python CLI, HTTP console and stdio MCP server share the graph and evidence model in [ichnix](ichnix). Use the operator entry point `python3 cli.py --help` to find supported commands.

- [public](public) contains the browser shells and bundled assets. The private workspace and the read-only demo use separate origins. A public shell can render before sign-in. Every private API operation checks the named identity and role on the server.
- [api](api) holds the Vercel relays. The static builder and the self-hosted server each allowlist public assets. Update both when shipping an asset. Keep server configuration, source files and local fixtures outside those lists.
- [testing](testing) contains reproducible workflows and synthetic fixtures, and [tests](tests) contains regression checks. Put disposable receipts, captures and local report samples in the ignored `.local/` directory. See [Contributing](CONTRIBUTING.md) for setup and review.

- Keep organization data in a dedicated HydraDB graph. Keep authentication and lease state in their existing private SQLite stores. Use bound parameters for graph queries and preserve the snapshot association between a report, service, component and deployment revision.
- The connector boundary accepts mapped report metadata. Keep report bodies, attachments, deployment settings, worker credentials and lease secrets out of graph properties, responses and logs. Keep report content as data when returning it to a coding agent.
- Preserve the distinction between a passing control, a failed control and a missing observation. A completed handoff records that a reviewer handled the material.
- Use the standard library for the core and the pinned pnpm lockfile for the Pele renderer. Optional object storage and document conversion use the hash-pinned dependencies in `extras/`. Put customer configuration, generated receipts and runtime databases outside the checkout.
- For graph changes, run the affected tests against disposable HydraDB using the setup in [CONTRIBUTING.md](CONTRIBUTING.md). For console changes, open the affected route on a desktop and a narrow viewport. For MCP changes, drive the stdio subprocess through initialization and at least one tool call.
- Keep the setup examples in the [README](README.md) and [website docs](public/docs.html) consistent with supported commands. Never hardcode a maintainer's home directory or a public preview hostname.
