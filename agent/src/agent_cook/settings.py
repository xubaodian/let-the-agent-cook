import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import NamedTemporaryFile

from jsonschema import Draft202012Validator, ValidationError

from agent_cook.model.catalog import MODEL_APIS, ModelCatalog

DATA = Path(__file__).parent / "data"
SERVICE = "Agent Cook"
ACCOUNT = "OpenRouter"
CONFIG_FILE = "agent-cook.json"
_TEXT = {"type": ["string", "null"]}
_POSITIVE_INTEGER = {"type": "integer", "minimum": 1}


def _section(**properties: dict) -> dict:
    return {"type": "object", "properties": properties, "additionalProperties": False}


_CONFIG = Draft202012Validator(_section(
    api_keys=_section(openrouter=_TEXT, exa=_TEXT, e2b=_TEXT),
    models={"type": "object"},
    model_api={"enum": [*MODEL_APIS, None]},
    sandbox=_section(
        backend={"enum": ["docker", "e2b", "none", None]},
        docker_image=_TEXT, e2b_template=_TEXT, network={"type": "boolean"},
    ),
    server=_section(
        token=_TEXT, host={"type": "string", "minLength": 1},
        port={"type": "integer", "minimum": 1, "maximum": 65535},
        data_dir=_TEXT, concurrency=_POSITIVE_INTEGER,
    ),
    run=_section(
        context_window=_POSITIVE_INTEGER, max_turns=_POSITIVE_INTEGER,
        timeout={"type": "number", "exclusiveMinimum": 0},
    ),
))


class CredentialStoreError(ValueError):
    pass


def settings_directory() -> Path:
    home = Path.home()
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or home / "AppData/Roaming") / SERVICE
    if sys.platform == "darwin":
        return home / "Library/Application Support" / SERVICE
    return Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config") / "agent-cook"


@dataclass(frozen=True, slots=True)
class AgentConfig:
    models: ModelCatalog | None = None
    model_api: str | None = None
    api_keys: dict = field(default_factory=dict, repr=False)
    sandbox: dict = field(default_factory=dict)
    server: dict = field(default_factory=dict, repr=False)
    run: dict = field(default_factory=dict)

    @classmethod
    def load(cls, directory: Path, path: Path | None = None) -> "AgentConfig":
        if path is None:
            candidates = (directory / CONFIG_FILE, Path.cwd() / CONFIG_FILE, settings_directory() / CONFIG_FILE)
            path = next((candidate for candidate in candidates if candidate.is_file()), None)
        if path is None:
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            _CONFIG.validate(data)
        except json.JSONDecodeError as error:
            raise ValueError(f"Invalid JSON at line {error.lineno} in '{path}'.") from None
        except ValidationError as error:
            raise ValueError(f"Invalid configuration at {error.json_path} in '{path}'.") from None
        if "models" in data:
            try:
                data["models"] = ModelCatalog.parse(data["models"])
            except ValueError as error:
                raise ValueError(f"Invalid models in '{path}'. {error}") from None
        return cls(**data)

    def api_key(self, provider: str) -> str | None:
        return os.environ.get(f"{provider.upper()}_API_KEY", "").strip() or (
            self.api_keys.get(provider) or ""
        ).strip() or None

    @property
    def e2b_template(self) -> str:
        return os.environ.get("E2B_TEMPLATE", "").strip() or (self.sandbox.get("e2b_template") or "").strip()


def model_catalog(path: Path | None) -> ModelCatalog:
    if path is not None:
        return ModelCatalog.load(path)
    for candidate in (settings_directory() / "models.json", DATA / "models.json"):
        if candidate.is_file():
            return ModelCatalog.load(candidate)
    return ModelCatalog()


def release_image() -> str | None:
    path = DATA / "release.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))["sandbox_image"]


def saved_model() -> str | None:
    path = settings_directory() / "settings.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as error:
        raise ValueError(f"Check the model settings in '{path}'.") from error
    if not isinstance(data, dict) or not isinstance(model := data.get("model"), str) or not model.strip():
        raise ValueError(f"Choose a valid model ID in '{path}'.")
    return model.strip()


def save_model(model: str) -> None:
    directory = settings_directory()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump({"model": model}, stream)
            stream.close()
            temporary.replace(directory / "settings.json")
        finally:
            temporary.unlink(missing_ok=True)


def _keyring():
    # Select the OS store explicitly so a plaintext backend cannot be chosen by keyring configuration.
    if sys.platform == "win32":
        from keyring.backends.Windows import WinVaultKeyring

        return WinVaultKeyring()
    if sys.platform == "darwin":
        from keyring.backends.macOS import Keyring
    else:
        from keyring.backends.SecretService import Keyring
    return Keyring()


def stored_key() -> str | None:
    try:
        return _keyring().get_password(SERVICE, ACCOUNT)
    except Exception as error:
        raise CredentialStoreError("Could not read the OS credential store.") from error


def save_key(key: str) -> None:
    try:
        _keyring().set_password(SERVICE, ACCOUNT, key)
    except Exception as error:
        raise CredentialStoreError("Could not save the OpenRouter key to the OS credential store.") from error
