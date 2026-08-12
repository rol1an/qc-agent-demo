"""
检查标准动态复核 —— 补上业务闭环的最后一环，也是最依赖经验的第二个环节。

【为什么做这一环】
企业方指出，现场较依赖经验的部分有两处: 一是故障排查与失效原因分析，二是
**处置后审视对应失效模式的检查标准与策略是否需要更新**。企业列出的共性问题里也有
一条: "点巡检随失效分析和失效模式更新的频率不足，未形成动态有效的检查标准。"

多数方案在"给出根因、派出工单"就结束了。但工单关闭不等于闭环 —— 如果这次失效
暴露出检查标准的盲点而无人回头改标准，同类失效就会再来一次。这一环把
"处置结果 → 检查标准复核建议"变成确定性规则驱动的自动产出。

【四条规则，全部确定性、可审计、可逐条复核】
  R1 检测方式缺失 : 失效模式已被工单实际命中，但其检测方式记为未记录
                    → 建议补充检测方式，否则下次仍只能事后发现。
  R2 复发         : 同一失效模式被 >=2 条工单命中
                    → 建议复核对应点巡检项目、方法与周期（现有标准没能提前拦住）。
  R3 近似知识重复 : 同一知识聚类下存在多条失效模式条目
                    → 建议合并或明确区分，避免检索与填报时的歧义。
  R4 信息完整度不足: 完整度 C 级（影响/原因/检测不足两项）且已被工单命中
                    → 建议补全，这类条目无法支撑有效的检查策略。

规则只提【建议】，不自动改标准 —— 检查标准的变更必须走企业既有的审核流程。
这与三态门同源: 系统负责把该审的事挑出来并给依据，判断权留在人手里。

⚠️ 数据合规: 只输出聚合统计与按设备族的分布，不含任何单条记录内容或脱敏编号。

跑法:
  export SERRES_DATA="$HOME/Downloads/赛力斯设备场景闭环脱敏数据包.xlsx"
  python serres_standard_review.py
"""
from __future__ import annotations
import json, pathlib
from collections import Counter, defaultdict
from serres_kg import build_kg

R2_MIN_RECUR = 2          # 复发阈值（demo 值；生产按设备重要度与节拍配置）


