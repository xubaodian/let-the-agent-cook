import json
import os
import sys
from pathlib import Path
from tempfile import NamedTemporaryFile

from agent_cook.model.catalog import ModelCatalog

DATA = Path(__file__).parent / "data"
SERVICE = "Agent Cook"
ACCOUNT = "OpenRouter"


class CredentialStoreError(ValueError):
    pass


def settings_directory() -> Path:
    home = Path.home()
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or home / "AppData/Roaming") / SERVICE
    if sys.platform == "darwin":
        return home / "Library/Application Support" / SERVICE
    return Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config") / "agent-cook"


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
