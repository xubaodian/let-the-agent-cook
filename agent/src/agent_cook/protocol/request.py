import json
from dataclasses import dataclass
from pathlib import Path

from jsonschema import Draft202012Validator, ValidationError

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
        "plugins": _STRINGS,
        "skill_directories": _STRINGS,
        "skills": {"oneOf": [_STRINGS, _LEGACY_SKILLS]},
        "mcp_tools": _STRINGS,
    },
    "additionalProperties": False,
})


@dataclass(frozen=True, slots=True)
class RunRequest:
    task: str
    plugins: tuple[Path, ...] = ()
    skill_directories: tuple[Path, ...] = ()
    mounted_skills: tuple[str, ...] | None = None
    mcp_tools: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if not self.task.strip():
            raise ValueError("A task must contain text.")

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
        return cls(
            task=data["task"],
            plugins=tuple(base / item for item in data.get("plugins", ())),
            skill_directories=tuple(base / item for item in directories),
            mounted_skills=tuple(skills) if skills is not None else None,
            mcp_tools=tuple(data["mcp_tools"]) if "mcp_tools" in data else None,
        )
