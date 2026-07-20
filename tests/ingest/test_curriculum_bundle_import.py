from pathlib import Path

from scripts.import_curriculum_bundle import (
    _find_bundle_root,
    _is_separately_registered_principles,
    _natural_key,
)


def test_natural_key_keeps_numbered_curriculum_folders_in_human_order() -> None:
    values = [Path("10管理学院/a.pdf"), Path("2实验班/a.pdf"), Path("1拔尖班/a.pdf")]
    assert sorted(values, key=_natural_key) == [
        Path("1拔尖班/a.pdf"),
        Path("2实验班/a.pdf"),
        Path("10管理学院/a.pdf"),
    ]


def test_find_bundle_root_handles_nested_download_directory(tmp_path: Path) -> None:
    expected = tmp_path / "outer" / "inner" / "25级本科人才培养方案"
    expected.mkdir(parents=True)
    assert _find_bundle_root(tmp_path, 2025) == expected


def test_principles_are_excluded_from_aggregate_but_remain_traceable() -> None:
    path = Path("0原则性意见/西南财经大学本科专业人才培养方案原则性意见（2025年版）.pdf")
    assert _is_separately_registered_principles(path, 2025)
    assert not _is_separately_registered_principles(path, 2024)
