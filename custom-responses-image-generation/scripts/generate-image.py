#!/usr/bin/env python3

import argparse
import base64
import json
import mimetypes
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.request


HELP = """Generate or edit images with OpenAI Responses API image_generation.

Runtime:
  Requires Python 3 only. No pip packages are needed.
"""


def die(message, code=1):
    print(f"Error: {message}", file=sys.stderr)
    raise SystemExit(code)


def parse_args():
    parser = argparse.ArgumentParser(description=HELP)
    parser.add_argument("--prompt")
    parser.add_argument("--prompt-file")
    parser.add_argument("--out", default="generated.png")
    parser.add_argument("--codex-home")
    parser.add_argument("--base-url")
    parser.add_argument("--response-model")
    parser.add_argument("--api-key", dest="deprecated_api_key", help=argparse.SUPPRESS)
    parser.add_argument("--api-key-env")
    parser.add_argument("--action", choices=["generate", "edit", "auto"])
    parser.add_argument("--image", action="append", default=[])
    parser.add_argument("--mask")
    parser.add_argument("--image-model")
    parser.add_argument("--size")
    parser.add_argument("--quality", choices=["low", "medium", "high", "auto"])
    parser.add_argument("--format", choices=["png", "webp", "jpeg"])
    parser.add_argument("--background", choices=["transparent", "opaque", "auto"])
    parser.add_argument("--input-fidelity", choices=["high", "low"])
    parser.add_argument("--moderation", choices=["auto", "low"])
    parser.add_argument("--output-compression", type=int)
    parser.add_argument("--partial-images", type=int)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.deprecated_api_key is not None:
        die("--api-key was removed to avoid exposing secrets in command lines. Use Codex auth.json or --api-key-env <name>.")
    return args


def parse_toml_value(raw):
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    if value == "true":
        return True
    if value == "false":
        return False
    if re.match(r"^-?\d+(\.\d+)?$", value):
        return float(value) if "." in value else int(value)
    return value


def parse_toml_lite(text):
    root = {}
    sections = {}
    current = root

    for line in text.splitlines():
        trimmed = line.strip()
        if not trimmed or trimmed.startswith("#"):
            continue

        section_match = re.match(r"^\[([^\]]+)\]$", trimmed)
        if section_match:
            section = section_match.group(1).replace('"', "")
            current = sections.setdefault(section, {})
            continue

        key_value_match = re.match(r"^([A-Za-z0-9_.-]+)\s*=\s*(.+)$", trimmed)
        if not key_value_match:
            continue
        key, raw_value = key_value_match.groups()
        current[key] = parse_toml_value(raw_value)

    return {"root": root, "sections": sections}


def read_json_if_exists(file_path):
    if not file_path.exists():
        return {}
    try:
        return json.loads(file_path.read_text(encoding="utf-8"))
    except Exception as error:
        die(f"failed to parse {file_path}: {error}")


def env_value(name):
    if name in os.environ:
        return os.environ[name]
    lower_name = name.lower()
    for key, value in os.environ.items():
        if key.lower() == lower_name:
            return value
    return None


def validate_env_name(name, option_name):
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
        die(f"{option_name} must be an environment variable name, not a secret value.")


def default_codex_home():
    return Path(env_value("CODEX_HOME") or Path.home() / ".codex").resolve()


def resolve_codex_config(args):
    codex_home = Path(args.codex_home).resolve() if args.codex_home else default_codex_home()
    config_path = codex_home / "config.toml"
    auth_path = codex_home / "auth.json"

    codex_config = {"root": {}, "sections": {}}
    if config_path.exists():
        codex_config = parse_toml_lite(config_path.read_text(encoding="utf-8"))

    provider_name = codex_config["root"].get("model_provider") or "OpenAI"
    provider = codex_config["sections"].get(f"model_providers.{provider_name}", {})
    auth = read_json_if_exists(auth_path)
    api_key_env_name = args.api_key_env or "OPENAI_API_KEY"
    validate_env_name(api_key_env_name, "--api-key-env")

    base_url = (
        args.base_url
        or provider.get("base_url")
        or env_value("OPENAI_BASE_URL")
        or "https://api.openai.com/v1"
    ).rstrip("/")
    response_model = args.response_model or codex_config["root"].get("model") or env_value("OPENAI_MODEL")
    auth_api_key = auth.get("OPENAI_API_KEY")
    env_api_key = env_value(api_key_env_name)
    api_key = auth_api_key or env_api_key
    api_key_source = "codex-auth" if auth_api_key else f"env:{api_key_env_name}" if env_api_key else "none"

    if not response_model:
        die("no Responses model found. Set Codex top-level model or pass --response-model.")
    if not api_key and not args.dry_run:
        die(f"no API key found. Expected OPENAI_API_KEY in Codex auth.json or environment variable {api_key_env_name}.")

    return {
        "codex_home": str(codex_home),
        "config_path": str(config_path),
        "auth_path": str(auth_path),
        "provider_name": provider_name,
        "base_url": base_url,
        "response_model": response_model,
        "api_key": api_key,
        "api_key_source": api_key_source,
        "has_api_key": bool(api_key),
    }


