from scripts.merge_curriculum_cohort import _section_owner


def test_standard_college_is_derived_from_official_bundle_folder() -> None:
    assert _section_owner(
        {
            "section_title": "法学专业人才培养方案",
            "relative_path": "12法学院/1法学.pdf",
        }
    ) == "法学院"


def test_cross_school_experiment_uses_explicit_scope() -> None:
    assert _section_owner(
        {
            "section_title": "经管国际化创新实验班人才培养方案",
            "relative_path": "2实验班/3经管国际化创新实验班.pdf",
        }
    ) == "全校"


def test_minor_degree_owner_is_not_inferred_from_shared_general_courses() -> None:
    assert _section_owner(
        {
            "section_title": "财务管理专业辅修学位人才培养方案",
            "relative_path": "18辅修学位教学计划/财务管理.pdf",
        }
    ) == "会计学院"
