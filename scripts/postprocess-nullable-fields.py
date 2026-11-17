"""Restore OpenAPI `nullable: true` on fields of the Python WebSocket models.

Modelina reads the shared schema as JSON Schema, which has no `nullable`
keyword, so a required nullable field is generated as a plain type and a null
value fails validation. The REST generator honours the keyword. Each such field
becomes Optional while staying required, so the key must still be present.
Rules are read from the shared source schema.
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


def snake(identifier):
    return re.sub(r"(?<!^)(?=[A-Z])", "_", identifier).lower()


for name, definition in definitions.items():
    nullable = [key for key, value in properties(definition).items() if value.get("nullable") is True]
    if not nullable:
        continue
    path = output_path / (snake(name) + ".py")
    if not path.exists():
        # A schema may be unreachable from this WS specification.
        continue
    source = path.read_text()
    for key in nullable:
        field = snake(key)
        declaration = re.compile(rf"^( +{field}: )(.+?)( = Field\()", re.M)
        matches = declaration.findall(source)
        assert len(matches) == 1, (path, field)
        if matches[0][1].startswith("Optional["):
            continue
        source = declaration.sub(lambda m: f"{m.group(1)}Optional[{m.group(2)}]{m.group(3)}", source)
    if not re.search(r"^from typing import .*\bOptional\b", source, re.M):
        source = source.replace("from typing import ", "from typing import Optional, ", 1)
        assert re.search(r"^from typing import .*\bOptional\b", source, re.M), path
    path.write_text(source)
