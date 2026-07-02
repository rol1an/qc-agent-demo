"""
可视化后端 —— 零依赖(标准库 http.server)，把【真实 Python 引擎】的计算结果以 JSON 推给前端。

关键: 前端画的每个点/每条边/每个裁决都来自本文件调用真实引擎算出的结果，
前端不造任何数据。数据流 = 后端真算 → 前端渲染(与玩具"前端假装算"相反)。

启动:
  cd src && python server.py            # 默认 stub 后端
  QC_LLM_BACKEND=openai OPENAI_BASE_URL=https://api.deepseek.com \
    OPENAI_API_KEY=你的key QC_MODEL=deepseek-chat python server.py   # 接真模型
然后浏览器打开 http://127.0.0.1:8000
"""
from __future__ import annotations
import json, os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from pathlib import Path
import numpy as np
import networkx as nx

from data_loader import load_secom, pick_process_variable
from spc import fit_control_limits, detect, rolling_cpk
from ontology import build_ontology, attach_sensor_cluster, graph_rag
from agent import diagnose
from gate import decide_and_close
from run import entry_entity

STATIC = Path(__file__).resolve().parent / "static"

# 启动时加载一次真实数据(1567×590)，之后复用，避免每次请求重算
print("加载真实 SECOM 数据…")
_X, _LABEL, _TS = load_secom()
_PS = pick_process_variable(_X, _LABEL, _TS)
print(f"就绪: {_X.shape}, 选中传感器#{_PS.sensor_id}")


def ego_edges(g: nx.DiGraph, root: str, radius: int = 5):
    """抽出以 root 为中心的子图节点+边，供前端画证据链(真实检索结果)。"""
    if root not in g:
        return {"nodes": [], "edges": []}
    sub = nx.ego_graph(g, root, radius=radius, undirected=True)
    nodes = [{"id": n, "type": sub.nodes[n].get("ntype", "?"), "root": n == root} for n in sub.nodes]
    edges = [{"s": u, "t": v, "rel": g.get_edge_data(u, v, {}).get("rel")
              or g.get_edge_data(v, u, {}).get("rel", "")} for u, v in sub.edges]
    return {"nodes": nodes, "edges": edges}


def analyze(backend: str, baseline: int = 200, n: int = 300, max_events: int = 8) -> dict:
    """🟢 跑真实全链路，产出前端要画的一切(真实计算结果)。"""
    os.environ["QC_LLM_BACKEND"] = backend
    ps = _PS
    series = ps.values
    cl = fit_control_limits(series[:baseline])
    events = detect(series, cl)
    g = build_ontology()
    cluster = f"传感器簇#{ps.sensor_id}"
    attach_sensor_cluster(g, cluster, "薄膜沉积CVD")

    ev_out, tally = [], {}
    for e in [e for e in events if e.idx < n][:max_events]:
        root = entry_entity(e, cluster)
        sg = graph_rag(g, root)
        feats = {"批次": e.idx, "规则": e.rule, "Cpk": round(e.cpk, 2), "前瞻": e.proactive}
        hyp = diagnose(feats, sg)
        d = decide_and_close(hyp, g, series, e.idx, cl)
        tally[d.branch] = tally.get(d.branch, 0) + 1
        ev_out.append({
            "idx": e.idx, "kind": e.kind, "rule": e.rule, "value": round(e.value, 2),
            "cpk": None if np.isnan(e.cpk) else round(e.cpk, 2), "proactive": e.proactive,
            "retrieval": {"root": root, "summary": sg.community_summary,
                          "graph": ego_edges(g, root),
                          "evidence_paths": sg.evidence_paths},
            "hypothesis": {"cause": hyp.cause_entity, "evidence": hyp.evidence_node_ids,
                           "conflict": hyp.conflict, "confidence": round(hyp.confidence, 2),
                           "disposition": hyp.disposition, "source": hyp.source,
                           "rationale": hyp.rationale},
            "gate": {"branch": d.branch, "action": d.action, "reason": d.reason,
                     "recheck_ok": d.recheck_ok},
        })

    return {
        "meta": {"rows": int(_X.shape[0]), "cols": int(_X.shape[1]),
                 "sensor": ps.sensor_id, "sensor_reason": ps.reason,
                 "ts_start": str(_TS.iloc[0].date()), "ts_end": str(_TS.iloc[-1].date()),
                 "backend": backend, "n": n},
        "spc": {"cl": cl.cl, "ucl": cl.ucl, "lcl": cl.lcl, "usl": cl.usl, "lsl": cl.lsl,
                "series": [round(float(v), 3) for v in series[:n]],
                "cpk": [None if np.isnan(c := rolling_cpk(series, cl, i)) else round(c, 3)
                        for i in range(n)]},
        "events": ev_out,
        "tally": tally,
        "proactive": sum(e["proactive"] for e in ev_out),
    }


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        self.send_response(code); self.send_header("Content-Type", ctype)
        b = body if isinstance(body, bytes) else body.encode()
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/api/analyze":
            q = parse_qs(u.query)
            backend = q.get("backend", ["stub"])[0]
            n = int(q.get("n", ["300"])[0])
            try:
                self._send(200, json.dumps(analyze(backend, n=n), ensure_ascii=False))
            except Exception as ex:
                self._send(500, json.dumps({"error": str(ex)}, ensure_ascii=False))
            return
        # 静态文件
        fn = "index.html" if u.path in ("/", "") else u.path.lstrip("/")
        fp = STATIC / fn
        if fp.is_file() and STATIC in fp.resolve().parents:
            ct = {"html": "text/html", "js": "text/javascript", "css": "text/css"}.get(
                fp.suffix.lstrip("."), "application/octet-stream")
            self._send(200, fp.read_bytes(), f"{ct}; charset=utf-8")
        else:
            self._send(404, b"not found", "text/plain")

    def log_message(self, *a):  # 静音访问日志
        pass


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    print(f"服务启动: http://127.0.0.1:{port}  (LLM 后端默认 stub, 可用 QC_LLM_BACKEND 切换)")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
