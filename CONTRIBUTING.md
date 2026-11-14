# Contributing to Ichnix

From the repository root on Ubuntu 24.04 x86-64, install Python 3.12, Node 24 and the pnpm version in package.json, then run:

```sh
pnpm install --frozen-lockfile
python3 scripts/install-hydra.py
python3 -m unittest discover -s tests -v
```

Run `python3 cli.py survey` to exercise the [testing fixtures](testing/fixtures) as a host operator, reader, reviewer, local MCP adviser and two named MCP workers. The survey writes its databases and private credentials in temporary directories and checks persisted outcomes before teardown. `python3 testing/model_survey.py` is a separate, opt-in run using a signed-in Codex CLI and model access.

The suite starts disposable HydraDB nodes. Its provider tests use fixtures and do not require accounts. A separate acceptance command fetches metadata for a public disclosure from HackerOne and exercises the private console, signed manifest verification, review queue and worker MCP against a fresh local graph:

```sh
python3 cli.py acceptance
```

For a change to graph retrieval, compare the held-out owner and CI-check results with the catalog baseline using `python3 cli.py evaluate`. For worker throughput, run `python3 cli.py bench-workers --workers 100 --jobs 100` on an idle host. Report the worker count, job count and whether your timings include process startup.

Keep a pull request focused on one observable change. Describe the previous behavior, what changed and how you checked it. Include a regression test for a bug that could recur. Use synthetic or deliberately public records in examples and remove customer data, tokens and machine-specific paths before sharing diagnostics. See the [project instructions](AGENTS.md) and [security policy](SECURITY.md).

Ichnix contributions are distributed under the [MIT license](LICENSE). Retain separate licenses for fonts and dependencies. The [HydraDB runtime](https://github.com/hydra-db/hydradb/blob/v0.4.0/LICENSE) has its own license.