def read_prompt(args):
    if args.prompt:
        return args.prompt
    if args.prompt_file:
        return Path(args.prompt_file).read_text(encoding="utf-8").strip()
    if not sys.stdin.isatty():
        prompt = sys.stdin.read().strip()
        if prompt:
            return prompt
    die("missing --prompt, --prompt-file, or stdin prompt.")


def mime_type_for(file_path):
    guessed, _ = mimetypes.guess_type(str(file_path))
    return guessed or "image/png"


def image_file_to_data_url(file_path):
    path = Path(file_path)
    if not path.exists():
        die(f"image file not found: {file_path}")
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type_for(path)};base64,{data}"


def build_tool(args):
    tool = {"type": "image_generation"}
    option_map = {
        "action": "action",
        "background": "background",
        "input_fidelity": "input_fidelity",
        "image_model": "model",
        "moderation": "moderation",
        "output_compression": "output_compression",
        "format": "output_format",
        "partial_images": "partial_images",
        "quality": "quality",
        "size": "size",
    }

    for arg_name, body_name in option_map.items():
        value = getattr(args, arg_name)
        if value is not None:
            tool[body_name] = value

    if args.mask:
        tool["input_image_mask"] = {"image_url": image_file_to_data_url(args.mask)}

    if "action" not in tool and not args.image:
        tool["action"] = "generate"

    return tool


def build_input(prompt, args):
    if not args.image:
        return prompt

    return [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": prompt},
                *[
                    {"type": "input_image", "image_url": image_file_to_data_url(image_path)}
                    for image_path in args.image
                ],
            ],
        }
    ]


def output_path_for(base_path, index, count, output_format):
    path = Path(base_path)
    if count == 1:
        return path
    suffix = path.suffix or f".{output_format or 'png'}"
    return path.with_name(f"{path.stem}-{index + 1}{suffix}")


def redact_request(body):
    def redact(value):
        if isinstance(value, dict):
            return {key: redact(item) for key, item in value.items()}
        if isinstance(value, list):
            return [redact(item) for item in value]
        if isinstance(value, str) and value.startswith("data:") and "," in value:
            return value.split(",", 1)[0] + ",<base64-redacted>"
        return value

    return redact(body)


def summarize_response(response_json):
    return {
        "id": response_json.get("id"),
        "status": response_json.get("status"),
        "error": response_json.get("error", {}).get("message") if isinstance(response_json.get("error"), dict) else response_json.get("error"),
        "output": [
            {
                "type": item.get("type"),
                "status": item.get("status"),
                "role": item.get("role"),
                "content_types": [content.get("type") for content in item.get("content", [])] if isinstance(item.get("content"), list) else None,
                "error": item.get("error", {}).get("message") if isinstance(item.get("error"), dict) else item.get("error"),
            }
            for item in response_json.get("output", [])
        ],
    }


def parse_response_json(status, response_bytes):
    text = response_bytes.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except Exception:
        die(f"API returned non-JSON response with status {status}: {text[:500]}")


def post_json(endpoint, api_key, body):
    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def main():
    args = parse_args()
    prompt = read_prompt(args)
    config = resolve_codex_config(args)
    tool = build_tool(args)
    request_body = {
        "model": config["response_model"],
        "input": build_input(prompt, args),
        "tools": [tool],
    }
    endpoint = f"{config['base_url']}/responses"

    if args.dry_run:
        print(json.dumps({
            "codex_home": config["codex_home"],
            "config_path": config["config_path"],
            "auth_path": config["auth_path"],
            "provider": config["provider_name"],
            "base_url": config["base_url"],
            "endpoint": endpoint,
            "response_model": config["response_model"],
            "has_api_key": config["has_api_key"],
            "api_key_source": config["api_key_source"],
            "request": redact_request(request_body),
        }, indent=2))
        return

    status, response_bytes = post_json(endpoint, config["api_key"], request_body)
    response_json = parse_response_json(status, response_bytes)
    if status < 200 or status >= 300:
        message = response_json.get("error", {}).get("message") or response_json.get("message") or json.dumps(response_json)[:1000]
        die(f"API request failed with status {status}: {message}")

    image_results = [
        item["result"]
        for item in response_json.get("output", [])
        if item.get("type") == "image_generation_call" and item.get("result")
    ]
    if not image_results:
        print(json.dumps(summarize_response(response_json), indent=2), file=sys.stderr)
        die("response did not contain output[].type == image_generation_call with a result.")

    output_format = tool.get("output_format") or Path(args.out).suffix.lstrip(".") or "png"
    written = []
    for index, image_base64 in enumerate(image_results):
        target = output_path_for(args.out, index, len(image_results), output_format)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(base64.b64decode(image_base64))
        written.append(str(target))

    summary = {
        "response_id": response_json.get("id"),
        "provider": config["provider_name"],
        "base_url": config["base_url"],
        "response_model": config["response_model"],
        "image_model": tool.get("model") or "api-default",
        "outputs": written,
    }

    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        for file_path in written:
            print(f"Wrote {file_path}")


if __name__ == "__main__":
    main()
