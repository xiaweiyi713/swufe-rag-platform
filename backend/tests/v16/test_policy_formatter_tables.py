from generation.policy_formatter import deterministic_policy_answer


def chunk(text: str, chunk_id: str = "c1") -> dict:
    return {
        "chunk_id": chunk_id,
        "text": text,
        "doc_title": "2023级培养方案",
        "article": "原文件第9页",
        "page_url": "https://example.edu/plan.pdf#page=9",
        "file_url": "https://example.edu/plan.pdf",
    }


def test_cross_cultural_course_rows_are_summarized():
    source = chunk(
        "原表：演讲与辩论；英美文学；英美文化；跨文化商务沟通。"
    )
    answer = deterministic_policy_answer("跨文化交际模块有哪些课程可以选择？", [source])
    assert answer["refused"] is False
    assert "演讲与辩论、英美文学、英美文化和跨文化商务沟通" in answer["answer_md"]


def test_listening_training_code_and_semester_are_not_raw_table_text():
    source = chunk("原表：| ENG125 | 听说写能力训练 | 2 | 第一至六学期 |")
    code = deterministic_policy_answer("听说写能力训练课程代码是什么？", [source])
    semester = deterministic_policy_answer("听说写能力训练一般在哪些学期开设？", [source])
    assert "ENG125" in code["answer_md"]
    assert "第一至第六学期" in semester["answer_md"]
    assert "原表" not in code["answer_md"]


def test_program_admission_rule_includes_course_names():
    source = chunk("专业准入标准\n(2)专业准入课程:高等代数I,高等数学I,高等数学II。")
    answer = deterministic_policy_answer("计算机科学与技术专业的专业准入课程有哪些？", [source])
    assert "高等代数I" in answer["answer_md"]
    assert answer["answer_md"] != "专业准入标准。"

