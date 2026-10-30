"""Minimal JSON Schema subset used by ge (no jsonschema dependency).

Supported keys (anything else in a node schema is an error at spec load, not silently ignored):

    type                 object | array | string | integer | number | boolean | null   (one type, not a list)
    properties, required, additionalProperties (false only)                            (object)
    items, minItems, maxItems                                                          (array)
    minLength, maxLength, pattern                                                      (string)
    minimum, maximum, exclusiveMinimum, exclusiveMaximum                               (integer, number)
    enum, const                                                                        (any type)
    title, description, $comment, default, examples                                   (ignored annotations)
"""
import re

TYPES = ("object", "array", "string", "integer", "number", "boolean", "null")
CHECKED = {"type", "properties", "required", "additionalProperties", "items", "minItems", "maxItems",
           "minLength", "maxLength", "pattern", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
           "enum", "const"}
ANNOTATIONS = {"title", "description", "$comment", "default", "examples"}


def schema_problems(schema, path="$"):
    """Unsupported or malformed schema keys. Empty list = the validator checks everything the schema says."""
    if not isinstance(schema, dict):
        return [f"{path}: schema 는 object 여야 한다"]
    out = [f"{path}: 지원하지 않는 키 {k!r}" for k in schema if k not in CHECKED | ANNOTATIONS]
    t = schema.get("type")
    if t is not None and t not in TYPES:
        out.append(f"{path}: 지원하지 않는 type {t!r} (하나의 문자열: {', '.join(TYPES)})")
    if "additionalProperties" in schema and schema["additionalProperties"] is not False:
        out.append(f"{path}: additionalProperties 는 false 만 지원")
    for k, sub in (schema.get("properties") or {}).items():
        out += schema_problems(sub, f"{path}.{k}")
    if "items" in schema:
        out += schema_problems(schema["items"], f"{path}[]")
    return out


def _is_int(x):
    return isinstance(x, int) and not isinstance(x, bool)


def _is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def validate(obj, schema, path="$"):
    """Return a list of error strings (empty = valid)."""
    errs = []
    t = schema.get("type")
    if t == "object":
        if not isinstance(obj, dict):
            return [f"{path}: object 아님"]
        for k in schema.get("required", []):
            if k not in obj:
                errs.append(f"{path}.{k}: 필수 누락")
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            errs += [f"{path}.{k}: 허용되지 않은 키" for k in obj if k not in props]
        for k, sub in props.items():
            if k in obj:
                errs += validate(obj[k], sub, f"{path}.{k}")
    elif t == "array":
        if not isinstance(obj, list):
            return [f"{path}: array 아님"]
        if "minItems" in schema and len(obj) < schema["minItems"]:
            errs.append(f"{path}: 항목 {len(obj)} < minItems {schema['minItems']}")
        if "maxItems" in schema and len(obj) > schema["maxItems"]:
            errs.append(f"{path}: 항목 {len(obj)} > maxItems {schema['maxItems']}")
        for i, it in enumerate(obj):
            errs += validate(it, schema.get("items", {}), f"{path}[{i}]")
    elif t == "string":
        if not isinstance(obj, str):
            return [f"{path}: string 아님"]
        if "minLength" in schema and len(obj) < schema["minLength"]:
            errs.append(f"{path}: 길이 {len(obj)} < {schema['minLength']}")
        if "maxLength" in schema and len(obj) > schema["maxLength"]:
            errs.append(f"{path}: 길이 {len(obj)} > {schema['maxLength']}")
        if "pattern" in schema and not re.search(schema["pattern"], obj):
            errs.append(f"{path}: pattern 불일치")
    elif t in ("integer", "number"):
        if not (_is_int(obj) if t == "integer" else _is_num(obj)):
            return [f"{path}: {t} 아님"]
        if "minimum" in schema and obj < schema["minimum"]:
            errs.append(f"{path}: {obj} < minimum {schema['minimum']}")
        if "maximum" in schema and obj > schema["maximum"]:
            errs.append(f"{path}: {obj} > maximum {schema['maximum']}")
        if "exclusiveMinimum" in schema and obj <= schema["exclusiveMinimum"]:
            errs.append(f"{path}: {obj} <= exclusiveMinimum {schema['exclusiveMinimum']}")
        if "exclusiveMaximum" in schema and obj >= schema["exclusiveMaximum"]:
            errs.append(f"{path}: {obj} >= exclusiveMaximum {schema['exclusiveMaximum']}")
    elif t == "boolean":
        if not isinstance(obj, bool):
            return [f"{path}: boolean 아님"]
    elif t == "null":
        if obj is not None:
            return [f"{path}: null 아님"]
    if "enum" in schema and obj not in schema["enum"]:
        errs.append(f"{path}: enum 밖 {obj!r}")
    if "const" in schema and obj != schema["const"]:
        errs.append(f"{path}: const 불일치 {obj!r}")
    return errs
