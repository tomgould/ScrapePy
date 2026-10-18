"""Convert OpenAPI/Swagger JSON or YAML spec to clean Markdown.

Converts OpenAPI specs to structured Markdown.
"""

import json
import re
from pathlib import Path

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


def load_spec(filepath: str) -> dict:
    """Load OpenAPI spec from JSON or YAML file."""
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    is_yaml_file = filepath.lower().endswith((".yaml", ".yml"))

    if is_yaml_file and HAS_YAML:
        try:
            return yaml.safe_load(content)
        except Exception as e:
            print(f"YAML parse error: {e}")

    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        if not is_yaml_file:
            print(f"JSON parse error: {e}")

    if HAS_YAML and not is_yaml_file:
        try:
            return yaml.safe_load(content)
        except Exception:
            pass

    raise ValueError("Could not parse file as JSON or YAML")


def resolve_ref(spec: dict, ref: str):
    if not ref or not ref.startswith("#/"):
        return None
    parts = ref[2:].split("/")
    current = spec
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


def schema_to_string(spec: dict, schema: dict, indent: int = 0) -> str:
    if not schema:
        return "any"

    if "$ref" in schema:
        ref_name = schema["$ref"].split("/")[-1]
        return f"[{ref_name}](#schema-{ref_name.lower()})"

    schema_type = schema.get("type", "")

    if schema_type == "array":
        items_str = schema_to_string(spec, schema.get("items", {}), indent)
        return f"array of {items_str}"

    if schema_type == "object":
        if "additionalProperties" in schema:
            add_props = schema["additionalProperties"]
            if isinstance(add_props, dict):
                return f"object (map of {schema_to_string(spec, add_props, indent)})"
            return "object"
        return "object"

    if "enum" in schema:
        enum_values = ", ".join(f"`{v}`" for v in schema["enum"])
        return f"{schema_type} (enum: {enum_values})"

    if schema_type:
        fmt = schema.get("format", "")
        return f"{schema_type} ({fmt})" if fmt else schema_type

    if "oneOf" in schema:
        return "one of: " + ", ".join(schema_to_string(spec, s, indent) for s in schema["oneOf"])
    if "anyOf" in schema:
        return "any of: " + ", ".join(schema_to_string(spec, s, indent) for s in schema["anyOf"])

    return "any"


def format_description(desc: str) -> str:
    if not desc:
        return ""
    desc = desc.replace("\n", " ").replace("|", "\\|")
    desc = re.sub(r"\s+", " ", desc).strip()
    return desc


