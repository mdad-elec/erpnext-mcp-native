# n8n / Automation Setup

The API-key flavour is built for headless callers: n8n, scripts, cron, any HTTP
client. One endpoint, JSON-RPC in, JSON-RPC out.

## Endpoint

```
POST https://your-site.example.com/api/method/erpnext_mcp_native.api.handle_mcp
```

## Authentication headers

Any of these forms carries the `api_key:api_secret` pair (from
`erpnext_mcp_native.setup.setup_mcp`):

| Header | Value |
|---|---|
| `X-Frappe-API-Key` | `<key>:<secret>` — preferred; bypasses Frappe's auth middleware cleanly |
| `X-API-Key` | `<key>:<secret>` — alternative |
| `Authorization` | `Bearer <key>:<secret>` or `token <key>:<secret>` |

## Quick check (curl)

```bash
curl -s -X POST https://your-site.example.com/api/method/erpnext_mcp_native.api.handle_mcp \
  -H "X-Frappe-API-Key: <key>:<secret>" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

A `200` with a `result.tools` array means the pair works and the user has the
required role. `401` means the pair is wrong or the secret was rotated;
`403` means the user lacks `System Manager` / `MCP User`.

Calling a tool:

```bash
curl -s -X POST https://your-site.example.com/api/method/erpnext_mcp_native.api.handle_mcp \
  -H "X-Frappe-API-Key: <key>:<secret>" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call",
       "params":{"name":"get_stock_balance","arguments":{"warehouse":"Stores - FC"}}}'
```

Tool results arrive in `result.structuredContent`; check its `success` field — a
`false` there is a tool-level error with a `error`/`remedy` message, not a transport
failure.

## n8n HTTP Request node recipe

One node per MCP call:

1. **Method:** `POST`
2. **URL:** `https://your-site.example.com/api/method/erpnext_mcp_native.api.handle_mcp`
3. **Authentication:** *Generic Credential Type* → *Header Auth*, with
   - Name: `X-Frappe-API-Key`
   - Value: `<key>:<secret>` (store it once as a credential, reference it everywhere)
4. **Body:** *JSON*, e.g.
   ```json
   {
     "jsonrpc": "2.0",
     "id": 1,
     "method": "tools/call",
     "params": {
       "name": "get_outstanding_invoices",
       "arguments": {"overdue_only": true, "limit": 10}
     }
   }
   ```
5. Read results from `{{ $json.result.structuredContent }}`.

Tips:

- `initialize` and `tools/list` need no arguments — good nodes to smoke-test the
  credential.
- JSON-RPC notifications (no `id`) get `202 Accepted` with an empty body — that's
  the MCP spec, not an error.
- The API-key flavour responds to every method with `id` present; if you write an
  expression that drops the `id`, you'll get 202s instead of results.
