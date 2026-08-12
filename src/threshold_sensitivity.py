"""
门限敏感性分析 —— 回答一个评委必问的问题: "Cpk 门限 1.33 这个数，换了会怎样?"

【为什么要做这个分析】
Cpk 门限不是我们发明的常数，它按特性等级取值(IATF 16949:2016 条款 8.5.1.5 +
AIAG-VDA SPC 手册 6.2):
    一般特性                          量产 Cpk ≥ 1.33
    特殊特性 CC/SC(安全、法规相关)     优先要求 Cpk ≥ 1.67
迁到赛力斯时，制动/转向这类安全件走 1.67 档。门限抬高 → 前瞻立案更早触发、事件更多、
人工介入也更多。这个代价必须先算清楚，而不是上线后才发现质量工程师被工单淹了。

【测量口径】(全部可从代码复核)
  样本    : UCI SECOM 真实数据(1567 批次×590 传感器)上被监控参数的全序列。
  自变量  : Cpk 前瞻立案门限，取 1.33(一般特性档) 与 1.67(安全特殊特性档)。
  因变量  : 检出事件数、其中前瞻立案数、三态裁决分布、人工介入率。
  不变量  : 数据、控制限、本体、Agent 后端、gate 规则全部不变，只动门限。

跑法: python threshold_sensitivity.py   (确定性，任何人可复现)
"""
from __future__ import annotations
import json, pathlib
from data_loader import load_secom, pick_process_variable
from spc import fit_control_limits, detect, jarque_bera, CAPABILITY_GATES
from ontology import build_ontology, attach_sensor_cluster, dual_level_retrieve
from agent import diagnose
from gate import decide_and_close
from shadow import AutonomyLedger


def run_at_gate(series, cl, g, cluster, cpk_gate: float) -> dict:
    """在给定 Cpk 门限下跑完整链路，返回该门限的结果画像。"""
    events = detect(series, cl, cpk_gate=cpk_gate)
    ledger, tally = AutonomyLedger(), {}
    for e in events:
        _, sg = dual_level_retrieve(g, e, cluster)
        hyp = diagnose({"批次": e.idx, "规则": e.rule, "Cpk": round(e.cpk, 2),
                        "前瞻": e.proactive}, sg)
        d = decide_and_close(hyp, g, series, e.idx, cl, ledger=ledger)
        tally[d.branch] = tally.get(d.branch, 0) + 1
    human = sum(v for k, v in tally.items() if k in ("B", "A→B", "影子"))
    return {
        "Cpk门限": cpk_gate,
        "检出事件数": len(events),
        "其中前瞻立案": sum(e.proactive for e in events),
        "最早前瞻立案批次": min([e.idx for e in events if e.proactive], default=None),
        "分支分布": tally,
        "需人工参与的事件数": human,
        "人工参与率": round(human / len(events), 3) if events else None,
        "放权台账": ledger.summary(),
    }


def compute() -> dict:
    X, label, ts = load_secom()
    ps = pick_process_variable(X, label, ts)
    series = ps.values
    cl = fit_control_limits(series[:200])
    g = build_ontology()
    cluster = f"传感器簇#{ps.sensor_id}"
    attach_sensor_cluster(g, cluster, "薄膜沉积CVD")

    norm = jarque_bera(series)
    rows = [run_at_gate(series, cl, g, cluster, gate)
            for gate in sorted(set(CAPABILITY_GATES.values()))]
    lo, hi = rows[0], rows[-1]
    return {
        "门限依据": ("IATF 16949:2016 条款 8.5.1.5(特殊特性控制) + AIAG-VDA SPC 手册 6.2: "
                 "一般特性量产 Cpk≥1.33; 特殊特性 CC/SC(安全、法规相关)优先要求 ≥1.67。"),
        "Cpk前提校验": {"检验": "Jarque-Bera 正态性检验", "样本量": norm.n,
                    "偏度": round(norm.skew, 3), "超值峰度": round(norm.kurt, 3),
                    "JB统计量": round(norm.jb, 1), "通过": norm.passed,
                    "结论": norm.verdict},
        "逐门限结果": rows,
        "门限抬高的代价": {
            "门限": f"{lo['Cpk门限']} → {hi['Cpk门限']}",
            "事件数变化": f"{lo['检出事件数']} → {hi['检出事件数']}",
            "前瞻立案变化": f"{lo['其中前瞻立案']} → {hi['其中前瞻立案']}",
            "最早介入批次": f"{lo['最早前瞻立案批次']} → {hi['最早前瞻立案批次']}",
            "需人工参与": f"{lo['需人工参与的事件数']} → {hi['需人工参与的事件数']}",
        },
        "口径": ("同一份数据、同一套控制限/本体/gate 规则，只改 Cpk 前瞻立案门限。"
                "人工参与率 = (B + A→B + 影子) / 检出事件数 —— 影子期的处置由人执行，"
                "故计入人工参与。"),
    }


def main():
    result = compute()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    out = pathlib.Path(__file__).parent.parent / "docs" / "sensitivity.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {out} (供方案文档引用)")


if __name__ == "__main__":
    main()
