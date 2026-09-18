#!/usr/bin/env python3
"""Smoke harness: initialize handshake, tools/list parity, one call per tool class.

Usage: python3 smoke.py https://your-site.example.com <api_key>:<api_secret>
Exit 0 only if all checks pass. Stdlib only — runs anywhere, including in-container.
"""
import json
import sys
import urllib.error
import urllib.request

EXPECTED_16 = {
    "query_doctype", "query_with_aggregation", "get_doctype_info",
    "search_doctypes", "analyze_doctype", "get_system_health",
    "get_entity_counts", "get_sales_summary", "get_stock_balance",
    "get_outstanding_invoices", "get_profit_analysis", "run_report",
    "get_pending_approvals", "get_linked_documents", "global_search",
    "get_document_pdf",
}

def main():
    if len(sys.argv) != 3:
        print("usage: smoke.py <base_url> <api_key:api_secret>", file=sys.stderr)
        return 2
    base, creds = sys.argv[1].rstrip("/"), sys.argv[2]
    url = base + "/api/method/erpnext_mcp_native.api.handle_mcp"

    def rpc(method, params=None):
        body = json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
        ).encode()
        req = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": "application/json",
                     "X-Frappe-API-Key": creds},
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())

    failures = []

    try:
        init = rpc("initialize")
        name = init.get("result", {}).get("serverInfo", {}).get("name")
        print(f"initialize: {name}")
        if name != "erpnext-mcp-native":
            failures.append(f"serverInfo.name = {name!r}, expected 'erpnext-mcp-native'")

        tools = rpc("tools/list").get("result", {}).get("tools", [])
        names = {t.get("name") for t in tools}
        print(f"tools/list: {len(tools)} tools")
        if names != EXPECTED_16:
            missing, extra = EXPECTED_16 - names, names - EXPECTED_16
            failures.append(f"tool set mismatch — missing: {sorted(missing)} extra: {sorted(extra)}")

        calls = [
            ("get_system_health", {}),
            ("get_entity_counts", {"doctype": "Item"}),
            ("global_search", {"query": "test", "limit": 5}),
            ("query_doctype", {"doctype": "Company", "fields": ["name"]}),
            ("query_with_aggregation",
             {"doctype": "Item", "aggregations": {"name": "count"}}),
        ]
        for tool, args in calls:
            r = rpc("tools/call", {"name": tool, "arguments": args})
            ok = bool(r.get("result", {}).get("structuredContent", {}).get("success"))
            print(f"{tool}: {'PASS' if ok else 'FAIL'}")
            if not ok:
                failures.append(tool + ": " + json.dumps(r)[:300])
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.read().decode()[:300]}")
        return 1
    except Exception as e:
        print(f"ERROR: {e!r}")
        return 1

    if failures:
        print("SMOKE FAILED")
        for f in failures:
            print(" -", f)
        return 1
    print("SMOKE PASSED")
    return 0

if __name__ == "__main__":
    sys.exit(main())
