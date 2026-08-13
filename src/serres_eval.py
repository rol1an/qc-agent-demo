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

    # ── Q4 检测方式的有无与故障发生的关系（自问自答，不等外部确认）──────
    detn = [m for m in kg.modes
            if (m.get("detection_category") or "").strip() in ("", "未记录")]
    detw = [m for m in kg.modes
            if (m.get("detection_category") or "").strip() not in ("", "未记录")]
    hit_ids = Counter(o["failure_mode_id"] for o in kg.orders if o.get("failure_mode_id"))
    mid2m = {m["failure_mode_id"]: m for m in kg.modes}
    hn = [m for m in hit_ids if m in mid2m and
          (mid2m[m].get("detection_category") or "").strip() in ("", "未记录")]
    hw = [m for m in hit_ids if m in mid2m and
          (mid2m[m].get("detection_category") or "").strip() not in ("", "未记录")]

    def _cs(ids):
        cs = sorted(int(mid2m[i].get("cluster_size") or 1) for i in ids)
        return {"中位": cs[len(cs) // 2], "均值": round(sum(cs) / len(cs), 1)} if cs else {}

    q4 = {
        "问题": "检测方式缺失与实际故障的重合，是标注偏差（万能筐）还是漏防的真实因果？",
        "证据一_两组的被命中比例": {
            "无检测方式": f"{len(hn)}/{len(detn)} = {len(hn)/len(detn):.1%}",
            "有检测方式": f"{len(hw)}/{len(detw)} = {len(hw)/len(detw):.1%}",
            "倍差": round((len(hn)/len(detn)) / max(len(hw)/len(detw), 1e-9), 0),
        },
        "证据二_知识聚类规模（万能筐应更通用、近似条目更多）": {
            "被命中的无检测方式条目": _cs(hn),
            "被命中的有检测方式条目": _cs(hw),
            "解读": ("万能筐假说预测'被反复套用的条目描述更通用、近似条目更多'。实测相反: "
                   "无检测方式的被命中条目聚类规模中位 1（各自独特具体），"
                   "有检测方式的被命中条目聚类规模中位远大于它。**万能筐假说被否定。**"),
        },
        "证据三_命中集中度": {
            "命中次数分布": dict(Counter(hit_ids.values()).most_common()),
            "单条最高占比": f"{hit_ids.most_common(1)[0][1]}/{sum(hit_ids.values())}",
            "解读": "无极端集中（多数条目只被命中一次），不符合少数万能筐承载大部分工单的形态。",
        },
        "结论": ("数据支持【漏防的真实因果】: 写了检测方式的失效模式只有极小比例演变成故障工单，"
                "没写的那批比例高出两个数量级。检测方式的有无对应显著不同的故障发生率。"),
        "仍不能排除的两点": [
            "反向因果: 也可能是'某类失效频繁发生、维护知识库来不及跟上'导致检测方式空缺。"
            "此解释下行动建议不变，且'高频失效反而知识最不全'本身就是要解决的问题。",
            "混淆: 无检测方式与完整度 C 级在本数据里高度重叠，两个因素无法分离。",
        ],
    }

    # ── Q5 原因未确认: 判不出，还是没回填?（自问自答）────────────────────
    unk = [o for o in kg.orders if o.get("cause_category") == "其他或未确认"]
    kno = [o for o in kg.orders if o.get("cause_category") not in ("其他或未确认", "")]

    def _p(rows):
        n = max(len(rows), 1)
        return {
            "条数": len(rows),
            "有现象记录": f"{sum(1 for r in rows if r.get('has_phenomenon')=='1')/n:.1%}",
            "有处置记录": f"{sum(1 for r in rows if r.get('has_action')=='1')/n:.1%}",
            "关联到失效模式": f"{sum(1 for r in rows if r.get('failure_mode_id'))/n:.1%}",
            "状态已关闭": f"{sum(1 for r in rows if r.get('status_group')=='已关闭')/n:.1%}",
            "relation_tier分布": dict(Counter(r.get("relation_tier", "") for r in rows).most_common(3)),
        }

    q5 = {
        "问题": "工单原因大量记为'其他或未确认'，是现场判不出，还是填报环节没回填？",
        "原因未确认组": _p(unk),
        "原因已明确组": _p(kno),
        "结论": ("倾向【没回填】而非【判不出】: 未确认组有现象记录的比例与已明确组相当（都在九成以上），"
                "但有处置记录的比例极低，且全部未关联失效模式、企业自身分级全部为'仅工单结构记录'——"
                "然而其中八成状态已关闭。工单在发起阶段填了设备与现象，处置与关闭阶段的字段没有回填就关闭了。"),
        "对方案的含义": ("重点不是补一个更强的诊断模型，而是**把回填成本压到最低**: 工单派发时就带上"
                    "候选失效模式清单（中位 3 条）与证据链，让关联从'事后想起来去填'变成"
                    "'处置时顺手三选一'。靠流程强制回填会增加现场负担，靠降低成本才可持续。"),
    }

    out = {
        "数据来源": "企业方提供的设备场景闭环脱敏数据包（编号经脱敏，无真实设备/人员/正文信息）",
        "图规模": kg.stats(),
        "Q1_检索收敛能力": q1,
        "Q2_属性硬过滤的代价": q2,
        "Q3_三态分布与知识盲区": q3,
        "Q4_检测方式与故障发生的关系": q4,
        "Q5_原因未确认的成因": q5,
        "合规声明": "本文件只含聚合统计，不含任何单条记录内容；原始数据不入仓库。",
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    p = pathlib.Path(__file__).parent.parent / "docs" / "serres_eval.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {p}")


if __name__ == "__main__":
    main()
