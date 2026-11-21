import base64
import getpass
import hashlib
import json
import secrets
import sys
import time
import warnings
import webbrowser
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen

from agent_cook.model.catalog import ModelChoices
from agent_cook.model.openrouter import OPENROUTER_BASE_URL


def _openrouter(path: str, *, data: dict | None = None, key: str | None = None) -> dict:
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if key:
        headers["Authorization"] = "Bearer " + key
    request = Request(
        OPENROUTER_BASE_URL + path, data=json.dumps(data).encode() if data is not None else None, headers=headers,
    )
    try:
        with urlopen(request, timeout=15) as response:
            value = json.loads(response.read(8 * 1024 * 1024 + 1))
    except HTTPError as error:
        raise ValueError(f"OpenRouter returned HTTP {error.code}.") from error
    except (OSError, ValueError) as error:
        raise ValueError("Could not read the response from OpenRouter.") from error
    if not isinstance(value, dict):
        raise ValueError("OpenRouter returned an unexpected response.")
    return value


def connect_browser() -> str:
    verifier = secrets.token_urlsafe(32)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode().rstrip("=")
    callback = f"/{secrets.token_urlsafe(24)}/callback"
    code = None

    class Callback(BaseHTTPRequestHandler):
        timeout = 3

        def do_GET(self) -> None:
            nonlocal code
            request = urlsplit(self.path)
            codes = parse_qs(request.query).get("code", [])
            valid = request.path == callback and len(codes) == 1 and 0 < len(codes[0]) <= 4096
            self.send_response(200 if valid else 404)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if valid:
                code = codes[0]
                self.wfile.write(b"Authorization received. Return to Agent Cook to finish connecting.")

        def log_message(self, *args) -> None:
            # The callback URL contains the one-time authorization code.
            pass

    with HTTPServer(("127.0.0.1", 0), Callback) as server:
        server.timeout = 1
        query = urlencode({
            "callback_url": f"http://localhost:{server.server_port}{callback}",
            "code_challenge": challenge, "code_challenge_method": "S256",
        })
        url = f"https://openrouter.ai/auth?{query}"
        if not webbrowser.open(url):
            print(f"Open this URL in a browser on this computer: {url}", file=sys.stderr)
        print("Waiting for OpenRouter authorization. Press Ctrl+C to cancel.", file=sys.stderr)
        deadline = time.monotonic() + 180
        while code is None and time.monotonic() < deadline:
            server.handle_request()
    if code is None:
        raise ValueError("OpenRouter authorization timed out. Retry /setup or paste a key.")
    response = _openrouter("/auth/keys", data={
        "code": code, "code_verifier": verifier, "code_challenge_method": "S256",
    })
    key = response.get("key")
    if not isinstance(key, str) or not key.strip():
        raise ValueError("OpenRouter did not return an API key.")
    return key.strip()


def available_models() -> tuple[str, ...]:
    models = _openrouter("/models").get("data", [])
    if not isinstance(models, list):
        raise ValueError("OpenRouter did not return a model list.")
    return tuple(sorted({
        model["id"] for model in models
        if isinstance(model, dict) and isinstance(model.get("id"), str)
        and "tools" in (model.get("supported_parameters") or ())
        and "text" in ((model.get("architecture") or {}).get("output_modalities") or ())
    }))


def _choose_model(read: Callable[[str], str], choices: ModelChoices, current: str | None) -> str:
    options = choices.available
    if not options and current is None:
        try:
            options = available_models()
        except ValueError as error:
            print(f"{error} Enter a model ID manually.", file=sys.stderr)
    matches = options
    while True:
        for number, model in enumerate(matches[:15], 1):
            print(f"  {number}. {model}", file=sys.stderr)
        if len(matches) > 15:
            print("Enter /<text> to filter models.", file=sys.stderr)
        value = read(f"Model number or ID{f' [{current}]' if current else ''}: ").strip()
        if not value and current:
            return current
        if value.startswith("/"):
            matches = tuple(model for model in options if value[1:].casefold() in model.casefold())
        elif value.isascii() and value.isdecimal():
            if len(value) <= 2 and 1 <= int(value) <= min(15, len(matches)):
                return matches[int(value) - 1]
        elif value and not any(char.isspace() for char in value):
            return value


def read_key(provider: str, *, optional: bool, configured: bool = False) -> str | None:
    name = {"openrouter": "OpenRouter", "exa": "Exa"}[provider]
    hint = (" [Enter to keep]" if configured else " [Enter to skip]") if optional else ""
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            key = getpass.getpass(f"{name} API key{hint}: ").strip()
        except getpass.GetPassWarning as error:
            raise ValueError("Use a terminal that can hide your API key.") from error
    if not key and optional:
        return None
    if not key or any(char.isspace() for char in key):
        raise ValueError(f"Enter a valid {name} API key.")
    if provider == "openrouter":
        _openrouter("/key", key=key)
    return key


def connect(
    read: Callable[[str], str], choices: ModelChoices, *,
    api_key: str | None, exa_key: str | None, model: str | None,
) -> tuple[dict[str, str], str]:
    keys = {}
    prompt = (
        "OpenRouter is configured. Press Enter to keep it, or type browser or key to replace it: "
        if api_key else "Connect OpenRouter in browser [Enter], or type key to paste a key: "
    )
    method = read(prompt).strip().lower()
    if method == "key":
        if key := read_key("openrouter", optional=False):
            keys["openrouter"] = key
    elif method == "browser" or (not method and not api_key):
        keys["openrouter"] = connect_browser()
    elif method:
        raise ValueError("Choose browser or key to connect OpenRouter.")
    if key := read_key("exa", optional=True, configured=bool(exa_key)):
        keys["exa"] = key
    return keys, _choose_model(read, choices, model or choices.default)
