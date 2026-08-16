"""抽一片真实 SECOM 原始数据给放映页用。

放映页要证明"数据不是编的"，光说 1567×590 没有说服力，得让评委看见真实数字在滚。
产物 docs/rawslice.json 只含原始文件的前若干行若干列 —— 公开数据集，无合规问题。

跑法: python gen_rawslice.py
"""
from __future__ import annotations
import json, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
ROWS, COLS = 60, 12          # 放映页一屏能滚完的量
SENSOR = 59                  # 受监控的关键过程变量，见 snapshots.meta.sensor


def main():
    raw = (ROOT / "data" / "secom.data").read_text().splitlines()
    lab = (ROOT / "data" / "secom_labels.data").read_text().splitlines()

    rows = []
    for i in range(min(ROWS, len(raw))):
        cells = raw[i].split()
        flag, _, ts = lab[i].partition(" ")
        rows.append({
            "ts": ts.strip('"'),
            # NaN 在原始文件里就是字面量 NaN，照实传给前端显示成缺失
            "v": [c for c in cells[:COLS]],
            "s59": cells[SENSOR] if len(cells) > SENSOR else "NaN",
            "fail": flag == "1",
        })

    n_fail = sum(1 for l in lab if l.startswith("1"))
    out = {
        "source": "UCI SECOM (半导体制造过程真实数据)",
        "total_rows": len(raw),
        "total_cols": len(raw[0].split()),
        "shown_rows": len(rows),
        "shown_cols": COLS,
        "sensor_col": SENSOR,
        "fail_count": n_fail,
        "pass_count": len(lab) - n_fail,
        "ts_start": lab[0].split('"')[1],
        "ts_end": lab[-1].split('"')[1],
        "rows": rows,
    }
    p = ROOT / "docs" / "rawslice.json"
    p.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"已写入 {p}  {len(rows)} 行 × {COLS} 列 / 全量 {out['total_rows']}×{out['total_cols']}")
    print(f"合格 {out['pass_count']} · 不合格 {n_fail} · {out['ts_start']} → {out['ts_end']}")


if __name__ == "__main__":
    main()
