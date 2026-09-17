"""
Enhanced ERPNext MCP Server with Universal Query Tools
Replaces all specific tools with a powerful generic query system

SECURITY MODEL:
- Authentication is handled by Server Script wrapper (erpnext_mcp_native.auth.validate_bearer_api_key)
- Once authenticated via API Key, chatbots have FULL ACCESS to all doctypes
- All database queries use ignore_permissions=True by default
- Role-based restrictions are enforced at the Server Script level
- This design allows AI assistants to query any data without permission barriers

USAGE:
- Direct endpoint (no auth): /api/method/erpnext_mcp_native.api.handle_mcp
- Authenticated endpoint: /api/method/erpnext_mcp_native.api.handle_mcp_oauth
"""

import frappe
from . import frappe_mcp
from datetime import datetime, timedelta, date
import json
from typing import Dict, List, Optional, Any, Union

# Create MCP instance
# Authentication is handled by Server Script wrapper (erpnext_mcp_native.auth.validate_bearer_api_key)
# This endpoint focuses purely on MCP functionality
mcp = frappe_mcp.MCP("erpnext-mcp-universal")

# Database schema cache
SCHEMA_CACHE = {}

# ==========================================
# HELPER FUNCTIONS
# ==========================================

def validate_doctype_fields(doctype: str, fields: List[str]) -> List[str]:
    """Validate which fields exist in a doctype and return only valid fields"""
    cache_key = f"{doctype}_fields"

    # Standard fields that exist on all Frappe doctypes
    standard_fields = {
        "name", "owner", "creation", "modified", "modified_by",
        "docstatus", "idx", "_user_tags", "_comments", "_assign", "_liked_by"
    }

    if cache_key not in SCHEMA_CACHE:
        try:
            # Get all custom fields for this doctype from DocField table
            all_fields = frappe.db.get_all("DocField",
                filters={"parent": doctype},
                fields=["fieldname"],
                ignore_permissions=True
            )
            # Combine custom fields with standard fields
            SCHEMA_CACHE[cache_key] = {field.fieldname for field in all_fields} | standard_fields
        except Exception as e:
            frappe.log_error(f"Failed to get schema for {doctype}: {str(e)}")
            # On error, at minimum include standard fields
            SCHEMA_CACHE[cache_key] = standard_fields

    valid_fields = SCHEMA_CACHE[cache_key]
    return [field for field in fields if field in valid_fields]

