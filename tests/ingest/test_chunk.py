from __future__ import annotations

from pathlib import Path
import unittest

from contracts import CHUNK_FIELDS
from ingest.chunk import build_chunks
from ingest.models import DocumentElement, ParsedDocument, SourceRecord


class ChunkingTests(unittest.TestCase):
    def _source(self) -> SourceRecord:
        return SourceRecord(
            file="school/policy.txt",
            doc_title="本科课程管理办法",
            level="校级",
            college="全校",
            cohort="不限",
            year=2026,
            status="现行",
            page_url="https://jwc.swufe.edu.cn/policy",
            file_url="https://jwc.swufe.edu.cn/policy.txt",
            collected_at="2026-07-14",
        )

    def test_articles_split_and_tables_remain_atomic(self) -> None:
        parsed = ParsedDocument(
            Path("policy.txt"),
            [
                DocumentElement("heading", "第一章 总则"),
                DocumentElement("heading", "第一条 适用范围"),
                DocumentElement("paragraph", "本办法适用于全日制本科生。"),
                DocumentElement("heading", "第二条 学分要求"),
                DocumentElement("paragraph", "学生应修满规定学分。"),
                DocumentElement("table", "| 课程 | 学分 |\n| --- | --- |\n| 人工智能 | 3 |"),
            ],
        )
        chunks = build_chunks(parsed, self._source(), chunk_max_len=220)
        self.assertTrue(any(chunk["article"].endswith("第一条") for chunk in chunks))
        table = next(chunk for chunk in chunks if chunk["is_table"])
        self.assertIn("| 人工智能 | 3 |", table["text"])
        self.assertEqual(set(table), set(CHUNK_FIELDS))
        self.assertEqual(len({chunk["chunk_id"] for chunk in chunks}), len(chunks))

    def test_large_table_is_split_with_repeated_header(self) -> None:
        rows = "\n".join(f"| 课程{i} | {i % 5 + 1} |" for i in range(40))
        parsed = ParsedDocument(
            Path("policy.txt"),
            [DocumentElement("table", "| 课程 | 学分 |\n| --- | --- |\n" + rows)],
        )
        chunks = build_chunks(parsed, self._source(), chunk_max_len=220)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(chunk["is_table"] for chunk in chunks))
        self.assertTrue(all("| 课程 | 学分 |" in chunk["text"] for chunk in chunks))
        self.assertTrue(all(len(chunk["text"]) <= 220 for chunk in chunks))

    def test_program_heading_is_retained_in_article_scope(self) -> None:
        parsed = ParsedDocument(
            Path("plan.pdf"),
            [DocumentElement("paragraph", "计算机科学与技术专业 2024级本科人才培养方案\n一、培养目标\n培养复合型人才。")],
        )
        chunks = build_chunks(parsed, self._source())
        self.assertIn("计算机科学与技术专业", chunks[0]["article"])
        self.assertIn("一、培养目标", chunks[0]["article"])

    def test_experimental_program_heading_resets_article_scope(self) -> None:
        parsed = ParsedDocument(
            Path("plan.pdf"),
            [
                DocumentElement(
                    "heading", "数字经济（基础学科拔尖实验班）人才培养方案", page=1
                ),
                DocumentElement("paragraph", "一、培养目标\n培养拔尖人才。", page=1),
            ],
        )
        chunks = build_chunks(parsed, self._source())
        self.assertTrue(
            all("数字经济(基础学科拔尖实验班)" in item["article"] for item in chunks)
        )

    def test_section_sidecar_title_wins_over_conflicting_pdf_heading(self) -> None:
        parsed = ParsedDocument(
            Path("plan.pdf"),
            [
                DocumentElement(
                    "section", "金融学专业辅修学位人才培养方案", page=16
                ),
                DocumentElement("paragraph", "金融学专业人才培养方案", page=16),
                DocumentElement(
                    "table", "| 课程 | 学分 |\n| --- | --- |\n| 金融学 | 3 |", page=16
                ),
            ],
        )
        chunks = build_chunks(parsed, self._source())
        assert all("金融学专业辅修学位人才培养方案" in item["article"] for item in chunks)

    def test_missing_program_title_is_inferred_from_body_and_applies_to_table(self) -> None:
        parsed = ParsedDocument(
            Path("plan.pdf"),
            [
                DocumentElement("paragraph", "一、培养目标\n西南财经大学人工智能专业人才培养遵循党的教育方针。", page=1),
                DocumentElement("table", "| 课程 | 学分 |\n| --- | --- |\n| 机器学习 | 3 |", page=1),
            ],
        )
        chunks = build_chunks(parsed, self._source())
        self.assertTrue(all("人工智能专业人才培养方案" in item["article"] for item in chunks))
        table = next(item for item in chunks if item["is_table"])
        self.assertTrue(table["article"].endswith("第1页表格"))

    def test_long_clause_splits_at_budget_with_stable_ids(self) -> None:
        parsed = ParsedDocument(
            Path("policy.txt"),
            [DocumentElement("paragraph", "第一条 " + "本条规定适用于学生。" * 80)],
        )
        first = build_chunks(parsed, self._source(), chunk_max_len=200)
        second = build_chunks(parsed, self._source(), chunk_max_len=200)
        self.assertGreater(len(first), 2)
        self.assertEqual(
            [item["chunk_id"] for item in first],
            [item["chunk_id"] for item in second],
        )
        self.assertTrue(all(len(item["text"]) <= 200 for item in first))

    def test_numbered_items_become_independent_evidence(self) -> None:
        parsed = ParsedDocument(
            Path("policy.txt"),
            [
                DocumentElement("heading", "注意事项"),
                DocumentElement("paragraph", "1. 须在开考前2小时申请。\n2 · 未获批准按旷考处理。"),
            ],
        )
        chunks = build_chunks(parsed, self._source())
        self.assertEqual([item["article"] for item in chunks], ["注意事项 / 第1项", "注意事项 / 第2项"])

    def test_pdf_table_uses_page_label_instead_of_previous_heading(self) -> None:
        parsed = ParsedDocument(
            Path("policy.pdf"),
            [
                DocumentElement("heading", "三、培养目标", page=1),
                DocumentElement("table", "| 专业 | 学分 |\n| --- | --- |\n| 计算机 | 151 |", page=1),
            ],
        )
        table = next(item for item in build_chunks(parsed, self._source()) if item["is_table"])
        self.assertEqual(table["article"], "第1页表格")

    def test_original_pdf_pages_are_preserved_in_article_and_url(self) -> None:
        source = SourceRecord(
            file="school/23级培养方案.pdf",
            doc_title="西南财经大学2023级本科人才培养方案（完整总册）",
            level="校级",
            college="全校",
            cohort="2023",
            year=2023,
            status="现行",
            page_url="https://jwc.swufe.edu.cn/2023-plan.pdf?e=.pdf",
            file_url="https://jwc.swufe.edu.cn/2023-plan.pdf?e=.pdf",
            collected_at="2026-07-16",
        )
        parsed = ParsedDocument(
            Path("23级培养方案.pdf"),
            [
                DocumentElement("paragraph", "计算机科学与技术专业2023级本科人才培养方案\n一、培养目标\n培养复合型人才。", page=448),
                DocumentElement("paragraph", "本专业毕业最低学分为165学分。", page=449),
            ],
        )
        chunks = build_chunks(parsed, source)
        self.assertEqual(len(chunks), 2)
        self.assertIn("第448页", chunks[0]["article"])
        self.assertIn("第449页", chunks[1]["article"])
        self.assertTrue(chunks[0]["page_url"].endswith("#page=448"))
        self.assertTrue(chunks[1]["page_url"].endswith("#page=449"))


    def test_pdf_file_url_is_used_when_page_url_is_an_html_landing_page(self) -> None:
        source = SourceRecord(
            file="school/principles.pdf",
            doc_title="本科专业人才培养方案原则性意见",
            level="校级",
            college="全校",
            cohort="2025",
            year=2025,
            status="现行",
            page_url="https://jwc.swufe.edu.cn/info/1032/37201.htm",
            file_url="https://jwc.swufe.edu.cn/principles.pdf",
            collected_at="2026-07-16",
        )
        parsed = ParsedDocument(
            Path("principles.pdf"),
            [DocumentElement("paragraph", "第一条 本意见适用于2025级本科生。", page=3)],
        )
        chunk = build_chunks(parsed, source)[0]
        self.assertEqual(
            chunk["page_url"],
            "https://jwc.swufe.edu.cn/principles.pdf#page=3",
        )
        self.assertEqual(chunk["file_url"], source.file_url)

if __name__ == "__main__":
    unittest.main()
