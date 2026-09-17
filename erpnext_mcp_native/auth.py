"""
Custom auth hook to support Bearer token with api_key:api_secret format
This enables n8n and other external clients to authenticate using Bearer tokens
"""
import frappe
from frappe.utils.password import get_decrypted_password

def validate_bearer_api_key():
    """
    Authenticate Bearer tokens in format: Bearer api_key:api_secret
    This is called by Frappe auth_hooks mechanism
    """
    # Only process if user is still Guest (not authenticated by other methods)
    if frappe.session.user not in ("", "Guest"):
        return
    
    auth_header = frappe.get_request_header("Authorization") or ""
    
    # Check for Bearer token
    if not auth_header.startswith("Bearer "):
        return
    
    token = auth_header[7:]  # Remove "Bearer " prefix
    
    # Check if token contains api_key:api_secret format
    if ":" not in token:
        return
    
    api_key, api_secret = token.split(":", 1)
    
    # Validate API key
    user = frappe.db.get_value("User", {"api_key": api_key, "enabled": 1}, "name")
    if not user:
        return
    
    # Validate API secret
    stored_secret = get_decrypted_password("User", user, "api_secret", raise_exception=False)
    
    if stored_secret and api_secret == stored_secret:
        frappe.set_user(user)
