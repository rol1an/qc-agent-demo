"""
知识底座层 —— 用 NetworkX 建【真实工艺本体】+【真实图检索(Graph RAG)】。

本体结构对应命题里的:
  工序(process) → 关键控制特性(KCC) → 失效机理(mechanism) → 失效模式(mode)
                → 影响的质量特性(KPC) → 处置策略(disposition)
节点/关系取自【真实半导体制造工艺知识】(SECOM 是半导体数据)。传感器为匿名，故按
统计关联把"受监控传感器簇"挂到工序节点上(数据驱动映射，非杜撰传感器物理含义)。

Graph RAG = 真的图算法(ego-graph 多跳子图检索 + 社区摘要)，不是查字典:
  给定异常实体 → 检索其可达的失效机理/模式/处置子图 → 供 Agent 推理，且每个结论
  都能回溯到具体图节点(可解释证据链)。
"""
from __future__ import annotations
import networkx as nx
from dataclasses import dataclass


# 🟢 真实半导体制造工艺本体（工序→KCC→机理→失效模式→KPC→处置）
# 三元组: (源, 关系, 目标, 源类型, 目标类型)
_TRIPLES = [
    # 薄膜沉积 CVD
    ("薄膜沉积CVD", "受控于", "腔体温度", "process", "kcc"),
    ("薄膜沉积CVD", "受控于", "前驱体流量", "process", "kcc"),
    ("腔体温度", "偏离引发", "沉积速率漂移", "kcc", "mechanism"),
    ("前驱体流量", "偏离引发", "沉积速率漂移", "kcc", "mechanism"),
    ("沉积速率漂移", "表现为", "膜厚超差", "mechanism", "mode"),
    ("膜厚超差", "影响", "膜厚均匀性KPC", "mode", "kpc"),
    ("膜厚超差", "处置", "校正温度/流量补偿", "mode", "disposition"),
    # 刻蚀 Etch
    ("等离子刻蚀", "受控于", "刻蚀时间", "process", "kcc"),
    ("等离子刻蚀", "受控于", "RF功率", "process", "kcc"),
    ("刻蚀时间", "偏离引发", "刻蚀速率异常", "kcc", "mechanism"),
    ("RF功率", "偏离引发", "刻蚀速率异常", "kcc", "mechanism"),
    ("刻蚀速率异常", "表现为", "过刻/欠刻", "mechanism", "mode"),
    ("过刻/欠刻", "影响", "关键尺寸CD_KPC", "mode", "kpc"),
    ("过刻/欠刻", "处置", "调整刻蚀时间窗", "mode", "disposition"),
    # 光刻 Photo
    ("光刻曝光", "受控于", "曝光剂量", "process", "kcc"),
    ("曝光剂量", "偏离引发", "显影线宽偏移", "kcc", "mechanism"),
    ("显影线宽偏移", "表现为", "套刻误差", "mechanism", "mode"),
    ("套刻误差", "影响", "关键尺寸CD_KPC", "mode", "kpc"),
    ("套刻误差", "处置", "重工/调剂量", "mode", "disposition"),
    # CMP 平坦化
    ("CMP平坦化", "受控于", "抛光压力", "process", "kcc"),
    ("抛光压力", "偏离引发", "去除速率不均", "kcc", "mechanism"),
    ("去除速率不均", "表现为", "碟形凹陷", "mechanism", "mode"),
    ("碟形凹陷", "影响", "平坦度KPC", "mode", "kpc"),
    ("碟形凹陷", "处置", "调压力/更换垫", "mode", "disposition"),
]


@dataclass
class Subgraph:
    root: str
    modes: list[str]              # 检索到的候选失效模式
    kpcs: list[str]
    dispositions: list[str]
    evidence_paths: list[list[str]]   # 从 root 到 disposition 的可回溯路径(=证据链)
    community_summary: str


def build_ontology() -> nx.DiGraph:
    """构建有向工艺知识图谱。"""
    g = nx.DiGraph()
    for s, rel, t, st, tt in _TRIPLES:
        g.add_node(s, ntype=st); g.add_node(t, ntype=tt)
        g.add_edge(s, rel_to := t, rel=rel)
    return g


def attach_sensor_cluster(g: nx.DiGraph, cluster_name: str, process: str) -> None:
    """把'受监控传感器簇'(数据驱动)挂到某工序 —— 迁移到赛力斯时改这里即可。"""
    g.add_node(cluster_name, ntype="sensor_cluster")
    g.add_edge(cluster_name, process, rel="监控")


def graph_rag(g: nx.DiGraph, root: str, max_hops: int = 5) -> Subgraph:
    """
    🟢 真图检索: 从 root 出发做多跳可达子图，抽出失效模式/KPC/处置，并记录每条
    root→disposition 的路径作为可回溯证据链。命中多个失效模式 = 证据可能冲突(交给 gate)。
    """
    if root not in g:
        return Subgraph(root, [], [], [], [], f"实体『{root}』不在工艺本体内")
    # 无向多跳: 从终端质量特性(如 CD)反查会命中跨工艺段的多个上游失效模式 —— 正是
    # "根因横跨多工艺段"这类向量检索召回不全、而图检索擅长的全局问题。
    reach = nx.ego_graph(g, root, radius=max_hops, undirected=True)
    modes = [n for n in reach if reach.nodes[n].get("ntype") == "mode"]
    kpcs = [n for n in reach if reach.nodes[n].get("ntype") == "kpc"]
    disps = [n for n in reach if reach.nodes[n].get("ntype") == "disposition"]
    ug = g.to_undirected()
    paths = []
    for d in disps:
        try:
            paths.append(nx.shortest_path(ug, root, d))
        except nx.NetworkXNoPath:
            pass
    summ = (f"『{root}』可达 {len(modes)} 个失效模式、{len(kpcs)} 个质量特性、"
            f"{len(disps)} 条处置；共 {len(paths)} 条可回溯证据路径。")
    return Subgraph(root, modes, kpcs, disps, paths, summ)


def entity_in_ontology(g: nx.DiGraph, name: str) -> bool:
    """gate 分支 C 用: LLM 提出的根因实体是否在本体白名单内(防幻觉硬护栏)。"""
    return name in g


if __name__ == "__main__":
    g = build_ontology()
    print(f"本体: {g.number_of_nodes()} 节点 / {g.number_of_edges()} 边")
    attach_sensor_cluster(g, "传感器簇#59", "薄膜沉积CVD")
    sg = graph_rag(g, "传感器簇#59")
    print(sg.community_summary)
    print("候选失效模式:", sg.modes)
    print("证据路径示例:", sg.evidence_paths[0] if sg.evidence_paths else "无")
    print("本体外实体检测:", entity_in_ontology(g, "等离子喷涂枪老化"))
