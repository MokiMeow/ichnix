<p align="center"><img src="public/evidence-graph.png" width="72" height="72" alt="" /></p>

# Ichnix

A self-hosted evidence workspace for product security teams running a bug bounty program. Start with a reviewed report, identify the deployed component, check its staging configuration against policy, and hand the evidence to a person or coding agent. The next reviewer can see which checks and owners helped with the same component revision.

**HydraDB Track 1 · The self-improving graph.** A reviewer’s accepted or rejected owner changes the ranking for the next case on that component revision. Prior CI results influence which control the next reviewer sees first. The evaluator reports both rankings after each feedback batch alongside a fixed catalog baseline.

**Who would buy it.** The proposed buyer is the product security lead who routes bounty reports across engineering teams. A pilot can start with one program and compare analyst time to a confirmed owner and the number of reassigned cases against the team’s current queue. Customer willingness to pay and time savings have yet to be measured.

[Watch the 54-second demo](public/ichnix-demo.mp4) · [Operator manual](public/docs.html) · [Contributing](CONTRIBUTING.md) · [Security reporting](SECURITY.md)

## Try the workflow

From the checkout on Ubuntu 24.04 x86-64 with Python 3.12, Node 24 and pnpm 12:

```sh
pnpm install --frozen-lockfile
python3 scripts/install-hydra.py
python3 cli.py survey
```

The installer verifies a pinned HydraDB runtime. Once installed, the survey runs offline against a disposable graph and local SQLite stores. It walks four synthetic reports through a host operator, reader, reviewer, local MCP reader and two scoped workers. One retention control fails, another check has missing evidence, and a human gets the final owner decision. The same service on revision r8 passes all three checks. A signed r7 export submitted against r8 is rejected and leaves the r8 evidence unchanged. [Survey fixtures](testing/fixtures)

To see the interface, run `python3 cli.py demo --public-readonly` and open its printed loopback URL. The demo shows synthetic cases, evidence timelines, signed-manifest provenance where available, human decisions and the measured learning curve. Its temporary graph is discarded on exit. The full operator manual is served at `/docs`.

## What is verified

| Run                                                     | Source and observed result                                                                                                                                                                                                                                                                                                                                                                                                     |
| ------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `python3 cli.py acceptance`                             | Fetches the public title and ID of curl report #2236133 without a provider account. It records three headers observed on the running **local Ichnix console**, verifies a signed manifest, and checks the worker handoff and human audit trail. The receipt includes the digest and observation time. [Original disclosure](https://hackerone.com/reports/2236133) · [curl advisory](https://curl.se/docs/CVE-2023-46219.html) |
| `python3 testing/model_survey.py`                       | Two real, isolated Codex sessions used synthetic data and separate worker identities. One identified failed retention and missing masking evidence and recorded `needs-evidence`. The other selected `records-team` and recorded `needs-owner-review`. Both graph outcomes matched their SQLite receipts on the first attempt. This optional run uses your signed-in Codex CLI and model access.                               |
| `python3 cli.py evaluate`                               | On six synthetic holdouts sharing three component revisions, top-ranked owner and CI-check accuracy increased from 0/6 through 2/6 and 4/6 to 6/6 as separate training cases arrived. This measures retrieval in the fixture. Production accuracy and time savings are unmeasured.                                                                                                                                             |
| `python3 cli.py bench-workers --workers 100 --jobs 100` | On one two-vCPU host, a single worker completed 13.34 synthetic jobs/s with 0.087 s p95 latency. With 100 workers, throughput was 11.70 jobs/s and p95 was 8.315 s. These are deterministic stdio clients using real HTTP, SQLite and HydraDB. Startup and model inference are excluded from the per-job measurement.                                                                                                          |

Run `python3 cli.py demo --public-readonly --with-disclosure` to include the disclosure and a filtered acceptance receipt in a read-only demo. Authenticated private-program sync and webhook handling are exercised with provider fixtures. Each customer needs to validate its own program access during onboarding.

## How evidence moves

<img src="docs/architecture.svg" width="510" alt="Bounty metadata and CI observations enter HydraDB. Reviewers record decisions; coding agents read and complete scoped reviews via the handoff API and its SQLite lease store." />

1. **Map a report.** Read-only HackerOne or Bugcrowd intake matches a reviewed report to the organization's private inventory. A mapped case stores its provider ID, title, service, component, deployment revision and default owner. Unmapped reports are skipped. Attachments, researcher correspondence and payouts stay on the bounty platform. [Catalog example](examples/catalog.json)
2. **Record staging evidence.** Customer CI exports settings and can sign the exact manifest bytes with HMAC-SHA256. Ichnix compares the export to the service policy and records each check as passed, failed or inconclusive. The original finding still needs the team's independent verification. [Policy](examples/policy.json) · [Manifest](examples/staging.json)
3. **Retrieve context.** HydraDB links reports to versioned snapshots, component revisions, observations and feedback. Shared component revisions carry owner votes and prior CI results. Sparse character-trigram title vectors live on report nodes. Graph traversal selects up to 128 current candidates, and application code ranks up to five by cosine similarity. This relates wording and component history.
4. **Review the handoff.** A named worker claims one job, retrieves its case context, and completes or records a disposition. Unfinished leases expire. SQLite coordinates the lease; HydraDB stores the attributed outcome. Only human owner decisions affect the owner ranking. [Worker API](server.py) · [Graph implementation](graph.py)

