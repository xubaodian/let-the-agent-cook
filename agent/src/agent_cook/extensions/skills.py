import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

import yaml

from agent_cook.sandbox.interface import SandboxError
from agent_cook.sandbox.limits import (
    MAX_FILE_BYTES, MAX_SCAN_ENTRIES, MAX_TEXT_FILE_BYTES, MAX_TRANSFER_BYTES,
)
from agent_cook.sandbox.worker.workspace import Workspace


@dataclass(frozen=True, slots=True)
class _Skill:
    name: str
    description: str
    source: Workspace

    @property
    def entry(self) -> str:
        return f"{self.name}/SKILL.md"


class SkillCatalog:
    """Discover Skill metadata and supply package files to the task workspace."""

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

    def workspace_files(self, inputs: Mapping[str, bytes]) -> dict[str, bytes]:
        files = dict(inputs)
        remaining_bytes = MAX_TRANSFER_BYTES - sum(len(content) for content in files.values())
        if remaining_bytes < 0:
            raise SandboxError("Workspace inputs exceed the batch transfer limit.")
        remaining_entries = MAX_SCAN_ENTRIES
        occupied = {path.split("/", 1)[0] for path in inputs}

        for skill in self._skills.values():
            if skill.name in occupied:
                raise SandboxError(f"Skill directory '{skill.name}' conflicts with a workspace input.")
            try:
                entries, scan_limited = skill.source.entries(
                    skill.name, recursive=True, scan_limit=remaining_entries,
                )
            except OSError as error:
                raise SandboxError(f"Could not list files for skill '{skill.name}'.") from error
            if scan_limited:
                raise SandboxError("Configured skills exceed the workspace file scan limit.")
            if (skill.entry, "file") not in entries:
                raise SandboxError(f"Skill '{skill.name}' is missing its SKILL.md file.")
            remaining_entries -= len(entries)

            for path, kind in entries:
                if kind != "file":
                    continue
                try:
                    content = skill.source.read_bytes(path, min(MAX_FILE_BYTES, remaining_bytes))
                except OSError as error:
                    raise SandboxError(f"Could not read skill file '{path}'.") from error
                except ValueError as error:
                    raise SandboxError(f"Skill file '{path}' exceeds the file or batch transfer limit.") from error
                files[path] = content
                remaining_bytes -= len(content)

        return files


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
