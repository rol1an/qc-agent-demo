"""
可靠性校验门 —— 【确定性三态判定】。这是方案里"确定性质量管控"的落点:
把 Agent 的概率性假设，用确定性代码收敛成"自动执行 / 转人审 / 拦截"三态，并对
自动执行的处置强制【复测闭环】(未恢复自动转人审) —— 封堵"证据齐全但错"的漏洞。
"""
from __future__ import annotations
import numpy as np
from dataclasses import dataclass
import networkx as nx
from agent import RootCauseHypothesis
from spc import ControlLimits, rolling_cpk


@dataclass
class GateDecision:
    branch: str        # 'A' 自主执行+复测 | '影子' 建议转人审对照 | 'B' 拒答转人审
                       # | 'A→B' 复测未恢复升级 | 'C' 拦截(防幻觉)
    action: str
    reason: str
    recheck_ok: bool | None = None   # A/影子 分支复测(或对照)结果
    note: str | None = None          # 分级放权事件(晋升/降级)说明，来自影子协同台账


CONF_MIN = 0.6         # 自动执行的最低置信度门槛


def three_state_gate(hyp: RootCauseHypothesis, g: nx.DiGraph) -> GateDecision:
    """
    🟢 与开题报告/HTML 中展示的 gate() 逻辑一一对应:
      C 分支: 根因实体不在本体内 → 拦截(防幻觉硬护栏)
      A 分支: 证据可回溯、不冲突、置信度达标 → 自动执行(随后复测)
      B 分支: 其余(缺证/冲突/低置信/复测未恢复) → 拒答转人审
    """
    if hyp.cause_entity not in g:
        return GateDecision("C", "拦截，不下发任何动作",
                            f"根因实体『{hyp.cause_entity}』不在工艺本体内，判为幻觉")
    if hyp.evidence_node_ids and (not hyp.conflict) and hyp.confidence >= CONF_MIN:
        return GateDecision("A", f"自动执行处置: {hyp.disposition}",
                            f"证据链可回溯({len(hyp.evidence_node_ids)}节点)、不冲突、"
                            f"置信度{hyp.confidence:.2f}≥{CONF_MIN}")
    why = "证据冲突" if hyp.conflict else ("证据缺失" if not hyp.evidence_node_ids
                                        else f"置信度{hyp.confidence:.2f}<{CONF_MIN}")
    return GateDecision("B", "拒答，升级人审", f"{why}——宁可漏报转人工，不乱下发")


def recheck(series: np.ndarray, event_idx: int, cl: ControlLimits, horizon: int = 15) -> bool:
    """
    复测闭环(数据回放): 处置后 horizon 个批次内，滚动 Cpk 是否回到受控(≥1.33)。
    真实系统里这是"下发处置→复测确认恢复"；此处用数据回放模拟该闭环。
    """
    j = min(event_idx + horizon, len(series) - 1)
    return rolling_cpk(series, cl, j) >= 1.33


def decide_and_close(hyp, g, series, event_idx, cl, ledger=None) -> GateDecision:
    """
    A 分支的完整闭环，叠加【影子协同分级放权】(ledger 非空时):
      未获自主权的处置 → 即使判 A 也只出建议(影子分支)，转人审执行；同时用数据回放
        中人工处置后的实际走向对照 AI 建议，累积一致率(达标才放权，见 shadow.py)。
      已获自主权的处置 → 自主执行+强制复测；复测未恢复 → 升级人审并【立即收权】。
    """
    d = three_state_gate(hyp, g)
    if d.branch != "A":
        return d
    if ledger is not None and not ledger.is_autonomous(hyp.disposition):
        agreed = recheck(series, event_idx, cl)   # 数据回放中人工处置后的实际走向
        promo = ledger.observe_shadow(hyp.disposition, agreed, event_idx)
        return GateDecision("影子", f"影子建议: {hyp.disposition} —— 本类处置自主权未获得，转人审执行",
                            d.reason + f"；影子对照: AI 建议与实际走向{'一致' if agreed else '不一致'}",
                            recheck_ok=agreed, note=promo)
    d.recheck_ok = recheck(series, event_idx, cl)
    if not d.recheck_ok:
        note = ledger.demote(hyp.disposition) if ledger is not None else None
        return GateDecision("A→B", "复测未恢复 → 升级人审", d.reason + "；但复测未回到受控",
                            recheck_ok=False, note=note)
    d.action += "；复测已回到受控，闭环并回灌底座"
    return d
