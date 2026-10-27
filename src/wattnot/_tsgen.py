"""Turn the frozen JSON Schemas into TypeScript types.

Small, dependency-free converter covering only the constructs the three
WattNot schemas use: ``const``, ``enum``, ``$ref`` into ``$defs``,
``oneOf``/``anyOf``, union ``type`` arrays, objects with ``properties`` and
``required``, and arrays. It is not a general JSON Schema compiler.

The frontend imports the generated ``schemas.ts`` so backend and UI share one
definition of each contract. ``scripts/gen_ts_types.py`` writes the file; a
test fails if the committed copy drifts from this output.
"""

from __future__ import annotations

import json
import re

# Logical schema name -> TypeScript root type name.
ROOT_TYPES: dict[str, str] = {
    "event": "RunEvent",
    "receipt": "Receipt",
    "ui-view": "UiView",
    "decision": "PlannerDecision",
}

_IDENTIFIER = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")


def _pascal(name: str) -> str:
    return "".join(part.capitalize() for part in re.split(r"[_\-/]", name) if part)


def _ref_name(ref: str) -> str:
    return _pascal(ref.rsplit("/", 1)[-1])


def _literal(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, str):
        return json.dumps(value)
    return str(value)


def _union(parts: list[str]) -> str:
    seen: list[str] = []
    for part in parts:
        if part not in seen:
            seen.append(part)
    return " | ".join(seen)


def _field_name(key: str) -> str:
    return key if _IDENTIFIER.match(key) else json.dumps(key)


def _scalar(kind: str | None) -> str:
    return {
        "string": "string",
        "integer": "number",
        "number": "number",
        "boolean": "boolean",
        "null": "null",
    }.get(kind or "", "unknown")


def _object_type(node: dict, indent: str) -> str:
    props = node.get("properties")
    if not props:
        return "Record<string, unknown>"
    required = set(node.get("required", []))
    inner = indent + "  "
    lines = []
    for key, sub in props.items():
        optional = "" if key in required else "?"
        lines.append(f"{inner}{_field_name(key)}{optional}: {_ts_type(sub, inner)};")
    return "{\n" + "\n".join(lines) + "\n" + indent + "}"


def _array_type(node: dict, indent: str) -> str:
    items = node.get("items")
    inner = _ts_type(items, indent) if items else "unknown"
    # Parenthesise unions so the [] binds to the whole thing.
    if " | " in inner:
        inner = f"({inner})"
    return f"{inner}[]"


def _simple(node: dict, kind: str, indent: str) -> str:
    if kind == "object":
        return _object_type(node, indent)
    if kind == "array":
        return _array_type(node, indent)
    return _scalar(kind)


def _ts_type(node: dict, indent: str = "") -> str:
    if not node:
        return "unknown"
    if "$ref" in node:
        return _ref_name(node["$ref"])
    if "const" in node:
        return _literal(node["const"])
    if "enum" in node:
        return _union([_literal(v) for v in node["enum"]])
    for combiner in ("oneOf", "anyOf"):
        if combiner in node:
            return _union([_ts_type(sub, indent) for sub in node[combiner]])
    kind = node.get("type")
    if isinstance(kind, list):
        return _union([_simple(node, one, indent) for one in kind])
    if kind == "object" or (kind is None and "properties" in node):
        return _object_type(node, indent)
    if kind == "array":
        return _array_type(node, indent)
    return _scalar(kind)


def _emit_named(name: str, node: dict) -> str:
    if node.get("type") == "object" and node.get("properties"):
        return f"export interface {name} {_object_type(node, '')}\n"
    return f"export type {name} = {_ts_type(node)};\n"


def render_typescript(schemas: dict[str, dict]) -> str:
    """Render the frozen schemas into one TypeScript module (as a string)."""

    out: list[str] = [
        "// Generated from schemas/*.json by scripts/gen_ts_types.py.",
        "// Do not edit by hand; run the generator after a schema change.",
        "",
    ]
    for logical, root in ROOT_TYPES.items():
        schema = schemas[logical]
        out.append(f"// ---- {logical} ({schema.get('$id', root)}) ----")
        out.append("")
        for def_name, def_node in schema.get("$defs", {}).items():
            out.append(_emit_named(_pascal(def_name), def_node))
        out.append(_emit_named(root, schema))
    return "\n".join(out).rstrip("\n") + "\n"
