"""
SPC 引擎层 —— 【真实的】统计过程控制，不是画折线。

实现：
  - I-MR 控制限估计（用移动极差 MR-bar/1.128 估 σ，这是 IATF/SPC 教科书做法，
    比直接 std 更稳健，抗离群点）
  - Nelson / Western Electric 判异规则（rule 1/2/3）—— 逐点真判定
  - 滚动过程能力指数 Cpk + 按特性等级分级的能力门限（见 CAPABILITY_GATES）
  - Cpk 前提校验（Jarque-Bera 正态性检验）—— 不满足就如实降级为趋势指标，不硬算
  - "前瞻立案"：在【尚无点越规格】时，靠 rule 2/3（连续单边/趋势）或 Cpk 下滑提前预警
     —— 这就是方案里"主动 vs 被动"的落点。

所有函数纯确定性，输入相同则输出相同。
"""
from __future__ import annotations
import numpy as np
from dataclasses import dataclass, field


# ── 能力门限按特性等级分级 ────────────────────────────────────────────────
# 依据 IATF 16949:2016 条款 8.5.1.5（特殊特性控制）+ AIAG-VDA SPC 手册 6.2：
#   一般特性                     量产 Cpk ≥ 1.33
#   特殊特性 CC/SC（安全、法规相关，如制动/转向件）  优先要求 Cpk ≥ 1.67
# 门限是【按特性等级配置的参数】，不是一个全局常数 —— 迁到赛力斯时由质量部门按
# 各 KPC 的特性等级标定（本体里 KPC 节点带 char_class 属性，见 ontology.py）。
CAPABILITY_GATES = {"general": 1.33, "special": 1.67}


def capability_gate(char_class: str = "general") -> float:
    """取某特性等级的量产能力门限。未知等级按最严处理（安全优先）。"""
    return CAPABILITY_GATES.get(char_class, max(CAPABILITY_GATES.values()))


@dataclass
class ControlLimits:
    cl: float      # 中心线
    sigma: float   # 过程标准差(由 MR 估计)
    ucl: float     # 上控制限 = cl + 3σ
    lcl: float     # 下控制限 = cl - 3σ
    usl: float     # 上规格限(演示规格，由稳定基线派生, 见 fit)
    lsl: float     # 下规格限


@dataclass
class SpcEvent:
    idx: int              # 触发点在序列中的位置
    kind: str             # 'beyond_spec'(越规格,被动) | 'trend'(趋势,前瞻) | 'run'(连续单边,前瞻)
    rule: str             # 命中的规则描述
    value: float
    cpk: float
    proactive: bool       # True=缺陷发生前介入(前瞻立案)；False=已越限(被动告警)


def fit_control_limits(baseline: np.ndarray, spec_k: float = 6.0) -> ControlLimits:
    """
    🟢 用稳定基线段估计控制限。
    σ 用移动极差法: σ̂ = MR̄ / 1.128 (d2 常数, n=2)。规格限用 cl ± spec_k·σ 派生
    (SECOM 无工程规格，此处按稳定基线派生规格用于演示，spec_k=6 → 基线 Cpk≈2)。
    """
    cl = float(np.median(baseline))                      # 中位数当中心, 抗离群
    mr = np.abs(np.diff(baseline))                       # 移动极差
    sigma = float(np.mean(mr) / 1.128) or float(np.std(baseline))
    sigma = max(sigma, 1e-9)
    usl, lsl = cl + spec_k * sigma, cl - spec_k * sigma
    return ControlLimits(cl=cl, sigma=sigma, ucl=cl + 3 * sigma, lcl=cl - 3 * sigma,
                         usl=usl, lsl=lsl)


