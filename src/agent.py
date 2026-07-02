"""
Autonomous Agent 层 —— 根因推理。

关键设计(呼应方案"把概率判断转成确定性保证"):
  Agent 只【提出根因假设】(概率性)，是否执行由 gate.py 的确定性代码裁决。
  Agent 的每个结论都必须引用 Graph RAG 检索到的图节点作为证据(可解释)。

LLM 后端【可配置、可切换】(QC_LLM_BACKEND 环境变量):
  - stub   : 不调外部，基于真实检索子图确定性地产出假设(默认，本地跑通全管线用)
  - openai : 调 OpenAI 兼容接口(OPENAI_API_KEY / OPENAI_BASE_URL / QC_MODEL)——接真模型
  - ollama : 调本地 Ollama(http://localhost:11434)——完全离线跑真模型
接真模型时，除本文件其余层(数据/SPC/图检索/gate)完全不变。
"""
from __future__ import annotations
import os, json
from dataclasses import dataclass
from ontology import Subgraph


@dataclass
class RootCauseHypothesis:
    cause_entity: str          # 根因假设指向的图节点(供 gate 校验是否在本体内)
    evidence_node_ids: list[str]
    conflict: bool             # 是否存在互斥的多个候选根因
    confidence: float          # 0~1
    disposition: str
    rationale: str
    source: str                # 'stub' | 'openai' | 'ollama'


def _prompt(features: dict, sg: Subgraph) -> str:
    triples = "\n".join(" -> ".join(p) for p in sg.evidence_paths) or "(无可回溯路径)"
    return (
        "你是产线质量根因诊断 Agent。基于以下【异常特征】和【知识图谱检索到的候选路径】，"
        "给出最可能的单一根因，并【只能引用图中出现过的节点】作为证据(不得杜撰实体)。\n"
        f"异常特征: {json.dumps(features, ensure_ascii=False)}\n"
        f"候选证据路径(root->...->处置):\n{triples}\n"
        f"候选失效模式: {sg.modes}\n"
        '严格输出 JSON: {"cause_entity":"图中某节点","evidence_node_ids":[...],'
        '"conflict":true/false,"confidence":0~1,"disposition":"图中某处置","rationale":"..."}'
    )


def _stub(features: dict, sg: Subgraph) -> RootCauseHypothesis:
    """确定性 stub: 基于【真实检索结果】生成假设(不是写死文案)。"""
    if not sg.modes:
        return RootCauseHypothesis("(无候选)", [], False, 0.0, "无", "检索无失效模式", "stub")
    conflict = len(set(sg.modes)) > 1          # 多个互斥失效模式 = 证据冲突
    mode = sg.modes[0]
    path = sg.evidence_paths[0] if sg.evidence_paths else [sg.root, mode]
    disp = path[-1] if sg.dispositions else "无"
    # 置信度 = 证据完整度: 有唯一模式且有完整路径 → 高; 冲突或缺路径 → 低
    conf = 0.85 if (not conflict and sg.evidence_paths) else 0.45
    return RootCauseHypothesis(cause_entity=mode, evidence_node_ids=path, conflict=conflict,
                               confidence=conf, disposition=disp,
                               rationale=f"检索子图中 {sg.root} 可达失效模式 {mode}，"
                                         f"{'但存在多个互斥候选' if conflict else '路径唯一、证据自洽'}。",
                               source="stub")


def _call_openai(prompt: str) -> dict:
    from urllib import request
    body = json.dumps({"model": os.getenv("QC_MODEL", "gpt-4o-mini"),
                       "messages": [{"role": "user", "content": prompt}],
                       "response_format": {"type": "json_object"}, "temperature": 0}).encode()
    base = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    req = request.Request(f"{base}/chat/completions", data=body,
                          headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}",
                                   "Content-Type": "application/json"})
    with request.urlopen(req, timeout=60) as r:
        return json.loads(json.loads(r.read())["choices"][0]["message"]["content"])


def _call_ollama(prompt: str) -> dict:
    from urllib import request
    body = json.dumps({"model": os.getenv("QC_MODEL", "qwen2.5"),
                       "prompt": prompt, "format": "json", "stream": False,
                       "options": {"temperature": 0}}).encode()
    req = request.Request("http://localhost:11434/api/generate", data=body,
                          headers={"Content-Type": "application/json"})
    with request.urlopen(req, timeout=120) as r:
        return json.loads(json.loads(r.read())["response"])


def diagnose(features: dict, sg: Subgraph) -> RootCauseHypothesis:
    """🟢 统一入口: 按后端产出根因假设。真后端解析失败时安全降级为 stub(可靠性优先)。"""
    backend = os.getenv("QC_LLM_BACKEND", "stub").lower()
    if backend == "stub":
        return _stub(features, sg)
    try:
        raw = _call_openai(_prompt(features, sg)) if backend == "openai" else _call_ollama(_prompt(features, sg))
        return RootCauseHypothesis(
            cause_entity=raw.get("cause_entity", "(未指定)"),
            evidence_node_ids=raw.get("evidence_node_ids", []),
            conflict=bool(raw.get("conflict", False)),
            confidence=float(raw.get("confidence", 0.5)),
            disposition=raw.get("disposition", "无"),
            rationale=raw.get("rationale", ""), source=backend)
    except Exception as e:
        h = _stub(features, sg)
        h.rationale = f"[{backend} 调用失败，降级 stub: {str(e)[:60]}] " + h.rationale
        h.source = f"{backend}->stub"
        return h
