import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from agent_cook.core.types import RunResult
from agent_cook.delivery import publish_results
from agent_cook.local_files import collect_inputs, portable_path
from agent_cook.protocol.artifacts import ArtifactRef


class LocalFilesTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory())).resolve()

    def write(self, path, data=b"contents"):
        path = self.root / path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def test_folder_import_preserves_paths_and_skips_links(self):
        file = self.write("资料 样例/子目录/销售 数据.csv")
        outside = self.write("outside/private.txt").parent
        selected = self.root / "资料 样例"
        link = selected / "outside"
        if os.name == "nt":
            subprocess.run(["cmd", "/c", "mklink", "/j", str(link), str(outside)], check=True, capture_output=True)
        else:
            link.symlink_to(outside, target_is_directory=True)
        self.assertEqual(collect_inputs([selected]), {"资料 样例/子目录/销售 数据.csv": file.read_bytes()})
        with self.assertRaisesRegex(ValueError, "outside"):
            collect_inputs([link])

    def test_transfer_error_identifies_the_file(self):
        self.write("data/a.txt", b"a" * 3)
        self.write("data/b.txt", b"b" * 3)
        with patch("agent_cook.local_files.MAX_FILE_BYTES", 2):
            with self.assertRaisesRegex(ValueError, "a.txt"):
                collect_inputs([self.root / "data"])
        with patch("agent_cook.local_files.MAX_TRANSFER_BYTES", 5):
            with self.assertRaisesRegex(ValueError, "b.txt"):
                collect_inputs([self.root / "data"])

    def test_alias_names_and_device_paths_are_rejected(self):
        first = self.write("first/Report.txt")
        second = self.write("second/report.txt")
        with self.assertRaisesRegex(ValueError, "report.txt.*Report.txt"):
            collect_inputs([first, second])
        for path in ("results/NUL.txt", "results/name.", "results/name:stream", "results/C:"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                portable_path(path)

    def test_publish_does_not_expose_an_incomplete_result(self):
        existing = self.write("abc-artifacts/one/folder/first.txt")
        refs = (
            ArtifactRef("one-1", "abc-artifacts/one/folder/first.txt", "text/plain"),
            ArtifactRef("one-2", "abc-artifacts/one/folder/missing.txt", "text/plain"),
        )
        with self.assertRaises(FileNotFoundError):
            publish_results(self.root / "abc.jsonl", RunResult("abc", "", refs))
        self.assertTrue(existing.is_file())
        self.assertFalse((self.root / "abc-results").exists())
        self.assertFalse(list(self.root.glob(".abc-results-*")))