def parse_date_filter(date_str: str) -> Optional[str]:
    """Parse date string and return formatted date for database query"""
    if not date_str:
        return None
    
    try:
        # Handle relative date formats
        if date_str.lower() == "today":
            return datetime.now().strftime("%Y-%m-%d")
        elif date_str.lower() == "yesterday":
            return (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        elif date_str.lower() == "this_week":
            start_of_week = datetime.now() - timedelta(days=datetime.now().weekday())
            return start_of_week.strftime("%Y-%m-%d")
        elif date_str.lower() == "last_week":
            start_of_last_week = datetime.now() - timedelta(days=datetime.now().weekday() + 7)
            return start_of_last_week.strftime("%Y-%m-%d")
        elif date_str.lower() == "this_month":
            return datetime.now().replace(day=1).strftime("%Y-%m-%d")
        elif date_str.lower() == "last_month":
            first_day_last_month = datetime.now().replace(day=1) - timedelta(days=datetime.now().replace(day=1).day)
            return first_day_last_month.strftime("%Y-%m-%d")
        elif date_str.lower() == "this_year":
            return datetime.now().replace(month=1, day=1).strftime("%Y-%m-%d")
        elif date_str.lower() == "last_year":
            return (datetime.now().replace(year=datetime.now().year-1, month=1, day=1)).strftime("%Y-%m-%d")
        elif date_str.lower() == "last_30_days":
            return (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        elif date_str.lower() == "last_90_days":
            return (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
        elif date_str.lower() == "last_6_months":
            return (datetime.now() - timedelta(days=180)).strftime("%Y-%m-%d")
        elif date_str.lower() == "last_1_year":
            return (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
        
        # Try to parse as ISO date
        datetime.strptime(date_str, "%Y-%m-%d")
        return date_str
    except ValueError:
        # Try datetime parsing
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except:
            return None

def apply_date_filters(filters: Dict, from_date: Optional[str] = None, 
                      to_date: Optional[str] = None, date_field: str = "posting_date"):
    """Apply date filters to query filters"""
    parsed_from_date = parse_date_filter(from_date)
    parsed_to_date = parse_date_filter(to_date)
    
    if parsed_from_date:
        filters[date_field] = [">=", parsed_from_date]
    if parsed_to_date:
        filters[date_field] = ["<=", parsed_to_date]
    
    return filters

def create_response(data: Any, total_count: int = 0, **kwargs) -> Dict:
    """Create standardized response format"""
    response = {
        "success": True,
        "data": data,
        "total_count": total_count,
        "timestamp": datetime.now().isoformat(),
        **kwargs
    }
    return response

def create_error_response(error_message: str, error_code: str = None) -> Dict:
    """Create standardized error response"""
    return {
        "success": False,
        "error": error_message,
        "error_code": error_code,
        "timestamp": datetime.now().isoformat()
    }


def _has_column(doctype: str, fieldname: str) -> bool:
    """True only if `fieldname` is a real DB column on the doctype's table.
    Used to keep tools from SELECTing columns that do not exist (1054 errors)."""
    try:
        meta = frappe.get_meta(doctype)
        df = meta.get_field(fieldname)
        if df is None:
            return fieldname in (getattr(meta, "get_valid_columns", lambda: [])() or [])
        if df.get("is_virtual"):
            return False
        try:
            from frappe.model import no_value_fields, table_fields
            if df.fieldtype in (set(no_value_fields) | set(table_fields)):
                return False
        except Exception:
            pass
        return True
    except Exception:
        return False


def _get_default_fields(doctype: str) -> List[str]:
    """Get default fields for a doctype"""
    fields = []
    
    # Always include standard fields
    standard_fields = ["name", "creation", "modified", "owner", "docstatus"]
    fields.extend(standard_fields)
    
    # Try to get doctype fields.
    # 2026-07-29 FIX: the previous version skipped only layout breaks, so it also selected
    # child-table and virtual fields, which have NO database column — producing
    # (1054, "Unknown column 'companies'") on Customer and 'allowed_roles' on OAuth Client.
    # Only fields that actually exist as columns may be auto-selected.
    try:
        from frappe.model import no_value_fields, table_fields
        skip_types = set(no_value_fields) | set(table_fields)
    except Exception:
        skip_types = {"Section Break", "Column Break", "Tab Break", "HTML", "Button", "Fold",
                      "Heading", "Table", "Table MultiSelect"}
    try:
        meta = frappe.get_meta(doctype)
        for df in meta.get("fields"):
            if not df.fieldname or df.fieldtype in skip_types:
                continue
            if df.get("is_virtual"):          # computed at runtime, no column
                continue
            if len(fields) >= 30:             # keep the response bounded
                break
            fields.append(df.fieldname)
    except Exception:
        pass
    
    # Ensure unique fields
    return list(dict.fromkeys(fields))[:30]

def build_aggregation_query(doctype: str, aggregations: Dict, group_by: Optional[List[str]] = None,
                           filters: Optional[Dict] = None, limit: int = 100) -> tuple:
    """Build SQL query with aggregations"""
    # Get table name.
    # 2026-07-29 ROOT-CAUSE FIX: this stripped the space -> `tabSalesInvoice`, but Frappe tables keep
    # it (`tabSales Invoice`). Every multi-word doctype therefore raised
    # (1146, "Table ... doesn't exist") and the tool silently fell back to a partial result. The
    # aggregation SQL path has never worked for a multi-word doctype until now.
    table_name = f"tab{doctype}"
    
    # Build SELECT clause with aggregations
    select_fields = []
    if group_by:
        select_fields.extend([f"`{field}`" for field in group_by])
    
    # 2026-07-29 FIX: this expects {alias: "SUM(field)"} — a full SQL expression. Callers naturally
    # send the friendly {field: "sum"}, which produced `sum as `grand_total`` and returned ONLY the
    # group keys with success:true — a silent wrong answer, the worst failure mode for an agent.
    # Now the friendly form is translated, and anything unusable raises instead of returning garbage.
    _AGG_FNS = {"sum", "count", "avg", "min", "max"}
    for alias, expr in aggregations.items():
        e = str(expr).strip()
        if "(" not in e:                      # friendly form: {"grand_total": "sum"}
            fn = e.lower()
            if fn not in _AGG_FNS:
                raise ValueError(
                    f"aggregations['{alias}']='{expr}' is not usable. Use a SQL expression such as "
                    f"'SUM(grand_total)', or the shorthand {{'grand_total': 'sum'}} with one of "
                    f"{sorted(_AGG_FNS)}.")
            # Keep the caller's alias UNCHANGED: the response builder maps values back by the
            # original aggregations key, so renaming it (e.g. to sum_grand_total) silently dropped
            # the aggregate from the output — the very bug this fix exists to remove.
            e = f"{fn.upper()}(`{alias}`)"
        else:
            head = e.split("(", 1)[0].strip().lower()
            if head not in _AGG_FNS:
                raise ValueError(
                    f"aggregations['{alias}']='{expr}' does not start with an aggregate function "
                    f"({sorted(_AGG_FNS)}).")
        select_fields.append(f"{e} as `{alias}`")
    
    # Build WHERE clause
    where_clause = ""
    values = []
    if filters:
        conditions = []
        for field, value in filters.items():
            if isinstance(value, list):
                if len(value) == 2 and value[0] in ["=", "!=", ">", "<", ">=", "<=", "like", "not like"]:
                    operator = value[0]
                    filter_value = value[1]
                    if operator.lower() == "like":
                        conditions.append(f"`{field}` {operator} %s")
                        values.append(f"%{filter_value}%")
                    else:
                        conditions.append(f"`{field}` {operator} %s")
                        values.append(filter_value)
                elif value[0] == "in":
                    placeholders = ", ".join(["%s"] * len(value[1]))
                    conditions.append(f"`{field}` IN ({placeholders})")
                    values.extend(value[1])
            else:
                conditions.append(f"`{field}` = %s")
                values.append(value)
        
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)
    
    # Build GROUP BY clause
    group_clause = ""
    if group_by:
        group_clause = f"GROUP BY {', '.join([f'`{field}`' for field in group_by])}"
    
    # Build final query
    query = f"SELECT {', '.join(select_fields)} FROM `{table_name}` {where_clause} {group_clause} LIMIT {limit}"
    
    return query, values

# ==========================================
# UNIVERSAL QUERY TOOL
# ==========================================

@mcp.tool()
def query_doctype(doctype: str, 
                  filters: Optional[Dict[str, Any]] = None,
                  fields: Optional[List[str]] = None,
                  limit: int = 20,
                  start: int = 0,
                  order_by: Optional[str] = None,
                  date_range: Optional[Dict[str, str]] = None,
                  include_count: bool = True,
                  ignore_permissions: bool = True,
                  debug: bool = False) -> Dict:
    """
    Universal tool to query ANY ERPNext doctype with advanced filtering and capabilities
    
    This tool replaces ALL specific doctype tools (get_purchase_orders, search_customers, etc.)
    
    Args:
        doctype: The doctype name (e.g., "Purchase Order", "Sales Invoice", "Customer", "Item")
        filters: Dictionary of field filters with support for:
            - Simple filters: {"status": "Submitted", "grand_total": 1000}
            - Operator filters: {"grand_total": [">", 1000], "name": ["like", "%ABC%"]}
            - List filters: {"status": ["in", ["Draft", "Submitted"]]}
        fields: List of fields to return (default: auto-selects common fields)
        limit: Maximum number of records to return (max: 500)
        start: Starting index for pagination
        order_by: Order by clause (e.g., "posting_date desc", "customer asc, posting_date desc")
        date_range: Date filtering object with:
            - field: Date field name (default: "posting_date")
            - from: Start date (supports: "today", "this_month", "2024-01-01")
            - to: End date
        include_count: Whether to return total count (slower for large tables)
        ignore_permissions: Whether to bypass permission checks (default: true)
        debug: Return debug information including SQL query
    
    Examples:
    1. Get all submitted purchase orders:
       query_doctype("Purchase Order", filters={"docstatus": 1}, limit=10)
    
    2. Get high-value sales invoices this month:
       query_doctype("Sales Invoice", 
           filters={"docstatus": 1, "grand_total": [">", 5000]},
           date_range={"from": "this_month"},
           fields=["name", "customer", "grand_total", "posting_date"],
           order_by="grand_total desc")
    
    3. Search customers by name:
       query_doctype("Customer", 
           filters={"customer_name": ["like", "%John%"]},
           fields=["name", "customer_name", "email", "territory"])
    
    4. Get items with low stock:
       query_doctype("Bin", 
           filters={"actual_qty": ["<", 10]},
           fields=["item_code", "warehouse", "actual_qty"],
           limit=100)
    
    5. Complex query with multiple filters:
       query_doctype("Sales Order",
           filters={
               "docstatus": 1,
               "status": ["!=", "Closed"],
               "grand_total": [">", 0],
               "customer": ["like", "%Corp%"]
           },
           date_range={"field": "transaction_date", "from": "last_90_days"},
           order_by="grand_total desc",
           limit=50)
    """
    try:
        # Validate doctype exists
        if not frappe.db.exists("DocType", doctype):
            available_doctypes = frappe.db.get_all("DocType",
                filters={"istable": 0},
                fields=["name"],
                limit=10,
                order_by="name asc",
                ignore_permissions=True)
            return create_error_response(
                f"Doctype '{doctype}' not found. Available doctypes: {[d.name for d in available_doctypes]}",
                "DOCTYPE_NOT_FOUND"
            )
        
        # Validate limit
        limit = min(limit, 500)  # Max 500 records for performance
        
        # Build filters
        query_filters = filters or {}
        
        # Add date range filtering
        if date_range:
            date_field = date_range.get("field", "posting_date")
            from_date = date_range.get("from")
            to_date = date_range.get("to")
            query_filters = apply_date_filters(query_filters, from_date, to_date, date_field)
        
        # Get default fields if not specified
        if not fields:
            fields = _get_default_fields(doctype)
        
        # Validate fields
        valid_fields = validate_doctype_fields(doctype, fields)
        
        # Set default order by - use creation instead of modified for better compatibility
        if not order_by:
            order_by = "creation desc"
        
        # Get documents
        documents = frappe.db.get_all(
            doctype,
            filters=query_filters,
            fields=valid_fields,
            limit=limit,
            start=start,
            order_by=order_by,
            ignore_permissions=ignore_permissions,
            debug=debug
        )
        
        # Get total count (optional - can be slow for large tables)
        total_count = None
        if include_count:
            try:
                # Use SQL count for better performance when ignoring permissions
                if ignore_permissions:
                    table_name = f"tab{doctype.replace(' ', '')}"
                    if query_filters:
                        # Build WHERE clause for filters
                        conditions = []
                        values = []
                        for field, value in query_filters.items():
                            if isinstance(value, list):
                                operator = value[0]
                                filter_value = value[1]
                                conditions.append(f"`{field}` {operator} %s")
                                values.append(filter_value)
                            else:
                                conditions.append(f"`{field}` = %s")
                                values.append(value)
                        where_clause = " AND ".join(conditions)
                        total_count = frappe.db.sql(f"SELECT COUNT(*) FROM `{table_name}` WHERE {where_clause}", values)[0][0]
                    else:
                        total_count = frappe.db.sql(f"SELECT COUNT(*) FROM `{table_name}`")[0][0]
                else:
                    total_count = frappe.db.count(doctype, filters=query_filters)
            except:
                total_count = len(documents)
        
        # Process results
        results = []
        for doc in documents:
            processed_doc = {}
            for key, value in doc.items():
                if isinstance(value, (datetime, date)):
                    processed_doc[key] = value.isoformat()
                elif hasattr(value, '__dict__'):
                    processed_doc[key] = str(value)
                else:
                    processed_doc[key] = value
            results.append(processed_doc)
        
        # Build response
        response_data = {
            "success": True,
            "doctype": doctype,
            "data": results,
            "count": len(results),
            "pagination": {
                "limit": limit,
                "start": start,
                "has_more": (start + limit) < (total_count or len(results))
            },
            "filters_applied": query_filters,
            "fields_returned": valid_fields,
            "order_by": order_by
        }
        
        if total_count is not None:
            response_data["total_count"] = total_count
        
        if debug:
            response_data["debug"] = {
                "table_name": f"tab{doctype.lower().replace(' ', '')}",
                "query_filters": query_filters,
                "ignore_permissions": ignore_permissions
            }
        
        return response_data
        
    except Exception as e:
        frappe.log_error(f"query_doctype error: {str(e)}")
        return create_error_response(str(e), "QUERY_DOCTYPE_ERROR")

@mcp.tool()
def query_with_aggregation(doctype: str,
                          aggregations: Dict[str, str],
                          group_by: Optional[List[str]] = None,
                          filters: Optional[Dict[str, Any]] = None,
                          having: Optional[Dict[str, Any]] = None,
                          limit: int = 100) -> Dict:
    """
    Advanced query tool with aggregations and grouping for reporting and analytics
    
    This tool enables complex reporting queries similar to SQL GROUP BY with aggregations
    
    Args:
        doctype: The doctype name
        aggregations: Dictionary of aggregation expressions (SQL syntax)
            Examples:
                {"total_sales": "SUM(grand_total)", "avg_amount": "AVG(grand_total)", 
                 "count": "COUNT(name)", "max_date": "MAX(posting_date)"}
        group_by: List of fields to group by
        filters: Standard filters same as query_doctype
        having: Having clause for aggregated values
            Examples: {"total_sales": [">", 10000], "count": [">", 5]}
        limit: Maximum number of groups to return
    
    Examples:
    1. Sales by customer:
       query_with_aggregation("Sales Invoice",
           aggregations={"total_sales": "SUM(grand_total)", "invoice_count": "COUNT(name)"},
           group_by=["customer"],
           filters={"docstatus": 1, "posting_date": [">=", "this_year"]})
    
    2. Monthly sales trend:
       query_with_aggregation("Sales Order",
           aggregations={"monthly_total": "SUM(grand_total)", "order_count": "COUNT(name)"},
           group_by=["DATE_FORMAT(transaction_date, '%Y-%m')"],
           filters={"docstatus": 1},
           having={"monthly_total": [">", 5000]})
    
    3. Top selling items:
       query_with_aggregation("Sales Invoice Item",
           aggregations={"total_qty": "SUM(qty)", "total_amount": "SUM(amount)"},
           group_by=["item_code"],
           order_by="total_amount desc",
           limit=20)
    
    4. Supplier performance:
       query_with_aggregation("Purchase Order",
           aggregations={"po_count": "COUNT(name)", "total_value": "SUM(grand_total)"},
           group_by=["supplier"],
           filters={"docstatus": 1, "posting_date": [">=", "last_6_months"]})
    """
    try:
        # Validate doctype
        if not frappe.db.exists("DocType", doctype):
            return create_error_response(f"Doctype '{doctype}' not found", "DOCTYPE_NOT_FOUND")
        
        # Validate aggregations
        if not aggregations:
            return create_error_response("Aggregations are required", "MISSING_AGGREGATIONS")
        
        # Build query
        query, values = build_aggregation_query(doctype, aggregations, group_by, filters, limit)
        
        # Execute query
        try:
            results = frappe.db.sql(query, values, as_dict=True)
        except Exception as e:
            # 2026-07-29: this fallback used to be SILENT — a failed aggregation SQL returned
            # success:true with ONLY the group keys and no aggregate values, i.e. a confidently
            # wrong answer (the worst outcome for an agent consuming this). The fallback still runs,
            # but it now reports that it degraded and why, and the attempted SQL is surfaced.
            fb = _fallback_aggregation(doctype, aggregations, group_by, filters, having, limit)
            if isinstance(fb, dict):
                fb["degraded"] = True
                fb["degraded_reason"] = f"aggregation SQL failed: {str(e)[:200]}"
                fb["attempted_sql"] = str(query)[:400]
            return fb
        
        # Apply having clause if specified
        if having and results:
            filtered_results = []
            for row in results:
                keep_row = True
                for field, condition in having.items():
                    if field in row:
                        op, value = condition if isinstance(condition, list) else ["=", condition]
                        if op == ">" and not row[field] > value:
                            keep_row = False
                        elif op == "<" and not row[field] < value:
                            keep_row = False
                        elif op == "=" and not row[field] == value:
                            keep_row = False
                        elif op == ">=" and not row[field] >= value:
                            keep_row = False
                        elif op == "<=" and not row[field] <= value:
                            keep_row = False
                if keep_row:
                    filtered_results.append(row)
            results = filtered_results
        
        return create_response(
            data=results,
            doctype=doctype,
            aggregations=aggregations,
            group_by=group_by,
            filters=filters,
            having=having,
            query=query if results else None,
            success=True
        )
        
    except Exception as e:
        frappe.log_error(f"query_with_aggregation error: {str(e)}")
        return create_error_response(str(e), "AGGREGATION_QUERY_ERROR")

def _fallback_aggregation(doctype: str, aggregations: Dict, group_by: Optional[List[str]],
                         filters: Optional[Dict], having: Optional[Dict], limit: int) -> Dict:
    """Fallback method using frappe.get_all for aggregations"""
    try:
        # Get all records with filters
        all_records = frappe.db.get_all(
            doctype,
            filters=filters or {},
            # keys may be ALIASES (e.g. "total") rather than columns; keep only real columns,
            # otherwise this select 1054s and the fallback fails too.
            fields=(group_by or []) + [k for k in aggregations.keys() if _has_column(doctype, k)],
            limit=5000,  # Limit for performance
            ignore_permissions=True
        )
        
        # Process aggregations manually
        if group_by:
            # Group by specified fields
            groups = {}
            for record in all_records:
                group_key = tuple(record.get(field) for field in group_by)
                if group_key not in groups:
                    groups[group_key] = []
                groups[group_key].append(record)
            
            # Calculate aggregations for each group
            results = []
            for group_key, records in groups.items():
                row = {}
                # Add group by fields
                for i, field in enumerate(group_by):
                    row[field] = group_key[i]
                
                # Calculate aggregations
                for agg_field, agg_expr in aggregations.items():
                    if agg_expr.upper().startswith("SUM("):
                        row[agg_field] = sum(r.get(agg_field, 0) for r in records if r.get(agg_field))
                    elif agg_expr.upper().startswith("COUNT("):
                        row[agg_field] = len([r for r in records if r.get(agg_field)])
                    elif agg_expr.upper().startswith("AVG("):
                        values = [r.get(agg_field, 0) for r in records if r.get(agg_field)]
                        row[agg_field] = sum(values) / len(values) if values else 0
                    elif agg_expr.upper().startswith("MAX("):
                        values = [r.get(agg_field) for r in records if r.get(agg_field)]
                        row[agg_field] = max(values) if values else 0
                    elif agg_expr.upper().startswith("MIN("):
                        values = [r.get(agg_field) for r in records if r.get(agg_field)]
                        row[agg_field] = min(values) if values else 0
                
                results.append(row)
        else:
            # No grouping, return single aggregated row
            row = {}
            for agg_field, agg_expr in aggregations.items():
                if agg_expr.upper().startswith("SUM("):
                    row[agg_field] = sum(r.get(agg_field, 0) for r in all_records if r.get(agg_field))
                elif agg_expr.upper().startswith("COUNT("):
                    row[agg_field] = len([r for r in all_records if r.get(agg_field)])
                elif agg_expr.upper().startswith("AVG("):
                    values = [r.get(agg_field, 0) for r in all_records if r.get(agg_field)]
                    row[agg_field] = sum(values) / len(values) if values else 0
                elif agg_expr.upper().startswith("MAX("):
                    values = [r.get(agg_field) for r in all_records if r.get(agg_field)]
                    row[agg_field] = max(values) if values else 0
                elif agg_expr.upper().startswith("MIN("):
                    values = [r.get(agg_field) for r in all_records if r.get(agg_field)]
                    row[agg_field] = min(values) if values else 0
            results = [row]
        
        return create_response(
            data=results[:limit],
            doctype=doctype,
            aggregations=aggregations,
            group_by=group_by,
            filters=filters,
            success=True
        )
        
    except Exception as e:
        return create_error_response(str(e), "FALLBACK_AGGREGATION_ERROR")

# ==========================================
# METADATA AND DISCOVERY TOOLS
# ==========================================

@mcp.tool()
def get_doctype_info(doctype: str, include_sample_data: bool = False, 
                    include_field_counts: bool = False) -> Dict:
    """
    Get comprehensive information about any doctype including fields, metadata, and sample data
    
    Args:
        doctype: The doctype name
        include_sample_data: Whether to include sample records
        include_field_counts: Whether to include field value counts (slower)
    
    Examples:
    1. Basic info: get_doctype_info("Sales Invoice")
    2. With samples: get_doctype_info("Customer", include_sample_data=True)
    3. Full analysis: get_doctype_info("Purchase Order", include_sample_data=True, include_field_counts=True)
    """
    try:
        # Check if doctype exists
        if not frappe.db.exists("DocType", doctype):
            return create_error_response(f"Doctype '{doctype}' not found", "DOCTYPE_NOT_FOUND")
        
        # Get doctype metadata
        meta = frappe.get_meta(doctype)
        doctype_data = frappe.db.get_value("DocType", doctype, 
            ["name", "module", "custom", "engine", "naming_rule", "autoname"], as_dict=True)
        
        # Get all fields
        all_fields = []
        key_fields = []
        mandatory_fields = []
        table_fields = []
        
        for df in meta.get("fields"):
            # Skip layout field types that don't have proper fieldnames
            if df.fieldtype in ["Section Break", "Column Break", "Tab Break", "HTML", "Button"]:
                continue
            field_info = {
                "label": df.label,
                "fieldname": df.fieldname,
                "fieldtype": df.fieldtype,
                "options": df.options,
                "required": df.reqd,
                "unique": df.unique,
                "read_only": df.read_only,
                "hidden": df.hidden,
                "precision": df.precision,
                "length": df.length
            }
            
            all_fields.append(field_info)
            
            if df.fieldtype == "Link":
                key_fields.append(field_info)
            elif df.reqd:
                mandatory_fields.append(field_info)
            elif df.fieldtype == "Table":
                table_fields.append(field_info)
        
        # Group fields by type
        field_types = {}
        for df in all_fields:
            ftype = df["fieldtype"]
            if ftype not in field_types:
                field_types[ftype] = []
            field_types[ftype].append(df)
        
        # Get record count
        record_count = frappe.db.count(doctype)
        
        # Build response
        response = {
            "doctype_name": doctype,
            "module": doctype_data.module,
            "is_custom": doctype_data.custom,
            "naming_rule": doctype_data.naming_rule,
            "autoname": doctype_data.autoname,
            "total_fields": len(all_fields),
            "total_records": record_count,
            "mandatory_fields": mandatory_fields,
            "key_fields": key_fields,
            "table_fields": table_fields,
            "field_types": field_types,
            "common_fields": all_fields[:50]  # First 50 fields
        }
        
        # Add sample data
        if include_sample_data and record_count > 0:
            sample_fields = [f["fieldname"] for f in all_fields[:20] if f["fieldname"]]
            sample_docs = frappe.db.get_all(doctype,
                fields=sample_fields,
                limit=3,
                ignore_permissions=True
            )
            
            processed_samples = []
            for doc in sample_docs:
                sample = {}
                for key, value in doc.items():
                    if isinstance(value, (datetime, date)):
                        sample[key] = value.strftime("%Y-%m-%d %H:%M:%S")
                    else:
                        sample[key] = value
                processed_samples.append(sample)
            
            response["sample_data"] = processed_samples
        
        # Add field counts (value distribution)
        if include_field_counts and record_count > 0:
            field_counts = {}
            for field_type in ["Select", "Link"]:
                for field_info in field_types.get(field_type, [])[:5]:  # Limit to 5 fields
                    field_name = field_info["fieldname"]
                    try:
                        counts = frappe.db.get_all(doctype,
                            fields=[field_name, "count(*) as count"],
                            filters={field_name: ["is", "set"]},
                            group_by=field_name,
                            order_by="count desc",
                            limit=10,
                            ignore_permissions=True
                        )
                        field_counts[field_name] = [{"value": c[field_name], "count": c.count} for c in counts]
                    except:
                        continue
            response["field_counts"] = field_counts
        
        return create_response(
            data=response,
            doctype=doctype,
            success=True
        )
        
    except Exception as e:
        frappe.log_error(f"get_doctype_info error: {str(e)}")
        return create_error_response(str(e), "DOCTYPE_INFO_ERROR")

@mcp.tool()
def search_doctypes(search_term: str = "", module: Optional[str] = None, 
                  include_custom: Optional[bool] = None, limit: int = 100,
                  include_counts: bool = True) -> Dict:
    """
    Search and discover available doctypes in the system
    
    Args:
        search_term: Text to search in doctype names
        module: Filter by specific module (e.g., "Stock", "Accounts", "Selling")
        include_custom: Filter custom doctypes (None = all, True = only custom, False = only standard)
        limit: Maximum results to return
        include_counts: Whether to include record counts (slower)
    
    Examples:
    1. All doctypes: search_doctypes()
    2. Search: search_doctypes(search_term="Order")
    3. By module: search_doctypes(module="Selling")
    4. Custom only: search_doctypes(include_custom=True)
    """
    try:
        filters = {"istable": 0}
        
        if search_term:
            filters["name"] = ["like", f"%{search_term}%"]
        
        if module:
            filters["module"] = module
        
        if include_custom is not None:
            filters["custom"] = include_custom
        
        doctypes = frappe.db.get_all("DocType",
            filters=filters,
            fields=["name", "module", "custom", "modified", "creation"],
            limit=limit,
            order_by="module asc, name asc",
            ignore_permissions=True
        )
        
        # Get record counts (optional - can be slow)
        doctype_counts = {}
        if include_counts:
            for dt in doctypes[:50]:  # Limit count queries
                try:
                    doctype_counts[dt.name] = frappe.db.count(dt.name)
                except:
                    doctype_counts[dt.name] = 0
        
        # Group by module
        grouped = {}
        module_info = {}
        for dt in doctypes:
            mod = dt.module or "Other"
            if mod not in grouped:
                grouped[mod] = []
                module_info[mod] = {
                    "doctype_count": 0,
                    "custom_count": 0,
                    "total_records": 0
                }
            grouped[mod].append(dt.name)
            module_info[mod]["doctype_count"] += 1
            if dt.custom:
                module_info[mod]["custom_count"] += 1
            if include_counts and dt.name in doctype_counts:
                module_info[mod]["total_records"] += doctype_counts[dt.name]
        
        return create_response(
            data={
                "doctypes": doctypes,
                "grouped_by_module": grouped,
                "module_info": module_info,
                "doctype_counts": doctype_counts if include_counts else None,
                "total_found": len(doctypes)
            },
            search_term=search_term,
            module_filter=module,
            include_custom=include_custom,
            success=True
        )
        
    except Exception as e:
        frappe.log_error(f"search_doctypes error: {str(e)}")
        return create_error_response(str(e), "SEARCH_DOCTYPES_ERROR")

@mcp.tool()
def analyze_doctype(doctype: str, analysis_type: str = "summary", 
                   filters: Optional[Dict] = None) -> Dict:
    """
    Analyze a doctype for insights and statistics
    
    Args:
        doctype: Name of the doctype to analyze
        analysis_type: Type of analysis
            - "summary": Basic counts and status breakdown
            - "field_stats": Field value distributions
            - "recent_activity": Recent activity trends
            - "data_quality": Data quality metrics
        filters: Optional filters to apply before analysis
    
    Examples:
    1. Summary: analyze_doctype("Sales Invoice")
    2. Field stats: analyze_doctype("Customer", analysis_type="field_stats")
    3. Recent activity: analyze_doctype("Purchase Order", analysis_type="recent_activity")
    4. Quality check: analyze_doctype("Item", analysis_type="data_quality")
    """
    try:
        if not frappe.db.exists("DocType", doctype):
            return create_error_response(f"Doctype '{doctype}' not found", "DOCTYPE_NOT_FOUND")
        
        # Apply filters if provided
        base_filters = filters or {}
        
        if analysis_type == "summary":
            # Basic summary statistics
            total = frappe.db.count(doctype, filters=base_filters)
            
            # Status breakdown (if docstatus exists)
            status_breakdown = {}
            try:
                status_data = frappe.db.get_all(doctype,
                    fields=["docstatus", "count(*) as count"],
                    filters=base_filters,
                    group_by="docstatus",
                    ignore_permissions=True
                )
                for row in status_data:
                    status_label = {0: "Draft", 1: "Submitted", 2: "Cancelled"}.get(row.docstatus, f"Status {row.docstatus}")
                    status_breakdown[status_label] = row.count
            except:
                pass
            
            # Recent activity
            recent_7_days = frappe.db.count(doctype, 
                filters={**base_filters, "modified": [">=", datetime.now() - timedelta(days=7)]},
                
            )
            recent_30_days = frappe.db.count(doctype, 
                filters={**base_filters, "modified": [">=", datetime.now() - timedelta(days=30)]},
                
            )
            
            # Creation trend (last 6 months)
            creation_trend = []
            for i in range(6):
                date = datetime.now() - timedelta(days=30*i)
                month_end = date.replace(day=1) + timedelta(days=32)
                month_end = month_end.replace(day=1) - timedelta(days=1)
                count = frappe.db.count(doctype,
                    filters={
                        **base_filters,
                        "creation": ["between", [date.replace(day=1), month_end]]
                    },
                    
                )
                creation_trend.append({
                    "month": date.strftime("%Y-%m"),
                    "count": count
                })
            
            return create_response(
                data={
                    "doctype": doctype,
                    "total_records": total,
                    "status_breakdown": status_breakdown,
                    "recent_activity": {
                        "last_7_days": recent_7_days,
                        "last_30_days": recent_30_days
                    },
                    "creation_trend": creation_trend,
                    "analysis_type": "summary"
                },
                success=True
            )
        
        elif analysis_type == "field_stats":
            # Field statistics
            meta = frappe.get_meta(doctype)
            fields = [df.fieldname for df in meta.get("fields") 
                     if df.fieldtype in ["Link", "Select", "Currency"] and df.fieldname]
            
            field_stats = {}
            for field in fields[:15]:  # Limit to 15 fields for performance
                try:
                    stats = frappe.db.get_all(doctype,
                        fields=[f"{field} as value", "count(*) as count"],
                        filters={**base_filters, field: ["is", "set"]},
                        group_by=field,
                        order_by="count desc",
                        limit=5,
                        ignore_permissions=True
                    )
                    field_stats[field] = [{"value": row.value, "count": row.count} for row in stats]
                except:
                    continue
            
            return create_response(
                data={
                    "doctype": doctype,
                    "field_statistics": field_stats,
                    "analysis_type": "field_stats"
                },
                success=True
            )
        
        elif analysis_type == "recent_activity":
            # Recent activity analysis
            now = datetime.now()
            activity_by_hour = []
            
            for i in range(24):
                hour = now - timedelta(hours=i)
                count = frappe.db.count(doctype,
                    filters={**base_filters, "modified": [
                        "between", [
                            hour.replace(minute=0, second=0, microsecond=0),
                            hour.replace(minute=59, second=59, microsecond=999999)
                        ]
                    ]},
                    
                )
                activity_by_hour.append({
                    "hour": hour.strftime("%H:00"),
                    "count": count
                })
            
            # Recent documents
            recent_docs = frappe.db.get_all(doctype,
                fields=["name", "creation", "modified", "owner"],
                filters=base_filters,
                order_by="modified desc",
                limit=10,
                ignore_permissions=True
            )
            
            processed_docs = []
            for doc in recent_docs:
                processed_docs.append({
                    "name": doc.name,
                    "created": doc.creation.isoformat() if doc.creation else None,
                    "modified": doc.modified.isoformat() if doc.modified else None,
                    "owner": doc.owner
                })
            
            return create_response(
                data={
                    "doctype": doctype,
                    "activity_by_hour": activity_by_hour[:12],  # Last 12 hours
                    "recent_documents": processed_docs,
                    "analysis_type": "recent_activity"
                },
                success=True
            )
        
        elif analysis_type == "data_quality":
            # Data quality analysis
            meta = frappe.get_meta(doctype)
            required_fields = [df.fieldname for df in meta.get("fields") if df.reqd]
            
            # Check for missing mandatory fields
            missing_counts = {}
            for field in required_fields[:10]:  # Limit checks
                try:
                    missing = frappe.db.count(doctype,
                        filters={**base_filters, field: ["is", "set"]},
                        
                    )
                    total = frappe.db.count(doctype, filters=base_filters)
                    missing_counts[field] = {
                        "missing": total - missing,
                        "percentage": round(((total - missing) / total * 100), 2) if total > 0 else 100
                    }
                except:
                    missing_counts[field] = {"missing": "N/A", "percentage": "N/A"}
            
            # Check for duplicates
            duplicate_checks = []
            potential_duplicate_fields = ["name", "email", "mobile_no", "customer_name", "supplier_name"]
            for field in potential_duplicate_fields:
                try:
                    duplicates = frappe.db.get_all(doctype,
                        fields=[field, "count(*) as count"],
                        filters={**base_filters, field: ["is", "set"]},
                        group_by=field,
                        having=["count", ">", 1],
                        limit=5,
                        ignore_permissions=True
                    )
                    if duplicates:
                        duplicate_checks.append({
                            "field": field,
                            "duplicates": duplicates
                        })
                except:
                    pass
            
            return create_response(
                data={
                    "doctype": doctype,
                    "mandatory_fields_complete": missing_counts,
                    "potential_duplicates": duplicate_checks,
                    "analysis_type": "data_quality"
                },
                success=True
            )
        
        else:
            return create_error_response(f"Invalid analysis_type: {analysis_type}", "INVALID_ANALYSIS_TYPE")
        
    except Exception as e:
        frappe.log_error(f"analyze_doctype error: {str(e)}")
        return create_error_response(str(e), "ANALYZE_DOCTYPE_ERROR")

# ==========================================
# SYSTEM HEALTH AND DIAGNOSTICS
# ==========================================

@mcp.tool()
def get_system_health() -> Dict:
    """Get system health information and diagnostics"""
    try:
        health_info = {}
        
        # Database connection check
        try:
            db_count = frappe.db.count("User", filters={"enabled": 1})
            health_info["database"] = {
                "status": "healthy",
                "message": "Connected successfully",
                "active_users": db_count
            }
        except Exception as db_error:
            health_info["database"] = {
                "status": "error",
                "message": str(db_error)
            }
        
        # Memory and performance
        try:
            import psutil
            process = psutil.Process()
            memory_info = process.memory_info()
            health_info["system"] = {
                "memory_usage_mb": round(memory_info.rss / 1024 / 1024, 2),
                "cpu_percent": process.cpu_percent()
            }
        except:
            health_info["system"] = {
                "status": "metrics_unavailable"
            }
        
        # Doctype count
        try:
            doctype_count = frappe.db.count("DocType", filters={"istable": 0})
            health_info["doctype_count"] = doctype_count
        except:
            health_info["doctype_count"] = "N/A"
        
        # Schema cache size
        health_info["schema_cache_size"] = len(SCHEMA_CACHE)
        
        return create_response(data=health_info, success=True)
    except Exception as e:
        return create_error_response(str(e), "SYSTEM_HEALTH_ERROR")

# ==========================================
# LEGACY COMPATIBILITY (automatic mappings)
# ==========================================

# Optional: Create wrapper functions for common use cases
@mcp.tool()
def get_entity_counts(doctype: str = "Customer", filters: Optional[Dict] = None) -> Dict:
    """
    Get count of records for any doctype (legacy compatibility)
    
    This is a simplified version of query_doctype for quick counts
    """
    try:
        count = frappe.db.count(doctype, filters=filters or {})
        return create_response(
            data={"count": count},
            doctype=doctype,
            filters=filters,
            success=True
        )
    except Exception as e:
        return create_error_response(str(e), "ENTITY_COUNT_ERROR")

# ==========================================
# New MCP Tools - Reports, Analytics, Search, and Utilities

# ==========================================
# REPORTS & ANALYTICS TOOLS
# ==========================================

@mcp.tool()
def get_sales_summary(
    period: str = "this_month",
    group_by: str = "customer",
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    customer: Optional[str] = None,
    item_group: Optional[str] = None,
    limit: int = 50
) -> Dict:
    """
    Get sales summary with flexible grouping and filtering
    
    Args:
        period: Time period - "today", "this_week", "this_month", "this_quarter", "this_year", "last_month", "last_quarter", "custom"
        group_by: Group results by - "customer", "item", "item_group", "territory", "sales_person", "date"
        from_date: Start date (required if period="custom")
        to_date: End date (required if period="custom")
        customer: Filter by specific customer
        item_group: Filter by item group
        limit: Maximum results to return
    
    Returns:
        Sales summary with totals, counts, and breakdown by group
    """
    try:
        from datetime import datetime, timedelta, date
        
        # Calculate date range
        today = datetime.now().date()
        if period == "today":
            start_date = end_date = today
        elif period == "this_week":
            start_date = today - timedelta(days=today.weekday())
            end_date = today
        elif period == "this_month":
            start_date = today.replace(day=1)
            end_date = today
        elif period == "this_quarter":
            quarter_month = ((today.month - 1) // 3) * 3 + 1
            start_date = today.replace(month=quarter_month, day=1)
            end_date = today
        elif period == "this_year":
            start_date = today.replace(month=1, day=1)
            end_date = today
        elif period == "last_month":
            first_of_this_month = today.replace(day=1)
            end_date = first_of_this_month - timedelta(days=1)
            start_date = end_date.replace(day=1)
        elif period == "last_quarter":
            current_quarter = (today.month - 1) // 3
            if current_quarter == 0:
                start_date = today.replace(year=today.year-1, month=10, day=1)
                end_date = today.replace(year=today.year-1, month=12, day=31)
            else:
                quarter_month = (current_quarter - 1) * 3 + 1
                start_date = today.replace(month=quarter_month, day=1)
                end_month = quarter_month + 2
                if end_month == 12:
                    end_date = today.replace(month=12, day=31)
                else:
                    end_date = today.replace(month=end_month+1, day=1) - timedelta(days=1)
        elif period == "custom" and from_date and to_date:
            start_date = from_date
            end_date = to_date
        else:
            start_date = today.replace(day=1)
            end_date = today
        
        # Build filters
        filters = {
            "docstatus": 1,
            "posting_date": ["between", [str(start_date), str(end_date)]]
        }
        if customer:
            filters["customer"] = customer
        
        # Group by mapping
        group_field_map = {
            "customer": "customer",
            "item": "si.item_code",
            "item_group": "si.item_group", 
            "territory": "territory",
            "sales_person": "si.sales_person",
            "date": "posting_date"
        }
        
        group_field = group_field_map.get(group_by, "customer")
        
        if group_by in ["item", "item_group", "sales_person"]:
            # Need to join with Sales Invoice Item
            query = """
                SELECT 
                    {group_field} as group_key,
                    COUNT(DISTINCT s.name) as invoice_count,
                    SUM(si.amount) as total_amount,
                    SUM(si.qty) as total_qty
                FROM `tabSales Invoice` s
                INNER JOIN `tabSales Invoice Item` si ON si.parent = s.name
                WHERE s.docstatus = 1 
                AND s.posting_date BETWEEN %s AND %s
                {customer_filter}
                {item_group_filter}
                GROUP BY {group_field}
                ORDER BY total_amount DESC
                LIMIT %s
            """.format(
                group_field=group_field,
                customer_filter="AND s.customer = %s" if customer else "",
                item_group_filter="AND si.item_group = %s" if item_group else ""
            )
            
            params = [str(start_date), str(end_date)]
            if customer:
                params.append(customer)
            if item_group:
                params.append(item_group)
            params.append(limit)
            
            data = frappe.db.sql(query, params, as_dict=True)
        else:
            # Simple aggregation on Sales Invoice
            query = """
                SELECT 
                    {group_field} as group_key,
                    COUNT(*) as invoice_count,
                    SUM(grand_total) as total_amount,
                    SUM(total_qty) as total_qty
                FROM `tabSales Invoice`
                WHERE docstatus = 1 
                AND posting_date BETWEEN %s AND %s
                {customer_filter}
                GROUP BY {group_field}
                ORDER BY total_amount DESC
                LIMIT %s
            """.format(
                group_field=group_field,
                customer_filter="AND customer = %s" if customer else ""
            )
            
            params = [str(start_date), str(end_date)]
            if customer:
                params.append(customer)
            params.append(limit)
            
            data = frappe.db.sql(query, params, as_dict=True)
        
        # Get totals
        total_query = """
            SELECT 
                COUNT(*) as total_invoices,
                COALESCE(SUM(grand_total), 0) as grand_total,
                COALESCE(SUM(total_qty), 0) as total_qty
            FROM `tabSales Invoice`
            WHERE docstatus = 1 
            AND posting_date BETWEEN %s AND %s
            {customer_filter}
        """.format(customer_filter="AND customer = %s" if customer else "")
        
        total_params = [str(start_date), str(end_date)]
        if customer:
            total_params.append(customer)
        
        totals = frappe.db.sql(total_query, total_params, as_dict=True)[0]
        
        return {
            "success": True,
            "data": {
                "period": period,
                "from_date": str(start_date),
                "to_date": str(end_date),
                "group_by": group_by,
                "totals": {
                    "invoice_count": totals.get("total_invoices", 0),
                    "total_amount": float(totals.get("grand_total", 0)),
                    "total_qty": float(totals.get("total_qty", 0))
                },
                "breakdown": [{
                    "group": row.get("group_key"),
                    "invoice_count": row.get("invoice_count", 0),
                    "total_amount": float(row.get("total_amount", 0)),
                    "total_qty": float(row.get("total_qty", 0))
                } for row in data]
            }
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
def get_stock_balance(
    item_code: Optional[str] = None,
    warehouse: Optional[str] = None,
    item_group: Optional[str] = None,
    show_zero_stock: bool = False,
    limit: int = 100
) -> Dict:
    """
    Get current stock balance by item and warehouse
    
    Args:
        item_code: Filter by specific item
        warehouse: Filter by specific warehouse
        item_group: Filter by item group
        show_zero_stock: Include items with zero stock
        limit: Maximum results to return
    
    Returns:
        Stock balance with quantity, valuation, and reorder info
    """
    try:
        filters = []
        params = []
        
        if item_code:
            filters.append("b.item_code = %s")
            params.append(item_code)
        if warehouse:
            filters.append("b.warehouse = %s")
            params.append(warehouse)
        if item_group:
            filters.append("i.item_group = %s")
            params.append(item_group)
        if not show_zero_stock:
            filters.append("b.actual_qty != 0")
        
        where_clause = " AND ".join(filters) if filters else "1=1"
        
        query = """
            SELECT 
                b.item_code,
                i.item_name,
                i.item_group,
                b.warehouse,
                b.actual_qty,
                b.reserved_qty,
                b.ordered_qty,
                b.projected_qty,
                b.valuation_rate,
                (b.actual_qty * b.valuation_rate) as stock_value,
                i.safety_stock,
                ir.warehouse_reorder_level,
                ir.warehouse_reorder_qty
            FROM `tabBin` b
            INNER JOIN `tabItem` i ON i.name = b.item_code
            LEFT JOIN `tabItem Reorder` ir ON ir.parent = b.item_code AND ir.warehouse = b.warehouse
            WHERE {where_clause}
            ORDER BY b.item_code, b.warehouse
            LIMIT %s
        """.format(where_clause=where_clause)
        
        params.append(limit)
        data = frappe.db.sql(query, params, as_dict=True)
        
        # Calculate totals
        total_value = sum(float(row.get("stock_value", 0) or 0) for row in data)
        total_qty = sum(float(row.get("actual_qty", 0) or 0) for row in data)
        
        # Find items below reorder level
        low_stock_items = [
            row for row in data 
            if row.get("warehouse_reorder_level") and 
            float(row.get("actual_qty", 0)) < float(row.get("warehouse_reorder_level", 0))
        ]
        
        return {
            "success": True,
            "data": {
                "total_stock_value": total_value,
                "total_quantity": total_qty,
                "item_count": len(data),
                "low_stock_count": len(low_stock_items),
                "items": [{
                    "item_code": row.get("item_code"),
                    "item_name": row.get("item_name"),
                    "item_group": row.get("item_group"),
                    "warehouse": row.get("warehouse"),
                    "actual_qty": float(row.get("actual_qty", 0)),
                    "reserved_qty": float(row.get("reserved_qty", 0)),
                    "ordered_qty": float(row.get("ordered_qty", 0)),
                    "projected_qty": float(row.get("projected_qty", 0)),
                    "valuation_rate": float(row.get("valuation_rate", 0)),
                    "stock_value": float(row.get("stock_value", 0) or 0),
                    "reorder_level": float(row.get("warehouse_reorder_level", 0) or 0),
                    "is_low_stock": row in low_stock_items
                } for row in data],
                "low_stock_items": [{
                    "item_code": row.get("item_code"),
                    "warehouse": row.get("warehouse"),
                    "actual_qty": float(row.get("actual_qty", 0)),
                    "reorder_level": float(row.get("warehouse_reorder_level", 0))
                } for row in low_stock_items]
            }
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
def get_outstanding_invoices(
    party_type: str = "Customer",
    party: Optional[str] = None,
    overdue_only: bool = False,
    min_amount: float = 0,
    limit: int = 100
) -> Dict:
    """
    Get outstanding (unpaid) invoices for customers or suppliers
    
    Args:
        party_type: "Customer" for receivables, "Supplier" for payables
        party: Filter by specific customer/supplier
        overdue_only: Show only overdue invoices
        min_amount: Minimum outstanding amount filter
        limit: Maximum results to return
    
    Returns:
        Outstanding invoices with aging analysis
    """
    try:
        from datetime import datetime
        today = datetime.now().date()
        
        if party_type == "Customer":
            doctype = "Sales Invoice"
            party_field = "customer"
        else:
            doctype = "Purchase Invoice"
            party_field = "supplier"
        
        filters = ["docstatus = 1", "outstanding_amount > %s"]
        params = [min_amount]
        
        if party:
            filters.append(f"{party_field} = %s")
            params.append(party)
        
        if overdue_only:
            filters.append("due_date < %s")
            params.append(str(today))
        
        where_clause = " AND ".join(filters)
        
        query = """
            SELECT 
                name,
                {party_field} as party,
                posting_date,
                due_date,
                grand_total,
                outstanding_amount,
                currency,
                DATEDIFF(%s, due_date) as days_overdue
            FROM `tab{doctype}`
            WHERE {where_clause}
            ORDER BY outstanding_amount DESC
            LIMIT %s
        """.format(
            party_field=party_field,
            doctype=doctype,
            where_clause=where_clause
        )
        
        params.insert(0, str(today))  # For DATEDIFF
        params.append(limit)
        
        data = frappe.db.sql(query, params, as_dict=True)
        
        # Aging buckets
        aging = {"current": 0, "1_30": 0, "31_60": 0, "61_90": 0, "over_90": 0}
        total_outstanding = 0
        
        for row in data:
            amount = float(row.get("outstanding_amount", 0))
            total_outstanding += amount
            days = row.get("days_overdue", 0) or 0
            
            if days <= 0:
                aging["current"] += amount
            elif days <= 30:
                aging["1_30"] += amount
            elif days <= 60:
                aging["31_60"] += amount
            elif days <= 90:
                aging["61_90"] += amount
            else:
                aging["over_90"] += amount
        
        return {
            "success": True,
            "data": {
                "party_type": party_type,
                "total_outstanding": total_outstanding,
                "invoice_count": len(data),
                "aging_summary": aging,
                "invoices": [{
                    "invoice": row.get("name"),
                    "party": row.get("party"),
                    "posting_date": str(row.get("posting_date")),
                    "due_date": str(row.get("due_date")),
                    "grand_total": float(row.get("grand_total", 0)),
                    "outstanding_amount": float(row.get("outstanding_amount", 0)),
                    "currency": row.get("currency"),
                    "days_overdue": max(0, row.get("days_overdue", 0) or 0),
                    "status": "Overdue" if (row.get("days_overdue", 0) or 0) > 0 else "Current"
                } for row in data]
            }
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
def get_profit_analysis(
    period: str = "this_month",
    group_by: str = "item",
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    limit: int = 50
) -> Dict:
    """
    Get gross profit analysis by item, customer, or item group
    
    Args:
        period: Time period - "this_month", "last_month", "this_quarter", "this_year", "custom"
        group_by: Group by - "item", "customer", "item_group"
        from_date: Start date (for custom period)
        to_date: End date (for custom period)
        limit: Maximum results
    
    Returns:
        Profit analysis with gross profit, margin percentages
    """
    try:
        from datetime import datetime, timedelta, date
        
        today = datetime.now().date()
        if period == "this_month":
            start_date = today.replace(day=1)
            end_date = today
        elif period == "last_month":
            first_of_this_month = today.replace(day=1)
            end_date = first_of_this_month - timedelta(days=1)
            start_date = end_date.replace(day=1)
        elif period == "this_quarter":
            quarter_month = ((today.month - 1) // 3) * 3 + 1
            start_date = today.replace(month=quarter_month, day=1)
            end_date = today
        elif period == "this_year":
            start_date = today.replace(month=1, day=1)
            end_date = today
        elif period == "custom" and from_date and to_date:
            start_date = from_date
            end_date = to_date
        else:
            start_date = today.replace(day=1)
            end_date = today
        
        group_field_map = {
            "item": "si.item_code",
            "customer": "s.customer",
            "item_group": "si.item_group"
        }
        group_field = group_field_map.get(group_by, "si.item_code")
        
        query = """
            SELECT 
                {group_field} as group_key,
                SUM(si.qty) as total_qty,
                SUM(si.amount) as revenue,
                SUM(si.qty * COALESCE(si.incoming_rate, i.valuation_rate, 0)) as cost,
                SUM(si.amount) - SUM(si.qty * COALESCE(si.incoming_rate, i.valuation_rate, 0)) as gross_profit
            FROM `tabSales Invoice` s
            INNER JOIN `tabSales Invoice Item` si ON si.parent = s.name
            LEFT JOIN `tabItem` i ON i.name = si.item_code
            WHERE s.docstatus = 1
            AND s.posting_date BETWEEN %s AND %s
            GROUP BY {group_field}
            ORDER BY gross_profit DESC
            LIMIT %s
        """.format(group_field=group_field)
        
        data = frappe.db.sql(query, [str(start_date), str(end_date), limit], as_dict=True)
        
        # Calculate totals
        total_revenue = sum(float(row.get("revenue", 0) or 0) for row in data)
        total_cost = sum(float(row.get("cost", 0) or 0) for row in data)
        total_profit = total_revenue - total_cost
        overall_margin = (total_profit / total_revenue * 100) if total_revenue else 0
        
        return {
            "success": True,
            "data": {
                "period": period,
                "from_date": str(start_date),
                "to_date": str(end_date),
                "group_by": group_by,
                "summary": {
                    "total_revenue": total_revenue,
                    "total_cost": total_cost,
                    "gross_profit": total_profit,
                    "gross_margin_percent": round(overall_margin, 2)
                },
                "breakdown": [{
                    "group": row.get("group_key"),
                    "quantity": float(row.get("total_qty", 0) or 0),
                    "revenue": float(row.get("revenue", 0) or 0),
                    "cost": float(row.get("cost", 0) or 0),
                    "gross_profit": float(row.get("gross_profit", 0) or 0),
                    "margin_percent": round(
                        (float(row.get("gross_profit", 0) or 0) / float(row.get("revenue", 1) or 1)) * 100, 2
                    )
                } for row in data]
            }
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
def run_report(
    report_name: str,
    filters: Optional[str] = None,
    limit: int = 100
) -> Dict:
    """
    Execute any ERPNext report and get results
    
    Args:
        report_name: Name of the report (e.g., "General Ledger", "Stock Balance", "Accounts Receivable")
        filters: JSON string of report filters (e.g., {company: My Company, from_date: 2024-01-01})
        limit: Maximum rows to return
    
    Returns:
        Report data with columns and rows
    """
    try:
        # Parse filters
        report_filters = {}
        if filters:
            if isinstance(filters, str):
                report_filters = json.loads(filters)
            else:
                report_filters = filters
        
        # Check if report exists
        if not frappe.db.exists("Report", report_name):
            # Try to find similar reports
            similar = frappe.db.get_all("Report", 
                filters={"name": ["like", f"%{report_name}%"]},
                fields=["name"],
                limit=5
            )
            return {
                "success": False,
                "error": f"Report {report_name} not found",
                "similar_reports": [r.name for r in similar]
            }
        
        # Get report
        report = frappe.get_doc("Report", report_name)
        
        # Execute report
        # 2026-07-29: ignore_prepared_report must be set for EVERY report type, not just Query
        # Reports. A report with prepared_report=1 (e.g. Stock Balance) otherwise gets queued as a
        # background Prepared Report and run() returns empty columns/result immediately — which this
        # tool then reported as a successful run with 0 rows. MCP callers are synchronous and cannot
        # poll for the background doc, so always execute inline.
        result = frappe.desk.query_report.run(
            report_name,
            filters=report_filters,
            ignore_prepared_report=True
        )

        columns = result.get("columns", [])
        all_rows = result.get("result") or []
        data = all_rows[:limit]
        
        # Format columns
        formatted_columns = []
        for col in columns:
            if isinstance(col, dict):
                formatted_columns.append({
                    "fieldname": col.get("fieldname", ""),
                    "label": col.get("label", ""),
                    "fieldtype": col.get("fieldtype", "Data")
                })
            elif isinstance(col, str):
                parts = col.split(":")
                formatted_columns.append({
                    "fieldname": parts[0],
                    "label": parts[0],
                    "fieldtype": parts[1] if len(parts) > 1 else "Data"
                })
        
        payload = {
            "report_name": report_name,
            "filters_used": report_filters,
            "total_rows": len(all_rows),      # rows the report produced …
            "returned_rows": len(data),       # … vs rows returned after `limit`
            "truncated": len(all_rows) > len(data),
            "columns": formatted_columns,
            "rows": data
        }
        # Never let "the report did not execute" look identical to "the report found nothing".
        if not formatted_columns:
            payload["degraded"] = True
            payload["degraded_reason"] = (
                "the report returned no columns, so it did not execute — usually a missing required "
                "filter (company / fiscal_year / date range) or a queued Prepared Report. Check "
                "filters_used against the report's own filter set."
            )
        elif not data:
            payload["degraded"] = False
            payload["note"] = ("report executed and matched no rows — the filters are valid but "
                               "select nothing (verify company and date range).")
        return {"success": True, "data": payload}
    except Exception as e:
        msg = str(e)
        if "access" in msg.lower() and "report" in msg.lower():
            # 2026-07-29: the MCP user has no Report permission, so this tool is unusable until a
            # role grants it. Return an actionable remedy instead of a bare framework string.
            return {"success": False, "error": msg, "error_code": "REPORT_PERMISSION",
                    "remedy": ("the MCP user lacks Report access — grant the report's role (or "
                               "'Report Manager') to the connector user, or use query_doctype / "
                               "query_with_aggregation instead.")}
        return {"success": False, "error": msg}


# ==========================================
# WORKFLOW & UTILITIES TOOLS
# ==========================================

@mcp.tool()
def get_pending_approvals(
    user: Optional[str] = None,
    doctype: Optional[str] = None,
    limit: int = 50
) -> Dict:
    """
    Get documents pending approval for a user
    
    Args:
        user: Filter by specific user (default: current user)
        doctype: Filter by specific doctype
        limit: Maximum results
    
    Returns:
        List of documents pending approval with workflow details
    """
    try:
        target_user = user or frappe.session.user
        
        # Get all doctypes with active workflows
        workflows = frappe.db.get_all("Workflow",
            filters={"is_active": 1},
            fields=["name", "document_type"]
        )
        
        if doctype:
            workflows = [w for w in workflows if w.document_type == doctype]
        
        pending_docs = []
        
        skipped = []   # doctypes we could not scan, reported instead of failing the call
        
        for workflow in workflows:
          try:
            dt = workflow.document_type
            
            # Get workflow states that allow the user to take action
            transitions = frappe.db.get_all("Workflow Transition",
                filters={
                    "parent": workflow.name,
                    "allowed": ["like", f"%{target_user}%"]
                },
                fields=["state", "action", "next_state", "allowed"],
                or_filters={
                    "allowed": ["like", f"%{target_user}%"]
                }
            )
            
            if not transitions:
                # Check by role
                user_roles = frappe.get_roles(target_user)
                transitions = frappe.db.get_all("Workflow Transition",
                    filters={"parent": workflow.name},
                    fields=["state", "action", "next_state", "allowed"]
                )
                transitions = [t for t in transitions if any(role in t.allowed for role in user_roles)]
            
            states = list(set([t.state for t in transitions]))
            
            # 2026-07-29 FIX: a doctype can have a Workflow record while its `workflow_state`
            # custom column was never created (or was lost in a restore) -> 1054. This path is
            # only reached when the USER's roles yield transitions, which is why it failed for
            # Administrator / OAuth users and passed for the low-privilege connector user.
            if states and not _has_column(dt, "workflow_state"):
                skipped.append({"doctype": dt, "reason": "no workflow_state column"})
                continue
            if states:
                # Get documents in these states
                docs = frappe.db.get_all(dt,
                    filters={"workflow_state": ["in", states], "docstatus": 0},
                    fields=["name", "workflow_state", "owner", "creation", "modified"],
                    limit=limit,
                    order_by="modified desc"
                )
                
                for doc in docs:
                    doc_transitions = [t for t in transitions if t.state == doc.workflow_state]
                    pending_docs.append({
                        "doctype": dt,
                        "name": doc.name,
                        "workflow_state": doc.workflow_state,
                        "owner": doc.owner,
                        "created": str(doc.creation),
                        "modified": str(doc.modified),
                        "available_actions": [t.action for t in doc_transitions]
                    })
          except Exception as _de:
            # 2026-07-29: degrade to a partial result. Previously any single doctype error
            # (e.g. a missing workflow_state column) failed the WHOLE tool call.
            skipped.append({"doctype": workflow.document_type, "reason": str(_de)[:120]})
        
        # Sort by modified date
        pending_docs.sort(key=lambda x: x["modified"], reverse=True)
        pending_docs = pending_docs[:limit]
        
        return {
            "success": True,
            "data": {
                "user": target_user,
                "total_pending": len(pending_docs),
                "documents": pending_docs,
                "skipped_doctypes": skipped,
                "partial": bool(skipped)
            }
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
def get_linked_documents(
    doctype: str,
    name: str,
    link_types: Optional[str] = None
) -> Dict:
    """
    Get all documents linked to a specific document
    
    Args:
        doctype: Source document type (e.g., "Sales Order")
        name: Document name/ID
        link_types: Comma-separated list of doctypes to include (e.g., "Sales Invoice,Delivery Note")
    
    Returns:
        Linked documents organized by type with status and amounts
    """
    try:
        if not frappe.db.exists(doctype, name):
            return {"success": False, "error": f"{doctype} {name} not found"}
        
        # Parse link_types filter
        allowed_types = None
        if link_types:
            allowed_types = [t.strip() for t in link_types.split(",")]
        
        linked_docs = {}
        
        # Common link patterns in ERPNext
        link_patterns = {
            "Sales Order": [
                ("Sales Invoice", "sales_order", "Sales Invoice Item"),
                ("Delivery Note", "against_sales_order", "Delivery Note Item"),
                ("Purchase Order", "sales_order", "Purchase Order Item"),
                ("Material Request", "sales_order", "Material Request Item"),
            ],
            "Purchase Order": [
                ("Purchase Invoice", "purchase_order", "Purchase Invoice Item"),
                ("Purchase Receipt", "purchase_order", "Purchase Receipt Item"),
                ("Sales Order", "purchase_order", "Sales Order Item"),
            ],
            "Quotation": [
                ("Sales Order", "prevdoc_docname", None),
            ],
            "Sales Invoice": [
                ("Payment Entry", "reference_name", "Payment Entry Reference"),
                ("Sales Order", None, "link_from_items"),
                ("Delivery Note", None, "link_from_items"),
            ],
            "Purchase Invoice": [
                ("Payment Entry", "reference_name", "Payment Entry Reference"),
                ("Purchase Order", None, "link_from_items"),
                ("Purchase Receipt", None, "link_from_items"),
            ],
            "Customer": [
                ("Sales Order", "customer", None),
                ("Sales Invoice", "customer", None),
                ("Quotation", "party_name", None),
                ("Delivery Note", "customer", None),
            ],
            "Supplier": [
                ("Purchase Order", "supplier", None),
                ("Purchase Invoice", "supplier", None),
                ("Purchase Receipt", "supplier", None),
            ],
            "Item": [
                ("Sales Invoice Item", "item_code", None),
                ("Purchase Invoice Item", "item_code", None),
                ("Stock Entry Detail", "item_code", None),
                ("Bin", "item_code", None),
            ]
        }
        
        patterns = link_patterns.get(doctype, [])
        
        for link_doctype, link_field, child_table in patterns:
            if allowed_types and link_doctype not in allowed_types:
                continue
                
            try:
                if child_table and child_table != "link_from_items":
                    # Link through child table
                    docs = frappe.db.sql("""
                        SELECT DISTINCT parent as name
                        FROM `tab{child_table}`
                        WHERE {link_field} = %s
                    """.format(child_table=child_table, link_field=link_field), 
                    [name], as_dict=True)
                    
                    doc_names = [d.name for d in docs]
                elif link_field:
                    # Direct link
                    docs = frappe.db.get_all(link_doctype,
                        filters={link_field: name},
                        fields=["name"]
                    )
                    doc_names = [d.name for d in docs]
                else:
                    continue
                
                if doc_names:
                    # Get full doc info
                    full_docs = frappe.db.get_all(link_doctype,
                        filters={"name": ["in", doc_names]},
                        fields=["name", "docstatus", "creation", "modified", 
                                "grand_total" if link_doctype in ["Sales Invoice", "Purchase Invoice", "Sales Order", "Purchase Order"] else "name"]
                    )
                    
                    if link_doctype not in linked_docs:
                        linked_docs[link_doctype] = []
                    
                    for doc in full_docs:
                        linked_docs[link_doctype].append({
                            "name": doc.name,
                            "status": "Draft" if doc.docstatus == 0 else ("Submitted" if doc.docstatus == 1 else "Cancelled"),
                            "created": str(doc.creation),
                            "amount": float(doc.get("grand_total", 0) or 0)
                        })
            except Exception:
                continue
        
        # Also check dynamic links
        dynamic_links = frappe.db.get_all("Dynamic Link",
            filters={"link_doctype": doctype, "link_name": name},
            fields=["parent", "parenttype"]
        )
        
        for dl in dynamic_links:
            if allowed_types and dl.parenttype not in allowed_types:
                continue
            if dl.parenttype not in linked_docs:
                linked_docs[dl.parenttype] = []
            
            try:
                doc = frappe.db.get_value(dl.parenttype, dl.parent, 
                    ["name", "docstatus", "creation"], as_dict=True)
                if doc:
                    linked_docs[dl.parenttype].append({
                        "name": doc.name,
                        "status": "Draft" if doc.docstatus == 0 else ("Submitted" if doc.docstatus == 1 else "Cancelled"),
                        "created": str(doc.creation)
                    })
            except:
                continue
        
        total_linked = sum(len(docs) for docs in linked_docs.values())
        
        return {
            "success": True,
            "data": {
                "source_doctype": doctype,
                "source_name": name,
                "total_linked_documents": total_linked,
                "linked_documents": linked_docs
            }
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
def global_search(
    query: str,
    doctypes: Optional[str] = None,
    limit: int = 20
) -> Dict:
    """
    Search across all or specified doctypes
    
    Args:
        query: Search text
        doctypes: Comma-separated list of doctypes to search (default: common business doctypes)
        limit: Maximum results per doctype
    
    Returns:
        Search results organized by doctype
    """
    try:
        # Default doctypes to search
        default_doctypes = [
            "Customer", "Supplier", "Item", "Sales Order", "Sales Invoice",
            "Purchase Order", "Purchase Invoice", "Quotation", "Lead",
            "Employee", "Project", "Task"
        ]
        
        if doctypes:
            search_doctypes = [dt.strip() for dt in doctypes.split(",")]
        else:
            search_doctypes = default_doctypes
        
        results = {}
        total_results = 0
        
        for dt in search_doctypes:
            try:
                if not frappe.db.exists("DocType", dt):
                    continue
                
                # Get searchable fields
                meta = frappe.get_meta(dt)
                search_fields = ["name"]
                
                # Add title field if exists
                if meta.title_field:
                    search_fields.append(meta.title_field)
                
                # Add common searchable fields
                for field in ["customer_name", "supplier_name", "item_name", "subject", 
                              "email", "mobile_no", "phone", "description", "title"]:
                    if meta.has_field(field):
                        search_fields.append(field)
                
                search_fields = list(set(search_fields))
                
                # Build OR conditions for search
                or_conditions = []
                for field in search_fields:
                    or_conditions.append(f"`{field}` LIKE %s")
                
                if not or_conditions:
                    continue
                
                where_clause = " OR ".join(or_conditions)
                search_term = f"%{query}%"
                params = [search_term] * len(or_conditions)
                
                # Get display fields
                display_fields = ["name"]
                if meta.title_field and meta.title_field != "name":
                    display_fields.append(meta.title_field)
                
                for field in ["customer_name", "supplier_name", "item_name", "subject", "status", "docstatus"]:
                    if meta.has_field(field) and field not in display_fields:
                        display_fields.append(field)
                
                display_fields = display_fields[:5]  # Limit fields
                
                sql_query = """
                    SELECT {fields}
                    FROM `tab{doctype}`
                    WHERE ({where_clause})
                    ORDER BY modified DESC
                    LIMIT %s
                """.format(
                    fields=", ".join([f"`{f}`" for f in display_fields]),
                    doctype=dt,
                    where_clause=where_clause
                )
                
                params.append(limit)
                docs = frappe.db.sql(sql_query, params, as_dict=True)
                
                if docs:
                    results[dt] = [{
                        "name": doc.get("name"),
                        "title": doc.get(meta.title_field) or doc.get("name") if meta.title_field else doc.get("name"),
                        "status": doc.get("status") or ("Submitted" if doc.get("docstatus") == 1 else "Draft"),
                        **{k: v for k, v in doc.items() if k not in ["name", "docstatus"]}
                    } for doc in docs]
                    total_results += len(docs)
                    
            except Exception:
                continue
        
        return {
            "success": True,
            "data": {
                "query": query,
                "total_results": total_results,
                "doctypes_searched": len(search_doctypes),
                "results": results
            }
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
def get_document_pdf(
    doctype: str,
    name: str,
    print_format: Optional[str] = None
) -> Dict:
    """
    Get PDF download URL for a document
    
    Args:
        doctype: Document type
        name: Document name
        print_format: Specific print format to use (default: Standard)
    
    Returns:
        PDF download URL and document info
    """
    try:
        if not frappe.db.exists(doctype, name):
            return {"success": False, "error": f"{doctype} {name} not found"}
        
        # Get available print formats
        print_formats = frappe.db.get_all("Print Format",
            filters={"doc_type": doctype, "disabled": 0},
            fields=["name", "default_print_language"]
        )
        
        # Determine print format to use
        selected_format = print_format or "Standard"
        if print_format and not any(pf.name == print_format for pf in print_formats):
            selected_format = "Standard"
        
        # Get site URL
        site_url = frappe.utils.get_url()
        
        # Build PDF URL
        pdf_url = f"{site_url}/api/method/frappe.utils.print_format.download_pdf?doctype={doctype}&name={name}&format={selected_format}"
        
        # Get document info
        doc = frappe.get_doc(doctype, name)
        
        return {
            "success": True,
            "data": {
                "doctype": doctype,
                "name": name,
                "print_format": selected_format,
                "pdf_url": pdf_url,
                "available_formats": [pf.name for pf in print_formats] + ["Standard"],
                "document_status": "Submitted" if doc.docstatus == 1 else ("Cancelled" if doc.docstatus == 2 else "Draft")
            }
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

# MCP SERVER ENDPOINT
# ==========================================

@mcp.register(allow_guest=False)
def handle_mcp():
    """
    Main MCP handler endpoint with built-in authentication

    Endpoint: /api/method/erpnext_mcp_native.api.handle_mcp
    - Requires authentication (API Key, Bearer Token, or Session)
    - Validates roles: System Manager, MCP User
    - Returns full doctype access with ignore_permissions=True

    Authentication:
    - Use API Key: Authorization header with "token API_KEY:API_SECRET"
    - User must have "System Manager" or "MCP User" role
    - Once authenticated, full access to all doctypes is granted

    Security Model:
    - Role-based authentication is the ONLY security layer
    - All database queries use ignore_permissions=True
    - Chatbots with valid API keys have unrestricted doctype access
    """
    # Check user authentication and roles
    current_user = frappe.session.user

    # User is already authenticated by allow_guest=False
    # Now check if they have required roles
    REQUIRED_ROLES = ["System Manager", "MCP User"]

    # Get user roles
    user_roles_data = frappe.db.sql("""
        SELECT role
        FROM `tabHas Role`
        WHERE parent = %s AND parenttype = 'User'
    """, (current_user,), as_dict=1)

    user_roles = [r.role for r in user_roles_data]

    # Check if user has any of the required roles
    has_required_role = any(role in user_roles for role in REQUIRED_ROLES)

    if not has_required_role:
        frappe.throw(
            f"Insufficient permissions. User '{current_user}' needs one of these roles: {', '.join(REQUIRED_ROLES)}. Current roles: {', '.join(user_roles)}",
            frappe.PermissionError
        )

    # User is authenticated and authorized - MCP will handle the request
    # The @mcp.register decorator handles the JSON-RPC request/response
    pass