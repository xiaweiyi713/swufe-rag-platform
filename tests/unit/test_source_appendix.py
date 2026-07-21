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


def test_source_appendix_groups_markers_from_the_same_file_and_page() -> None:
    citations = [
        {
            "marker": marker,
            "doc_title": "西南财经大学本科生转专业管理办法",
            "article": f"第三章 / 第{marker}条 / 原文件第2页",
            "physical_page": 2,
            "page_url": "https://jwc.swufe.edu.cn/transfer?page=2",
            "file_url": "https://jwc.swufe.edu.cn/transfer.pdf",
        }
        for marker in (2, 3, 5, 6, 9)
    ]

    appendix = _source_appendix(citations)

    assert "[2][3][5][6][9]《西南财经大学本科生转专业管理办法》" in appendix
    assert appendix.count("原文件第2页") == 1
    assert appendix.count("下载原文件") == 1
    assert appendix.count("transfer.pdf") == 1


def test_source_appendix_groups_distinct_pages_from_the_same_file() -> None:
    appendix = _source_appendix(
        [
            {
                "marker": 1,
                "doc_title": "培养方案",
                "article": "第一章 / 原文件第2页",
                "physical_page": 2,
                "page_url": "https://example.edu/curriculum",
                "file_url": "https://example.edu/curriculum.pdf",
            },
            {
                "marker": 4,
                "doc_title": "培养方案",
                "article": "第二章 / 原文件第7页",
                "physical_page": 7,
                "page_url": "https://example.edu/curriculum",
                "file_url": "https://example.edu/curriculum.pdf",
            },
        ]
    )

    assert "[1][4]《培养方案》" in appendix
    assert "原文件第2、7页" in appendix
    assert appendix.count("下载原文件") == 1


def test_source_appendix_keeps_distinct_files_separate() -> None:
    appendix = _source_appendix(
        [
            {
                "marker": 1,
                "doc_title": "文件甲",
                "article": "原文件第1页",
                "file_url": "https://example.edu/a.pdf",
            },
            {
                "marker": 2,
                "doc_title": "文件乙",
                "article": "原文件第1页",
                "file_url": "https://example.edu/b.pdf",
            },
        ]
    )

    assert appendix.count("下载原文件") == 2
    assert "文件甲" in appendix
    assert "文件乙" in appendix
