"""
赛力斯设备知识图 —— 用【企业提供的脱敏数据包】构建真实规模的设备知识图谱，
并在其上做「工单 → 失效模式」检索评测。

⚠️ 数据合规（重要）
  数据包由企业方提供，仅含结构与关联关系（编号经 HMAC 脱敏，无真实设备编号、
  名称、工位、品牌、型号，无工单原始正文与人员信息）。即便如此:
    · 数据文件【不进本仓库】，路径由环境变量 SERRES_DATA 指定；
    · 本模块只输出【聚合统计与评测指标】，不打印任何单条记录内容；
    · 提交材料中只引用聚合数字，不贴明细。

为什么要做这件事
  企业方明确说明该数据包"仅能在数据结构、关联关系、对象定义方面提供参考，
  不具备提取知识和进行时序分析的条件"。时序分析确实做不了 —— 但
  【构建知识图并评测检索】只需要对象与关联，而这正是数据包提供的。
  于是本项目的知识底座不再是我们手搓的演示本体，而是对齐企业真实对象模型:

    设备类别 → 设备实例 → 故障工单 → 失效模式 → 设备功能 → 设备类别

跑法:
  export SERRES_DATA="$HOME/Downloads/赛力斯设备场景闭环脱敏数据包.xlsx"
  python serres_kg.py
"""
from __future__ import annotations
import os, zipfile, pathlib
from xml.etree import ElementTree as ET
from dataclasses import dataclass, field
import networkx as nx

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_RNS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def _data_path() -> pathlib.Path:
    p = os.getenv("SERRES_DATA", "")
    if not p:
        p = str(pathlib.Path.home() / "Downloads" / "赛力斯设备场景闭环脱敏数据包.xlsx")
    fp = pathlib.Path(p).expanduser()
    if not fp.is_file():
        raise SystemExit(
            f"找不到脱敏数据包: {fp}\n"
            "请设置 SERRES_DATA 指向企业方提供的 xlsx（该文件不随仓库分发）。")
    return fp


# ── xlsx 读取（纯标准库，不引 openpyxl，保持零额外依赖）─────────────────
class _Xlsx:
    def __init__(self, path: pathlib.Path):
        self.z = zipfile.ZipFile(path)
        try:
            self.ss = ["".join(t.text or "" for t in si.iter(_NS + "t"))
                       for si in ET.fromstring(self.z.read("xl/sharedStrings.xml")).iter(_NS + "si")]
        except KeyError:
            self.ss = []
        wb = ET.fromstring(self.z.read("xl/workbook.xml"))
        rels = {r.get("Id"): r.get("Target")
                for r in ET.fromstring(self.z.read("xl/_rels/workbook.xml.rels"))}
        self.sheets = {}
        for sh in wb.iter(_NS + "sheet"):
            tgt = rels[sh.get(_RNS + "id")].lstrip("/")
            self.sheets[sh.get("name")] = tgt if tgt.startswith("xl/") else "xl/" + tgt

    def _val(self, c):
        t = c.get("t")
        if t == "inlineStr":
            return "".join(x.text or "" for x in c.iter(_NS + "t"))
        v = c.find(_NS + "v")
        if v is None:
            return ""
        if t == "s":
            i = int(v.text)
            return self.ss[i] if i < len(self.ss) else ""
        return v.text or ""

    def records(self, sheet: str) -> list[dict]:
        """返回 dict 列表。表头行 = 第一行含 '_id' 或 'field' 的行（前两行是标题与说明）。"""
        root = ET.fromstring(self.z.read(self.sheets[sheet]))
        rows = [[self._val(c) for c in r.iter(_NS + "c")] for r in root.iter(_NS + "row")]
        hdr_i = next((i for i, r in enumerate(rows)
                      if any("_id" in (x or "") or x == "field" for x in r)), 0)
        hdr = rows[hdr_i]
        out = []
        for r in rows[hdr_i + 1:]:
            if not any(x.strip() for x in r):
                continue
            out.append({h: (r[j] if j < len(r) else "") for j, h in enumerate(hdr) if h})
        return out


@dataclass
class KG:
    g: nx.DiGraph
    types: list[dict]
    instances: list[dict]
    functions: list[dict]
    modes: list[dict]
    orders: list[dict]
    causes: list[dict] = field(default_factory=list)

    def stats(self) -> dict:
        n = self.g.number_of_nodes()
        by = {}
        for _, d in self.g.nodes(data=True):
            by[d.get("ntype", "?")] = by.get(d.get("ntype", "?"), 0) + 1
        return {"节点总数": n, "边总数": self.g.number_of_edges(), "按类型": by}


