import frappe
from frappe.utils.password import set_encrypted_password, get_decrypted_password


def setup_mcp(connector_email="mcp@example.com"):
    """Idempotent bootstrap: MCP User role + connector user + generated API credentials.

    Prints the api_key:api_secret pair exactly once, at generation time only —
    the secret is stored encrypted and cannot be shown again. Re-running never
    rotates existing credentials.
    """
    if "erpnext" not in frappe.get_installed_apps():
        raise RuntimeError(
            "erpnext is not installed on this site — erpnext-mcp-native is an "
            "ERPNext app and cannot function without it."
        )

    out = []

    if not frappe.db.exists("Role", "MCP User"):
        r = frappe.new_doc("Role")
        r.role_name = "MCP User"
        r.desk_access = 0
        r.insert(ignore_permissions=True)
        out.append("role_created")

    if not frappe.db.exists("User", connector_email):
        u = frappe.new_doc("User")
        u.email = connector_email
        u.first_name = "MCP Connector"
        u.user_type = "System User"
        u.send_welcome_email = 0
        u.flags.no_welcome_mail = True
        u.insert(ignore_permissions=True)
        out.append("user_created")

    # Role grant via direct Has Role child insert. NEVER frappe.get_doc("User",...).save():
    # saving the User doc wipes api_secret (password fields aren't loaded on the doc).
    if not frappe.db.exists(
        "Has Role",
        {"parent": connector_email, "parenttype": "User", "role": "MCP User"},
    ):
        frappe.get_doc(
            {
                "doctype": "Has Role",
                "parent": connector_email,
                "parenttype": "User",
                "parentfield": "roles",
                "role": "MCP User",
            }
        ).insert(ignore_permissions=True)
        frappe.clear_cache(user=connector_email)
        out.append("role_assigned")

    existing_key = frappe.db.get_value("User", connector_email, "api_key")
    existing_secret = get_decrypted_password(
        "User", connector_email, "api_secret", raise_exception=False
    )
    if existing_key and existing_secret:
        out.append("api_creds_kept")
    else:
        api_key = frappe.generate_hash(length=16)
        api_secret = frappe.generate_hash(length=32)
        frappe.db.set_value("User", connector_email, "api_key", api_key)
        set_encrypted_password("User", connector_email, api_secret, "api_secret")
        out.append("api_creds_generated")
        print("=" * 62)
        print("MCP connector credentials — shown ONCE, secret NOT retrievable:")
        print(f"  API Key:    {api_key}")
        print(f"  API Secret: {api_secret}")
        print(f"  Header:     X-Frappe-API-Key: {api_key}:{api_secret}")
        print("=" * 62)

    frappe.db.commit()
    return out
