import hashlib
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


class BaselineCompatibilityTest(unittest.TestCase):
    def test_manshu_html_unchanged_from_phase0_baseline(self):
        manifest = json.loads((ROOT / "reports/research_v2/baseline_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(sha256(ROOT / "manshu.html"), manifest["manshu_html_hash"])

    def test_target_production_script_hashes_unchanged(self):
        manifest = json.loads((ROOT / "reports/research_v2/baseline_manifest.json").read_text(encoding="utf-8"))
        for rel_path, expected_hash in manifest["script_hashes"].items():
            with self.subTest(path=rel_path):
                self.assertEqual(sha256(ROOT / rel_path), expected_hash)


if __name__ == "__main__":
    unittest.main()
