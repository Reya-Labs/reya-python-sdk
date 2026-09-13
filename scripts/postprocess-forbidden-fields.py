"""Preserve JSON Schema `not: {}` fields which the Python generators emit as Any.

Reject explicit values, including None, while keeping defaults absent. The REST
from_dict generator otherwise inserts missing fields as None, so preserve field
presence there too. Rules are read from the shared source schema.
"""

import json
import re
import sys
from pathlib import Path

schema_path, output_path = map(Path, sys.argv[1:])
definitions = json.loads(schema_path.read_text())["definitions"]


def properties(schema):
    if "$ref" in schema:
        schema = definitions[schema["$ref"].split("/")[-1]]
    result = {}
    for parent in schema.get("allOf", []):
        result.update(properties(parent))
    result.update(schema.get("properties", {}))
    return result


for name, definition in definitions.items():
    forbidden = [key for key, value in properties(definition).items() if value.get("not") == {}]
    if not forbidden:
        continue
    filename = re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower() + ".py"
    path = output_path / filename
    if not path.exists():
        # A schema may be unreachable from this REST/WS specification.
        continue
    source = path.read_text()
    fields = []
    for key in forbidden:
        field = re.sub(r"(?<!^)(?=[A-Z])", "_", key).lower()
        assert re.search(rf"^ +{field}: ", source, re.M), (path, field)
        fields.append(field)
        source = source.replace(
            f'"{key}": obj.get("{key}")',
            f'**({{"{key}": obj["{key}"]}} if "{key}" in obj else {{}})',
        )
    match = re.search(r"\n( +)\w+: ", source)
    assert match is not None, path
    indent = match.group(1)
    marker = f"class {name}(BaseModel):"
    assert source.count(marker) == 1, path
    if not re.search(r"from pydantic import .*field_validator", source):
        source = source.replace("from pydantic import ", "from pydantic import field_validator, ", 1)
    args = ", ".join(repr(field) for field in fields)
    validator = (
        f"\n{indent}@field_validator({args}, mode='before')\n"
        f"{indent}@classmethod\n"
        f"{indent}def reject_forbidden_fields(cls, value):\n"
        f"{indent * 2}raise ValueError('These fields must be omitted: {', '.join(forbidden)}')\n"
    )
    source += validator
    path.write_text(source)
