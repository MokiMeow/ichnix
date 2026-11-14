# Ichnix

Ichnix is a self-hosted review workspace for product security teams. It maps reviewed bounty metadata to deployed components, compares signed CI observations to team policy, and gives coding agents a scoped remediation handoff.

## Verify the workflow without a bounty account

On Ubuntu 24.04 x86-64, install Python 3.12, Node 24 and pnpm 12, then run:

```sh
pnpm install --frozen-lockfile
python3 scripts/install-hydra.py
python3 cli.py acceptance
```

The command reads one [public HackerOne disclosure](https://hackerone.com/reports/2236133) and maps it to a disposable local workspace. It measures three HTTP headers on the running local console, signs the manifest, verifies it, and reads the stored evidence through a named MCP worker. Its receipt includes a timestamp and manifest digest. These observations concern the local console. The [curl advisory](https://curl.se/docs/CVE-2023-46219.html) describes the original report.

Start the app to browse the setup and provider guides at `/docs`. [Contributing](CONTRIBUTING.md) · [agent instructions](AGENTS.md) · [security reporting](SECURITY.md).

## Run the local demo

On Ubuntu 24.04 x86-64, with Python 3.12, Node 24 and pnpm 12 installed:

```sh
pnpm install --frozen-lockfile
python3 scripts/install-hydra.py
python3 demo.py
```

Open the loopback URL printed by the demo. Read the operator token from the indicated private file and enter it in the console. Choose IX-108 to see retention, redaction and audit evidence from a synthetic staging manifest. The demo starts its own HydraDB process and discards its database on exit. The installer verifies a pinned official HydraDB image and its layers. On other platforms, supply a compatible graph-node executable via HYDRA_BINARY with its runtime libraries installed.

To share the public disclosure alongside three synthetic cases, run the live acceptance workflow at startup and tunnel the read-only console. Omit `--with-disclosure` for an offline synthetic demo. It runs the evaluation separately from the sample cases. Visitors can read case context, audit history, diagrams and the measured learning curve. The demo rejects writes, including webhooks.

```sh
python3 demo.py --public-readonly --with-disclosure --port 18766
# In another terminal
cloudflared tunnel --url http://127.0.0.1:18766
```

## Persistent installation

With Docker Compose on an amd64 host, run `docker compose up --build --detach --wait`, open `http://127.0.0.1:8765/console` and retrieve your operator token privately from `/var/lib/ichnix/console-token` in the console container. The stack uses named volumes for graph data, graph credentials and console state. Preserve those volumes across upgrades.

For a native installation, run these commands in separate terminals. Both services bind to loopback by default.

```sh
scripts/start-hydra.sh
scripts/console.sh
```

The default console address is http://127.0.0.1:8765. Database files and generated tokens live under the operator's private XDG state directory, in an ichnix subdirectory. Set ICHNIX_STATE_DIR to select another directory. Use a dedicated database and service account per organization. To connect to an existing instance use ICHNIX_HYDRA_URL, ICHNIX_HYDRA_TOKEN_FILE, ICHNIX_HYDRA_NAMESPACE, ICHNIX_HYDRA_GRAPH and ICHNIX_HYDRA_CELL. Remote database connections require HTTPS. Terminate public TLS at your authenticated reverse proxy and restrict origin access. The operator launcher generates one local reviewer credential. Start server.py directly to inject ICHNIX_REVIEW_TOKEN and optional ICHNIX_READ_TOKEN through a secret manager.

For individual identities, set ICHNIX_IDENTITY_DB to a private file on the console host. Issue one credential per user or worker. The registry stores hashes and supports immediate revocation. A reader can view the organization workspace, a reviewer can also submit owner decisions, and a worker can claim handoffs and read the case context covered by its active lease. Registry mode disables shared token access. Case history records the reviewer, decision and UTC timestamp. HTTP clients can supply an Idempotency-Key on a review retry.

```sh
export ICHNIX_IDENTITY_DB="$HOME/.local/state/ichnix/identities.sqlite3"
python3 credentials.py issue alice --role review --out "$HOME/.local/state/ichnix/alice.token"
python3 credentials.py list
python3 server.py
# When access ends
python3 credentials.py revoke alice
```

## Intake and staging evidence

Use a private inventory with the shape shown in examples/catalog.json. Supply read-only program credentials through your environment. The scope mapping connects report IDs or asset identifiers to a service, component, deployed revision and default owner. Records without a mapping are skipped. Researcher communication, attachments and rewards remain on the bounty platform.

```sh
python3 cli.py sync --program example --catalog private/catalog.json
python3 cli.py sync-bugcrowd --engagement example --catalog private/catalog.json
python3 cli.py context IX-108
python3 staging.py IX-108 --policy private/policy.json --manifest private/staging.json
```

HackerOne uses HACKERONE_API_USER and HACKERONE_API_TOKEN. For signed event intake, configure ICHNIX_WEBHOOK_SECRET, ICHNIX_CATALOG and ICHNIX_PROGRAM on the web service and send program events to /hooks/hackerone. Bugcrowd uses BUGCROWD_API_TOKEN in token-id:token-secret format. Its inventory uses the engagement code as program and target UUIDs or submission UUIDs as mapping keys. For Bugcrowd event intake, configure ICHNIX_BUGCROWD_WEBHOOK_SECRET, ICHNIX_BUGCROWD_CATALOG, ICHNIX_BUGCROWD_ENGAGEMENT and the read-only API token. Send submission.created and submission.updated to /hooks/bugcrowd. The receiver verifies the digest and fetches current submission metadata by UUID before checking the engagement and inventory. Provider contracts are exercised with fixtures; validate with a program owner before onboarding production data.

Stage checks run where the customer exports their manifest. The policy is a per-service map of approved scalar settings. Each observation references the deployment revision and run ID. Exit 0 means every expected setting matched, exit 1 means at least one failed or was missing, and exit 2 indicates invalid inputs or an unavailable service. The console renders captured observations with Pele. For signed CI exports, set ICHNIX_CI_HMAC_KEY_FILE on CI and intake to a private file containing the same 32–1024 random bytes. Run `python3 staging.py --sign --manifest private/staging.json --out private/staging.sig` in CI, and add `--signature private/staging.sig` to the intake command. With the key configured, intake rejects missing or invalid signatures. Evidence carries the manifest digest and verification method. The check establishes whether observed settings match policy for the declared revision. The original bounty finding still needs the team's independent review.

## CLI and coding agents

Use python3 cli.py --help for operator commands. For an MCP client, configure a local stdio server with command python3 and arguments /absolute/path/to/ichnix/cli.py and mcp. Scope the process credentials to the organization's graph. The two local read tools are list_cases and get_case_context. Administrative review decisions are made through the console or CLI. The local process identity controls access to the CLI, graph credentials and staging files.

Use `python3 cli.py check-workspace` to exercise a read query against the configured HydraDB graph and render a Pele trace. Generate a worker profile with `python3 cli.py mcp-config --profile worker --workspace-url https://workspace.example.org --worker-token-file /private/worker.token --out /private/mcp.json`. For Codex, add `--format codex` and select a new TOML output path. These files reference private token paths.

## Agent handoffs

Set ICHNIX_AUTO_HANDOFFS=1 in the importing and staging processes to queue owner review after report intake and evidence review after completed CI checks. They must use the console host's state directory and graph configuration. Supply `--run-id` on staging intake to retry the same CI run. To queue a specific case manually:

```sh
python3 handoff_cli.py enqueue IX-108 evidence-review
```

A handoff identifies the service, component, revision and current review material. Repeated enqueues for that material return the existing job. A single console host coordinates leases in a private SQLite file. Expired work can be claimed again and stale lease tokens cannot complete it. Keep the queue on the console host's local disk. Each agent should check the case revision and current evidence before acting on a handoff.

To connect a remote coding agent, issue a named worker credential, deliver its token file privately, and configure the agent's stdio MCP command as `python3 /path/to/ichnix/cli.py mcp --worker`. Set ICHNIX_WORKSPACE_URL to the private HTTPS console origin and ICHNIX_WORKER_TOKEN_FILE to the token file on the worker. Agent machines need the Ichnix client and Python. The three worker tools are claim_review, get_review_context and finish_review. The agent gets case data through the lease. The console retains all HydraDB credentials.

## Learning and validation

HydraDB stores reports, versioned snapshots, component revisions, evidence and owner reviews. Graph traversals retrieve other cases sharing the component revision. Owner ranking combines the catalog assignment with accepted and rejected reviews. Existing CI checks are ranked from distinct case snapshots, with failed controls weighted above passing ones. Repeated observations in one case do not multiply its vote. Inconclusive observations remain visible on the case. For related reports, HydraDB stores normalized sparse title vectors as graph properties. A component-revision traversal selects up to 128 current reports and the application ranks them by lexical cosine similarity, returning up to five. This compares wording, not the underlying cause.

```sh
python3 -m unittest discover -s tests -v
python3 eval_graph.py --clients 16 --requests 128
```

The evaluation uses a disposable real graph and six synthetic holdouts across three shared component revisions. It measures retrieval of the expected first CI check and remediation owner. It does not measure discovery of new vulnerabilities. The optional load phase measures context reads on this fixture.

Run `python3 cli.py bench-workers --workers 100 --jobs 100` to compare one worker with 100 independent, separately credentialed MCP clients on equal job counts. The benchmark records handshake time, throughput, per-job latency and persisted ownership. The jobs are synthetic and the transport, database and processes are real.

Ichnix application code is MIT licensed. The bundled fonts retain their accompanying licenses. The graph runtime is separately distributed by HydraDB. TT Commons is used when available locally, with bundled DM Sans as fallback. Anonymous Pro supplies the monospaced text.

## References

- [HydraDB runtime and query guide](https://github.com/hydra-db/hydradb/blob/v0.4.0/USING-HYDRADB.md) and [Elastic License 2.0](https://github.com/hydra-db/hydradb/blob/v0.4.0/LICENSE)
- [HackerOne customer API](https://api.hackerone.com/customer-resources/) and [webhook signatures and payloads](https://api.hackerone.com/webhooks/)
- [Bugcrowd API setup](https://docs.bugcrowd.com/api/getting-started/) and [webhook authentication](https://docs.bugcrowd.com/api/webhooks/)
- [MCP stdio transport](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)
