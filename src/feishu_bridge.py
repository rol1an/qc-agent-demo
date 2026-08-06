"""
飞书协同层 —— 把三态 gate 的"转人审"输出真实派发为飞书多维表格工单。

对应架构④协同层: gate 的 B(拒答转人审)与 A→B(复测未恢复升级)两态天然是
派单出口 —— POST 到多维表格自动化的「收到 Webhook 请求时」触发器,自动化
把 payload 映射成「质量风险工单」表的一条新记录,人审与 AI 摘要在飞书侧接力。

配置(不配置则跳过派单,demo 仍可离线全真跑通):
  QC_FEISHU_WEBHOOK=https://...   多维表格自动化 Webhook 触发器 URL
"""
from __future__ import annotations
import json
import os
import urllib.request

_WEBHOOK = os.getenv("QC_FEISHU_WEBHOOK", "").strip()


def enabled() -> bool:
    return bool(_WEBHOOK)


_TITLE = {"A→B": "复测未恢复升级人审", "影子": "影子建议·人审对照", "B": "拒答转人审"}


def build_ticket(event, batch_time, hyp, d, backend: str) -> dict:
    """构造工单 payload —— 键名与多维表格字段名一一对应,自动化里直接映射。"""
    return {
        "工单标题": f"批次#{event.idx} {_TITLE.get(d.branch, d.branch)}",
        "分支": d.branch,
        "批次号": int(event.idx),
        "批次时间": str(batch_time),
        "触发规则": event.rule,
        "Cpk": round(float(event.cpk), 2),
        "立案方式": "前瞻" if event.proactive else "被动",
        "根因假设": hyp.cause_entity,
        "置信度": round(float(hyp.confidence), 2),
        "证据节点数": len(hyp.evidence_node_ids),
        "处置建议": hyp.disposition,
        "判定理由": d.reason,
        "LLM后端": backend,
    }


def send_ticket(payload: dict, timeout: float = 8.0) -> str:
    """POST 工单;派单失败降级为本地留存,不阻塞判定主链路。"""
    req = urllib.request.Request(
        _WEBHOOK,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return f"已派单 → 飞书多维表格 (HTTP {resp.status})"
    except Exception as ex:
        return f"飞书派单失败({ex}) —— 工单已本地留存"
