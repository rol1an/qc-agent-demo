"""
端到端编排 + 终端可视化 —— 质量风险自主管控数字员工（最小真闭环）。

链路(与开题报告四层架构一一对应):
  真实数据 → SPC引擎(前瞻立案) → Graph RAG(跨工艺段检索) → Agent(根因假设)
           → 确定性三态gate(A自动执行+复测 / B转人审 / C拦截) → 回灌

跑法:
  python run.py                 # 默认 stub 后端，本地全真跑通(除LLM)
  QC_LLM_BACKEND=openai OPENAI_API_KEY=sk-... python run.py   # 接真模型
  QC_LLM_BACKEND=ollama python run.py                          # 本地开源模型
"""
from __future__ import annotations
import os, argparse, textwrap
from data_loader import load_secom, pick_process_variable
from spc import fit_control_limits, detect, ascii_chart
from ontology import build_ontology, attach_sensor_cluster, graph_rag
from agent import diagnose, RootCauseHypothesis
from gate import decide_and_close, three_state_gate

C = {"A": "\033[32m", "B": "\033[33m", "C": "\033[31m", "!": "\033[33m",
     "dim": "\033[90m", "b": "\033[1m", "0": "\033[0m", "cy": "\033[36m"}
def c(s, k): return f"{C[k]}{s}{C['0']}"


def entry_entity(event, cluster: str) -> str:
    """把 SPC 事件映射到本体检索入口(数据驱动):
       终端越规格(严重质量失效) → 从质量特性 CD 反查(多因、跨工艺段);
       其余(前瞻/控制限) → 从受监控传感器簇查(单工序)。"""
    return "关键尺寸CD_KPC" if event.kind == "beyond_spec" else cluster


def banner(t): print(f"\n{c('━━ ' + t + ' ' + '━'*(60-len(t)), 'cy')}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", type=int, default=200, help="稳定基线批次数")
    ap.add_argument("--cpk-gate", type=float, default=1.33)
    ap.add_argument("--max-events", type=int, default=6)
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
    events = detect(ps.values, cl, cpk_gate=args.cpk_gate)
    print(f"控制限 CL={cl.cl:.2f} ±3σ=[{cl.lcl:.2f},{cl.ucl:.2f}] 规格=[{cl.lsl:.2f},{cl.usl:.2f}]")
    print(ascii_chart(ps.values, cl, events))
    print(f"检出 {len(events)} 事件: 前瞻 {c(sum(e.proactive for e in events),'!')} / "
          f"被动 {sum(not e.proactive for e in events)}")

    g = build_ontology()
    cluster = f"传感器簇#{ps.sensor_id}"
    attach_sensor_cluster(g, cluster, "薄膜沉积CVD")

    banner("2 · 逐事件闭环 (Graph RAG → Agent → 三态 gate)")
    tally = {}
    for e in events[:args.max_events]:
        root = entry_entity(e, cluster)
        sg = graph_rag(g, root)
        feats = {"批次": e.idx, "规则": e.rule, "Cpk": round(e.cpk, 2), "前瞻": e.proactive}
        hyp = diagnose(feats, sg)
        d = decide_and_close(hyp, g, ps.values, e.idx, cl)
        tag = "前瞻立案" if e.proactive else "被动告警"
        print(f"\n{c('●','!')} 批次#{e.idx} [{tag}] {e.rule}  Cpk={e.cpk:.2f}")
        print(f"  {c('检索','dim')} {sg.community_summary}")
        print(f"  {c('假设','dim')} 根因『{hyp.cause_entity}』 证据{len(hyp.evidence_node_ids)}节点 "
              f"conf={hyp.confidence:.2f} {'冲突' if hyp.conflict else '自洽'} [{hyp.source}]")
        br = d.branch[0]
        print(f"  {c('裁决','dim')} {c('['+d.branch+']','A' if br=='A' else 'B' if br=='B' else 'C')} "
              f"{d.action}")
        print(f"       {c(d.reason,'dim')}")
        tally[d.branch] = tally.get(d.branch, 0) + 1

    banner("3 · 防幻觉护栏演示 (分支 C)")
    print(c("模拟真实 LLM 幻觉出一个本体外根因(接真模型时会自然发生):", "dim"))
    fake = RootCauseHypothesis("等离子喷涂枪老化", ["x"], False, 0.9, "更换喷枪", "(LLM 幻觉)", "demo")
    dc = three_state_gate(fake, g)
    print(f"  根因『{fake.cause_entity}』→ {c('['+dc.branch+']','C')} {dc.action}")
    print(f"       {c(dc.reason,'dim')}")
    tally[dc.branch] = tally.get(dc.branch, 0) + 1

    banner("汇总")
    print(f"三态分布: {tally}")
    print(textwrap.dedent(f"""\
      {c('说明','dim')} 以上每个数字都来自真实数据上的真实计算:
        · SPC 事件由真 Nelson 规则从 SECOM 真实传感器序列检出
        · 根因由真 Graph RAG 子图检索驱动(跨工艺段=CD 多因→冲突→转人审)
        · 三态由确定性 gate 代码裁决，A 分支强制复测闭环
      引擎与领域解耦: 迁到赛力斯只需换 data_loader 的数据接口 + ontology 的工艺本体。"""))


if __name__ == "__main__":
    main()
