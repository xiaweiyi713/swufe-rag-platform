from swufe_rag.orchestration import _source_appendix


def test_source_appendix_prefers_structured_physical_page() -> None:
    appendix = _source_appendix(
        [
            {
                "marker": 1,
                "doc_title": "西南财经大学2025级本科人才培养方案（完整总册）",
                "article": "数字经济人才培养方案 / 第6页表格",
                "physical_page": 6,
                "page_url": "https://jwc.swufe.edu.cn/info/1005/37211.htm",
                "file_url": "https://jwc.swufe.edu.cn/curriculum.zip",
            }
        ]
    )

    assert "原文件第6页" in appendix
    assert "页码未标注" not in appendix


def test_source_appendix_falls_back_to_article_page() -> None:
    appendix = _source_appendix(
        [
            {
                "marker": 1,
                "doc_title": "培养方案",
                "article": "某专业 / 第18页表格",
                "page_url": "https://example.edu/curriculum",
                "file_url": "https://example.edu/curriculum.pdf",
            }
        ]
    )

    assert "原文件第18页" in appendix
