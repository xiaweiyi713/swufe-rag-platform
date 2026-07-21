from __future__ import annotations

import io
import json
from pathlib import Path
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from contracts import KnowledgeBaseNotReadyError
from retrieval.embed import BGEEncoder, HashingEncoder
from retrieval.index import build_index, load_index
from scripts.build_tier1_dataset import _official_url
from scripts.fetch_runtime_data import extract_archive, load_release
from scripts.reproduce_tier1 import CHUNKS, SOURCE_MANIFEST, SOURCES, verify_sources


FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "chunks.jsonl"


class RevisionEncoder(HashingEncoder):
    def __init__(self, revision: str) -> None:
        super().__init__(256)
        self.revision = revision

    @property
    def model_name(self) -> str:
        return "revision-test-encoder"

    @property
    def model_revision(self) -> str:
        return self.revision


class ReproducibilityTests(unittest.TestCase):
    def test_index_manifest_pins_model_revision(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            artifacts = Path(temporary) / "artifacts"
            manifest = build_index(
                FIXTURE_PATH,
                artifacts,
                RevisionEncoder("abc123"),
                allow_test_backend=True,
            )
            self.assertEqual(manifest["model_revision"], "abc123")
            load_index(
                FIXTURE_PATH,
                artifacts,
                RevisionEncoder("abc123"),
                allow_test_backend=True,
            )
            with self.assertRaisesRegex(
                KnowledgeBaseNotReadyError, "revision"
            ):
                load_index(
                    FIXTURE_PATH,
                    artifacts,
                    RevisionEncoder("different"),
                    allow_test_backend=True,
                )

    def test_committed_tier1_inputs_match_manifest(self) -> None:
        manifest = verify_sources()
        self.assertEqual(manifest["files"]["chunks.jsonl"]["rows"], 482)
        self.assertEqual(manifest["files"]["sources.csv"]["rows"], 5)
        self.assertTrue(CHUNKS.is_file())
        self.assertTrue(SOURCES.is_file())
        self.assertTrue(SOURCE_MANIFEST.is_file())
        titles = {document["doc_title"] for document in manifest["documents"]}
        self.assertEqual(len(titles), 5)

    def test_tier1_source_guard_rejects_lookalike_domains(self) -> None:
        self.assertTrue(_official_url("https://jwc.swufe.edu.cn/plan.pdf"))
        self.assertFalse(_official_url("http://jwc.swufe.edu.cn/plan.pdf"))
        self.assertFalse(_official_url("https://swufe.edu.cn.attacker.example/plan.pdf"))

    def test_local_model_path_keeps_canonical_index_identity(self) -> None:
        model = Mock()
        model.device = "cpu"
        constructor = Mock(return_value=model)
        encoder = BGEEncoder(
            "BAAI/bge-large-zh-v1.5",
            revision="pinned-revision",
            model_path="/models/bge-large-zh-v1.5",
        )
        with patch.dict(
            "sys.modules",
            {"sentence_transformers": SimpleNamespace(SentenceTransformer=constructor)},
        ):
            self.assertIs(encoder._load_model(), model)
        constructor.assert_called_once_with("/models/bge-large-zh-v1.5")
        self.assertEqual(encoder.model_name, "BAAI/bge-large-zh-v1.5")
        self.assertEqual(encoder.model_revision, "pinned-revision")

    def test_runtime_archive_rejects_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive_path = root / "unsafe.tar.gz"
            payload = b"not allowed"
            with tarfile.open(archive_path, "w:gz") as archive:
                member = tarfile.TarInfo("../escape")
                member.size = len(payload)
                archive.addfile(member, io.BytesIO(payload))
            with self.assertRaisesRegex(RuntimeError, "unsafe archive path"):
                extract_archive(archive_path, root / "output")

    def test_tier1_manifest_is_json_serializable(self) -> None:
        manifest = json.loads(SOURCE_MANIFEST.read_text(encoding="utf-8"))
        self.assertIn("79e7739b6ab944e86d6171e44d24c997fc1e0116", json.dumps(manifest))

    def test_runtime_release_pins_archive_and_model(self) -> None:
        release = load_release()
        self.assertEqual(release["archive"]["size"], 537956303)
        self.assertEqual(
            release["archive"]["sha256"],
            "b4cb04c3f3e018907f39e405271d676fbdb16d7d4deec86ae195da1ab8c96934",
        )
        self.assertEqual(
            release["model"]["revision"],
            "79e7739b6ab944e86d6171e44d24c997fc1e0116",
        )


if __name__ == "__main__":
    unittest.main()
