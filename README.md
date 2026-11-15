<p align="center"><img src="public/evidence-graph.png" width="64" height="64" alt="" /></p>

# Ichnix

Ichnix is a self-hosted bug bounty triage workspace for product security teams. It connects each report to the software version it affects, the checks that ran in staging, and the people who reviewed it. Engineers and coding agents can pick up a review with the same evidence and see which questions are still open.

[Explore the read-only console](https://ichnix.vercel.app/console) · [Set up a private workspace](https://ichnix.vercel.app/docs#install)

## How it works

1. **Bring in a report.** Connect a HackerOne or Bugcrowd program through signed webhooks, or upload mapped JSON in the private console. The bounty platform continues to handle researcher communication and payouts.
2. **Trace the evidence.** Self-hosted [HydraDB](https://github.com/hydra-db/hydradb) ties each report snapshot to its staging results. Through the component revision, Ichnix finds earlier reviews, ranks potential owners using their accepted or rejected assignments, and suggests checks that mattered on that release.
   Closely matching titles from separate submissions on the same service and component revision are flagged as possible duplicates for a reviewer to compare. Each report keeps its own evidence.
3. **Hand off the review.** Engineers inspect the case in the console or CLI. A coding agent can claim a review through the Model Context Protocol (MCP), read the evidence for its assigned case, and record an outcome such as a request for more evidence.

<p align="center"><img src="docs/architecture.svg" width="640" alt="Report intake and staging checks feed HydraDB. Reviewers use the console or CLI. MCP workers retrieve assigned cases through an API backed by SQLite leases." /></p>

## Try it locally

For a private workspace on Windows, macOS or Linux, follow the [Docker Compose guide](https://ichnix.vercel.app/docs#containers). It covers startup, credentials and sign-in. Once connected, you can [import your first report](https://ichnix.vercel.app/docs#first-upload).

For a local read-only demo on Ubuntu 24.04 x86-64, install Python 3.12, Node 24 and pnpm 12. From the repository root run:

```sh
pnpm install --frozen-lockfile
python3 scripts/install-hydra.py
python3 cli.py demo --public-readonly
```

Open the URL printed by the demo.

## Owner routing results

> [!NOTE]
> **Synthetic fixtures · 64 held-out reports.** After 0, 8, 16, 24 and 32 separate training reports, Ichnix ranked the expected owner first on **16, 16, 24, 33 and 46** cases. At the final checkpoint, pooled feedback scored **19/64** and the catalog alone scored **16/64**. On the 16 cases whose release had no training feedback, Ichnix and the catalog each scored **4/16**.

> [!IMPORTANT]
> **Public disclosures · 75 real curl reports.** In a chronological replay, Ichnix and the catalog each matched a credited patch author on **56/75** reports. None had earlier public feedback on the inferred release. Both missed all 19 reports where the usual patch author lacked credit. Patch credit approximates ownership because internal assignments are unavailable.

The [operator manual](https://ichnix.vercel.app/docs#routing-results) explains the two evaluations. The live console shows a separate six-case synthetic demonstration.

## Guides

| I want to… | Start here |
| --- | --- |
| Bring a bounty program into my workspace | [Connect a program](https://ichnix.vercel.app/docs#providers) |
| Work through a review from my terminal | [Configure the CLI](https://ichnix.vercel.app/docs#cli) |
| Give a coding agent access to assigned reviews | [Set up MCP](https://ichnix.vercel.app/docs#agents) |
| Store or export case data | [Configure report archives](https://ichnix.vercel.app/docs#report-archive) or [export SelfGraph facts](https://ichnix.vercel.app/docs#selfgraph) |
| Evaluate or contribute to Ichnix | [Run the agent evaluation](AGENTS.md#evaluate-a-checkout) or [read the contributing guide](CONTRIBUTING.md) |

Report security concerns using the [private reporting instructions](SECURITY.md). The application uses the [MIT license](LICENSE). Bundled components have [separate notices](licenses/Runtime-sources.txt).
