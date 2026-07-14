from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest

import numpy as np

from retrieval.embed import BGEEncoder, HashingEncoder
from retrieval.index import build_index, load_index


FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "chunks.jsonl"


class OptionalProductionSmokeTests(unittest.TestCase):
    @unittest.skipUnless(
        os.getenv("RUN_BGE_SMOKE") == "1",
        "set RUN_BGE_SMOKE=1 to download and exercise the real BGE model",
    )
    def test_real_bge_encoder(self) -> None:
        encoder = BGEEncoder()
        documents = encoder.encode_documents(
            ["课程考核不合格的学生可以按规定重修。", "金融科技是专业选修课。"]
        )
        query = encoder.encode_query("重修课程有什么规定")
        self.assertEqual(documents.shape[1], encoder.dimension)
        self.assertEqual(query.shape, (encoder.dimension,))
        self.assertTrue(np.isfinite(documents).all())
        self.assertGreater(float(documents[0] @ query), float(documents[1] @ query))

    @unittest.skipUnless(
        os.getenv("RUN_FAISS_SMOKE") == "1",
        "set RUN_FAISS_SMOKE=1 to exercise the installed faiss-cpu backend",
    )
    def test_real_faiss_artifact_backend(self) -> None:
        encoder = HashingEncoder(256)
        with tempfile.TemporaryDirectory() as artifacts:
            manifest = build_index(FIXTURE_PATH, artifacts, encoder)
            self.assertEqual(manifest["backend"], "faiss-index-flat-ip")
            bundle = load_index(FIXTURE_PATH, artifacts, encoder)
            self.assertEqual(bundle.faiss_index.ntotal, len(bundle.chunks))


if __name__ == "__main__":
    unittest.main()

