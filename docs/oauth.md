# OAuth Setup — Claude.ai & ChatGPT Connectors

The OAuth endpoint (`/api/method/erpnext_mcp_native.api.handle_mcp_oauth`)
authenticates with standard Frappe OAuth 2.0 Bearer tokens. You create an OAuth
Client on your site, register it as a remote MCP connector in the AI product, and
log in as a real user who holds `System Manager` or `MCP User`.

## Discovery

Your site publishes standard authorization-server metadata:

```
GET https://your-site.example.com/.well-known/oauth-authorization-server
```

This returns the authorize/token/introspect/revoke endpoints (all under
`frappe.integrations.oauth2`), supported grant types (`authorization_code`,
`refresh_token`), PKCE (`S256`), and the dynamic-registration endpoint if your
Frappe build exposes one. Claude.ai and ChatGPT both read this document when you
add the connector.

## ChatGPT (connector flow)

1. In ChatGPT: Settings → Connectors → **Add connector** (or your org's developer
   connector flow). Point it at
   `https://your-site.example.com/api/method/erpnext_mcp_native.api.handle_mcp_oauth`.
2. ChatGPT performs dynamic client registration against your site if available;
   otherwise create the OAuth Client manually (below) and paste the client id/secret
   into the connector config.
3. Complete the consent screen by logging in as a user who holds `MCP User` or
   `System Manager`.

## Claude.ai (remote MCP connector)

1. In Claude.ai: Settings → Connectors → **Add connector**, URL
   `https://your-site.example.com/api/method/erpnext_mcp_native.api.handle_mcp_oauth`.
2. Create an OAuth Client on your site with redirect URIs
   `https://claude.ai/api/mcp/auth_callback` and
   `https://claude.com/api/mcp/auth_callback` (newline-separated), grant type
   `Authorization Code`, response type `Code`, scopes `all openid`.
3. Authorize as a `MCP User`/`System Manager` user.

## Creating the OAuth Client (bench console)

```python
bench --site your-site.example.com console
```
```python
c = frappe.get_doc({
    "doctype": "OAuth Client",
    "app_name": "MCP Remote Connectors",
    "scopes": "all openid",
    "grant_type": "Authorization Code",
    "response_type": "Code",
    "redirect_uris": "https://claude.ai/api/mcp/auth_callback\nhttps://claude.com/api/mcp/auth_callback",
    "default_redirect_uri": "https://claude.ai/api/mcp/auth_callback",
})
c.insert(ignore_permissions=True)
frappe.db.commit()
print(c.name, c.client_secret)  # client_id = c.name
```

## Traps — read before debugging

- **`client_id` is the OAuth Client doctype's `name`, and Frappe autonames it.**
  You cannot choose the client_id when creating a client; you create first, then
  read the generated name into the connector config. The only way to force a
  specific id (rarely needed — see the next trap) is setting `d.name = "<id>"` with
  `d.flags.name_set = True` before insert.

- **ChatGPT caches the client_id it obtained from dynamic registration.** If your
  site's data is later restored from a backup or reset, the OAuth Client record
  behind that cached id may no longer exist — and authorize then fails with
  `{"error":"invalid_request","description":"Invalid client_id parameter value."}`.
  Creating a *new* client does not help, because ChatGPT keeps sending the *old*
  id. The fix is to recreate a client **under the exact cached name** (via the
  `flags.name_set` force above), after which authorize succeeds again.

- **Redirect URIs are per-connector, and products generate them per connector
  instance.** Recreating the connector in ChatGPT changes its callback path
  (`/connector/oauth/<id>`), which invalidates the previously registered
  redirect_uri. If you recreate a connector, update the OAuth Client's redirect URIs
  to match — for ChatGPT also keep
  `https://chatgpt.com/connector_platform_oauth_redirect` in the list.

- **Probing `/authorize` with curl is non-diagnostic.** Frappe redirects anonymous
  requests to `/login` *before* validating `client_id`, so a good id and a bad id
  look identical from the outside — you'll see a login redirect either way. To test
  a client_id, log in first (session cookie) or inspect the OAuth Client list on the
  site. Don't burn an hour on curl round-trips that cannot distinguish the cases.
