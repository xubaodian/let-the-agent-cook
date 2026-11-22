import json
from collections.abc import Mapping
from functools import lru_cache
from threading import Lock
from time import monotonic
from types import MappingProxyType
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from agent_cook.model.openrouter import OPENROUTER_BASE_URL

_CATALOG_REFRESH_SECONDS = 300
_CATALOG_LOCK = Lock()


def openrouter_json(
    path: str, *, data: dict | None = None, key: str | None = None, timeout: float = 15,
) -> dict:
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if key:
        headers["Authorization"] = "Bearer " + key
    request = Request(
        OPENROUTER_BASE_URL + path, data=json.dumps(data).encode() if data is not None else None, headers=headers,
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            value = json.loads(response.read(8 * 1024 * 1024 + 1))
    except HTTPError as error:
        raise ValueError(f"OpenRouter returned HTTP {error.code}.") from error
    except (OSError, ValueError) as error:
        raise ValueError("Could not read the response from OpenRouter.") from error
    if not isinstance(value, dict):
        raise ValueError("OpenRouter returned an unexpected response.")
    return value


@lru_cache(maxsize=1)
def _read_context_windows(_refresh_period: int) -> Mapping[str, int]:
    try:
        models = openrouter_json("/models", timeout=3).get("data")
    except ValueError:
        models = None
    if not isinstance(models, list):
        return MappingProxyType({})
    return MappingProxyType({
        model["id"]: model["context_length"]
        for model in models if isinstance(model, dict) and isinstance(model.get("id"), str)
        and type(model.get("context_length")) is int and model["context_length"] > 1
    })


def openrouter_context_windows() -> Mapping[str, int]:
    """Reuse the public model catalog across tasks in this process."""
    with _CATALOG_LOCK:
        return _read_context_windows(int(monotonic()) // _CATALOG_REFRESH_SECONDS)