def convert_to_markdown(spec: dict) -> str:
    md = []

    info = spec.get("info", {})
    title = info.get("title", "API Documentation")
    md.append(f"# {title}\n")

    if info.get("version"):
        md.append(f"**Version:** {info['version']}\n")

    if info.get("description"):
        md.append(f"\n{info['description']}\n")

    # Servers
    servers = spec.get("servers", [])
    if servers:
        md.append("\n## Base URL\n")
        for server in servers:
            url = server.get("url", "")
            desc = server.get("description", "")
            md.append(f"- `{url}` - {desc}" if desc else f"- `{url}`")
        md.append("")

    # Authentication
    security_schemes = spec.get("components", {}).get("securitySchemes", {})
    if security_schemes:
        md.append("\n## Authentication\n")
        for name, scheme in security_schemes.items():
            md.append(f"### {name}\n")
            md.append(f"- **Type:** {scheme.get('type', '')}")
            if scheme.get("scheme"):
                md.append(f"- **Scheme:** {scheme['scheme']}")
            if scheme.get("bearerFormat"):
                md.append(f"- **Bearer Format:** {scheme['bearerFormat']}")
            if scheme.get("description"):
                md.append(f"- **Description:** {scheme['description']}")
            md.append("")

    # Group endpoints by tag
    paths = spec.get("paths", {})
    tags_map: dict[str, list] = {}

    for path, methods in paths.items():
        for method in ("get", "post", "put", "patch", "delete", "options", "head"):
            if method not in methods:
                continue
            operation = methods[method]
            tags = operation.get("tags", ["Other"])
            for tag in tags:
                tags_map.setdefault(tag, []).append((path, method.upper(), operation))

    tag_descriptions = {}
    for tag_obj in spec.get("tags", []):
        tag_descriptions[tag_obj["name"]] = tag_obj.get("description", "")

    md.append("\n## Endpoints\n")

    for tag in sorted(tags_map.keys()):
        endpoints = tags_map[tag]
        md.append(f"\n### {tag}\n")

        if tag in tag_descriptions and tag_descriptions[tag]:
            md.append(f"{tag_descriptions[tag]}\n")

        for path, method, operation in endpoints:
            summary = operation.get("summary", "")
            description = operation.get("description", "")

            md.append(f"\n#### `{method}` {path}\n")
            if summary:
                md.append(f"**{summary}**\n")
            if description and description != summary:
                md.append(f"{format_description(description)}\n")

            # Parameters
            parameters = operation.get("parameters", [])
            if parameters:
                md.append("\n**Parameters:**\n")
                md.append("| Name | In | Type | Required | Description |")
                md.append("|------|-----|------|----------|-------------|")
                for param in parameters:
                    name = param.get("name", "")
                    location = param.get("in", "")
                    required = "\u2713" if param.get("required", False) else ""
                    desc = format_description(param.get("description", ""))
                    param_type = schema_to_string(spec, param.get("schema", {}))
                    md.append(f"| `{name}` | {location} | {param_type} | {required} | {desc} |")
                md.append("")

            # Request body
            request_body = operation.get("requestBody", {})
            if request_body:
                md.append("\n**Request Body:**\n")
                for content_type, content_schema in request_body.get("content", {}).items():
                    md.append(f"- Content-Type: `{content_type}`")
                    schema = content_schema.get("schema", {})
                    if "$ref" in schema:
                        ref_name = schema["$ref"].split("/")[-1]
                        md.append(f"- Schema: [{ref_name}](#schema-{ref_name.lower()})")
                    elif schema.get("type"):
                        md.append(f"- Type: {schema_to_string(spec, schema)}")
                md.append("")

            # Responses
            responses = operation.get("responses", {})
            if responses:
                md.append("\n**Responses:**\n")
                md.append("| Code | Description |")
                md.append("|------|-------------|")
                for code, response in sorted(responses.items()):
                    desc = format_description(response.get("description", ""))
                    md.append(f"| `{code}` | {desc} |")
                md.append("")

    # Schemas
    schemas = spec.get("components", {}).get("schemas", {})
    if schemas:
        md.append("\n## Schemas\n")
        for schema_name, schema in sorted(schemas.items()):
            md.append(f"\n### {schema_name}\n")
            md.append(f'<a id="schema-{schema_name.lower()}"></a>\n')

            if schema.get("description"):
                md.append(f"{format_description(schema['description'])}\n")

            md.append(f"**Type:** {schema.get('type', 'object')}\n")

            properties = schema.get("properties", {})
            required_props = schema.get("required", [])
            if properties:
                md.append("\n**Properties:**\n")
                md.append("| Property | Type | Required | Description |")
                md.append("|----------|------|----------|-------------|")
                for prop_name, prop_schema in properties.items():
                    prop_type = schema_to_string(spec, prop_schema)
                    required = "\u2713" if prop_name in required_props else ""
                    desc = format_description(prop_schema.get("description", ""))
                    md.append(f"| `{prop_name}` | {prop_type} | {required} | {desc} |")
                md.append("")

            if "enum" in schema:
                md.append("\n**Enum Values:**\n")
                for val in schema["enum"]:
                    md.append(f"- `{val}`")
                md.append("")

    return "\n".join(md)


def convert_openapi_file(input_file: str, output_file: str):
    """Convert an OpenAPI spec file to Markdown."""
    print(f"Input: {input_file}")
    print(f"Output: {output_file}")

    spec = load_spec(input_file)
    markdown = convert_to_markdown(spec)

    Path(output_file).parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(markdown)

    print(f"Done! Created {output_file}")
    print(f"File size: {len(markdown):,} characters")
