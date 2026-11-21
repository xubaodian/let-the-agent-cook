import json
from dataclasses import dataclass, field
from pathlib import Path

from jsonschema import Draft202012Validator, ValidationError

_MODEL_ID = {"type": "string", "minLength": 1, "not": {"pattern": r"\s"}}
MODEL_APIS = ("chat_completions", "responses")
_CHOICES = {
    "type": "object",
    "required": ["available"],
    "properties": {
        "default": {"oneOf": [_MODEL_ID, {"type": "null"}]},
        "available": {"type": "array", "items": _MODEL_ID, "uniqueItems": True},
    },
    "additionalProperties": False,
}
_CATALOG = Draft202012Validator({
    "type": "object",
    "required": ["chat"],
    "properties": {
        "chat": _CHOICES, "image": _CHOICES,
        "chat_apis": {
            "type": "object", "propertyNames": _MODEL_ID,
            "additionalProperties": {"enum": list(MODEL_APIS)},
        },
    },
    "additionalProperties": False,
})


@dataclass(frozen=True, slots=True)
class ModelChoices:
    available: tuple[str, ...] = ()
    default: str | None = None


@dataclass(frozen=True, slots=True)
class ModelCatalog:
    chat: ModelChoices = ModelChoices()
    image: ModelChoices = ModelChoices()
    chat_apis: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "ModelCatalog":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ValueError(f"Invalid model catalog JSON at line {error.lineno} in {path}.") from error
        return cls.parse(data)

    @classmethod
    def parse(cls, data: dict) -> "ModelCatalog":
        try:
            _CATALOG.validate(data)
        except ValidationError as error:
            raise ValueError(f"Invalid model catalog at {error.json_path}: {error.message}") from error

        groups = {}
        for group in ("chat", "image"):
            if group not in data:
                continue
            options = data[group]
            default = options.get("default")
            available = tuple(options["available"])
            if default is not None and default not in available:
                raise ValueError(f"Add the default {group} model '{default}' to {group}.available.")
            groups[group] = ModelChoices(available=available, default=default)
        apis = data.get("chat_apis", {})
        if missing := apis.keys() - set(groups["chat"].available):
            raise ValueError(f"Add {', '.join(sorted(missing))} to chat.available before selecting their API.")
        return cls(**groups, chat_apis=apis)
