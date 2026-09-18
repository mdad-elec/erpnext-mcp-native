# Tool Reference

All 16 tools are read-only. They are called over JSON-RPC:

```json
{"jsonrpc": "2.0", "id": 1, "method": "tools/call",
 "params": {"name": "get_stock_balance", "arguments": {"warehouse": "Stores - FC"}}}
```

Parameter lists below come straight from the tool signatures. When in doubt, the
`tools/list` response carries the authoritative inputSchema for each tool — **read
`tools/list`'s inputSchema before declaring a tool broken.** Several tools accept
arguments whose obvious-sounding names are wrong (see [Pitfalls](#pitfalls)); the
schema, not intuition, is the contract.

## Introspection & search

### `get_system_health`
One-call liveness and load picture: database connectivity, active users, doctype
count, memory/CPU. No arguments. Ideal first call after install.

### `search_doctypes(search_term="", module=None, include_custom=None, limit=100, include_counts=True)`
Find doctypes by name (optionally narrowed to a module), with approximate record
counts. Use it to discover the exact doctype name before querying it.

### `get_doctype_info(doctype, include_sample_data=False, …)`
Schema for one doctype: fields, types, links. Set `include_sample_data` to eyeball
real values when learning a doctype's shape.

### `analyze_doctype(doctype, analysis_type="summary", filters=None)`
Deeper analysis than `get_doctype_info` — field distributions and structure, with
`analysis_type` selecting the depth and optional `filters` scoping the data.

### `global_search(query, doctypes=None, limit=20)`
Full-text search across business doctypes. `doctypes` is a **comma-separated string**
(e.g. `"Sales Invoice,Customer"`); omit it to search the common business set.

### `get_entity_counts(doctype, filters=None)`
Record count for one doctype, optionally filtered. Cheap smoke-test and drift check
(e.g. before/after a data migration).

## Query

### `query_doctype(doctype, filters=None, fields=None, limit=20, start=0, order_by=None, date_range=None, include_count=True, ignore_permissions=True, …)`
The generic query tool. `filters` uses Frappe's operator form —
`{"grand_total": [">", 1000], "name": ["like", "%ABC%"]}`. `date_range` takes
`{"from": …, "to": …}`. `ignore_permissions=True` is the default because the
endpoint already authenticated and role-gated the caller. `run_report` is the
opposite: it enforces full Frappe per-doctype permissions, which is why the
[runbook](runbook.md) pairs the `MCP User` role with read-only doctype grants.

### `query_with_aggregation(doctype, aggregations, group_by=None, filters=None, having=None, limit=100)`
Aggregations with optional grouping. `aggregations` maps field → SQL function;
`having` filters aggregated groups. Handles multi-word doctypes correctly (the table
name keeps its space). When the SQL path cannot run, the response says so explicitly
(`degraded` + `degraded_reason` + `attempted_sql`) rather than silently returning
group keys without values.

## Business reports

### `get_sales_summary(period="this_month", group_by="customer", from_date=None, to_date=None, customer=None, item_group=None, limit=50)`
Sales totals grouped by customer/item/item group over a named period or explicit
date range.

### `get_stock_balance(item_code=None, warehouse=None, item_group=None, show_zero_stock=False, limit=100)`
Current stock by item and warehouse; filter to one item, one warehouse, or an item
group. `show_zero_stock=False` hides empty bins by default.

### `get_outstanding_invoices(party_type="Customer", party=None, overdue_only=False, min_amount=0, limit=100)`
Unpaid invoices for customers (`party_type="Customer"`) or suppliers
(`party_type="Supplier"`), with `overdue_only` and amount thresholds.

### `get_profit_analysis(period="this_month", group_by="item", from_date=None, to_date=None, limit=50)`
Gross profit by item, customer, or item group.

### `get_pending_approvals(user=None, doctype=None, limit=50)`
Documents sitting in workflow states awaiting action, optionally for a specific user
(defaults to the current user) or doctype. Response includes `partial` and
`skipped_doctypes` — doctypes whose workflow state cannot be queried cleanly are
skipped and named rather than failing the whole call.

### `get_linked_documents(doctype, name, link_types=None)`
Everything linked to one document — the impact map before you touch a record.

### `get_document_pdf(doctype, name, print_format=None)`
Download URL for a document's PDF in the given print format.

### `run_report(report_name, filters=None, limit=100)`
Execute any ERPNext report — `"General Ledger"`, `"Stock Balance"`,
`"Accounts Receivable"`, script reports, query reports. `filters` is a **JSON string**
of the report's own filter dict. Script reports with `prepared_report=1` are executed
inline (see [runbook](runbook.md)); the response distinguishes *did not execute*
(`degraded`) from *executed and matched nothing*, and reports `total_rows` vs
`returned_rows` + `truncated` when `limit` bites.

## Pitfalls

These argument shapes have all bitten real callers. The inputSchema is right;
the intuition is wrong.

- **`global_search` takes `query`**, not `text`. `{"text": "invoice"}` is silently
  not what you think — the parameter is named `query`.
- **`query_with_aggregation` takes `aggregations` as a dict**, e.g.
  `{"grand_total": "sum"}` (field → function shorthand), and **`group_by` as a
  list** (`["status"]`). A string `group_by` is coerced, but dict-vs-string swaps on
  `aggregations` produce confusing type errors — send the right types.
- **`get_entity_counts` takes `doctype` (singular)**, not `doctypes`.
- **`query_doctype` without explicit `fields`** auto-selects only real database
  columns — virtual fields and child-table fields are skipped, so a query for such a
  field returns nothing rather than erroring. Pass explicit `fields` when you need a
  specific column.
- **`run_report` returns `error_code: "REPORT_PERMISSION"` with a `remedy` string**
  when the connector user lacks the report's required roles. This is a permission
  boundary, not a bug — see the [runbook](runbook.md) for the least-privilege model
  that makes reports work without handing out System Manager.
