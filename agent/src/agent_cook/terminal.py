import json
import re
import sys
from pathlib import Path

from agent_cook.protocol.events import RunEvent

_MENTION = re.compile(r'(?<![\w@])@(?:"(?P<quoted>(?:[^"\\]|\\.)*)"|(?P<path>[^\s"@]+))')
_TYPING = re.compile(r'(?<![\w@])@(?P<path>"[^"]*|[^\s"@]*)$')


def file_mentions(prompt: str, directory: Path) -> tuple[str, list[Path]]:
    paths = []

    def attach(match: re.Match[str]) -> str:
        if match["quoted"] is not None:
            selected = json.loads('"' + match["quoted"] + '"')
        else:
            selected = match["path"]
        path = directory / Path(selected).expanduser()
        paths.append(path)
        return json.dumps(path.name, ensure_ascii=False)

    return _MENTION.sub(attach, prompt), paths


def prompt_reader(directory: Path):
    from prompt_toolkit import PromptSession
    from prompt_toolkit.completion import Completer, Completion, PathCompleter
    from prompt_toolkit.document import Document

    paths = PathCompleter(get_paths=lambda: [str(directory)], expanduser=True)

    class FileCompleter(Completer):
        def get_completions(self, document, event):
            match = _TYPING.search(document.text_before_cursor)
            if match is None:
                return
            raw = match["path"]
            prefix = raw.removeprefix('"')
            for completion in paths.get_completions(Document(prefix), event):
                offset = len(prefix) + completion.start_position
                path = prefix[:offset] + completion.text
                directory_candidate = (directory / Path(path).expanduser()).is_dir()
                if directory_candidate and not path.endswith("/"):
                    path += "/"
                quoted = json.dumps(path, ensure_ascii=False)
                if directory_candidate:
                    replacement = quoted[:-1]
                else:
                    replacement = quoted
                yield Completion(replacement, start_position=-len(raw), display=completion.display)

    return PromptSession(completer=FileCompleter(), complete_while_typing=True).prompt


def show_progress(event: RunEvent) -> None:
    if event.type == "tool.started":
        print(f"  {event.data['name']} …", file=sys.stderr)
    if event.type == "context.compacted":
        print("  Compacted conversation context.", file=sys.stderr)
