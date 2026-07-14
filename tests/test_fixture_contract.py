from __future__ import annotations

import json
from pathlib import Path
import unittest

from contracts import ContractError, validate_chunk


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "chunks.jsonl"


class FixtureContractTests(unittest.TestCase):
    def load_chunks(self) -> list[dict]:
        chunks: list[dict] = []
        with FIXTURE_PATH.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if line.strip():
                    chunks.append(
                        validate_chunk(json.loads(line), line_number=line_number)
                    )
        return chunks

    def test_fixture_has_required_coverage(self) -> None:
        chunks = self.load_chunks()
        self.assertGreaterEqual(len(chunks), 20)
        self.assertTrue(all(chunk["chunk_id"].startswith("fixture_") for chunk in chunks))
        self.assertEqual(len(chunks), len({chunk["chunk_id"] for chunk in chunks}))
        self.assertGreaterEqual(len({chunk["college"] for chunk in chunks}), 3)
        self.assertGreaterEqual(len({chunk["cohort"] for chunk in chunks}), 4)
        self.assertEqual({chunk["level"] for chunk in chunks}, {"校级", "院级"})
        self.assertEqual({chunk["status"] for chunk in chunks}, {"现行", "历史"})
        self.assertTrue(any(chunk["is_table"] for chunk in chunks))

    def test_school_chunk_requires_canonical_college(self) -> None:
        chunk = self.load_chunks()[0]
        invalid = {**chunk, "college": "校级"}
        with self.assertRaisesRegex(ContractError, "college=全校"):
            validate_chunk(invalid)

    def test_extra_field_is_rejected(self) -> None:
        invalid = {**self.load_chunks()[0], "debug": True}
        with self.assertRaisesRegex(ContractError, "unexpected fields"):
            validate_chunk(invalid)


if __name__ == "__main__":
    unittest.main()

