<p align="center"><img src="public/evidence-graph.png" width="64" height="64" alt="" /></p>

# Ichnix

Ichnix helps product security teams triage bug bounty reports. Each case connects a report to a deployed revision, its staging checks, and the team responsible. Reviewers and coding agents can pick up the same evidence through the console, CLI, or MCP.

[Open the demo](https://ichnix.vercel.app/console) · [Operator manual](https://ichnix.vercel.app/docs) · [Contributing](CONTRIBUTING.md)

## Try it locally

**Windows and macOS.** Start Docker Desktop in Linux container mode. From a fresh checkout, run these commands in PowerShell or a Mac terminal to start a private workspace:

```sh
docker compose --env-file examples/desktop.env build
docker compose --env-file examples/desktop.env run --rm console python3 cli.py storage init
docker compose --env-file examples/desktop.env up --detach --wait
docker compose --env-file examples/desktop.env exec -T console python3 cli.py identity issue desktop-intake --role intake --out /var/lib/ichnix/desktop-intake.token
```

Open [the local console](http://127.0.0.1:8765/console). The [desktop setup instructions](https://ichnix.vercel.app/docs#containers) show how to paste the intake credential and import a report. Both services use Linux amd64. Apple silicon runs the pinned HydraDB build through emulation.

**Native Ubuntu demo.** On Ubuntu 24.04 x86-64 with Python 3.12, Node 24, and pnpm 12, run:

```sh
pnpm install --frozen-lockfile
python3 scripts/install-hydra.py
python3 cli.py demo --public-readonly
```

Open the local URL printed by the demo. Select a case to inspect its revision, evidence, and suggested owner.

## How it learns

HydraDB links reports that share a component revision. An accepted or rejected owner changes the next recommendation, and previous CI results inform which check to review first. Run `python3 cli.py evaluate` to see the accuracy curve on synthetic holdouts for **HydraDB Track 1**.

<img src="docs/architecture.svg" width="440" alt="Report intake and staging checks feed HydraDB. Human reviewers and MCP workers retrieve evidence and record outcomes." />

[Self-hosting](https://ichnix.vercel.app/docs#install) · [Agent setup](https://ichnix.vercel.app/docs#agents) · [SelfGraph export](https://ichnix.vercel.app/docs#selfgraph) · [Security](SECURITY.md) · [MIT license](LICENSE) · [Third-party notices](licenses/Runtime-sources.txt)
