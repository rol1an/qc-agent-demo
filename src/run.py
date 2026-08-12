"""
端到端编排 + 终端可视化 —— 质量风险自主管控数字员工（最小真闭环）。

链路(与开题报告四层架构一一对应):
  真实数据 → SPC引擎(前瞻立案) → LightRAG式双层图检索(low局部/high跨段) → Agent(根因假设)
           → 确定性三态gate × 影子协同分级放权(影子建议 / A自主+复测 / B转人审 / C拦截)
           → 复测案例增量回灌底座 → 飞书多维表格工单

跑法:
  python run.py                 # 默认 stub 后端，本地全真跑通(除LLM)
  QC_LLM_BACKEND=openai OPENAI_API_KEY=sk-... python run.py   # 接真模型
  QC_LLM_BACKEND=ollama python run.py                          # 本地开源模型
  QC_FEISHU_WEBHOOK=https://... python run.py                  # 工单真实派发到飞书
"""
from __future__ import annotations
import os, argparse, textwrap
from data_loader import load_secom, pick_process_variable
from spc import fit_control_limits, detect, ascii_chart, jarque_bera, capability_gate
from ontology import kpc_char_class
from ontology import build_ontology, attach_sensor_cluster, dual_level_retrieve, feedback_upsert
from agent import diagnose, RootCauseHypothesis
from gate import decide_and_close, three_state_gate
from shadow import AutonomyLedger
import feishu_bridge

C = {"A": "\033[32m", "B": "\033[33m", "C": "\033[31m", "!": "\033[33m",
     "dim": "\033[90m", "b": "\033[1m", "0": "\033[0m", "cy": "\033[36m"}
def c(s, k): return f"{C[k]}{s}{C['0']}"


def branch_color(branch: str) -> str:
    return {"A": "A", "影": "cy", "B": "B"}.get(branch[0], "C")


