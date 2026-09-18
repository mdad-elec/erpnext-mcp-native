"""
ERPNext MCP Native — JSON-RPC endpoints.

Two flavours, both returning raw JSON-RPC (no Frappe wrapper):
- handle_mcp         API-key auth (n8n, curl, scripts)
- handle_mcp_oauth   OAuth 2.0 Bearer auth (Claude.ai, ChatGPT connectors)
"""
import frappe
import json
from werkzeug.wrappers import Response
from frappe.utils.password import get_decrypted_password
from frappe.oauth import get_server_url


# ---------------------------------------------------------------------------
# API-key flavour
# ---------------------------------------------------------------------------

def authenticate_api_key():
    """Authenticate using custom header to avoid Frappe middleware interference.

    Supports:
    - X-Frappe-API-Key header: "api_key:api_secret" (preferred - bypasses Frappe auth)
    - X-API-Key header: "api_key:api_secret" (alternative)
    - Authorization header: "api_key:api_secret" (may conflict with Frappe middleware)
    """
    # Try custom headers first (these bypass Frappe's auth middleware)
    auth_header = (
        frappe.get_request_header("X-Frappe-API-Key") or
        frappe.get_request_header("X-API-Key") or
        frappe.get_request_header("Authorization") or ""
    )

    # Support formats: "api_key:api_secret" or "Bearer api_key:api_secret" or "token api_key:api_secret"
    token = auth_header
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
    elif auth_header.startswith("token "):
        token = auth_header[6:]

    if ":" not in token:
        return None

    api_key, api_secret = token.split(":", 1)

    # Validate API key
    user = frappe.db.get_value("User", {"api_key": api_key, "enabled": 1}, "name")
    if not user:
        return None

    # Validate API secret
    stored_secret = get_decrypted_password("User", user, "api_secret", raise_exception=False)
    if stored_secret and api_secret == stored_secret:
        return user

    return None

@frappe.whitelist(allow_guest=True)
def handle_mcp():
    """
    Raw MCP endpoint that returns pure JSON-RPC responses
    
    Authentication: Authorization header with "api_key:api_secret"
    """
    # Authenticate
    user = authenticate_api_key()
    if not user:
        response_data = {
            "jsonrpc": "2.0",
            "error": {"code": -32001, "message": "Authentication failed"},
            "id": None
        }
        return Response(json.dumps(response_data, default=str), status=401, mimetype="application/json")
    
    # Set user for this request
    frappe.set_user(user)
    
    # Check roles
    user_roles = frappe.get_roles(user)
    if not any(role in user_roles for role in ["System Manager", "MCP User"]):
        response_data = {
            "jsonrpc": "2.0",
            "error": {"code": -32002, "message": "Insufficient permissions. Requires System Manager or MCP User role"},
            "id": None
        }
        return Response(json.dumps(response_data, default=str), status=403, mimetype="application/json")
    
    # Get request data
    try:
        data = frappe.request.get_json() or {}
    except:
        data = {}
    
    # Import MCP handler
    from erpnext_mcp_native.mcp import mcp
    
    # Handle JSON-RPC methods
    request_id = data.get("id")
    method = data.get("method", "")
    params = data.get("params", {}) or {}

    # MCP spec: notifications (no id, e.g. notifications/initialized) MUST receive
    # 202 Accepted with an EMPTY body — answering them breaks strict clients (rmcp/Codex).
    if request_id is None or method.startswith("notifications/"):
        return Response("", status=202, mimetype="application/json")
    
    try:
        if method == "initialize":
            result = {
                "protocolVersion": params.get("protocolVersion", "2024-11-05"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "erpnext-mcp-native", "version": "0.1.0"}
            }
        elif method == "tools/list":
            result = {"tools": mcp._get_tools_list()}
        elif method == "tools/call":
            tool_name = params.get("name")
            tool_args = params.get("arguments", {})
            result = mcp._execute_tool(tool_name, tool_args)
        else:
            result = {"error": f"Unknown method: {method}"}
        
        response_data = {
            "jsonrpc": "2.0",
            "result": result,
            "id": request_id
        }
    except Exception as e:
        response_data = {
            "jsonrpc": "2.0",
            "error": {"code": -32603, "message": str(e)},
            "id": request_id
        }
    
    return Response(json.dumps(response_data, default=str), status=200, mimetype="application/json")

# ---------------------------------------------------------------------------
# OAuth flavour
# ---------------------------------------------------------------------------

def get_bearer_token():
    """Extract Bearer token from Authorization header"""
    auth_header = frappe.get_request_header("Authorization") or ""
    if auth_header.startswith("Bearer "):
        return auth_header[7:]
    return None


def validate_oauth_token(token):
    """Validate OAuth Bearer token and return user if valid"""
    if not token:
        return None
    
    try:
        # Look up the token in OAuth Bearer Token doctype
        bearer_token = frappe.db.get_value(
            "OAuth Bearer Token",
            {"access_token": token, "status": "Active"},
            ["user", "expiration_time", "scopes"],
            as_dict=True
        )
        
        if not bearer_token:
            return None
        
        # Check if token is expired
        from datetime import datetime
        if bearer_token.expiration_time and bearer_token.expiration_time < datetime.now():
            return None
        
        return bearer_token.user
    except Exception as e:
        frappe.log_error(f"OAuth token validation error: {str(e)}")
        return None


