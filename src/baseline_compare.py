"""
对照实验 —— 纯 LLM 直批(无 gate) vs 确定性三态 gate。

回答一个问题: 三态 gate 相对"Agent 假设直接执行"的传统做法，改变了哪个关键结果?

【测量口径】(全部可从代码复核):
  样本      : UCI SECOM 真实数据(1567 批次×590 传感器)上 SPC 检出的全部事件
              + 1 例注入的本体外幻觉根因(模拟真实 LLM 幻觉，两组同样接收)。
  对照组    : 纯 LLM 直批 —— Agent 假设一律直接执行处置，无冲突检查/置信门槛/
              本体白名单/复测。
  实验组    : 三态 gate —— A 自主执行+强制复测(未恢复升级人审)、B 拒答转人审、
              C 本体白名单拦截。
  无效下发  : 执行处置后，数据回放 horizon=15 批次内滚动 Cpk 未回到 1.33
              (即处置没有让产线恢复受控，属于错误的自动动作)。
  幻觉执行  : 根因实体不在工艺本体内仍被执行。
  人工介入  : 事件被转给人审(B / A→B)。

跑法: python baseline_compare.py   (确定性，任何人可复现)
"""
from __future__ import annotations
import json, pathlib
from data_loader import load_secom, pick_process_variable
from spc import fit_control_limits, detect
from ontology import build_ontology, attach_sensor_cluster, dual_level_retrieve
from agent import diagnose, RootCauseHypothesis
from gate import three_state_gate, recheck, decide_and_close


def main():
    X, label, ts = load_secom()
    ps = pick_process_variable(X, label, ts)
    cl = fit_control_limits(ps.values[:200])
    events = detect(ps.values, cl, cpk_gate=1.33)

    g = build_ontology()
    cluster = f"传感器簇#{ps.sensor_id}"
    attach_sensor_cluster(g, cluster, "薄膜沉积CVD")

    # 两组接收完全相同的输入: 真实 SPC 事件 + 1 例注入幻觉
    hallucination = RootCauseHypothesis("等离子喷涂枪老化", ["x"], False, 0.9,
                                        "更换喷枪", "(注入的 LLM 幻觉)", "inject")
    base = {"auto_exec": 0, "无效下发_无机制发现": 0, "halluc_exec": 0, "human": 0}
    ours = {"auto_exec": 0, "无效下发_无机制发现": 0, "执行后未恢复_被复测闭环捕获升级": 0,
            "halluc_exec": 0, "human": 0}

    for e in events:
        _, sg = dual_level_retrieve(g, e, cluster)
        hyp = diagnose({"批次": e.idx, "规则": e.rule, "Cpk": round(e.cpk, 2)}, sg)
        effective = recheck(ps.values, e.idx, cl)   # 处置后产线是否恢复(数据回放)
        # 对照组: 一律直接执行，且没有任何机制发现处置无效
        base["auto_exec"] += 1
        base["无效下发_无机制发现"] += int(not effective)
        # 实验组: 三态 gate(不带影子台账 —— 单独度量 gate 本身的贡献)
        d = decide_and_close(hyp, g, ps.values, e.idx, cl)
        if d.branch == "A":
            ours["auto_exec"] += 1
        elif d.branch == "A→B":                      # 也执行了，但复测未恢复被闭环接住
            ours["auto_exec"] += 1
            ours["执行后未恢复_被复测闭环捕获升级"] += 1
            ours["human"] += 1
        else:                                        # B: 拒答转人审(未执行)
            ours["human"] += 1

    # 幻觉用例: 两组同样接收
    base["auto_exec"] += 1
    base["halluc_exec"] += 1                         # 直批组照单执行
    dc = three_state_gate(hallucination, g)
    assert dc.branch == "C"                          # gate 组被本体白名单拦截

    n = len(events) + 1
    result = {
        "样本事件数": n,
        "对照组_纯LLM直批": {**base,
                          "无效下发率": round(base["无效下发_无机制发现"] / base["auto_exec"], 3)},
        "实验组_三态gate": {**ours,
                          "未被发现的无效下发率": round(ours["无效下发_无机制发现"] / max(ours["auto_exec"], 1), 3),
                          "人工介入率": round(ours["human"] / n, 3)},
        "口径": ("无效=执行后15批次内滚动Cpk未回1.33(数据回放)。对照组无复测机制，无效下发无人发现;"
                "实验组执行后强制复测，未恢复即捕获升级人审。样本=SECOM全部SPC事件+1注入幻觉。"),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    out = pathlib.Path(__file__).parent.parent / "docs" / "contrast.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {out} (供方案文档引用)")


if __name__ == "__main__":
    main()