def banner(t): print(f"\n{c('━━ ' + t + ' ' + '━'*(60-len(t)), 'cy')}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", type=int, default=200, help="稳定基线批次数")
    ap.add_argument("--cpk-gate", type=float, default=1.33)
    # 默认跑全部事件: 只跑前 6 个只能看到"一路晋升"，跑全序列才能看到自主执行
    # 复测未恢复被【收权】、再退回影子期重新累积的完整生命周期。
    ap.add_argument("--max-events", type=int, default=12)
    args = ap.parse_args()
    backend = os.getenv("QC_LLM_BACKEND", "stub")

    banner("0 · 数据 (真实公开数据集 UCI SECOM)")
    X, label, ts = load_secom()
    ps = pick_process_variable(X, label, ts)
    print(f"批次×传感器: {X.shape} | pass/fail: {dict(label.value_counts())} | "
          f"时间: {ts.iloc[0].date()}→{ts.iloc[-1].date()}")
    print(c("· " + ps.reason, "dim"))
    print(c(f"· LLM 后端: {backend}" + ("  (确定性占位，接 key 即变真)" if backend == 'stub' else "  (真实模型)"), "dim"))

    banner("1 · SPC 引擎 (真 Nelson 规则 + 滚动 Cpk)")
    cl = fit_control_limits(ps.values[:args.baseline])
    nm = jarque_bera(ps.values)
    print(f"{c('前提校验','dim')} Jarque-Bera 正态性检验: {nm.verdict}")
    print(c("  → 不受影响: Rule2(中心线取中位数,两侧各50%由定义保证) / Rule3(纯序关系)；"
            "受影响: Cpk 绝对值与 Rule1 的 3σ 标称误报率", "dim"))
    events = detect(ps.values, cl, cpk_gate=args.cpk_gate)
    print(f"控制限 CL={cl.cl:.2f} ±3σ=[{cl.lcl:.2f},{cl.ucl:.2f}] 规格=[{cl.lsl:.2f},{cl.usl:.2f}]")
    print(ascii_chart(ps.values, cl, events))
    print(f"检出 {len(events)} 事件: 前瞻 {c(sum(e.proactive for e in events),'!')} / "
          f"被动 {sum(not e.proactive for e in events)}")

    g = build_ontology()
    cluster = f"传感器簇#{ps.sensor_id}"
    attach_sensor_cluster(g, cluster, "薄膜沉积CVD")

    banner("2 · 逐事件闭环 (双层检索 → Agent → gate × 影子放权 → 回灌)")
    ledger = AutonomyLedger()
    tally = {}
    for e in events[:args.max_events]:
        level, sg = dual_level_retrieve(g, e, cluster)
        klass = kpc_char_class(g, sg.kpcs)      # 该异常影响的 KPC 特性等级 → 能力门限档位
        feats = {"批次": e.idx, "规则": e.rule, "Cpk": round(e.cpk, 2), "前瞻": e.proactive}
        hyp = diagnose(feats, sg)
        d = decide_and_close(hyp, g, ps.values, e.idx, cl, ledger=ledger)
        tag = "前瞻立案" if e.proactive else "被动告警"
        print(f"\n{c('●','!')} 批次#{e.idx} [{tag}] {e.rule}  Cpk={e.cpk:.2f}")
        print(f"  {c('检索','dim')} [{level}-level] {sg.community_summary}")
        print(f"  {c('特性','dim')} 影响 KPC {sg.kpcs or '—'} → 特性等级 [{klass}]，"
              f"量产能力门限 Cpk≥{capability_gate(klass)} (IATF 16949 8.5.1.5)")
        print(f"  {c('假设','dim')} 根因『{hyp.cause_entity}』 证据{len(hyp.evidence_node_ids)}节点 "
              f"conf={hyp.confidence:.2f} {'冲突' if hyp.conflict else '自洽'} [{hyp.source}]")
        print(f"  {c('裁决','dim')} {c('['+d.branch+']', branch_color(d.branch))} {d.action}")
        print(f"       {c(d.reason,'dim')}")
        if d.branch in ("A", "A→B", "影子") and d.recheck_ok is not None:
            node = feedback_upsert(g, hyp.disposition, e.idx, d.recheck_ok)
            print(f"  {c('回灌','dim')} {c(f'处置结果写回本体 → 图节点『{node}』', 'dim')}")
        if d.note:
            print(f"  {c('放权','dim')} {c('⚖ ' + d.note, 'b')}")
        if d.branch in ("B", "A→B", "影子"):
            if feishu_bridge.enabled():
                ticket = feishu_bridge.build_ticket(e, ts.iloc[e.idx].date(), hyp, d, backend)
                print(f"  {c('派单','dim')} {feishu_bridge.send_ticket(ticket)}")
            else:
                print(f"  {c('派单','dim')} {c('未配置 QC_FEISHU_WEBHOOK，跳过飞书工单派发', 'dim')}")
        tally[d.branch] = tally.get(d.branch, 0) + 1

    banner("3 · 防幻觉护栏演示 (分支 C)")
    print(c("模拟真实 LLM 幻觉出一个本体外根因(接真模型时会自然发生):", "dim"))
    fake = RootCauseHypothesis("等离子喷涂枪老化", ["x"], False, 0.9, "更换喷枪", "(LLM 幻觉)", "demo")
    dc = three_state_gate(fake, g)
    print(f"  根因『{fake.cause_entity}』→ {c('['+dc.branch+']','C')} {dc.action}")
    print(f"       {c(dc.reason,'dim')}")
    tally[dc.branch] = tally.get(dc.branch, 0) + 1

    banner("汇总")
    print(f"分支分布: {tally}")
    print(f"\n{c('分级放权台账(影子协同)','b')}")
    for line in ledger.summary():
        print(f"  {line}")
    print(textwrap.dedent(f"""\

      {c('说明','dim')} 以上每个数字都来自真实数据上的真实计算:
        · SPC 事件由真 Nelson 规则从 SECOM 真实传感器序列检出
        · 根因由 LightRAG 式双层图检索驱动(low=工序局部 / high=KPC 跨段反查→多因→冲突→转人审)
        · 分支由确定性 gate 裁决；AI 自主权按影子协同一致率放权/收权，复测案例增量回灌本体
        · 能力门限按 KPC 特性等级分档(一般 1.33 / 安全特殊特性 1.67, IATF 16949 8.5.1.5)
      {c('局限','dim')} Cpk 的正态性前提在本数据上未通过(见上方前提校验)，故其绝对值不作
      能力判定；门限敏感性与三条局限边界见 `python threshold_sensitivity.py`。
      引擎与领域解耦: 迁到赛力斯只需换 data_loader 的数据接口 + ontology 的工艺本体。"""))


if __name__ == "__main__":
    main()
