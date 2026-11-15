<p align="center"><img src="public/evidence-graph.png" width="64" height="64" alt="" /></p>

# Ichnix

When a bug bounty fix passes staging, the next reviewer still needs to know which checks ran and what they covered. Ichnix gives product security teams that history. A signed HackerOne or Bugcrowd webhook, or a manual JSON upload, brings mapped report metadata into the team's workspace. The bounty platform handles the program and payout. Ichnix connects the report to its deployed component revision, signed staging observations and owner decisions in self-hosted [HydraDB](https://github.com/hydra-db/hydradb).

[Open the demo](https://ichnix.vercel.app/console) · [Operator manual](https://ichnix.vercel.app/docs) · [Contributing](CONTRIBUTING.md)

**Start with case `IX-214` in the read-only demo.** Select **Compare runs** to see which check changed after the fix. **Owner review** shows the suggested team, and **Worker handoff** shows why the sample review ended with `needs-evidence`. For a reproducible local review using the CLI and MCP, follow the [coding agent guide](AGENTS.md#evaluate-a-checkout).

## Try it locally

**Windows and macOS.** Start Docker Desktop in Linux container mode. From a fresh checkout, run these commands in PowerShell or a Mac terminal to start a private workspace:

```sh
docker compose --env-file examples/desktop.env build
docker compose --env-file examples/desktop.env run --rm console python3 cli.py storage init
docker compose --env-file examples/desktop.env up --detach --wait
docker compose --env-file examples/desktop.env exec -T console python3 cli.py identity issue desktop-intake --role intake --out /var/lib/ichnix/desktop-intake.token
```

Open [the local console](http://127.0.0.1:8765/console). Follow the [sign-in instructions](https://ichnix.vercel.app/docs#containers), then [import your first report](https://ichnix.vercel.app/docs#first-upload). Both services use Linux amd64. Apple silicon runs the pinned HydraDB build through emulation.

**Native Ubuntu demo.** On Ubuntu 24.04 x86-64 with Python 3.12, Node 24, and pnpm 12, run:

```sh
pnpm install --frozen-lockfile
python3 scripts/install-hydra.py
python3 cli.py demo --public-readonly
```

Open the local URL printed by the demo. Select a case to inspect its revision, evidence, and suggested owner.

## How it learns

**HydraDB Track 1 · the self-improving graph.** Starting at a report snapshot, Ichnix follows the component revision to earlier owner decisions and staging observations. Accepting or rejecting an owner updates the next shortlist for that revision. A failed check on an earlier report carries more weight than a passing one. Reviewers can inspect the underlying evidence through the console, CLI or MCP.

After native setup, run `python3 cli.py evaluate` to reproduce the [learning curve](https://ichnix.vercel.app/api/evaluation). On six synthetic holdouts, the correct owner ranked first on **0, 2, 4 and 6** cases after 0, 2, 4 and 6 reviewed training cases. Pooling the same feedback across components yielded **0, 2, 2 and 2**. This measures repeat triage on known component revisions. Customer-program routing accuracy is unmeasured.

<p align="center"><img src="docs/architecture.svg" width="640" alt="Signed webhooks and authenticated uploads feed HydraDB alongside staging checks. Reviewers use the console or CLI. MCP workers retrieve assigned cases through an API backed by SQLite leases." /></p>

[Report archives](https://ichnix.vercel.app/docs#report-archive) · [Agent Markdown](https://ichnix.vercel.app/docs#markdown-context) · [CLI setup](https://ichnix.vercel.app/docs#cli) · [Agent setup](https://ichnix.vercel.app/docs#agents) · [SelfGraph export](https://ichnix.vercel.app/docs#selfgraph) · [Security](SECURITY.md) · [MIT license](LICENSE) · [Third-party notices](licenses/Runtime-sources.txt)
