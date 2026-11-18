from pathlib import PurePosixPath


def workspace_path(value: str, *, allow_root: bool = False) -> str:
    if (
        not value
        or len(value.encode("utf-8")) > 1024
        or value.startswith("/")
        or "\\" in value
        or any(ord(char) < 32 for char in value)
        or ".." in value.split("/")
    ):
        raise ValueError("Use a relative path inside the task workspace.")
    path = PurePosixPath(value).as_posix()
    if path == "." and not allow_root:
        raise ValueError("Provide a file path inside the task workspace.")
    return path