@frappe.whitelist(allow_guest=True)
def oauth_authorization_server():
    """
    OAuth Authorization Server Metadata (RFC 8414)
    Endpoint: /.well-known/oauth-authorization-server
    """
    server_url = get_server_url()
    
    metadata = {
        "issuer": server_url,
        "authorization_endpoint": f"{server_url}/api/method/frappe.integrations.oauth2.authorize",
        "token_endpoint": f"{server_url}/api/method/frappe.integrations.oauth2.get_token",
        "token_endpoint_auth_methods_supported": ["client_secret_post", "client_secret_basic"],
        "revocation_endpoint": f"{server_url}/api/method/frappe.integrations.oauth2.revoke_token",
        "introspection_endpoint": f"{server_url}/api/method/frappe.integrations.oauth2.introspect_token",
        "userinfo_endpoint": f"{server_url}/api/method/frappe.integrations.oauth2.openid_profile",
        "response_types_supported": ["code", "token"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256", "plain"],
        "scopes_supported": ["openid", "all", "read", "write"],
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": ["HS256"]
    }
    
    return Response(
        json.dumps(metadata, default=str),
        status=200,
        mimetype="application/json",
        headers={"Access-Control-Allow-Origin": "*"}
    )


@frappe.whitelist(allow_guest=True)
def oauth_protected_resource():
    """
    OAuth Protected Resource Metadata (RFC 9728)
    Endpoint: /.well-known/oauth-protected-resource
    """
    server_url = get_server_url()
    
    metadata = {
        "resource": f"{server_url}/api/method/erpnext_mcp_native.api.handle_mcp_oauth",
        "authorization_servers": [server_url],
        "scopes_supported": ["openid", "all"],
        "bearer_methods_supported": ["header"],
        "resource_documentation": f"{server_url}/docs/mcp"
    }
    
    return Response(
        json.dumps(metadata, default=str),
        status=200,
        mimetype="application/json",
        headers={"Access-Control-Allow-Origin": "*"}
    )


@frappe.whitelist(allow_guest=True)
def handle_mcp_oauth():
    """
    OAuth 2.0 Bearer MCP endpoint for any MCP client
    (Claude.ai, ChatGPT, and other OAuth-capable clients)
    Returns pure JSON-RPC responses (no Frappe wrapper)
    """
    # Handle CORS preflight
    if frappe.request.method == "OPTIONS":
        return Response(
            "",
            status=200,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "POST, OPTIONS",
                "Access-Control-Allow-Headers": "Authorization, Content-Type",
                "Access-Control-Max-Age": "86400"
            }
        )
    
    # Get and validate OAuth token
    token = get_bearer_token()
    user = validate_oauth_token(token)
    
    if not user:
        # Return 401 with WWW-Authenticate header as per MCP spec
        server_url = get_server_url()
        response_data = {
            "jsonrpc": "2.0",
            "error": {
                "code": -32001,
                "message": "Authentication required"
            },
            "id": None
        }
        return Response(
            json.dumps(response_data, default=str),
            status=401,
            mimetype="application/json",
            headers={
                "WWW-Authenticate": f'Bearer resource="{server_url}/api/method/erpnext_mcp_native.api.handle_mcp_oauth"',
                "Access-Control-Allow-Origin": "*"
            }
        )
    
    # Set user for this request
    frappe.set_user(user)

    # Check roles (2026-09-18: same gate as the API-key flavour — without this, ANY
    # valid OAuth Bearer token, i.e. any site user with an OAuth client, reached the
    # tools regardless of MCP role)
    user_roles = frappe.get_roles(user)
    if not any(role in user_roles for role in ["System Manager", "MCP User"]):
        response_data = {
            "jsonrpc": "2.0",
            "error": {"code": -32002, "message": "Insufficient permissions. Requires System Manager or MCP User role"},
            "id": None
        }
        return Response(
            json.dumps(response_data, default=str),
            status=403,
            mimetype="application/json",
            headers={"Access-Control-Allow-Origin": "*"}
        )

    # Get request data
    try:
        data = frappe.request.get_json() or {}
    except:
        data = {}

    # Import MCP handler
    from erpnext_mcp_native.mcp import mcp

    # Handle JSON-RPC methods
    request_id = data.get("id")
    method = data.get("method", "")
    params = data.get("params", {}) or {}

    try:
        if method == "initialize":
            result = {
                "protocolVersion": params.get("protocolVersion", "2024-11-05"),
                "capabilities": {"tools": {}},
                "serverInfo": {
                    "name": "erpnext-mcp-native",
                    "version": "0.1.0"
                }
            }
        elif method == "tools/list":
            result = {"tools": mcp._get_tools_list()}
        elif method == "tools/call":
            tool_name = params.get("name")
            tool_args = params.get("arguments", {})
            result = mcp._execute_tool(tool_name, tool_args)
        elif method == "notifications/initialized":
            # Acknowledgment, no response needed
            result = {}
        else:
            result = {"error": f"Unknown method: {method}"}
        
        response_data = {
            "jsonrpc": "2.0",
            "result": result,
            "id": request_id
        }
    except Exception as e:
        frappe.log_error(f"OpenAI MCP error: {str(e)}")
        response_data = {
            "jsonrpc": "2.0",
            "error": {
                "code": -32603,
                "message": str(e)
            },
            "id": request_id
        }
    
    return Response(
        json.dumps(response_data, default=str),
        status=200,
        mimetype="application/json",
        headers={"Access-Control-Allow-Origin": "*"}
    )
