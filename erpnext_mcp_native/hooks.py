app_name = "erpnext_mcp_native"
app_title = "ERPNext MCP (Native)"
app_publisher = "The erpnext-mcp-native Authors"
app_description = (
    "In-process MCP server for ERPNext: 16 read tools over JSON-RPC, "
    "OAuth 2.0 (Claude.ai/ChatGPT) and API-key (n8n) auth, usage telemetry."
)
app_email = ""
app_license = "mit"

# Frappe concatenates auth_hooks across all installed apps' hooks.py
# (frappe/auth.py iterates frappe.get_hooks("auth_hooks")). This makes
# "Bearer <api_key>:<api_secret>" acceptable site-wide for MCP clients.
auth_hooks = ["erpnext_mcp_native.auth.validate_bearer_api_key"]
