from __future__ import annotations

from academic_audit.database import AcademicDatabase
from academic_audit.service import CurriculumAuditService


def test_database_options_expose_complete_major_college_ownership() -> None:
    database = AcademicDatabase("data/academic_v2.sqlite3")
    try:
        options = database.options()
    finally:
        database.close()

    owners = options["major_colleges_by_cohort"]
    assert owners["2023"]["人工智能专业"] == "计算机与人工智能学院"
    assert owners["2023"]["“智能金融”光华实验班"] == "计算机与人工智能学院"
    assert set(owners) == set(options["majors_by_cohort"])
    for cohort, majors in options["majors_by_cohort"].items():
        assert set(majors) == set(owners[cohort])
        assert set(owners[cohort].values()).issubset(options["colleges"])


def test_catalog_options_keep_major_college_mapping() -> None:
    service = CurriculumAuditService("data/curriculum_catalog_v2.json")
    options = service.options()
    owners = options["major_colleges_by_cohort"]

    assert owners["2024"]["人工智能专业"] == "计算机与人工智能学院"
    assert set(owners["2024"]) == set(options["majors_by_cohort"]["2024"])
