# Contributing to Ichnix

Use a local checkout and synthetic examples when developing or sharing a change. The [operator manual](public/docs.html) describes the user workflows, and the [project instructions](AGENTS.md) explain data boundaries and repository conventions.

## Set up a development environment

The native runtime installer targets Ubuntu 24.04 x86-64. Install Python 3.12, Node 24 and the pnpm version in [package.json](package.json), then run from the repository root:

```sh
pnpm install --frozen-lockfile
python3 scripts/install-hydra.py
python3 -m unittest discover -s tests -v
```

Python application code uses the standard library. The tests start disposable HydraDB nodes and local SQLite stores. Provider tests use fixtures. Neither provider accounts nor model credentials are required for this suite.

## Work on the right boundary

| Change                        | Code                                                                            | Check                                                                                                                                                 |
| ----------------------------- | ------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| Source intake and staging     | [Connectors](connectors.py), [CI checks](staging.py)                            | Preserve program and asset scope. Check mapped and unmapped reports or signed, altered and missing observations.                                      |
| Graph retrieval and feedback  | [Graph](graph.py), [evaluation](eval_graph.py)                                  | Run the affected graph tests and `python3 cli.py evaluate`. Check that prior evidence and owner decisions stay associated with the intended revision. |
| Agent access and coordination | [HTTP console](server.py), [handoffs](handoffs.py), [MCP server](mcp_server.py) | Drive a real stdio client through initialization and a tool call. Verify its role, scope, expiry or retry behavior, and persisted outcome.            |
| User interface and docs       | [UI design guide](DESIGN.md), [operator manual](public/docs.html)               | Start the demo and exercise the changed route at a narrow viewport and desktop size. Check the actual response to a user action.                      |

Use the smallest relevant regression test while developing. For example, `python3 -m unittest discover -s tests -p test_worker_api.py` exercises the private worker HTTP interface. Run the full suite for changes spanning boundaries. Use the existing pnpm lockfile and preserve separate runtime and font notices.

## Exercise complete workflows

- `python3 cli.py survey` replays four [synthetic cases](testing/fixtures) through six roles, including separate MCP workers and a human reviewer. It verifies stored outcomes and scopes before cleanup.
- `python3 cli.py acceptance` fetches public HackerOne metadata and signs observations from a local Ichnix instance. It requires internet access and works without a bounty account.
- `python3 testing/model_survey.py` sends bundled synthetic fixtures to two real sessions through a signed-in Codex CLI. Run it when you need to verify model behavior. The offline survey checks the protocol independently.
- `python3 cli.py bench-workers --workers 100 --jobs 100` compares one worker with 100 deterministic MCP clients on the same job count. Report host capacity, clients, jobs, throughput and p95. Report process startup separately.

## Update the architecture diagram

Edit the Mermaid flowchart in [the renderer](scripts/render-architecture.mjs). Regenerate the checked-in [architecture SVG](docs/architecture.svg) using the pinned Pele dependency:

```sh
node scripts/render-architecture.mjs
git diff -- docs/architecture.svg
```

Open the SVG in a browser and check labels, arrows and direction. Commit the renderer and regenerated output together when changing the topology. The separate case trace renderer builds sequence diagrams from observed CI events.

## Submit a change

Open a focused pull request describing the previous behavior, the user-visible change and how you checked it. Include a regression test when it captures a failure that could recur. Use synthetic data in screenshots, examples and logs. Keep credentials, private report identifiers, customer manifests, generated receipts and machine-specific paths out of submissions.

For suspected security issues, follow the [private reporting policy](SECURITY.md). Contributions to the Ichnix application use its [MIT license](LICENSE). See the [runtime notices](licenses/Runtime-sources.txt) for separately distributed components.

## Browser checks

From a checkout with the renderer and HydraDB installed, run the browser suite with a local Chrome executable. It uses disposable graph, registry and lease state. Playwright is only needed for this check.

```sh
python3 -m venv .venv
.venv/bin/pip install -r testing/browser-requirements.txt
.venv/bin/python testing/browser.py --chrome "$(command -v google-chrome)"
```