def rolling_cpk(series: np.ndarray, cl_obj: ControlLimits, i: int, window: int = 25) -> float:
    """滚动 Cpk = min(USL-μ, μ-LSL) / (3σ_window)，用最近 window 点。"""
    lo = max(0, i - window + 1)
    w = series[lo:i + 1]
    if len(w) < 8:
        return float("nan")
    mu, sd = float(np.mean(w)), float(np.std(w, ddof=1)) or 1e-9
    return min(cl_obj.usl - mu, mu - cl_obj.lsl) / (3 * sd)


# ── Cpk 的前提校验：正态性 ────────────────────────────────────────────────
# AIAG-VDA SPC 手册 6.2.3：非正态数据（强度、寿命这类特性）禁止直接算 Cp/Cpk，
# 须先做变换（如 Box-Cox）或改用非正态能力算法。本 demo 用 Jarque-Bera 检验
# 显式验一遍前提，不通过就在输出里如实降级 —— 不假装 Cpk 绝对值可作能力判定。

@dataclass
class NormalityCheck:
    n: int
    skew: float          # 偏度（正态≈0）
    kurt: float          # 超值峰度（正态≈0）
    jb: float            # Jarque-Bera 统计量，渐近 χ²(2)
    passed: bool         # jb < 5.99 → 5% 显著性下不拒绝正态假设
    verdict: str

    CRIT_5PCT = 5.99     # χ²(2) 的 95% 分位


def jarque_bera(x: np.ndarray) -> NormalityCheck:
    """🟢 Jarque-Bera 正态性检验（纯 numpy，无 scipy 依赖）: JB = n/6·(S² + K²/4)。"""
    v = np.asarray(x, dtype=float)
    v = v[np.isfinite(v)]
    n = len(v)
    if n < 20:
        return NormalityCheck(n, float("nan"), float("nan"), float("nan"), False,
                              "样本不足 20，不做正态性判定")
    mu, sd = float(np.mean(v)), float(np.std(v)) or 1e-9
    z = (v - mu) / sd
    skew = float(np.mean(z ** 3))
    kurt = float(np.mean(z ** 4) - 3.0)
    jb = n / 6.0 * (skew ** 2 + kurt ** 2 / 4.0)
    passed = jb < NormalityCheck.CRIT_5PCT
    verdict = (f"JB={jb:.1f} < 5.99，5% 显著性下不拒绝正态假设 → Cpk 绝对值可作能力判定"
               if passed else
               f"JB={jb:.1f} ≥ 5.99，拒绝正态假设(偏度{skew:+.2f} 峰度{kurt:+.2f}) → "
               f"本 demo 的 Cpk 仅作【相对趋势指标】，绝对值不作能力判定；"
               f"生产环境按 AIAG-VDA SPC 6.2.3 先做 Box-Cox 变换或改用非正态能力算法")
    return NormalityCheck(n, skew, kurt, jb, passed, verdict)


def nelson_flags(series: np.ndarray, cl_obj: ControlLimits, i: int) -> tuple[str, str, bool] | None:
    """
    🟢 对第 i 个点做 Nelson 判异（返回 kind, 规则描述, 是否前瞻）。只在有异常时返回。
    - 越规格(USL/LSL): 被动 —— 已出缺陷
    - rule1 越 3σ 控制限: 被动预警
    - rule2 连续 9 点单边 / rule3 连续 6 点递增或递减: 前瞻 —— 规格内趋势
    """
    v = series[i]
    cl, s = cl_obj.cl, cl_obj.sigma
    if v > cl_obj.usl or v < cl_obj.lsl:
        return ("beyond_spec", "点越规格限(USL/LSL)——缺陷已发生", False)
    if v > cl_obj.ucl or v < cl_obj.lcl:
        return ("beyond_ctrl", "Nelson Rule1: 点越 3σ 控制限", False)
    # rule2: 连续 9 点在中心线同侧
    if i >= 8:
        seg = series[i - 8:i + 1]
        if np.all(seg > cl) or np.all(seg < cl):
            return ("run", "Nelson Rule2: 连续 9 点位于中心线同侧(规格内偏移)", True)
    # rule3: 连续 6 点持续递增或递减
    if i >= 5:
        seg = series[i - 5:i + 1]
        d = np.diff(seg)
        if np.all(d > 0) or np.all(d < 0):
            return ("trend", "Nelson Rule3: 连续 6 点单调趋势(规格内漂移)", True)
    return None