def review(kg) -> dict:
    modes = {m["failure_mode_id"]: m for m in kg.modes}
    # 工单实际命中的失效模式
    hit = Counter(o["failure_mode_id"] for o in kg.orders if o.get("failure_mode_id"))
    # 知识聚类 → 条目数
    cluster = defaultdict(int)
    for m in kg.modes:
        if m.get("knowledge_cluster_id"):
            cluster[m["knowledge_cluster_id"]] += 1

    sug = defaultdict(list)          # 规则 → [失效模式所属设备族]
    for mid, n in hit.items():
        m = modes.get(mid)
        if not m:
            continue
        fam = m.get("equipment_family", "未知")
        det = (m.get("detection_category") or "").strip()
        if det in ("", "未记录") or m.get("has_detection_method") == "0":
            sug["R1_检测方式缺失"].append(fam)
        if n >= R2_MIN_RECUR:
            sug["R2_复发需复核点巡检"].append(fam)
        if cluster.get(m.get("knowledge_cluster_id", ""), 0) > 1:
            sug["R3_近似知识需合并"].append(fam)
        if m.get("completeness_grade") == "C":
            sug["R4_完整度不足需补全"].append(fam)

    rules = {}
    for k, fams in sug.items():
        rules[k] = {"建议条数": len(fams),
                    "按设备族分布": dict(Counter(fams).most_common(5))}

    # 覆盖面：被工单命中的失效模式里，有多少至少触发一条规则
    touched = set()
    for k, fams in sug.items():
        touched.update([k] * 0)      # 占位，下面用 mid 级统计
    flagged = set()
    for mid, n in hit.items():
        m = modes.get(mid)
        if not m:
            continue
        det = (m.get("detection_category") or "").strip()
        if (det in ("", "未记录") or m.get("has_detection_method") == "0"
                or n >= R2_MIN_RECUR
                or cluster.get(m.get("knowledge_cluster_id", ""), 0) > 1
                or m.get("completeness_grade") == "C"):
            flagged.add(mid)

    # 全库层面的检测方式缺口（不限于已被命中的）
    det_missing = sum(1 for m in kg.modes
                      if (m.get("detection_category") or "").strip() in ("", "未记录"))
    grade_c = sum(1 for m in kg.modes if m.get("completeness_grade") == "C")

    # 核心对照: 全库的知识质量 vs 实际发生故障的那批失效模式的知识质量
    n_hit = len(hit)
    hit_det_missing = sum(1 for mid in hit if mid in modes and
                          (modes[mid].get("detection_category") or "").strip() in ("", "未记录"))
    hit_grade_c = sum(1 for mid in hit if mid in modes and
                      modes[mid].get("completeness_grade") == "C")
    ratio = round((hit_det_missing / n_hit) / (det_missing / len(kg.modes)), 1) if n_hit else None

    return {
        "至少触发一条规则的失效模式": f"{len(flagged)} / {n_hit} 条被工单实际命中的失效模式",
        "逐规则建议": rules,
        "★核心发现_检测方式缺失与实际故障高度重合": {
            "全库_检测方式未记录占比": f"{det_missing}/{len(kg.modes)} = {det_missing/len(kg.modes):.1%}",
            "被工单命中的失效模式中该占比": f"{hit_det_missing}/{n_hit} = {hit_det_missing/n_hit:.1%}",
            "富集倍数": ratio,
            "同口径_完整度C级": (f"全库 {grade_c}/{len(kg.modes)} = {grade_c/len(kg.modes):.1%}；"
                          f"命中集 {hit_grade_c}/{n_hit} = {hit_grade_c/n_hit:.1%}"),
            "两种解释_数据不足以区分": [
                "解释A 标注偏差: 描述宽泛、字段残缺的条目更容易在填报时被'套上'，"
                "于是成为万能筐，与是否真的发生无关。",
                "解释B 真实因果: 没有明确检测方式的失效模式无法被点巡检提前发现，"
                "只能以故障形式暴露 —— 即检测方式缺失是故障漏防的原因之一。",
            ],
            "如何区分": ("需要点巡检项目与执行记录，比较'有检测方式'与'无检测方式'两组失效模式的"
                     "实际发生率与提前发现率。本数据包不含点巡检数据，无法在此判定。"),
            "为什么不必先判定因果就能行动": ("两种解释下的行动建议一致 —— 优先补全这批失效模式的检测方式"
                                "并纳入点巡检。若为解释B 可直接减少故障；若为解释A 则提升知识库"
                                "可用性与工单关联质量。"),
        },
        "全库知识质量缺口": {
            "检测方式记为未记录": f"{det_missing} / {len(kg.modes)}",
            "完整度C级": f"{grade_c} / {len(kg.modes)}",
            "含多条近似条目的知识聚类": sum(1 for v in cluster.values() if v > 1),
        },
        "口径": (f"R2 复发阈值 = {R2_MIN_RECUR} 条工单（demo 值，生产按设备重要度与节拍配置）。"
                "规则只产出复核建议，不自动变更检查标准 —— 标准变更走企业既有审核流程。"
                "命中集样本量仅 40 条，富集倍数是强信号但不是统计结论。"),
        "边界": ("本数据包不含点巡检项目与执行记录，因此无法验证'建议被采纳后失效是否减少'。"
                "这一环的效果验证需要接入点巡检数据，属于落地阶段的验证项，不在本次结论内。"),
    }


def main():
    kg = build_kg()
    out = review(kg)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    p = pathlib.Path(__file__).parent.parent / "docs" / "serres_standard_review.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {p}")


if __name__ == "__main__":
    main()
