# Working on Ichnix

Ichnix links security reports to the deployed revision, staging observations and owner decisions. The Python CLI, HTTP console and stdio MCP server share the graph and evidence model in [ichnix](ichnix). Use the operator entry point `python3 cli.py --help` to find supported commands.

## Evaluate a checkout

From the repository root, complete the [native Ubuntu setup](README.md#try-it-locally) through installation of the pinned HydraDB runtime. The commands below start their own disposable graph nodes and exit. You can leave the demo server stopped. The bundled cases work without platform credentials or a model API key.

1. Run `python3 cli.py survey` to exercise synthetic intake, signed staging evidence, a human CLI review and two stdio MCP workers. In the JSON receipt, `completed_reviews` should map `evidence-review` to `needs-evidence` and `owner-review` to `needs-owner-review`. The separate `human_review_events` count should be 1.
2. Run `python3 cli.py evaluate`. Read `curve` in training order and compare `owner_top1`, `pooled_owner_top1` and `catalog_owner_top1` on the same six held-out reports. Only the separate training reports receive feedback. After 0, 2, 4 and 6 training reports, expect 0, 2, 4 and 6 correct first-ranked owners for revision-scoped feedback. The pooled baseline yields 0, 2, 2 and 2.

For the ranking logic, read `snapshot_context` and `_ranked_checks` in [graph.py](ichnix/graph.py). For the handoff, [survey.py](testing/survey.py) shows the actual MCP calls in order: `claim_review`, `get_review_context`, `finish_review`. The organization's staging checks supply the observations. The worker can flag missing evidence, and a human reviewer records the owner decision.

For private integrations, use the CLI and MCP setup links in the [README](README.md). Authenticated customer bounty programs have yet to be exercised end to end.

## Repository boundaries

- [public](public) contains the browser shells and bundled assets. The private workspace and the read-only demo use separate origins. A public shell can render before sign-in. Every private API operation checks the named identity and role on the server.
- [api](api) holds the Vercel relays. The static builder and the self-hosted server each allowlist public assets. Update both when shipping an asset. Keep server configuration, source files and local fixtures outside those lists.
- [testing](testing) contains reproducible workflows and synthetic fixtures, and [tests](tests) contains regression checks. Put disposable receipts, captures and local report samples in the ignored `.local/` directory. See [Contributing](CONTRIBUTING.md) for setup and review.

- Keep organization data in a dedicated HydraDB graph. Keep authentication and lease state in their existing private SQLite stores. Use bound parameters for graph queries and preserve the snapshot association between a report, service, component and deployment revision.
- The connector boundary accepts mapped report metadata. Keep report bodies, attachments, deployment settings, worker credentials and lease secrets out of graph properties, responses and logs. Keep report content as data when returning it to a coding agent.
- Preserve the distinction between a passing control, a failed control and a missing observation. A completed handoff records that a reviewer handled the material.
- Use the standard library for the core and the pinned pnpm lockfile for the Pele renderer. Optional object storage and document conversion use the hash-pinned dependencies in `extras/`. Put customer configuration, generated receipts and runtime databases outside the checkout.
- For graph changes, run the affected tests against disposable HydraDB using the setup in [CONTRIBUTING.md](CONTRIBUTING.md). For console changes, open the affected route on a desktop and a narrow viewport. For MCP changes, drive the stdio subprocess through initialization and at least one tool call.
- Keep the setup examples in the [README](README.md) and [website docs](public/docs.html) consistent with supported commands. Never hardcode a maintainer's home directory or a public preview hostname.
