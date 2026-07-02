"""
数据加载层 —— 读取【真实公开数据集】UCI SECOM（半导体制造过程传感器 → pass/fail）。

诚实声明：SECOM 是半导体制造的真实过程数据（1567 批次 × 590 传感器 × 3 个月，
带真实时间戳与 pass/fail 标签）。本 demo 用它证明「过程监控 → 图检索根因 → 确定性 gate」
这条引擎在真实工业数据上真的跑得通；引擎与领域解耦，迁移到赛力斯汽车产线只需替换
本模块的数据接口与 ontology.py 里的工艺本体。我们不用编造的汽车数据充数。

数据来源: https://archive.ics.uci.edu/ml/machine-learning-databases/secom/
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from dataclasses import dataclass
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


@dataclass
class ProcessStream:
    """一条按时间排序的过程测量流 —— SPC 引擎的输入。"""
    sensor_id: int            # SECOM 传感器列号（匿名，无物理名）
    timestamps: pd.Series     # 真实采集时间
    values: np.ndarray        # 该传感器的时序读数（已按时间排序、插补）
    labels: np.ndarray        # 对应批次的 pass(-1)/fail(1) 标签
    reason: str               # 为什么选这个传感器做演示（数据驱动，非人为指定）


def load_secom() -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    """加载原始 SECOM，返回 (清洗后的特征矩阵 X, 标签 label, 时间戳 ts)，按时间排序。"""
    X = pd.read_csv(DATA_DIR / "secom.data", sep=r"\s+", header=None, na_values="NaN")
    lab = pd.read_csv(DATA_DIR / "secom_labels.data", sep=r"\s+", header=None,
                      names=["label", "ts"], quotechar='"')
    ts = pd.to_datetime(lab["ts"], format="%d/%m/%Y %H:%M:%S")

    # 按真实采集时间排序（SPC 是对"按时间到来的测量"做监控）
    order = ts.argsort().to_numpy()
    X, label, ts = X.iloc[order].reset_index(drop=True), lab["label"].iloc[order].reset_index(drop=True), ts.iloc[order].reset_index(drop=True)

    # 清洗：丢弃常量列 & 缺失>50% 的列（它们对监控无信息）
    keep = (X.nunique() > 1) & (X.isna().mean() <= 0.5)
    X = X.loc[:, keep]
    # 剩余缺失用列中位数插补（工业实践里的简单填补）
    X = X.fillna(X.median(numeric_only=True))
    return X, label, ts


def pick_process_variable(X: pd.DataFrame, label: pd.Series, ts: pd.Series) -> ProcessStream:
    """
    🟢 核心：用【数据驱动】的方式选一个最适合演示 SPC 的过程变量，而不是人为挑。
    判据 = 与质量结果(fail)的点二列相关最强、且方差充足、连续可监控。
    这保证"我们监控的这个量确实和质量相关"，不是随手挑一列。
    """
    y = (label.to_numpy() == 1).astype(float)  # fail=1
    Xv = X.to_numpy(dtype=float)
    # 点二列相关 = Pearson(特征, fail指示)；取绝对值最大者
    Xz = (Xv - Xv.mean(0)) / (Xv.std(0) + 1e-9)
    corr = np.abs((Xz * (y - y.mean())[:, None]).mean(0) / (y.std() + 1e-9))
    # 排除近乎无方差的列
    corr[Xv.std(0) < 1e-6] = 0
    best = int(np.argmax(corr))
    col = X.columns[best]
    series = Xv[:, best]
    reason = (f"传感器#{col} 与质量结果(fail)的点二列相关系数最高(|r|={corr[best]:.3f}), "
              f"方差充足、缺失已插补，故作为受监控的关键过程变量(代表 KPC)。")
    return ProcessStream(sensor_id=int(col), timestamps=ts, values=series,
                         labels=label.to_numpy(), reason=reason)


if __name__ == "__main__":
    X, label, ts = load_secom()
    print(f"清洗后特征矩阵: {X.shape} | pass/fail: {dict(label.value_counts())}")
    ps = pick_process_variable(X, label, ts)
    print(ps.reason)
    print(f"选中序列: n={len(ps.values)}, mean={ps.values.mean():.3f}, std={ps.values.std():.3f}, "
          f"范围=[{ps.values.min():.2f}, {ps.values.max():.2f}]")
