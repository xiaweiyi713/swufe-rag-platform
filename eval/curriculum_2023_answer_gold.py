"""Human-reviewed answer-level expectations for the 2023 curriculum mock set."""

from __future__ import annotations


ANSWER_PATTERNS: dict[int, str] = {
    1: r"160.*165", 2: r"30", 3: r"8", 4: r"9", 5: r"1",
    6: r"25%|25\s*%", 7: r"3", 8: r"1", 9: r"7",
    10: r"\u7b2c\u4e8c\u5b66\u5e74\u7b2c\u4e8c\u5b66\u671f|\u7b2c4\u5b66\u671f",
    11: r"\u7b2c\u4e8c\u5b66\u5e74\u7b2c\u4e00\u5b66\u671f|\u7b2c3\u5b66\u671f",
    12: r"2\s*\u5468", 13: r"19\s*\u5468",
    14: r"\u793e\u4f1a\u8c03\u67e5.*\u540d\u8457\u9605\u8bfb.*(?:\u79d1\u7814\u8bad\u7ec3|\u5b66\u672f\u7814\u7a76)",
    15: r"\u56fd\u9645\u4ea4\u6d41|\u56fd\u9645\u5b66\u672f\u5468|\u521b\u65b0\u521b\u4e1a|\u793e\u4f1a\u5b9e\u8df5",
    16: r"\u521b\u65b0(?:\u4e0e)?\u521b\u4e1a.*\u793e\u4f1a\u5b9e\u8df5.*\u6bd5\u4e1a\u5b9e\u4e60",
    17: r"8\s*\u5b66\u5206",
    18: r"\u901a\u7528\u82f1\u8bed.*\u4e13\u95e8\u7528\u9014\u82f1\u8bed.*\u8de8\u6587\u5316\u4ea4\u9645.*\u7efc\u5408\u6280\u80fd\u63d0\u5347",
    19: r"\u5b66\u672f\u82f1\u8bed.*\u5546\u52a1\u82f1\u8bed.*\u8d22\u7ecf\u82f1\u8bed\u65f6\u6587\u9605\u8bfb.*\u5546\u52a1\u7ffb\u8bd1",
    20: r"\u6f14\u8bb2\u4e0e\u8fa9\u8bba.*\u82f1\u7f8e\u6587\u5b66.*\u82f1\u7f8e\u6587\u5316.*\u8de8\u6587\u5316\u5546\u52a1\u6c9f\u901a",
    21: r"ENG125", 22: r"\u7b2c\u4e00\u81f3\u7b2c\u516d\u5b66\u671f|1.{0,3}6\u5b66\u671f",
    23: r"85%", 24: r"77%", 25: r"80%", 26: r"89%", 27: r"80%",
    28: r"\u9ad8\u7ea7", 29: r"6\s*\u5b66\u5206", 30: r"\u4e0d\u4e88\u514d\u4fee|\u4e0d\u80fd\u514d\u4fee",
    31: r"4\s*\u5e74|\u56db\u5e74", 32: r"6\s*\u5e74|\u516d\u5e74", 33: r"165\s*\u5b66\u5206",
    34: r"\u5de5\u5b66\u5b66\u58eb",
    35: r"\u9ad8\u7b49\u4ee3\u6570.?I.*\u9ad8\u7b49\u6570\u5b66.?I.*\u9ad8\u7b49\u6570\u5b66.?II",
    36: r"\u7a0b\u5e8f\u8bbe\u8ba1.*\u79bb\u6563\u6570\u5b66.*\u6570\u636e\u7ed3\u6784.*\u64cd\u4f5c\u7cfb\u7edf",
    37: r"64", 38: r"20", 39: r"20", 40: r"18", 41: r"6", 42: r"2", 43: r"35",
    44: r"\u8ba1\u7b97\u673a\u8f6f\u4ef6\u548c\u7cfb\u7edf|\u8f6f\u4ef6\u5f00\u53d1|\u7cfb\u7edf\u8bbe\u8ba1",
    45: r"\u6570\u636e\u7ed3\u6784.*\u64cd\u4f5c\u7cfb\u7edf|\u64cd\u4f5c\u7cfb\u7edf.*\u6570\u636e\u5e93",
    46: r"CST117.*CST120|CST120.*CST117", 47: r"CST116.*CST118|CST118.*CST116",
    48: r"MAT301.*MAT313.*MAT302|MAT301.*MAT302", 49: r"CST117.*CST116|CST116.*CST117",
    50: r"IPT107.*IPT102|IPT102.*IPT107", 51: r"PED100.*PED200", 52: r"MTT102.*MTT200",
    53: r"46\s*\u5b66\u5206", 54: r"21\s*\u5b66\u5206", 55: r"25\s*\u5b66\u5206",
    56: r"CST120", 57: r"CST120.*1\s*\u5b66\u5206|1\s*\u5b66\u5206.*CST120",
    58: r"CST117", 59: r"CST117.*17|17.*CST117", 60: r"CST116.*2|2.*CST116",
    61: r"CST118.*3.*2|\u79bb\u6563\u6570\u5b66.*3.*2", 62: r"CST124",
    63: r"CST124.*34.*17|34.*17.*CST124", 64: r"FEG517.*3|3.*FEG517",
    65: r"CST221.*\u5927\u5b66\u79d1\u57fa\u7840", 66: r"CST205.*3|3.*CST205",
    67: r"CST204.*4|4.*CST204", 68: r"FIT403.*\u8ba1\u7b97\u673a\u4e0e\u4eba\u5de5\u667a\u80fd\u5b66\u9662",
    69: r"CST203.*51|51.*CST203", 70: r"CST207.*\u4e13\u4e1a\u5fc5\u4fee",
    71: r"OPT898.*3|3.*OPT898", 72: r"CST209.*24|24.*CST209",
    73: r"DSC401.*7|7.*DSC401", 74: r"CST308.*\u9009\u4fee", 75: r"CST302",
    76: r"CST134.*PRT110.*PRT111|PRT110.*PRT111", 77: r"CST134",
    78: r"CST134.*\u6691\u671f\u5b66\u671f3|\u6691\u671f\u5b66\u671f3.*CST134",
    79: r"CST134.*\|\s*2\s*\|",
    80: r"\u79d1\u6280\u7ade\u8d5b.*\u8bc1\u660e|\u8bc1\u660e.*\u79d1\u6280\u7ade\u8d5b",
    81: r"CST911.*2|2.*CST911", 82: r"CST911.*7|7.*CST911",
    83: r"PRT110.*6|6.*PRT110", 84: r"PRT111.*8|8.*PRT111", 85: r"CST133.*51|51.*CST133",
    86: r"165\s*\u5b66\u5206", 87: r"4\s*\u5e74.*6\s*\u5e74|\u56db\u5e74.*\u516d\u5e74",
    88: r"\u5de5\u5b66\u5b66\u58eb", 89: r"(?=.*\u9ad8\u7b49\u4ee3\u6570.?I)(?=.*\u9ad8\u7b49\u6570\u5b66.?I)",
    90: r"\u7b97\u6cd5\u5206\u6790\u4e0e\u8bbe\u8ba1.*\u673a\u5668\u5b66\u4e60|\u673a\u5668\u5b66\u4e60.*\u6570\u636e\u6316\u6398",
    91: r"34", 92: r"2", 93: r"21",
    94: r"CST213.*CST345.*DSC402|CST345.*DSC402", 95: r"\u4eba\u5de5\u667a\u80fd|\u667a\u80fd\u7cfb\u7edf|\u6570\u636e\u5206\u6790",
    96: r"(?=.*CST418)(?=.*CST419)(?=.*\u6691\u671f\u5b66\u671f3)",
    97: r"CST345.*\u4e13\u4e1a\u65b9\u5411|\u77e5\u8bc6\u56fe\u8c31\u4e0e\u5e94\u7528.*\u9009\u4fee",
    98: r"DSC402.*3.*7", 99: r"35.*34", 100: r"(?=.*CST207)(?=.*OPT898)(?=.*CST209)",
}

