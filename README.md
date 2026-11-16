<p align="center"><img src="public/evidence-graph.png" width="64" height="64" alt="" /></p>

# Ichnix · Release-aware bug bounty triage

Ichnix is a self-hosted bug bounty triage workspace for product security teams. It connects each report to the software version it affects, the checks that ran in staging, and the people who reviewed it. The next engineer or coding agent can see which check failed, what a reviewer confirmed and where the team still needs evidence.

[Explore the read-only console](https://ichnix.vercel.app/console) · [Set up a private workspace](https://ichnix.vercel.app/docs#install)

## How it works

1. **Bring in a report.** Connect a HackerOne or Bugcrowd program through signed webhooks, or upload mapped JSON in the private console. The bounty platform continues to handle researcher communication and payouts.
2. **Trace the evidence.** Self-hosted HydraDB ties each report snapshot to its staging results. Through the component revision, Ichnix finds earlier reviews, ranks potential owners using their accepted or rejected assignments, and suggests checks that mattered on that release.
   Two researchers can describe the same failure in different words. Ichnix flags submissions on the same deployment when both record at least two of the same failures on one signed staging manifest. A reviewer can compare the named checks and both histories before deciding whether the reports describe the same bug.
   A named reviewer can confirm a signed failed check with a priority, dismiss it or request more evidence in the console or CLI. HydraDB links that decision to the observation on the reviewed release.
3. **Hand off the review.** Engineers inspect the case in the console or CLI. A coding agent can claim one review through the Model Context Protocol (MCP), read its evidence and human assessments, and return an outcome such as a request for more evidence. If an assessment changes during an evidence review, the worker must reread the case before submitting its outcome.

<p align="center"><img src="docs/architecture.svg" width="640" alt="HydraDB connects report snapshots, staging observations and reviewer assessments. MCP workers retrieve evidence through a leased review and return an outcome to the snapshot." /></p>

## Try it locally

For a private workspace on Windows, macOS or Linux, follow the [Docker Compose guide](https://ichnix.vercel.app/docs#containers). It covers startup, credentials and sign-in. Once connected, you can [import your first report](https://ichnix.vercel.app/docs#first-upload).

For a local read-only demo on Ubuntu 24.04 x86-64, install Python 3.12, Node 24 and pnpm 12. From the repository root run:

```sh
pnpm install --frozen-lockfile
python3 scripts/install-hydra.py
python3 cli.py demo --public-readonly
```

Open the URL printed by the demo.

## Selected track · Track 1, the self-improving graph

HydraDB connects each report snapshot to a component revision. An owner decision or staging observation on one report changes what Ichnix recommends for the next report on that revision. The comparison below gives the pooled baseline the same feedback, with every component and revision mapped to one shared node.

| What the graph does | How it helps triage | Evidence |
| --- | --- | --- |
| Ranks owners from reviews on the affected release | Surfaces teams that accepted earlier reports on that revision | On 64 **synthetic** holdouts, correct first choices rose from **16 to 46**. Pooled feedback finished at **19**, catalog alone at **16**. [Replay the comparison](testing/owner_curve.py) |
| Ranks staging checks from prior observations | Suggests which existing check to inspect first | On a separate six-case **synthetic** cohort, first-choice checks rose from **0 to 2 to 4 to 6** across four rounds. [Live evaluation JSON](https://ichnix.vercel.app/api/evaluation) · [Evaluator](ichnix/eval_graph.py) |
| Finds separate submissions that cite the same failed checks | Gives reviewers evidence to compare even when titles differ | In the [read-only console](https://ichnix.vercel.app/console), open **IX-214 → Compare submissions** and follow **IX-104**. Both cite the same two failures. [Matching rules and boundary tests](tests/test_related.py) |

> [!NOTE]
> **Reproduce the synthetic owner curve.** After local HydraDB setup, run `python3 -m testing.owner_curve`. It evaluates the same 64 held-out reports after 0, 8, 16, 24 and 32 separate training reports. Correct owners at rank one: **16 → 16 → 24 → 33 → 46**. On the 16 holdouts from unseen releases, the final score was **4/16** for both Ichnix and the catalog.

> [!IMPORTANT]
> **Public disclosures · 75 real curl reports.** In a chronological replay, Ichnix and the catalog each matched a credited patch author on **56/75**. None had earlier public feedback on the inferred release. Both missed all 19 reports where the usual patch author lacked credit. Patch credit is a proxy for internal ownership. [Evaluation details](https://ichnix.vercel.app/docs#routing-results)

The duplicate example is synthetic. Shared CI failures can have different causes. Duplicate precision and recall on a labeled customer program remain unmeasured.

## Core technology

| Repository | Role in Ichnix |
| --- | --- |
| [HydraDB](https://github.com/hydra-db/hydradb) | Stores the evidence graph. Ichnix traverses it to rank owners and checks and to find reports linked by staging failures. |
| [SelfGraph](https://github.com/hydra-db/selfgraph) | Consumers can import a case’s exported facts, including the release, recorded checks and their evidence sources. |
| [AnyDoc](https://github.com/firecrawl/anydoc) | Optional offline conversion of report documents to Markdown through the CLI. |
| [SQLite](https://github.com/sqlite/sqlite) | Stores workspace identities and review leases so an agent can claim a handoff and resume its work. |
| [Pele](https://github.com/obsidianmd/pele) | Renders the architecture diagram and the review and evidence diagrams in the console. |

## Guides

| I want to… | Start here |
| --- | --- |
| Bring a bounty program into my workspace | [Connect a program](https://ichnix.vercel.app/docs#providers) |
| Work through a review from my terminal | [Configure the CLI](https://ichnix.vercel.app/docs#cli) |
| Give a coding agent access to assigned reviews | [Set up MCP](https://ichnix.vercel.app/docs#agents) |
| Store or export case data | [Configure report archives](https://ichnix.vercel.app/docs#report-archive) or [export SelfGraph facts](https://ichnix.vercel.app/docs#selfgraph) |
| Evaluate or contribute to Ichnix | [Run the agent evaluation](AGENTS.md#evaluate-a-checkout) or [read the contributing guide](CONTRIBUTING.md) |

Report security concerns using the [private reporting instructions](SECURITY.md). The application uses the [MIT license](LICENSE). Bundled components have [separate notices](licenses/Runtime-sources.txt).
