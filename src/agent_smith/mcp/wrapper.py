"""
Dynamic tool wrapper factory and Pydantic schema validation for MCP tools.
"""

import inspect
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import pydantic
from pydantic import BaseModel, Field, create_model

logger = logging.getLogger(__name__)


def _json_type_to_python(prop: dict[str, Any]) -> Any:
    """Maps JSON schema types to native Python types."""
    jtype = prop.get("type")

    if jtype == "string":
        return str
    elif jtype == "integer":
        return int
    elif jtype == "number":
        return float
    elif jtype == "boolean":
        return bool
    elif jtype == "array":
        items = prop.get("items", {})
        item_type = _json_type_to_python(items) if items else Any
        return list[item_type]  # type: ignore[valid-type]
    elif jtype == "object":
        return dict[str, Any]

    if "anyOf" in prop or "oneOf" in prop:
        return Any

    return Any


def json_schema_to_pydantic_model(
    model_name: str, schema: dict[str, Any]
) -> type[BaseModel]:
    """
    Dynamically builds a Pydantic model from an MCP tool JSON schema.
    """
    properties = schema.get("properties", {})
    required = set(schema.get("required", []))

    fields: dict[str, Any] = {}

    for prop_name, prop_spec in properties.items():
        py_type = _json_type_to_python(prop_spec)
        description = prop_spec.get("description", "")

        if prop_name in required:
            fields[prop_name] = (py_type, Field(..., description=description))
        else:
            default = prop_spec.get("default", None)
            fields[prop_name] = (
                py_type | None,
                Field(default=default, description=description),
            )

    return create_model(model_name, **fields)


def build_inspect_signature(schema: dict[str, Any]) -> inspect.Signature:
    """
    Generates a native Python signature (`inspect.Signature`) from the JSON schema.
    Orders required parameters first to respect Python syntax.
    """
    properties = schema.get("properties", {})
    required = set(schema.get("required", []))

    req_names = [p for p in properties if p in required]
    opt_names = [p for p in properties if p not in required]

    params: list[inspect.Parameter] = []
    for name in req_names + opt_names:
        prop = properties[name]
        py_type = _json_type_to_python(prop)
        default = (
            inspect.Parameter.empty if name in required else prop.get("default", None)
        )
        params.append(
            inspect.Parameter(
                name=name,
                kind=inspect.Parameter.POSITIONAL_OR_KEYWORD,
                default=default,
                annotation=py_type,
            )
        )

    return inspect.Signature(parameters=params)


def create_tool_wrapper(
    tool_name: str,
    description: str,
    input_schema: dict[str, Any],
    call_tool_fn: Callable[[str, dict[str, Any]], Awaitable[str]],
) -> Callable[..., Awaitable[str]]:
    """
    Creates an async callable wrapper function for an MCP tool.

    Features:
    1. Dynamic Pydantic model for pre-flight local argument validation.
    2. Native `inspect.Signature` accessible via `inspect.signature(func)`.
    3. Enriched docstring with tool description and parameter details.
    4. Validation errors formatted as Observations to keep the agent loop resilient.
    """
    # 1. Generate dynamic Pydantic model
    model = json_schema_to_pydantic_model(f"{tool_name}_Input", input_schema)

    # 2. Build Python signature
    sig = build_inspect_signature(input_schema)

    # 3. Format docstring
    doc_lines = [description or f"MCP Tool: {tool_name}", "\nParameters:"]
    properties = input_schema.get("properties", {})
    required = set(input_schema.get("required", []))

    for prop_name, prop_spec in properties.items():
        is_req = "required" if prop_name in required else "optional"
        prop_desc = prop_spec.get("description", "")
        doc_lines.append(f"  * {prop_name} ({is_req}): {prop_desc}")

    full_doc = "\n".join(doc_lines)

    # 4. Async wrapper function
    async def wrapper(*args: Any, **kwargs: Any) -> str:
        # Step A: Signature binding (positional / keyword)
        try:
            bound = sig.bind(*args, **kwargs)
            bound.apply_defaults()
            arguments = bound.arguments
        except TypeError as sig_err:
            logger.warning(
                f"Signature binding failed for tool '{tool_name}': {sig_err}"
            )
            return f"Observation: Invalid arguments for tool '{tool_name}': {sig_err}"

        # Step B: Pre-flight local Pydantic validation (Fast Fail)
        try:
            validated_instance = model(**arguments)
            clean_kwargs = validated_instance.model_dump(exclude_unset=False)
        except pydantic.ValidationError as val_err:
            logger.warning(
                f"Pydantic validation failed for tool '{tool_name}': {val_err}"
            )
            return (
                f"Observation: Parameter validation failed for tool '{tool_name}':\n"
                f"{val_err}"
            )

        # Step C: Execute RPC call via MCP client
        return await call_tool_fn(tool_name, clean_kwargs)

    wrapper.__name__ = tool_name
    wrapper.__doc__ = full_doc
    wrapper.__signature__ = sig  # type: ignore[attr-defined]

    return wrapper
