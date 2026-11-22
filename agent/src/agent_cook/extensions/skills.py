import json
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from agent_cook.extensions.files import FileTree, collect_workspace_files
from agent_cook.extensions.frontmatter import read_frontmatter
from agent_cook.local_files import LocalTree
from agent_cook.settings import DATA


@dataclass(frozen=True, slots=True)
class _Skill:
    name: str
    description: str
    source: LocalTree
    bundled: bool

    @property
    def entry(self) -> str:
        return f"{self.name}/SKILL.md"


class SkillCatalog:
    """Discover Skill metadata and supply package files to the task workspace."""

    def __init__(
        self, directories: Iterable[Path], mount: tuple[str, ...] | None = None, *,
        include_bundled: bool = False, required: Iterable[str] = (),
    ) -> None:
        self._skills: dict[str, _Skill] = {}
        referenced = set(required)
        selected = None if mount is None else set(mount) | referenced
        for directory in directories:
            self._discover(directory, selected)
        if include_bundled:
            self._discover(DATA / "skills", selected, fallback=True)
        expected = selected if selected is not None else referenced
        if missing := expected - self._skills.keys():
            raise ValueError(f"Unknown mounted skills: {', '.join(sorted(missing))}.")

    def _discover(self, directory: Path, selected: set[str] | None, *, fallback: bool = False) -> None:
        source = LocalTree(directory)
        entries, scan_limited = source.entries(".", recursive=False)
        if scan_limited:
            raise ValueError(f"The skills directory '{directory}' has too many entries.")
        for name, kind in entries:
            if kind != "directory" or (selected is not None and name not in selected):
                continue
            if fallback and name in self._skills:
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
            self._skills[name] = _Skill(name, description, source, bundled=fallback)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._skills))

    def bundled_prefix(
        self, inputs: Iterable[str], extra_trees: Iterable[FileTree],
    ) -> str:
        paths = (*inputs, *(tree.directory for tree in extra_trees))
        occupied = {unicodedata.normalize("NFC", path.partition("/")[0].casefold()) for path in paths}
        if not any(skill.bundled and skill.name in occupied for skill in self._skills.values()):
            return ""
        occupied.update(self._skills)
        directory = "skills"
        suffix = 1
        while directory in occupied:
            suffix += 1
            directory = f"skills-{suffix}"
        return f"{directory}/"

    def instructions(self, base: str, *, bundled_prefix: str = "") -> str:
        if not self._skills:
            return base
        metadata = [
            {
                "name": skill.name,
                "description": skill.description,
                "entry": bundled_prefix + skill.entry if skill.bundled else skill.entry,
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
        trees = tuple(extra_trees)
        prefix = self.bundled_prefix(inputs, trees)
        custom: list[FileTree] = []
        bundled: list[FileTree] = []
        for skill in self._skills.values():
            target = bundled if skill.bundled else custom
            target.append(FileTree(skill.source, skill.name, skill.entry))
        files = collect_workspace_files(inputs, (*custom, *trees))
        # The installed guides have their own transfer allowance and cannot shadow task files.
        for path, contents in collect_workspace_files({}, bundled).items():
            files[prefix + path] = contents
        return files


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