def detect(series: np.ndarray, cl_obj: ControlLimits, cpk_gate: float = 1.33) -> list[SpcEvent]:
    """
    扫全序列，产出判异事件。前瞻立案额外条件: 命中趋势/连续单边，且滚动 Cpk 已跌破 cpk_gate。
    去抖: 同类事件 20 点内不重复报。
    """
    events, last = [], {}
    for i in range(len(series)):
        flag = nelson_flags(series, cl_obj, i)
        if flag is None:
            continue
        kind, rule, proactive = flag
        cpk = rolling_cpk(series, cl_obj, i)
        # 前瞻类要求 Cpk 确已下滑，避免过度敏感
        if proactive and not (np.isnan(cpk) or cpk < cpk_gate):
            continue
        if i - last.get(kind, -99) < 20:      # 去抖
            continue
        last[kind] = i
        events.append(SpcEvent(idx=i, kind=kind, rule=rule, value=float(series[i]),
                               cpk=float(cpk), proactive=proactive))
    return events


def ascii_chart(series: np.ndarray, cl_obj: ControlLimits, events: list[SpcEvent],
                width: int = 72, height: int = 15) -> str:
    """终端 ASCII 控制图：· 正常, x 越控制限, X 越规格, ! 前瞻立案点; 虚线为 CL/UCL/LCL。"""
    n = len(series)
    xs = np.linspace(0, n - 1, min(width, n)).astype(int)
    lo = min(cl_obj.lsl, series.min()); hi = max(cl_obj.usl, series.max())
    def row(v):  # 值 -> 行号(0在顶)
        return int(round((hi - v) / (hi - lo + 1e-9) * (height - 1)))
    grid = [[" "] * len(xs) for _ in range(height)]
    for lvl, ch in [(cl_obj.usl, "="), (cl_obj.ucl, "-"), (cl_obj.cl, "~"),
                    (cl_obj.lcl, "-"), (cl_obj.lsl, "=")]:
        r = row(lvl)
        if 0 <= r < height:
            for c in range(len(xs)):
                if grid[r][c] == " ":
                    grid[r][c] = ch
    ev_idx = {e.idx: e for e in events}
    for c, xi in enumerate(xs):
        v = series[xi]; r = row(v); r = min(max(r, 0), height - 1)
        mark = "·"
        for j in range(xi, min(n, xi + max(1, n // len(xs)))):
            if j in ev_idx:
                e = ev_idx[j]
                mark = "!" if e.proactive else ("X" if e.kind == "beyond_spec" else "x")
        grid[r][c] = mark
    lines = ["".join(r) for r in grid]
    legend = "  图例: ~CL  -±3σ控制限  =±规格限  ·正常  !前瞻立案  x越控制限  X越规格"
    return "\n".join(lines) + "\n" + legend


if __name__ == "__main__":
    from data_loader import load_secom, pick_process_variable
    X, label, ts = load_secom()
    ps = pick_process_variable(X, label, ts)
    cl = fit_control_limits(ps.values[:200])   # 前 200 批次当稳定基线
    ev = detect(ps.values, cl)
    print(f"控制限: CL={cl.cl:.2f} UCL={cl.ucl:.2f} LCL={cl.lcl:.2f} USL={cl.usl:.2f} LSL={cl.lsl:.2f}")
    print(f"检出事件 {len(ev)} 个 | 前瞻 {sum(e.proactive for e in ev)} / 被动 {sum(not e.proactive for e in ev)}")
    for e in ev[:6]:
        print(f"  #{e.idx:4d} {e.kind:12s} cpk={e.cpk:5.2f} {'[前瞻]' if e.proactive else '[被动]'} {e.rule}")
