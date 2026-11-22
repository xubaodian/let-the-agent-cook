import json
import os
import re
import sys
from pathlib import Path

from agent_cook.protocol.events import RunEvent

_MENTION = re.compile(r'(?<![\w@])@(?:"(?P<quoted>(?:[^"\\]|\\.)*)"|(?P<path>[^\s"@]+))')
_WINDOWS_MENTION = re.compile(r'(?<![\w@])@(?:"(?P<quoted>[^"]*)"|(?P<path>[^\s"@]+))')
_TYPING = re.compile(r'(?<![\w@])@(?P<path>"[^"]*|[^\s"@]*)$')


def file_mentions(prompt: str, directory: Path) -> tuple[str, list[Path]]:
    paths = []

    def attach(match: re.Match[str]) -> str:
        if match["quoted"] is not None:
            selected = match["quoted"]
            if os.name != "nt":
                selected = json.loads('"' + selected + '"')
        else:
            selected = match["path"]
        path = directory / Path(selected).expanduser()
        paths.append(path)
        return json.dumps(path.name, ensure_ascii=False)

    pattern = _WINDOWS_MENTION if os.name == "nt" else _MENTION
    return pattern.sub(attach, prompt), paths


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
                text = path.replace(os.sep, "/")
                quoted = json.dumps(text, ensure_ascii=False)
                yield Completion(quoted, start_position=-len(raw), display=completion.display)
                if directory_candidate:
                    browse = json.dumps(text.rstrip("/") + "/", ensure_ascii=False)[:-1]
                    yield Completion(
                        browse, start_position=-len(raw), display=f"Browse {completion.display_text}/",
                    )

    return PromptSession(completer=FileCompleter(), complete_while_typing=True).prompt


def show_progress(event: RunEvent) -> None:
    if event.type == "run.started" and event.parent_run_id is None and "context_window" in event.data:
        print(
            f"  Context window {event.data['context_window']:,} tokens. "
            f"Input budget {event.data['input_budget']:,}. Compact at {event.data['compaction_threshold']:,}.",
            file=sys.stderr,
        )
    elif event.type == "tool.started":
        print(f"  {event.data['name']} …", file=sys.stderr)
    elif event.type == "context.compaction_started":
        print(f"  Compacting context ({event.data['estimated_input_tokens']:,} estimated input tokens).", file=sys.stderr)
    elif event.type == "context.compaction_retry":
        print("  Retrying the context checkpoint.", file=sys.stderr)
    elif event.type == "context.compaction_failed":
        reason = str(event.data["reason"]).replace("_", " ")
        print(f"  Context compaction failed ({reason}).", file=sys.stderr)
    elif event.type == "context.compacted":
        print("  Compacted conversation context.", file=sys.stderr)
