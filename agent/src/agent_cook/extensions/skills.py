import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

import yaml

from agent_cook.sandbox.limits import MAX_SCAN_ENTRIES, MAX_TEXT_FILE_BYTES
from agent_cook.sandbox.paths import SKILL_URI_PREFIX, workspace_path
from agent_cook.sandbox.worker.workspace import Workspace, page_entries, read_file


@dataclass(frozen=True, slots=True)
class _Skill:
    name: str
    description: str
    source: Workspace


class SkillCatalog:
    """Expose Skill metadata and the registered packages' read-only files."""

    def __init__(self, directories: Iterable[Path]) -> None:
        self._skills: dict[str, _Skill] = {}
        for directory in directories:
            source = Workspace(directory)
            entries, scan_limited = source.entries(".", recursive=False)
            if scan_limited:
                raise ValueError(f"The skills directory '{directory}' has too many entries.")
            for name, kind in entries:
                if kind != "directory":
                    continue
                entry = f"{name}/SKILL.md"
                try:
                    with source.open_file(entry) as stream:
                        description = _description(stream, name)
                except FileNotFoundError:
                    continue
                except (OSError, ValueError) as error:
                    raise ValueError(f"Invalid skill '{entry}' in '{directory}': {error}") from error
                if name in self._skills:
                    raise ValueError(f"The skill name '{name}' is registered more than once.")
                self._skills[name] = _Skill(name, description, source)

    def instructions(self, base: str) -> str:
        if not self._skills:
            return base
        metadata = [
            {
                "name": skill.name,
                "description": skill.description,
                "entry": f"{SKILL_URI_PREFIX}{skill.name}/SKILL.md",
            }
            for skill in sorted(self._skills.values(), key=lambda skill: skill.name)
        ]
        catalog = (
            "Available skills. Use read_file at a relevant skill's entry to load its instructions. "
            "Use ls to discover files in that skill's directory.\n"
            + json.dumps(metadata, ensure_ascii=False)
        )
        return f"{base}\n\n{catalog}" if base else catalog

    def _resolve(self, uri: str) -> tuple[_Skill, str]:
        name, _, relative = uri.removeprefix(SKILL_URI_PREFIX).partition("/")
        skill = self._skills.get(name)
        if skill is None:
            raise ValueError("Use the entry of a configured skill, or ls at skill:// to discover skills.")
        relative = workspace_path(relative or ".", allow_root=True)
        return skill, name if relative == "." else f"{name}/{relative}"

    def read_file(self, path: str, start_line: int = 1, line_count: int = 200) -> dict[str, Any]:
        skill, relative = self._resolve(path)
        result = read_file(skill.source, relative, start_line=start_line, line_count=line_count)
        result["path"] = SKILL_URI_PREFIX + result["path"]
        return result

    def list_files(
        self, directory: str, recursive: bool = False, max_results: int = 80,
        kind: str = "all", cursor: str | None = None,
    ) -> dict[str, Any]:
        if directory == SKILL_URI_PREFIX:
            entries, scan_limited = self._catalog_entries(recursive)
        else:
            skill, relative = self._resolve(directory)
            entries, scan_limited = skill.source.entries(relative, recursive=recursive)
        return page_entries(
            [(SKILL_URI_PREFIX + path, entry_kind) for path, entry_kind in entries],
            scan_limited, max_results=max_results, kind=kind, cursor=cursor,
        )

    def _catalog_entries(self, recursive: bool) -> tuple[list[tuple[str, str]], bool]:
        entries = [(name, "directory") for name in sorted(self._skills)]
        if len(entries) > MAX_SCAN_ENTRIES:
            return entries[:MAX_SCAN_ENTRIES], True
        if recursive:
            for skill in self._skills.values():
                children, scan_limited = skill.source.entries(
                    skill.name, recursive=True, scan_limit=MAX_SCAN_ENTRIES - len(entries),
                )
                entries.extend(children)
                if scan_limited:
                    return sorted(entries), True
        return sorted(entries), False


def _description(stream: BinaryIO, directory_name: str) -> str:
    if stream.readline(MAX_TEXT_FILE_BYTES + 1).removeprefix(b"\xef\xbb\xbf").rstrip(b"\r\n") != b"---":
        raise ValueError("SKILL.md must start with YAML frontmatter.")
    header = bytearray()
    while line := stream.readline(MAX_TEXT_FILE_BYTES - len(header) + 1):
        if line.rstrip(b"\r\n") == b"---":
            break
        header.extend(line)
        if len(header) > MAX_TEXT_FILE_BYTES:
            raise ValueError("SKILL.md frontmatter exceeds the text file limit.")
    else:
        raise ValueError("SKILL.md frontmatter must end with ---.")

    try:
        metadata = yaml.safe_load(bytes(header))
    except yaml.YAMLError as error:
        raise ValueError("SKILL.md contains invalid YAML frontmatter.") from error
    if not isinstance(metadata, dict):
        raise ValueError("SKILL.md frontmatter must contain name and description.")
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
