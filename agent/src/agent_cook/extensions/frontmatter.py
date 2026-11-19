from typing import BinaryIO

import yaml

from agent_cook.sandbox.limits import MAX_TEXT_FILE_BYTES


def read_frontmatter(stream: BinaryIO) -> dict:
    first = stream.readline(MAX_TEXT_FILE_BYTES + 1)
    if first.removeprefix(b"\xef\xbb\xbf").rstrip(b"\r\n") != b"---":
        raise ValueError("The file must start with YAML frontmatter.")
    header = bytearray()
    while line := stream.readline(MAX_TEXT_FILE_BYTES - len(header) + 1):
        if line.rstrip(b"\r\n") == b"---":
            break
        header.extend(line)
        if len(header) > MAX_TEXT_FILE_BYTES:
            raise ValueError("Frontmatter exceeds the text file limit.")
    else:
        raise ValueError("Frontmatter must end with ---.")
    try:
        metadata = yaml.safe_load(bytes(header))
    except yaml.YAMLError as error:
        raise ValueError("The file contains invalid YAML frontmatter.") from error
    if not isinstance(metadata, dict):
        raise ValueError("Frontmatter must contain a mapping.")
    return metadata