def build_kg(path: pathlib.Path | None = None) -> KG:
    """🟢 用企业脱敏数据构建设备知识图（真实对象模型，非演示本体）。"""
    x = _Xlsx(path or _data_path())
    types = x.records("设备类别")
    instances = x.records("设备实例")
    functions = x.records("功能关系")
    modes = x.records("失效模式")
    orders = x.records("故障工单")
    causes = x.records("原因类别")

    g = nx.DiGraph()
    for t in types:
        tid = t["equipment_type_id"]
        g.add_node(tid, ntype="equipment_type", family=t.get("equipment_family", ""),
                   level=t.get("asset_level_group", ""))
    for ins in instances:
        iid, tid = ins["equipment_id"], ins.get("equipment_type_id", "")
        g.add_node(iid, ntype="equipment", family=ins.get("equipment_family", ""),
                   status=ins.get("status_group", ""),
                   has_chain=ins.get("has_knowledge_chain") == "1")
        if tid in g:
            g.add_edge(iid, tid, rel="属于类别")
    for f in functions:
        fid, tid = f["function_id"], f.get("equipment_type_id", "")
        g.add_node(fid, ntype="function", category=f.get("function_category", ""))
        if tid in g:
            g.add_edge(tid, fid, rel="具备功能")
    for m in modes:
        mid, fid = m["failure_mode_id"], m.get("function_id", "")
        g.add_node(mid, ntype="failure_mode",
                   phenomenon=m.get("phenomenon_category", ""),
                   cause=m.get("cause_category", ""),
                   effect=m.get("effect_category", ""),
                   detection=m.get("detection_category", ""),
                   component=m.get("component_family", ""),
                   grade=m.get("completeness_grade", ""),
                   cluster=m.get("knowledge_cluster_id", ""))
        if fid in g:
            g.add_edge(fid, mid, rel="可能失效为")
    for o in orders:
        oid = o["case_id"]
        g.add_node(oid, ntype="work_order",
                   phenomenon=o.get("phenomenon_category", ""),
                   cause=o.get("cause_category", ""),
                   action=o.get("action_category", ""),
                   outcome=o.get("outcome_group", ""),
                   severity=o.get("severity_band", ""),
                   tier=o.get("relation_tier", ""))
        eq = o.get("effective_equipment_id", "")
        if eq and eq in g:
            g.add_edge(oid, eq, rel="发生于设备")
        fm = o.get("failure_mode_id", "")
        if fm and fm in g:
            g.add_edge(oid, fm, rel="历史关联失效模式")
    return KG(g, types, instances, functions, modes, orders, causes)


# ── 分层检索: 从工单出发找候选失效模式 ──────────────────────────────────
# L1 粗召回 : 工单 → 设备实例 → 设备类别 → 功能 → 失效模式（结构可达全集）
# L2 现象过滤: 在 L1 结果里保留现象类别与工单一致的（业务语义收敛）
# 这与工艺本体上的双层检索同构: 先按结构可达，再按语义收敛。

def retrieve(kg: KG, order_id: str) -> dict:
    g = kg.g
    if order_id not in g:
        return {"l1": [], "l2": [], "equipment": None, "reason": "工单不在图内"}
    eqs = [v for v in g.successors(order_id) if g.nodes[v].get("ntype") == "equipment"]
    if not eqs:
        return {"l1": [], "l2": [], "equipment": None, "reason": "工单未关联到有效设备实例"}
    eq = eqs[0]
    l1 = []
    for tid in (v for v in g.successors(eq) if g.nodes[v].get("ntype") == "equipment_type"):
        for fid in (v for v in g.successors(tid) if g.nodes[v].get("ntype") == "function"):
            l1 += [v for v in g.successors(fid) if g.nodes[v].get("ntype") == "failure_mode"]
    ph = g.nodes[order_id].get("phenomenon", "")
    l2 = [m for m in l1 if g.nodes[m].get("phenomenon") == ph]
    reason = "" if l1 else "该设备所属类别没有功能/失效模式知识（知识链缺失）"
    return {"l1": sorted(set(l1)), "l2": sorted(set(l2)), "equipment": eq,
            "phenomenon": ph, "reason": reason}


MAX_HUMAN_CAND = 5      # 候选多于此数则视为未收敛，不进人审队列而回退补知识


def gate(res: dict) -> str:
    """
    三态门（与工艺本体版同源），判据是【结构可达候选集】而非属性过滤后的结果:
      C : 结构上取不到候选（该设备类别没有功能/失效模式知识）→ 拒答，输出知识盲区
      A : 候选唯一 → 可自动关联，仍需复测/复核确认
      B : 候选 2..MAX_HUMAN_CAND 个 → 转人审，附候选清单与证据链
      C : 候选过多（未收敛）→ 同样拒答，说明该类别知识颗粒度不足

    ⚠️ 为什么不按现象/部件类别做硬过滤后再判定（实测结论，见 serres_eval.py）:
    工单侧记录的是现场可观测表象（安全防护异常、报警事件占 91.4%），失效模式库
    归类的是机理层描述（动作异常、位置或夹持异常占 95.7%）—— 同一套词表却处在
    不同抽象层。按属性硬过滤会把正确答案滤掉: 结构全集命中 87.3%，按现象过滤只剩
    11.3%，按部件族过滤 50.7%。所以【结构负责收敛，语义判断交给模型或人】，
    不在检索层做硬过滤。
    """
    n = len(res["l1"])
    if n == 0:
        return "C"
    if n == 1:
        return "A"
    return "B" if n <= MAX_HUMAN_CAND else "C"
