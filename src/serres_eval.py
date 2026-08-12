"""
在企业脱敏数据包上做「工单 → 失效模式」检索评测 + 知识盲区分析。

⚠️ 数据合规: 数据文件不入仓库（路径由 SERRES_DATA 指定）；本脚本只输出聚合统计
与评测指标，不打印任何单条记录的内容。产物 docs/serres_eval.json 同样只含聚合值。

三个问题，三组答案:
  Q1 图检索能把根因候选收敛到多小，正确答案还在不在里面?
  Q2 为什么不能按属性（现象/部件/完整度）做硬过滤?
  Q3 知识库的盲区在哪，先补哪里最值?

跑法:
  export SERRES_DATA="$HOME/Downloads/赛力斯设备场景闭环脱敏数据包.xlsx"
  python serres_eval.py
"""
from __future__ import annotations
import json, pathlib, statistics as st
from collections import Counter
from serres_kg import build_kg, retrieve, gate, MAX_HUMAN_CAND


def main():
    kg = build_kg()
    g = kg.g
    wo = {o["case_id"]: o for o in kg.orders}
    truth = {cid: o["failure_mode_id"] for cid, o in wo.items() if o.get("failure_mode_id")}
    n_modes = sum(1 for _, d in g.nodes(data=True) if d.get("ntype") == "failure_mode")

    # ── Q1 检索收敛能力（验证集 = 有历史失效模式关联的工单）────────────────
    hit, sizes = 0, []
    for cid, fm in truth.items():
        r = retrieve(kg, cid)
        sizes.append(len(r["l1"]))
        hit += fm in r["l1"]
    nz = [s for s in sizes if s]
    q1 = {
        "验证集": f"{len(truth)} 条带历史失效模式关联的工单（企业标注为待核验，非真值标签）",
        "知识库失效模式总数": n_modes,
        "结构检索命中率": round(hit / len(truth), 3),
        "候选集大小_中位": st.median(sizes), "候选集大小_均值": round(st.mean(sizes), 1),
        "候选集大小_最大": max(sizes),
        "人工确认范围缩小倍数": round(n_modes / st.median(nz), 0) if nz else None,
    }

    # ── Q2 属性硬过滤的代价（同一验证集，只改过滤策略）──────────────────
    def strat(f):
        h, ss = 0, []
        for cid, fm in truth.items():
            r = retrieve(kg, cid)
            c = f(r, wo[cid])
            h += fm in c
            ss.append(len(c))
        return {"命中率": round(h / len(truth), 3), "候选中位": st.median(ss)}

    q2 = {
        "结构可达全集": strat(lambda r, o: r["l1"]),
        "再按现象类别精确过滤": strat(lambda r, o: r["l2"]),
        "再按部件族过滤": strat(lambda r, o: [m for m in r["l1"]
                              if g.nodes[m].get("component") == o.get("component_family")]),
        "再按信息完整度A过滤": strat(lambda r, o: [m for m in r["l1"]
                              if g.nodes[m].get("grade") == "A"]),
        "词表错位证据": {
            "工单侧现象分布": dict(Counter(o.get("phenomenon_category", "")
                                     for o in kg.orders).most_common()),
            "失效模式侧现象分布": dict(Counter(m.get("phenomenon_category", "")
                                       for m in kg.modes).most_common()),
        },
        "结论": ("工单侧记录现场可观测表象、失效模式库归类机理层描述，同一套词表处在不同"
                "抽象层。按属性硬过滤会滤掉正确答案 —— 结构负责收敛，语义判断交给模型或人。"),
    }

    # ── Q3 三态分布（全量工单）+ 知识盲区 ──────────────────────────────
    branches, blind_family = Counter(), Counter()
    unlinked_covered = 0
    unlinked = [cid for cid, o in wo.items() if not o.get("failure_mode_id")]
    for cid, o in wo.items():
        r = retrieve(kg, cid)
        b = gate(r)
        branches[b] += 1
        if b == "C":
            blind_family[o.get("equipment_family", "未知")] += 1
        if cid in unlinked and r["l1"]:
            unlinked_covered += 1

    types_no_func = [t for t in kg.types if t.get("function_count", "0") in ("", "0")]
    inst_no_chain = [i for i in kg.instances if i.get("has_knowledge_chain") != "1"]
    q3 = {
        "全量工单数": len(wo),
        "三态分布": dict(branches),
        "可自动关联_A": branches["A"], "转人审_B": branches["B"], "拒答_C": branches["C"],
        "当前未关联失效模式的工单": len(unlinked),
        "其中本系统能给出候选": unlinked_covered,
        "未关联工单的候选覆盖率": round(unlinked_covered / len(unlinked), 3) if unlinked else None,
        "知识盲区": {
            "无功能知识的设备类别": f"{len(types_no_func)} / {len(kg.types)}",
            "无知识链的设备实例": f"{len(inst_no_chain)} / {len(kg.instances)}",
            "C态工单按设备族分布": dict(blind_family.most_common(6)),
        },
        "口径": (f"A=候选唯一可自动关联；B=候选 2..{MAX_HUMAN_CAND} 个转人审并附候选清单与证据链；"
                "C=结构上无候选（知识链缺失）或候选过多未收敛，一律拒答并输出知识盲区。"),
    }

    out = {
        "数据来源": "企业方提供的设备场景闭环脱敏数据包（编号经脱敏，无真实设备/人员/正文信息）",
        "图规模": kg.stats(),
        "Q1_检索收敛能力": q1,
        "Q2_属性硬过滤的代价": q2,
        "Q3_三态分布与知识盲区": q3,
        "合规声明": "本文件只含聚合统计，不含任何单条记录内容；原始数据不入仓库。",
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    p = pathlib.Path(__file__).parent.parent / "docs" / "serres_eval.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {p}")


if __name__ == "__main__":
    main()
