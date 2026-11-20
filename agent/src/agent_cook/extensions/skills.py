import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from agent_cook.extensions.files import FileTree, collect_workspace_files
from agent_cook.extensions.frontmatter import read_frontmatter
from agent_cook.local_files import LocalTree


@dataclass(frozen=True, slots=True)
class _Skill:
    name: str
    description: str
    source: LocalTree

    @property
    def entry(self) -> str:
        return f"{self.name}/SKILL.md"


class SkillCatalog:
    """Discover Skill metadata and supply package files to the task workspace."""

    def __init__(self, directories: Iterable[Path], mount: tuple[str, ...] | None = None) -> None:
        self._skills: dict[str, _Skill] = {}
        selected = None if mount is None else set(mount)
        for directory in directories:
            source = LocalTree(directory)
            entries, scan_limited = source.entries(".", recursive=False)
            if scan_limited:
                raise ValueError(f"The skills directory '{directory}' has too many entries.")
            for name, kind in entries:
                if kind != "directory" or (selected is not None and name not in selected):
                    continue
                entry = f"{name}/SKILL.md"
                try:
                    with source.open_file(entry) as stream:
                        description = _description(read_frontmatter(stream), name)
                except FileNotFoundError:
                    continue
                except (OSError, ValueError) as error:
                    raise ValueError(f"Invalid skill '{entry}' in '{directory}': {error}") from error
                if name in self._skills:
                    raise ValueError(f"The skill name '{name}' is registered more than once.")
                self._skills[name] = _Skill(name, description, source)
        if selected is not None and (missing := selected - self._skills.keys()):
            raise ValueError(f"Unknown mounted skills: {', '.join(sorted(missing))}.")

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._skills))

    def instructions(self, base: str) -> str:
        if not self._skills:
            return base
        metadata = [
            {
                "name": skill.name,
                "description": skill.description,
                "entry": skill.entry,
            }
            for skill in sorted(self._skills.values(), key=lambda skill: skill.name)
        ]
        catalog = (
            "Available skills. Use read_file at a relevant skill's entry to load its instructions. "
            "Use ls to discover files in that skill's directory.\n"
            + json.dumps(metadata, ensure_ascii=False)
        )
        return f"{base}\n\n{catalog}" if base else catalog

    def workspace_files(
        self, inputs: Mapping[str, bytes], *, extra_trees: Iterable[FileTree] = (),
    ) -> dict[str, bytes]:
        skills = (FileTree(skill.source, skill.name, skill.entry) for skill in self._skills.values())
        return collect_workspace_files(inputs, (*skills, *extra_trees))


def _description(metadata: dict, directory_name: str) -> str:
    name = metadata.get("name")
    if (
        not isinstance(name, str) or len(name) > 64 or name != directory_name
        or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) is None
    ):
        raise ValueError("The skill name must match its directory and use 1 to 64 lowercase characters, digits, or hyphens.")
    description = metadata.get("description")
    if not isinstance(description, str) or not description.strip() or len(description) > 1024:
        raise ValueError("The skill description must contain 1 to 1024 characters.")
    return description.strip()
