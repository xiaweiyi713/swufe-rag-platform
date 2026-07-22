from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest

from retrieval.index import file_sha256, load_chunks
from storage.metadata_db import MetadataDB, _chunk_page_url


FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "chunks.jsonl"


class MetadataDBTests(unittest.TestCase):
    def setUp(self) -> None:
        self.chunks = load_chunks(FIXTURE_PATH)
        self.db = MetadataDB.from_chunks(
            self.chunks, trusted_by_default=True
        )

    def tearDown(self) -> None:
        self.db.close()

    def test_opening_current_schema_database_is_byte_stable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metadata.sqlite3"
            first = MetadataDB(path)
            first.close()
            initial_hash = file_sha256(path)

            second = MetadataDB(path)
            second.close()

            self.assertEqual(file_sha256(path), initial_hash)

    def test_sql_scope_is_applied_before_embedding_rows_are_returned(self) -> None:
        rows = self.db.candidate_rows(
            college="计算机与人工智能学院",
            cohort="2023",
        )
        selected = [self.chunks[index] for index in rows]
        self.assertTrue(selected)
        self.assertTrue(all(chunk["status"] == "现行" for chunk in selected))
        self.assertTrue(
            all(
                chunk["level"] == "校级"
                or chunk["college"] == "计算机与人工智能学院"
                for chunk in selected
            )
        )
        self.assertTrue(
            all(chunk["cohort"] in {"不限", "2023"} for chunk in selected)
        )
        self.assertNotIn("fixture_fin_recommend_019", {c["chunk_id"] for c in selected})
        self.assertNotIn("fixture_it_recommend_old_014", {c["chunk_id"] for c in selected})

    def test_explicit_cohort_can_select_its_historical_curriculum(self) -> None:
        historical = {
            **self.chunks[6],
            "chunk_id": "fixture_it_py2022_historical",
            "text": "《计算机科学与技术专业2022级培养方案》毕业最低学分为160学分。",
            "doc_title": "计算机科学与技术专业2022级培养方案",
            "cohort": "2022",
            "year": 2022,
            "status": "历史",
        }
        current_general = self.chunks[0]
        database = MetadataDB.from_chunks(
            [current_general, historical], trusted_by_default=True
        )
        try:
            rows = database.candidate_rows(
                college="计算机与人工智能学院", cohort="2022"
            )
            selected = [current_general, historical]
            ids = {selected[index]["chunk_id"] for index in rows}
            self.assertIn("fixture_it_py2022_historical", ids)
            self.assertIn(current_general["chunk_id"], ids)
        finally:
            database.close()

    def test_explicit_policy_year_can_select_historical_version(self) -> None:
        rows = self.db.candidate_rows(
            college="计算机与人工智能学院",
            policy_year=2024,
            topic="promotion",
        )
        ids = {self.chunks[index]["chunk_id"] for index in rows}
        self.assertEqual(ids, {"fixture_it_recommend_old_014"})

    def test_promotion_implementation_links_include_historical_versions(self) -> None:
        links = self.db.promotion_implementation_links(
            college="计算机与人工智能学院"
        )

        self.assertEqual(
            [link.source_id for link in links],
            [
                self.db.chunk("fixture_it_recommend_013").source_id,
                self.db.chunk("fixture_it_recommend_old_014").source_id,
            ],
        )
        self.assertTrue(all("it.swufe.edu.cn" in link.file_url for link in links))

    def test_promotion_links_preserve_explicit_former_college_name(self) -> None:
        template = next(
            chunk
            for chunk in self.chunks
            if chunk["chunk_id"] == "fixture_it_recommend_old_014"
        )
        former_college = {
            **template,
            "chunk_id": "fixture_eie_recommend_2021",
            "doc_title": "经济信息工程学院推荐免试研究生工作实施细则",
            "text": "经济信息工程学院2021年推荐免试研究生工作实施细则。",
            "year": 2021,
            "page_url": "https://it.swufe.edu.cn/fixture/eie2021",
            "file_url": "https://it.swufe.edu.cn/fixture/eie2021.docx",
        }
        database = MetadataDB.from_chunks(
            [*self.chunks, former_college], trusted_by_default=True
        )
        try:
            links = database.promotion_implementation_links(
                college="计算机与人工智能学院",
                title_college="经济信息工程学院",
            )
        finally:
            database.close()

        self.assertEqual([link.title for link in links], [former_college["doc_title"]])

    def test_disabled_or_untrusted_source_never_enters_candidate_set(self) -> None:
        stored = self.db.chunk("fixture_it_recommend_013")
        self.assertIsNotNone(stored)
        self.db.set_source_state(stored.source_id, trusted=False)
        rows = self.db.candidate_rows(
            college="计算机与人工智能学院", topic="promotion"
        )
        ids = {self.chunks[index]["chunk_id"] for index in rows}
        self.assertNotIn("fixture_it_recommend_013", ids)
        self.assertIsNone(self.db.chunk("fixture_it_recommend_013"))

    def test_schema_rejects_invalid_level_and_boolean_state(self) -> None:
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.connection.execute(
                """
                INSERT INTO sources(
                    source_id, source_key, doc_title, level, college, cohort,
                    year, status, topic, page_url, file_url, trusted, enabled
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "bad",
                    "bad",
                    "bad",
                    "部门级",
                    "全校",
                    "不限",
                    2026,
                    "现行",
                    "school_policy",
                    "https://jwc.swufe.edu.cn/bad",
                    "https://jwc.swufe.edu.cn/bad",
                    2,
                    1,
                ),
            )

    def test_integrity_report_has_no_orphans(self) -> None:
        report = self.db.integrity_report()
        self.assertEqual(report["chunks"], len(self.chunks))
        self.assertEqual(report["orphan_chunks"], 0)
        self.assertEqual(report["eligible_untrusted"], 0)

    def test_pdf_page_anchor_is_chunk_local_not_source_identity(self) -> None:
        chunk = self.chunks[0]
        anchored = {
            **chunk,
            "page_url": chunk["file_url"].split("#", 1)[0] + "#page=12",
        }
        self.assertEqual(
            MetadataDB._signature(chunk), MetadataDB._signature(anchored)
        )
        self.assertEqual(
            _chunk_page_url(
                chunk["page_url"],
                "https://jwc.swufe.edu.cn/policy.pdf",
                "\u6b63\u6587 / \u539f\u6587\u4ef6\u7b2c12\u9875",
            ),
            "https://jwc.swufe.edu.cn/policy.pdf#page=12",
        )

    def test_scope_values_are_bound_parameters_not_executable_sql(self) -> None:
        before = self.db.connection.execute(
            "SELECT count(*) FROM sources"
        ).fetchone()[0]
        rows = self.db.candidate_rows(topic="promotion' OR 1=1 --")
        self.assertEqual(rows, [])
        self.assertEqual(
            self.db.connection.execute("SELECT count(*) FROM sources").fetchone()[0],
            before,
        )


if __name__ == "__main__":
    unittest.main()
