"""模块D接口自测(smoke):过一遍全部接口与关键行为分支。

用法(先在另一个终端启动服务):
    uvicorn app.server:app --port 8000
    python eval/smoke_test.py [--base-url http://127.0.0.1:8000]

覆盖:契约4字段完整性、拒答链路、学院/年级过滤(含跨学院零污染、
历史版本零泄漏)、同题不同院差异化、/source、/meta、参数校验。
"""
import argparse
import sys

import httpx

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = ""):
    (PASS if cond else FAIL).append(name)
    mark = "✓" if cond else "✗"
    print(f"  {mark} {name}" + (f"  [{detail}]" if detail and not cond else ""))


def ask(client, question, college=None, cohort=None):
    r = client.post("/ask", json={"question": question, "college": college, "cohort": cohort})
    r.raise_for_status()
    return r.json()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    # trust_env=False:本地回环自测,屏蔽系统代理环境变量的干扰
    client = httpx.Client(base_url=args.base_url, timeout=30, trust_env=False)

    print("== /meta ==")
    meta = client.get("/meta").json()
    check("colleges 含两所学院", set(meta["colleges"]) == {"计算机与人工智能学院", "金融学院"}, str(meta))
    check("cohorts 含 2022/2023", set(meta["cohorts"]) == {"2022", "2023"}, str(meta))

    print("== /ask 契约4字段 ==")
    r = ask(client, "毕业需要修满多少学分", "计算机与人工智能学院", "2023")
    for key in ("answer_md", "citations", "retrieved", "latency_ms", "refused"):
        check(f"响应含 {key}", key in r)
    check("正常问答 refused=false", r["refused"] is False)
    check("citations 非空且含契约3字段",
          bool(r["citations"]) and all(k in r["citations"][0] for k in
                                       ("marker", "chunk_id", "doc_title", "article", "quote", "page_url", "file_url")))
    check("retrieved 含约定D-4字段",
          bool(r["retrieved"]) and all(k in r["retrieved"][0] for k in
                                       ("chunk_id", "doc_title", "article", "college", "cohort", "score", "snippet")))
    check("retrieved 按 score 降序",
          all(a["score"] >= b["score"] for a, b in zip(r["retrieved"], r["retrieved"][1:])))
    check("2023级答案为165学分", "165" in r["answer_md"], r["answer_md"][:60])

    print("== 年级过滤与差异化 ==")
    r22 = ask(client, "毕业需要修满多少学分", "计算机与人工智能学院", "2022")
    check("2022级答案为166学分", "166" in r22["answer_md"], r22["answer_md"][:60])
    check("2022级检索不到2023级块",
          all(c["cohort"] in ("不限", "2022") for c in r22["retrieved"]),
          str([(c["chunk_id"], c["cohort"]) for c in r22["retrieved"]]))
    rjr = ask(client, "毕业需要修满多少学分", "金融学院", "2023")
    check("金融学院答案为160学分", "160" in rjr["answer_md"], rjr["answer_md"][:60])

    print("== 跨学院零污染 / 历史版本零泄漏 ==")
    pollution = [c["chunk_id"] for c in rjr["retrieved"] if c["college"] == "计算机与人工智能学院"]
    check("金融学生的检索结果无计算机学院块", not pollution, str(pollution))
    r_tm = ask(client, "推免申请有什么条件", "计算机与人工智能学院", "2023")
    hist = [c["chunk_id"] for c in r_tm["retrieved"] if c["chunk_id"] == "it_tm2024_003"]
    check("历史版细则(2024)不出现在检索结果", not hist, str(hist))

    print("== 多引用 / 表格 / 同题不同院 ==")
    r = ask(client, "我挂过一门课重修通过了,还能保研吗", "计算机与人工智能学院", "2023")
    check("重修保研题引用≥2条", len(r["citations"]) >= 2, f"{len(r['citations'])}条")
    check("重修保研题提到前30%", "30%" in r["answer_md"])
    r = ask(client, "各类课程的学分怎么分布", "计算机与人工智能学院", "2023")
    check("学分分布答案含Markdown表格", "| 课程类别 |" in r["answer_md"] or "| --- |" in r["answer_md"])
    r_it = ask(client, "保研对英语六级有什么要求", "计算机与人工智能学院", "2023")
    r_jr = ask(client, "保研对英语六级有什么要求", "金融学院", "2023")
    check("六级要求:计算机425分", "425" in r_it["answer_md"], r_it["answer_md"][:80])
    check("六级要求:金融480分", "480" in r_jr["answer_md"], r_jr["answer_md"][:80])
    r_w_it = ask(client, "推免综合成绩的权重构成是什么", "计算机与人工智能学院", "2023")
    r_w_jr = ask(client, "推免综合成绩的权重构成是什么", "金融学院", "2023")
    check("综合成绩:计算机80%口径", "80%" in r_w_it["answer_md"])
    check("综合成绩:金融85%口径", "85%" in r_w_jr["answer_md"])

    print("== 范围提醒 / 其他路由 ==")
    r = ask(client, "2022级培养方案要求修多少学分", "计算机与人工智能学院", "2023")
    check("跨年级提问触发适用范围提醒", "适用范围提醒" in r["answer_md"], r["answer_md"][:80])
    r = ask(client, "金融科技导论算不算专业选修学分", "计算机与人工智能学院", "2023")
    check("金融科技选修题命中(计算机版)", "FIN3021" in r["answer_md"] and not r["refused"])
    for q, kw in [("转专业需要什么条件", "第一学年末"), ("绩点是怎么计算的", "GPA"),
                  ("休学最多可以休多久", "两学年"), ("保研需要满足哪些条件", "校级基本条件")]:
        r = ask(client, q, "计算机与人工智能学院", "2023")
        check(f"路由:{q}", (not r["refused"]) and kw in r["answer_md"],
              f"refused={r['refused']} {r['answer_md'][:50]}")

    print("== 拒答链路 ==")
    for q in ("食堂几点关门", "今天天气怎么样", "帮我写一首诗"):
        r = ask(client, q, "计算机与人工智能学院", "2023")
        check(f"库外拒答:{q}", r["refused"] is True and r["citations"] == [],
              f"refused={r['refused']} top1={r['retrieved'][0]['score'] if r['retrieved'] else None}")

    print("== /source ==")
    r = client.get("/source/it_py2023_004")
    check("/source 返回契约1完整块", r.status_code == 200 and
          all(k in r.json() for k in ("chunk_id", "text", "doc_title", "article", "level",
                                      "college", "cohort", "year", "status", "page_url", "file_url", "is_table")))
    check("/source 不存在返回404", client.get("/source/nonexistent_id").status_code == 404)

    print("== 参数校验 ==")
    check("空问题返回422", client.post("/ask", json={"question": ""}).status_code == 422)
    check("缺问题字段返回422", client.post("/ask", json={}).status_code == 422)

    print(f"\n结果: {len(PASS)} 通过, {len(FAIL)} 失败")
    if FAIL:
        print("失败项:", *FAIL, sep="\n  - ")
        sys.exit(1)


if __name__ == "__main__":
    main()
