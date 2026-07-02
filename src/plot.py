"""生成【真实数据】的 SPC 控制图 + 滚动 Cpk 曲线 PNG，作为补充材料里的静态证据。"""
from __future__ import annotations
import numpy as np
import matplotlib
matplotlib.use("Agg")            # 无 GUI 后端
import matplotlib.pyplot as plt
from matplotlib import font_manager
from pathlib import Path
from data_loader import load_secom, pick_process_variable
from spc import fit_control_limits, detect, rolling_cpk

# 尽量用系统中文字体，找不到就退化(不影响数值真实性)
for f in ["PingFang SC", "Heiti SC", "Songti SC", "STHeiti", "Arial Unicode MS"]:
    if any(f in ff.name for ff in font_manager.fontManager.ttflist):
        plt.rcParams["font.sans-serif"] = [f]; break
plt.rcParams["axes.unicode_minus"] = False


def make(out="control_chart.png", n=300):
    X, label, ts = load_secom()
    ps = pick_process_variable(X, label, ts)
    v = ps.values[:n]
    cl = fit_control_limits(ps.values[:200])
    events = [e for e in detect(ps.values, cl) if e.idx < n]
    cpk = np.array([rolling_cpk(ps.values, cl, i) for i in range(n)])

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 6.2), height_ratios=[3, 1.4], sharex=True)
    x = np.arange(n)
    ax1.plot(x, v, lw=.9, color="#3b4252", zorder=2)
    for y, c, ls, lab in [(cl.usl, "#c0392b", "-", "USL"), (cl.ucl, "#e67e22", "--", "UCL(+3σ)"),
                          (cl.cl, "#2980b9", "-", "CL"), (cl.lcl, "#e67e22", "--", "LCL(−3σ)"),
                          (cl.lsl, "#c0392b", "-", "LSL")]:
        ax1.axhline(y, color=c, ls=ls, lw=1, alpha=.8)
        ax1.text(n * 1.005, y, lab, color=c, va="center", fontsize=8)
    for e in events:
        col = {"beyond_spec": "#c0392b", "beyond_ctrl": "#e67e22", "run": "#f1c40f", "trend": "#f1c40f"}[e.kind]
        mk = "^" if e.proactive else ("s" if e.kind == "beyond_spec" else "o")
        ax1.scatter(e.idx, e.value, c=col, s=70, marker=mk, zorder=5,
                    edgecolors="k", linewidths=.5,
                    label=("前瞻立案" if e.proactive else "被动告警"))
    h, l = ax1.get_legend_handles_labels()
    by = dict(zip(l, h)); ax1.legend(by.values(), by.keys(), loc="upper right", fontsize=8)
    ax1.set_title(f"真实数据 SPC 控制图 · UCI SECOM 传感器#{ps.sensor_id}(与质量结果相关最强) · 前{n}批次",
                  fontsize=11)
    ax1.set_ylabel("过程测量值")

    ax2.plot(x, cpk, color="#27ae60", lw=1.1)
    ax2.axhline(1.33, color="#c0392b", ls="--", lw=1)
    ax2.text(n * 1.005, 1.33, "Cpk=1.33\n(前瞻门限)", color="#c0392b", va="center", fontsize=7.5)
    ax2.fill_between(x, 0, cpk, where=cpk < 1.33, color="#c0392b", alpha=.12)
    ax2.set_ylabel("滚动 Cpk"); ax2.set_xlabel("生产批次序号(按真实采集时间)"); ax2.set_ylim(0, max(2.2, np.nanmax(cpk) * 1.1))
    fig.tight_layout()
    p = Path(__file__).resolve().parent.parent / out
    fig.savefig(p, dpi=130, bbox_inches="tight"); plt.close(fig)
    print(f"已生成 {p}  (事件 {len(events)} 个: 前瞻 {sum(e.proactive for e in events)})")
    return str(p)


if __name__ == "__main__":
    make()