RAG_IDS = set(range(1, 33)) | {34, 35, 36, 44, 45, 80, 87, 88, 89, 90, 95, 99, 100}
SQL_IDS = set(range(33, 101)) - RAG_IDS

EXACT_COURSE_CODES: dict[int, set[str]] = {
    46: {"CST117", "CST120", "ENG220", "ENG340", "IPT107", "IPT205", "MAT301", "MAT313", "MTT102", "PED100", "HUM100", "HUM200", "HUM300", "CST131", "CST421", "PRT109", "PRT115"},
    47: {"ENG340", "IPT205", "HUM100", "HUM200", "HUM300", "CST131", "CST421", "PRT109", "PRT115", "CST116", "CST118", "ENG230", "ENG240", "HUM104", "HUM117", "IPT102", "JOB100", "MAT302", "MTT200", "PED200", "CST124"},
    49: {"CST117", "CST116"},
    50: {"IPT205", "IPT107", "IPT102"},
    51: {"PED100", "PED200"},
    52: {"MTT102", "MTT200"},
    76: {"PRT115", "PRT109", "CST421", "CST131", "CST422", "CST132", "CST130", "CST133", "PRT102", "CST134", "PRT110", "CST911", "PRT111"},
    94: {"CST213", "CST415", "CST403", "CST339", "CST327", "CST412", "DSC202", "CST344", "CST345", "CST326", "DSC402"},
    96: {"CST418", "CST419"},
}


def expected_pages(case_id: int) -> set[int]:
    if case_id <= 16:
        return {5, 6, 7}
    if case_id <= 22:
        return {9}
    if case_id <= 30:
        return set()
    if case_id <= 45:
        return {448, 449, 450, 451}
    if case_id <= 85:
        return set(range(452, 458)) | {449, 451}
    if case_id <= 98:
        return set(range(458, 469))
    if case_id == 99:
        return {451, 461}
    return {454, 465, 466}


assert set(ANSWER_PATTERNS) == set(range(1, 101))
assert RAG_IDS.isdisjoint(SQL_IDS)
assert RAG_IDS | SQL_IDS == set(range(1, 101))
