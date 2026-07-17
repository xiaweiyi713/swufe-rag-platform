from generation.policy_formatter import deterministic_policy_answer


def chunk(text: str, chunk_id: str = "c1") -> dict:
    return {
        "chunk_id": chunk_id,
        "text": text,
        "doc_title": "测试文件",
        "article": "原文件第1页",
        "page_url": "https://example.edu/test.pdf#page=1",
        "file_url": "https://example.edu/test.pdf",
    }


def test_semester_credit_cap_prefers_exact_school_rule():
    values = [
        chunk("标题\n原则上学生每学期修读课程学分数不超过30个学分。"),
        chunk("标题\n备注:自由选修课最多修满22学分。", "c2"),
    ]
    answer = deterministic_policy_answer(
        "2023级学生原则上每学期最多可以修多少学分？", values
    )
    assert "30个学分" in answer["answer_md"]
    assert "22学分" not in answer["answer_md"]
    assert answer["citations"][0]["chunk_id"] == "c1"


def test_summer_activity_selects_requested_year_only():
    text = (
        "标题\n(2)暑期学期安排:大一学生参加社会调查、名著阅读、科研训练等;"
        "大二学生参加暑期国际周、创新创业教育、社会实践活动等;"
        "大三学生参加创新与创业实践、社会实践、毕业实习等。"
    )
    answer = deterministic_policy_answer("大二学生暑期学期通常安排哪些活动？", [chunk(text)])
    assert "暑期国际周" in answer["answer_md"]
    assert "社会调查" not in answer["answer_md"]


def test_english_table_is_rendered_as_clean_sentence():
    text = (
        "标题\n2023级公共外语课程设置：通用英语；专门用途英语；跨文化交际；"
        "综合技能提升；ENG125 听说写能力训练。"
    )
    answer = deterministic_policy_answer(
        "普通招生批次学生的大学英语课程设置包含哪些模块？", [chunk(text)]
    )
    assert "通用英语、专门用途英语、跨文化交际和综合技能提升" in answer["answer_md"]
    assert "Course Credi" not in answer["answer_md"]


def test_raw_table_generic_fallback_fails_closed():
    answer = deterministic_policy_answer(
        "一个没有可靠短句的问题？",
        [chunk("标题\n原表：| Course Credi Weekly Total | 3 | 51 |")],
    )
    assert answer["refused"] is True

