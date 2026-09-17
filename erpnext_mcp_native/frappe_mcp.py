"""
Frappe MCP Wrapper - Simplified
Provides MCP (Model Context Protocol) functionality for Frappe/ERPNext
"""

import json
from datetime import datetime, date, timedelta
import logging
import inspect
from typing import Dict, List, Callable, Any, Optional, get_args, get_origin, Union
from functools import wraps
import frappe

logger = logging.getLogger(__name__)
class DateTimeEncoder(json.JSONEncoder):
    """Custom JSON encoder that handles datetime, date, and timedelta objects"""
    def default(self, obj):
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        if isinstance(obj, timedelta):
            return str(obj)
        return super().default(obj)




class MCP:
    """MCP Server Implementation for Frappe"""

    def __init__(self, name: str):
        self.name = name
        self.tools = {}
        self.endpoints = {}

    def tool(self, description: str = ""):
        """Decorator to register a tool"""
        def decorator(func: Callable) -> Callable:
            tool_name = func.__name__
            
            @wraps(func)
            def wrapper(*args, **kwargs):
                return func(*args, **kwargs)
            
            self.tools[tool_name] = {
                "function": wrapper,
                "description": description or func.__doc__ or "",
                "inputSchema": self._build_input_schema(func)
            }
            
            return wrapper
        return decorator

    def _get_tools_list(self):
        """Get list of all registered tools"""
        tools_list = []
        for name, tool_info in self.tools.items():
            tools_list.append({
                "name": name,
                "description": tool_info.get("description", ""),
                "inputSchema": tool_info.get("inputSchema", {"type": "object", "properties": {}, "required": []})
            })
        return tools_list

    def _execute_tool(self, tool_name: str, arguments: dict):
        """Execute a tool by name with given arguments"""
        if tool_name not in self.tools:
            raise ValueError(f"Unknown tool: {tool_name}")
        
        tool_info = self.tools[tool_name]
        func = tool_info["function"]
        
        # Parse JSON string arguments (n8n sends complex types as JSON strings)
        parsed_args = {}
        for key, value in arguments.items():
            if isinstance(value, str) and value.strip().startswith(("{", "[")):
                try:
                    parsed_args[key] = json.loads(value)
                except:
                    parsed_args[key] = value
            else:
                parsed_args[key] = value

        # 2026-07-29 (FIX F) — coerce a scalar into a list where the signature wants a list.
        # Clients differ: n8n sends JSON strings, ChatGPT/Claude send native types, and humans send
        # `group_by="status"` where a List[str] is expected. That mismatch produced misleading errors
        # like "can only concatenate str (not list) to str" that looked like server bugs.
        try:
            import inspect as _i
            from typing import get_origin as _go, get_args as _ga, Union as _U
            sig = _i.signature(func)
            for k, v in list(parsed_args.items()):
                prm = sig.parameters.get(k)
                if prm is None or prm.annotation is _i._empty:
                    continue
                ann = prm.annotation
                if _go(ann) is _U:
                    non_none = [a for a in _ga(ann) if a is not type(None)]
                    ann = non_none[0] if len(non_none) == 1 else ann
                if _go(ann) is list and not isinstance(v, (list, tuple)) and v is not None:
                    parsed_args[k] = [v]
        except Exception:
            pass

        # (FIX E) usage telemetry: name, caller, duration, outcome. Successful calls were recorded
        # NOWHERE before this, so "which tools does the assistant actually use, and how slow are
        # they" was unanswerable. Logged best-effort — telemetry must never break a tool call.
        import time as _t
        _t0 = _t.time()
        _ok, _err = True, ""
        try:
            result = func(**parsed_args)
            if isinstance(result, dict) and result.get("success") is False:
                _ok, _err = False, str(result.get("error"))[:200]
            return {
                "content": [{"type": "text", "text": json.dumps(result, cls=DateTimeEncoder) if isinstance(result, (dict, list)) else str(result)}],
                "structuredContent": result,
                "isError": False
            }
        except Exception as e:
            _ok, _err = False, str(e)[:200]
            # keep structuredContent on the error path too, so callers can parse a failure
            # (previously an exception returned content only, with no machine-readable shape)
            return {
                "content": [{"type": "text", "text": str(e)}],
                "structuredContent": {"success": False, "error": str(e), "tool": tool_name},
                "isError": True
            }
        finally:
            try:
                self._log_usage(tool_name, int((_t.time() - _t0) * 1000), _ok, _err,
                                list(parsed_args.keys()))
            except Exception:
                pass

    def _log_usage(self, tool_name, ms, ok, err, arg_keys):
        """Append one line of tool telemetry. Uses frappe's logger (no schema change, no doctype)."""
        try:
            import frappe, json as _j, logging as _lg
            user = getattr(getattr(frappe, "session", None), "user", "?")
            logger = frappe.logger("mcp_usage")
            # frappe's logger defaults ABOVE info, so .info() was silently dropped and the log file
            # stayed 0 bytes (2026-07-29). Set the level explicitly and log a JSON *string*.
            try:
                logger.setLevel(_lg.INFO)
            except Exception:
                pass
            logger.info(_j.dumps({"tool": tool_name, "user": user, "ms": ms, "ok": ok,
                                  "args": arg_keys, "error": err}, default=str))
        except Exception:
            pass

    def _type_to_jsonschema(self, annotation: Any, default: Any = inspect._empty) -> Dict[str, Any]:
        """Convert Python type to JSON Schema - n8n compatible (string types only for complex)"""
        if annotation is inspect._empty:
            if default is not inspect._empty and default is not None:
                annotation = type(default)
            else:
                annotation = str

        origin = get_origin(annotation)
        args = list(get_args(annotation) or [])

        # Optional[T] -> treat as T
        if origin is Union and args:
            non_none = [a for a in args if a is not type(None)]
            if len(non_none) == 1:
                return self._type_to_jsonschema(non_none[0], default=default)

        # Primitives
        if annotation in (str,):
            return {"type": "string"}
        if annotation in (int, float):
            return {"type": "number"}
        if annotation in (bool,):
            return {"type": "boolean"}

        # Collections - convert to string for n8n compatibility
        if origin in (list, List):
            return {"type": "string", "description": "JSON array as string"}
        if origin in (dict, Dict) or annotation in (dict, Dict):
            return {"type": "string", "description": "JSON object as string"}

        return {"type": "string"}

    def _build_input_schema(self, func: Callable) -> Dict[str, Any]:
        """Build input schema from function signature"""
        schema = {"type": "object", "properties": {}, "required": []}

        try:
            sig = inspect.signature(func)
        except:
            return schema

        for name, param in sig.parameters.items():
            if name == "self":
                continue
            if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
                continue

            prop_schema = self._type_to_jsonschema(param.annotation, default=param.default)
            
            # Add defaults (skip None for n8n compatibility)
            if param.default is not inspect._empty and param.default is not None:
                if isinstance(param.default, (str, int, float, bool, list, dict)):
                    prop_schema["default"] = param.default

            schema["properties"][name] = prop_schema

            # Required if no default
            if param.default is inspect._empty:
                ann_origin = get_origin(param.annotation)
                ann_args = get_args(param.annotation) or ()
                is_optional = ann_origin is Union and any(a is type(None) for a in ann_args)
                if not is_optional:
                    schema["required"].append(name)

        return schema

    def register(self, allow_guest: bool = False):
        """Decorator to register an endpoint"""
        def decorator(func: Callable) -> Callable:
            @wraps(func)
            def wrapper(*args, **kwargs):
                return func(*args, **kwargs)
            
            wrapper = frappe.whitelist(allow_guest=allow_guest)(wrapper)
            return wrapper
        return decorator
