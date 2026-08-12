"""生成【真实引擎计算结果】快照 + 对照实验数字，内嵌进自包含前端。key 只走环境变量，不写文件。

产出(docs/):
  snapshots.json  引擎快照(供人工检查)
  snapshots.js    window.SNAP=快照; window.CONTRAST=对照实验 —— 前端唯一数据源
  contrast.json   对照实验结果(供方案文档引用)
无 OPENAI_API_KEY 时，deepseek 沿用已有快照(2026-07 生成的真实模型输出)并标注 schema_note。
"""
import json, os
from pathlib import Path
from server import analyze          # import 时加载真实 SECOM 一次
from baseline_compare import compute as contrast_compute
from threshold_sensitivity import compute as sensitivity_compute

docs = Path(__file__).resolve().parent.parent / "docs"
docs.mkdir(exist_ok=True)

out = {"stub": analyze("stub", n=700)}
if os.getenv("OPENAI_API_KEY"):
    try:
        out["deepseek"] = analyze("openai", n=700)
    except Exception as e:
        out["deepseek"] = {"error": str(e)}
else:
    old = docs / "snapshots.json"
    if old.exists():
        prev = json.loads(old.read_text(encoding="utf-8")).get("deepseek")
        if prev and "error" not in prev:
            prev.setdefault("schema_note",
                            "此快照为 2026-07 用真实 DeepSeek 生成(旧管线，无影子放权字段)；"
                            "配 OPENAI_API_KEY 重跑 gen_snapshot.py 即可更新")
            out["deepseek"] = prev

contrast = contrast_compute()
sens = sensitivity_compute()

(docs / "snapshots.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
(docs / "contrast.json").write_text(json.dumps(contrast, ensure_ascii=False, indent=2), encoding="utf-8")
(docs / "sensitivity.json").write_text(json.dumps(sens, ensure_ascii=False, indent=2), encoding="utf-8")
(docs / "snapshots.js").write_text(
    "window.SNAP=" + json.dumps(out, ensure_ascii=False)
    + ";\nwindow.CONTRAST=" + json.dumps(contrast, ensure_ascii=False)
    + ";\nwindow.SENS=" + json.dumps(sens, ensure_ascii=False) + ";",
    encoding="utf-8")

ds = out.get("deepseek")
print(f"stub 事件 {len(out['stub']['events'])} tally={out['stub']['tally']} "
      f"ledger={out['stub']['ledger']}")
print("deepseek " + ("未提供" if ds is None else
      ("FAIL:" + ds["error"] if "error" in ds else
       f"事件{len(ds['events'])}" + (" (沿用旧快照)" if "schema_note" in ds else " (本次新生成)"))))
print(f"对照实验 样本{contrast['样本事件数']} 已随快照嵌入")
print(f"门限敏感性 {sens['门限抬高的代价']} | 正态性 JB={sens['Cpk前提校验']['JB统计量']} "
      f"通过={sens['Cpk前提校验']['通过']}")
print("已写 docs/snapshots.json + snapshots.js + contrast.json + sensitivity.json")
