# Runbook — Permissions, Telemetry, Rotation

Operational rules for running the connector in production. Every item below was
learned from a real incident; none of them is theoretical.

## Least-privilege read model

The connector user needs `MCP User` (created by setup) plus **read-only access to the
doctypes its tools touch**. The pattern that works:

- Grant read via **read-only Custom DocPerm rows** — `read = 1`, `report = 1`,
  `export = 1`, everything else (`write/create/delete/submit/cancel/amend`) `0`.
- **Reports read other doctypes internally.** A report tool call is not one doctype's
  permission — Accounts Receivable fails with `Insufficient Permission for Journal
  Entry` until Journal Entry, Payment Entry, and Payment Ledger Entry are also
  readable. When a report 403s, grant the doctypes named in the error, not System
  Manager.
- Whitelisting a report via `Has Role` has **no effect** if a `Custom Role` row
  exists for that report — Frappe's `Report.is_permitted()` replaces the role list
  with the custom one. Append your role there too.
- `run_report` denials come back as `error_code: "REPORT_PERMISSION"` with a
  `remedy` string naming what to grant. That boundary is deliberate: broadening the
  connector's read scope is an owner decision, not a default.

### ⚠️ Never insert a Custom DocPerm row without `copy_perms`

In Frappe, the existence of **any** `Custom DocPerm` row for a doctype makes **all**
of that doctype's standard permission rows be ignored. Inserting a single lone
read-only row therefore silently revokes every other role's access — including
System Manager's on doctypes that were never customised before. Always copy first:

```python
frappe.permissions.copy_perms(doctype)   # replicate standard rows into Custom DocPerm
# ...then append your MCP row, then:
frappe.clear_cache()
```

Detection query for the damage pattern (doctypes whose only custom rows belong to
one role while standard rows exist):

```sql
select cdp.parent from `tabCustom DocPerm` cdp
group by cdp.parent
having count(*) = sum(cdp.role = 'MCP User')
   and (select count(*) from tabDocPerm dp where dp.parent = cdp.parent) > 0;
```

## ⚠️ Never grant a role via `User.save()`

Saving the User doc **wipes `api_secret`** — Frappe does not load password fields
onto the document, and the save writes back their empties. One role grant through
`User.save()` 401s every API-key consumer until the secret is restored. Grant roles
by inserting the `Has Role` child row directly:

```python
frappe.get_doc({
    "doctype": "Has Role",
    "parent": "mcp@example.com", "parenttype": "User", "parentfield": "roles",
    "role": "MCP User",
}).insert(ignore_permissions=True)
frappe.clear_cache(user="mcp@example.com")
```

This app's `setup_mcp` already does it this way; keep the pattern in any custom
scripts.

## Prepared reports must run inline

Script Reports with `prepared_report=1` are normally queued as background Prepared
Reports. A synchronous MCP caller can never poll for the background job — the report
would return **empty columns and rows immediately**, which looks exactly like
"executed and matched nothing" while actually having executed nothing. This app
always passes `ignore_prepared_report=True` so reports run inline. If you extend the
code, preserve that flag, and keep the response's distinction between `degraded`
(did not execute) and a genuine empty result.

## Telemetry

Every tool call logs one JSON line to `logs/mcp_usage.log` inside the bench:

```json
{"tool": "query_doctype", "user": "mcp@example.com", "ms": 190, "ok": true,
 "args": ["doctype", "fields"], "error": ""}
```

- `args` lists argument **names** only — never values, so no document data leaks
  into logs.
- Failures land in the site's Error Log too, but successes exist **only** here:
  without this file you cannot answer "which tools do the AI products actually
  use, how often, how fast".
- It is on by default and costs one line per call. Ship a logrotate rule if volume
  grows.

## Credential rotation

Rotating the API key/secret breaks every consumer that holds the old pair —
silently, on the next call. A rotation without its dependent list is an outage
scheduled.

1. Generate a new secret and set it:
   ```python
   from frappe.utils.password import set_encrypted_password
   set_encrypted_password("User", "mcp@example.com", "<new-secret>", "api_secret")
   frappe.db.commit()
   ```
2. **Enumerate dependents before you rotate**: the api_key is plaintext in
   `tabUser.api_key`, so `grep -rl <api_key>` across your config trees (automation
   platform configs, agent configs, cron scripts, `.env` files) finds every holder —
   including ones nobody documented.
3. Update each consumer with the new pair, then verify each one functionally
   (one `tools/list` per consumer), not by absence of complaints.
4. Keep backups of any config you edit (`*.bak_<date>`), and note that stale backup
   files carrying dead secrets are the usual reason a later grep looks alarming.

The user's `api_key` can stay constant across secret rotations; only the secret
changes.