## Run a persistent workspace

**Docker Compose, Linux amd64**

```sh
docker compose up --build --detach --wait
docker compose ps
```

Open `http://127.0.0.1:8765/console`. The initial operator token is in `/var/lib/ichnix/console-token` inside the console container. Retrieve it privately. Graph data, graph credentials and console state use separate named volumes. Preserve them across upgrades.

**Native installation**

After installing the dependencies and runtime above, run `scripts/start-hydra.sh` and `scripts/console.sh` in separate terminals. The launcher prints the local URL and private token file path. Use `python3 cli.py check-workspace` to verify graph authentication, trace rendering and configured stores.

**Named identities**

Set a separate identity registry and handoff store per organization. On a new native installation:

```sh
export ICHNIX_STATE_DIR="$HOME/.local/state/ichnix"
export ICHNIX_IDENTITY_DB="$ICHNIX_STATE_DIR/identities.sqlite3"
export ICHNIX_HANDOFF_DB="$ICHNIX_STATE_DIR/handoffs.sqlite3"
python3 cli.py storage init
python3 cli.py storage check
python3 cli.py identity issue operator --role review --out "$ICHNIX_STATE_DIR/operator.token"
python3 cli.py console --port 8765
```

Run the console in that environment with its port free. Registry mode authenticates named identities and disables shared tokens. Keep stores and credentials on private local storage owned by the service account. For remote teams, terminate HTTPS at your authenticated reverse proxy and restrict access to the origin. Remote HydraDB connections require HTTPS.

For a fresh named-identity Compose stack, use container paths consistently. Existing deployments should reuse their original paths and volumes.

```sh
export ICHNIX_IDENTITY_DB=/var/lib/ichnix/identities.sqlite3
export ICHNIX_HANDOFF_DB=/var/lib/ichnix/handoffs.sqlite3
docker compose build
docker compose run --rm console python3 cli.py storage init
docker compose run --rm console python3 cli.py identity issue operator --role review --out /var/lib/ichnix/operator.token
docker compose up --detach --wait
docker compose exec console python3 cli.py storage check
```

## Connect a team or coding agent

| Interface                   | Access                                                                                                                                                                     |
| --------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Local operator CLI          | Direct graph access under the host service account. Configure providers, imports, CI policy, identities and queues here.                                                   |
| Human console or remote CLI | A named `read` identity can inspect the workspace. A `review` identity can also decide ownership. Remote CLI requests reuse the console's HTTP authorization.              |
| Scoped worker MCP           | A named `worker` identity can claim and finish a leased job and read its case, evidence and recommendations. The worker process keeps lease tokens out of model responses. |
| Local read-only MCP         | A trusted host-side stdio process exposes `list_cases` and `get_case_context` for the organization's graph.                                                                |

From the operator host, issue a worker identity and queue an existing case. Use `owner-review` for ownership decisions or `evidence-review` once observations exist. Set `ICHNIX_AUTO_HANDOFFS=1` across intake, staging and console processes sharing the same store to queue reviews automatically.

```sh
python3 cli.py identity issue coding-worker --role worker --out "$ICHNIX_STATE_DIR/coding-worker.token"
python3 cli.py handoff enqueue CASE_ID owner-review
```

Deliver that credential privately to the worker machine. From the worker's checkout, generate a stdio client profile. The file references the credential by path.

```sh
python3 cli.py mcp-config --profile worker \
  --workspace-url https://workspace.example.org \
  --worker-token-file "$HOME/.config/ichnix/coding-worker.token" \
  --out ichnix-mcp.json
```

Use `--format codex --out ichnix-mcp.toml` for Codex. The session calls `claim_review`, `get_review_context` and `finish_review`. A human reviewer on another machine can use the following commands with a separately issued credential:

```sh
export ICHNIX_WORKSPACE_URL=https://workspace.example.org
export ICHNIX_USER_TOKEN_FILE="$HOME/.config/ichnix/reviewer.token"
python3 cli.py human cases
python3 cli.py human context CASE_ID
python3 cli.py human review CASE_ID TEAM --accept --request-id stable-review-id
```

The [operator manual](public/docs.html) covers provider credentials, signed delivery endpoints, catalog mapping, CI signatures, exit codes and graph ranking. Run `python3 cli.py --help` for all commands. Keep private reports, credentials, staging manifests and generated receipts outside the checkout.

## License and upstream references

Ichnix application code is [MIT licensed](LICENSE). HydraDB v0.4.0 is separately distributed under [Elastic License 2.0](licenses/HydraDB-ELv2.txt). Packaged libraries and fonts retain their own notices. [Runtime sources](licenses/Runtime-sources.txt) · [Font notices](public/fonts)

- [HydraDB query and administration guide](https://github.com/hydra-db/hydradb/blob/v0.4.0/USING-HYDRADB.md)
- [HackerOne customer API](https://api.hackerone.com/customer-reference/) and [webhook documentation](https://api.hackerone.com/webhooks/)
- [Bugcrowd API setup](https://docs.bugcrowd.com/api/getting-started/) and [webhook documentation](https://docs.bugcrowd.com/api/webhooks/)
- [Model Context Protocol stdio transport](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)
