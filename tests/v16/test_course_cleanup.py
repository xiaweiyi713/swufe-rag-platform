from academic_audit.course_subjects import clean_course_name
from academic_audit.structured_executor import _semester_number
from academic_audit.semesters import semester_display, semester_positions, semester_values


def test_split_chinese_and_orphan_english_tail_are_cleaned():
    assert clean_course_name("软件开发与项目管理实 训") == "软件开发与项目管理实训"
    assert clean_course_name("程序设计（C语言） C") == "程序设计（C语言）"


def test_prefixed_semester_is_normalized_for_filtering():
    assert _semester_number("S3") is None
    assert _semester_number("3") == 3



def test_semester_range_preserves_every_covered_term():
    assert semester_values("1-4") == frozenset({1, 2, 3, 4})
    assert semester_values("2\u81f36") == frozenset({2, 3, 4, 5, 6})
    assert semester_values("1\u30013") == frozenset({1, 3})


def test_summer_semester_is_not_a_regular_semester():
    assert semester_values("S3") == frozenset()
    assert semester_positions("S3") == frozenset({6.5})
    assert semester_display("S3") == "\u6691\u671f\u5b66\u671f3"
