import json
from dataclasses import dataclass
from pathlib import Path

from jsonschema import Draft202012Validator, ValidationError

from agent_cook.extensions.plugins import plugin_path
from agent_cook.model.catalog import MODEL_APIS
from agent_cook.sandbox.urls import public_https_origin

_STRINGS = {
    "type": "array", "items": {"type": "string", "minLength": 1}, "uniqueItems": True,
}
_LEGACY_SKILLS = {
    "type": "object",
    "properties": {"directories": _STRINGS, "mount": _STRINGS},
    "additionalProperties": False,
}
_REQUEST = Draft202012Validator({
    "type": "object",
    "required": ["task"],
    "properties": {
        "schema_version": {"type": "integer", "const": 1},
        "task": {"type": "string", "minLength": 1},
        "model": {"type": "string", "pattern": r"\S"},
        "model_api": {"enum": list(MODEL_APIS)},
        "image_model": {"type": "string", "pattern": r"\S"},
        "plugins": _STRINGS,
        "skill_directories": _STRINGS,
        "skills": {"oneOf": [_STRINGS, _LEGACY_SKILLS]},
        "mcp_tools": _STRINGS,
        "web": {
            "type": "object",
            "properties": {"enabled": {"type": "boolean"}, "browser_origins": _STRINGS},
            "additionalProperties": False,
        },
    },
    "additionalProperties": False,
})


@dataclass(frozen=True, slots=True)
class WebOptions:
    enabled: bool | None = None
    browser_origins: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        origins = tuple(public_https_origin(url, origin_only=True) for url in self.browser_origins)
        if len(set(origins)) != len(origins):
            raise ValueError("List each browser origin only once.")
        object.__setattr__(self, "browser_origins", origins)


@dataclass(frozen=True, slots=True)
class RunRequest:
    task: str
    plugins: tuple[Path, ...] = ()
    skill_directories: tuple[Path, ...] = ()
    mounted_skills: tuple[str, ...] | None = None
    mcp_tools: tuple[str, ...] | None = None
    web: WebOptions = WebOptions()
    model: str | None = None
    image_model: str | None = None
    model_api: str | None = None

    def __post_init__(self) -> None:
        if not self.task.strip():
            raise ValueError("A task must contain text.")
        if self.model_api is not None and self.model_api not in MODEL_APIS:
            raise ValueError("model_api must be chat_completions or responses.")

    @classmethod
    def from_file(cls, path: Path) -> "RunRequest":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ValueError(f"Invalid run request JSON at line {error.lineno}.") from error
        return cls.parse(data, base=path.resolve().parent)

    @classmethod
    def parse(cls, data: dict, *, base: Path = Path(".")) -> "RunRequest":
        try:
            _REQUEST.validate(data)
        except ValidationError as error:
            raise ValueError(f"Invalid run request at {error.json_path}: {error.message}") from error

        directories = data.get("skill_directories", ())
        skills = data.get("skills")
        if isinstance(skills, dict):
            directories = (*directories, *skills.get("directories", ()))
            skills = skills.get("mount")
        web = data.get("web", {})
        return cls(
            task=data["task"],
            plugins=tuple(plugin_path(item, base) for item in data.get("plugins", ())),
            skill_directories=tuple(base / item for item in directories),
            mounted_skills=tuple(skills) if skills is not None else None,
            mcp_tools=tuple(data["mcp_tools"]) if "mcp_tools" in data else None,
            web=WebOptions(web.get("enabled"), tuple(web.get("browser_origins", ()))),
            model=data.get("model"),
            image_model=data.get("image_model"),
            model_api=data.get("model_api"),
        )
