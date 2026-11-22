import csv
import json
import os
import re
import sqlite3
import sys
from contextlib import closing, suppress
from dataclasses import asdict, dataclass, field
from pathlib import Path
from subprocess import SubprocessError, run
from tempfile import mkstemp

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
        backend={"enum": ["native", "docker", "e2b", "none", None]},
        docker_image=_TEXT, e2b_template=_TEXT, network={"type": "boolean"},
    ),
    server=_section(
        token=_TEXT, host={"type": "string", "minLength": 1},
        port={"type": "integer", "minimum": 1, "maximum": 65535},
        data_dir=_TEXT, concurrency=_POSITIVE_INTEGER,
    ),
    workspace_dir=_TEXT,
    run=_section(
        context_window=_POSITIVE_INTEGER, max_turns=_POSITIVE_INTEGER,
        # Accept older context window and task deadline fields in saved configurations.
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
    path: Path
    models: ModelCatalog | None = None
    model_api: str | None = None
    api_keys: dict = field(default_factory=dict, repr=False)
    sandbox: dict = field(default_factory=dict)
    server: dict = field(default_factory=dict, repr=False)
    run: dict = field(default_factory=dict)
    workspace_dir: str | None = None

    @classmethod
    def load(cls, path: Path | None = None) -> "AgentConfig":
        location = (path if path is not None else settings_directory() / CONFIG_FILE).resolve()
        return cls._parse(location, _read_config(location, missing_ok=path is None))

    @classmethod
    def _parse(cls, path: Path, data: dict) -> "AgentConfig":
        try:
            _CONFIG.validate(data)
        except ValidationError as error:
            raise ValueError(f"Invalid configuration at {error.json_path} in '{path}'.") from None
        options = dict(data)
        if "models" in data:
            try:
                options["models"] = ModelCatalog.parse(data["models"])
            except ValueError as error:
                raise ValueError(f"Invalid models in '{path}'. {error}") from None
        return cls(path, **options)

    def ensure(self, catalog: ModelCatalog) -> "AgentConfig":
        return self if self.path.is_file() else self.update(catalog=catalog)

    def update(
        self, *, keys: dict[str, str] | None = None, model: str | None = None, catalog: ModelCatalog,
    ) -> "AgentConfig":
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Serialize changes from different CLI processes before reading the latest JSON.
        with closing(sqlite3.connect(path.with_name(f".{path.name}.lock"), timeout=5)) as lease:
            lease.execute("BEGIN IMMEDIATE")
            exists = path.is_file()
            data = _read_config(path, missing_ok=True)
            self._parse(path, data)
            if not exists:
                data["api_keys"] = {"openrouter": None, "exa": None}
                data["models"] = _catalog_data(catalog)
                if model is None:
                    with suppress(ValueError):
                        model = saved_model()
            if keys:
                data.setdefault("api_keys", {}).update(keys)
            if model is not None:
                if "models" not in data:
                    data["models"] = _catalog_data(catalog)
                chat = data["models"]["chat"]
                if model not in chat["available"]:
                    chat["available"].append(model)
                chat["default"] = model
            updated = self._parse(path, data)
            if not exists or keys or model is not None:
                _write_private_json(path, data)
        return updated

    def api_key(self, provider: str) -> str | None:
        return self.key_with_source(provider)[0]

    def key_with_source(self, provider: str) -> tuple[str | None, str | None]:
        variable = f"{provider.upper()}_API_KEY"
        if key := os.environ.get(variable, "").strip():
            return key, variable
        if key := (self.api_keys.get(provider) or "").strip():
            return key, self.path.name
        if provider == "openrouter":
            try:
                if key := stored_key():
                    return key, "legacy system credential store"
            except CredentialStoreError:
                pass
        return None, None

    @property
    def e2b_template(self) -> str:
        return os.environ.get("E2B_TEMPLATE", "").strip() or (self.sandbox.get("e2b_template") or "").strip()

    def workspace(self) -> Path:
        override = os.environ.get("AGENT_COOK_WORKSPACE", "").strip() or (self.workspace_dir or "").strip()
        if override:
            return (self.path.parent / Path(override).expanduser()).resolve()
        return Path.home() / "Agent Cook" / "workspace"


def _read_config(path: Path, *, missing_ok: bool) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        if missing_ok:
            return {}
        raise
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid JSON at line {error.lineno} in '{path}'.") from None


def _catalog_data(catalog: ModelCatalog) -> dict:
    data = asdict(catalog)
    for group in ("chat", "image"):
        data[group]["available"] = list(data[group]["available"])
    return data


def _restrict_windows_file(path: Path) -> None:
    system = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    options = {"check": True, "capture_output": True, "text": True, "errors": "replace", "timeout": 10}
    try:
        identity = run([system / "whoami.exe", "/user", "/fo", "csv", "/nh"], **options)
        user = next(csv.reader(identity.stdout.splitlines()), [])
        if len(user) != 2 or not re.fullmatch(r"S-1-[0-9-]+", user[1]):
            raise OSError("Could not identify the current Windows user.")
        run([system / "icacls.exe", path, "/inheritance:r", "/grant:r", f"*{user[1]}:F"], **options)
    except SubprocessError as error:
        raise OSError("Could not restrict the configuration to the current Windows user.") from error


def _write_private_json(path: Path, data: dict) -> None:
    write_private_file(path, (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def write_private_file(path: Path, content: bytes, *, executable: bool = False) -> None:
    descriptor, filename = mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(filename)
    os.close(descriptor)
    try:
        if sys.platform == "win32":
            _restrict_windows_file(temporary)
        elif executable:
            temporary.chmod(0o700)
        temporary.write_bytes(content)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


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
