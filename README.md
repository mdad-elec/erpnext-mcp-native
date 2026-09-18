# erpnext-mcp-native

An **in-process MCP server for ERPNext** — install it as a Frappe app and Claude,
ChatGPT, and n8n get 16 read-only ERP tools over JSON-RPC, authenticated by OAuth 2.0
or a simple API key. No separate process, no REST proxying: tools run inside your
bench with direct ORM access and your site's own permission system.

## Why this instead of…?

- **vs. [frappe/mcp](https://github.com/frappe/mcp)** — that's the official *framework*
  for building MCP servers into Frappe apps. This is a ready-made ERPNext toolset:
  install and go. (Building this app on that framework is a possible future direction.)
- **vs. standalone MCP servers** (TypeScript/Python processes calling ERPNext over
  REST) — no extra process to run, no second credential to manage, and per-user
  authorization is enforced by Frappe itself, not re-implemented.

## Install

Tested on **ERPNext/Frappe v16** (v15 untested).

```bash
bench get-app --skip-assets https://github.com/mdad-elec/erpnext-mcp-native
bench --site your-site.example.com install-app erpnext_mcp_native
bench --site your-site.example.com execute erpnext_mcp_native.setup.setup_mcp
```

**Why `--skip-assets`:** the app is pure Python with no frontend assets, and on
bench 5.29.1 the post-clone asset build runs before the app is registered —
frappe's bundler cannot resolve an unregistered app and the install aborts. The
flag skips that step; nothing is lost for an asset-less app.

**bench 5.29.1 registration gap:** `get-app --skip-assets` may write
`sites/apps.json` but not append `sites/apps.txt`. If `install-app` then errors
with the app "not in apps.txt", append the line `erpnext_mcp_native` to
`sites/apps.txt` yourself and re-run `install-app`. ⚠️ First check the file ends with a
newline — appending to a file whose last line lacks one fuses two app names
onto a single line (e.g. `paymentserpnext_mcp_native`), which breaks the
bench's app list.

The last step creates the `MCP User` role and a connector user, **generates** an
API key/secret pair, and prints it exactly once — the secret is stored encrypted and
cannot be shown again. It is idempotent: re-running never rotates existing
credentials. Optional: pass a connector email with
`bench --site <site> execute erpnext_mcp_native.setup.setup_mcp --args '["mcp@your-domain.example"]'`.

## Endpoints

| Flavour | URL | Auth |
|---|---|---|
| API key (n8n, curl) | `https://your-site.example.com/api/method/erpnext_mcp_native.api.handle_mcp` | `X-Frappe-API-Key: <key>:<secret>` (also `Authorization: Bearer <key>:<secret>`) |
| OAuth 2.0 (Claude.ai, ChatGPT) | `https://your-site.example.com/api/method/erpnext_mcp_native.api.handle_mcp_oauth` | Bearer token from your site's Frappe OAuth |

Access requires role `System Manager` or `MCP User`.

Quick check:

```bash
curl -s -X POST https://your-site.example.com/api/method/erpnext_mcp_native.api.handle_mcp \
  -H "X-Frappe-API-Key: <key>:<secret>" -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

`python3 scripts/smoke.py https://your-site.example.com <key>:<secret>` verifies a full
install (handshake, 16-tool parity, one call per tool class).

## Tools (16)

`get_system_health` · `get_entity_counts` · `search_doctypes` · `get_doctype_info` ·
`analyze_doctype` · `global_search` · `query_doctype` · `query_with_aggregation` ·
`get_sales_summary` · `get_stock_balance` · `get_outstanding_invoices` ·
`get_profit_analysis` · `get_pending_approvals` · `get_linked_documents` ·
`get_document_pdf` · `run_report` — see [docs/tools.md](docs/tools.md) for schemas
and argument-shape pitfalls.

## Docs

- [docs/tools.md](docs/tools.md) — tool reference + argument pitfalls
- [docs/oauth.md](docs/oauth.md) — Claude.ai / ChatGPT connector setup
- [docs/n8n.md](docs/n8n.md) — n8n / automation setup
- [docs/runbook.md](docs/runbook.md) — permissions model, telemetry, credential rotation

## Telemetry

Every tool call is logged as one JSON line to `logs/mcp_usage.log` inside the bench:
tool, user, duration ms, ok/fail, error text. Absent-by-default nowhere — it's on.

## Related

[**Deskpilot**](https://github.com/mdad-elec/deskpilot) — the same problem approached
from the other side. This server points ERPNext *outward*, so an external agent can
reach your data. Deskpilot puts an assistant *inside* the Desk, where it drives the
screen and fills the form in front of the user. They compose: run both and the same
ERP answers an agent over MCP and a user at their keyboard.

## License

MIT.
