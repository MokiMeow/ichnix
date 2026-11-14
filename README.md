# Ichnix

A self-hosted review workspace for product security teams. Import reviewed bounty report metadata, compare a staging configuration export to approved settings, and share the evidence and remediation handoff through a web console, CLI or read-only MCP server.

## Run the local demo

On Ubuntu 24.04 x86-64, with Python 3.12, Node 24 and pnpm 12 installed:

```sh
pnpm install --frozen-lockfile
python3 scripts/install-hydra.py
python3 demo.py
```

Open the loopback URL printed by the demo. Read the operator token from the indicated private file and enter it in the console. Choose IX-108 to see retention, redaction and audit evidence from a synthetic staging manifest. The demo starts its own HydraDB process and discards its database on exit. The installer verifies a pinned official HydraDB image and its layers. On other platforms, supply a compatible graph-node executable via HYDRA_BINARY with its runtime libraries installed.

## Persistent installation

Run these commands in separate terminals. Both services bind to loopback by default.

```sh
scripts/start-hydra.sh
scripts/console.sh
```

The default console address is http://127.0.0.1:8765. Database files and generated tokens live under the operator's private XDG state directory, in an ichnix subdirectory. Set ICHNIX_STATE_DIR to select another directory. Use a dedicated database and service account per organization. To connect to an existing instance use ICHNIX_HYDRA_URL, ICHNIX_HYDRA_TOKEN_FILE, ICHNIX_HYDRA_NAMESPACE, ICHNIX_HYDRA_GRAPH and ICHNIX_HYDRA_CELL. Remote database connections require HTTPS. Terminate public TLS at your authenticated reverse proxy and restrict origin access. The operator launcher generates one local reviewer credential. Start server.py directly to inject ICHNIX_REVIEW_TOKEN and optional ICHNIX_READ_TOKEN through a secret manager.

## Intake and staging evidence

Use a private inventory with the shape shown in examples/catalog.json. Supply read-only program credentials through your environment. The scope mapping connects report IDs or asset identifiers to a service, component, deployed revision and default owner. Records without a mapping are skipped. Researcher communication, attachments and rewards remain on the bounty platform.

```sh
python3 cli.py sync --program example --catalog private/catalog.json
python3 cli.py sync-bugcrowd --engagement example --catalog private/catalog.json
python3 cli.py context IX-108
python3 staging.py IX-108 --policy private/policy.json --manifest private/staging.json
```

HackerOne uses HACKERONE_API_USER and HACKERONE_API_TOKEN. For signed event intake, configure ICHNIX_WEBHOOK_SECRET, ICHNIX_CATALOG and ICHNIX_PROGRAM on the web service and send program events to /hooks/hackerone. Bugcrowd uses BUGCROWD_API_TOKEN in token-id:token-secret format. Its inventory uses the engagement code as program and target UUIDs or submission UUIDs as mapping keys. Provider contracts are exercised with fixtures; validate with a program owner before onboarding production data.

Stage checks run where the customer exports their manifest. The policy is a per-service map of approved scalar settings. Each observation references the deployment revision and run ID. Exit 0 means every expected setting matched, exit 1 means at least one failed or was missing, and exit 2 indicates invalid inputs or an unavailable service. The console renders captured observations with Pele. Reports imported through the webhooks and APIs do not trigger code execution.

## CLI and coding agents

Use python3 cli.py --help for operator commands. For an MCP client, configure a local stdio server with command python3 and arguments /absolute/path/to/ichnix/cli.py and mcp. Scope the process credentials to the organization's graph. The two agent tools are list_cases and get_case_context. Administrative review decisions are made through the console or CLI. The local process identity controls access to the CLI, graph credentials and staging files.

## Learning and validation

HydraDB stores reports, versioned snapshots, component revisions, evidence and owner reviews. Bounded graph queries retrieve other cases sharing the component revision. Owner ranking combines the catalog assignment with accepted and rejected reviews. Existing CI checks are ranked from distinct case snapshots, with failed controls weighted above passing ones. Repeated observations in one case do not multiply its vote. Inconclusive observations remain visible on the case.

```sh
python3 -m unittest discover -s tests -v
python3 eval_graph.py --clients 16 --requests 128
```

The evaluation uses a disposable real graph and six synthetic holdouts across three shared component revisions. It measures retrieval of the expected first CI check and remediation owner. It does not measure discovery of new vulnerabilities. The optional load phase measures context reads on this fixture.

Ichnix application code is MIT licensed. The bundled fonts retain their accompanying licenses. The graph runtime is separately distributed by HydraDB. TT Commons is used when available locally, with bundled DM Sans as fallback. Anonymous Pro supplies the monospaced text.
