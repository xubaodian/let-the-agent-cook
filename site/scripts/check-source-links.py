"""Verify pinned source links from built HTML against this Git checkout."""

from html.parser import HTMLParser
import os
from pathlib import Path
import subprocess
from urllib.parse import unquote

REPO = "https://github.com/xubaodian/let-the-agent-cook/"
EXPECTED_REF = os.environ.get("PUBLIC_REPO_REF")
ROOT = Path(__file__).resolve().parents[2]


class SourceLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths = set()

    def handle_starttag(self, tag, attrs):
        href = dict(attrs).get("href") or ""
        if tag != "a" or not href.startswith(REPO):
            return
        parts = href.removeprefix(REPO).split("/", 2)
        if len(parts) != 3 or parts[0] not in {"blob", "tree"}:
            return
        _, ref, path = parts
        if EXPECTED_REF and ref != EXPECTED_REF:
            raise ValueError(f"The source link is not pinned to the built revision: {href}")
        self.paths.add(unquote(path.split("#", 1)[0]))


parser = SourceLinks()
for index in (ROOT / "site/dist").rglob("*.html"):
    parser.feed(index.read_text(encoding="utf-8"))
if not parser.paths:
    raise SystemExit("The site build contains no repository source links.")
for path in sorted(parser.paths):
    subprocess.run(["git", "cat-file", "-e", f"HEAD:{path}"], cwd=ROOT, check=True)
    print(path)
