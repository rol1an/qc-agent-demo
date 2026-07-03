"""生成【真实引擎计算结果】快照，内嵌进自包含前端。key 只走环境变量，不写文件。"""
import json, os
from pathlib import Path
from server import analyze   # import 时加载真实 SECOM 一次

out = {"stub": analyze("stub", n=300)}
if os.getenv("OPENAI_API_KEY"):
    try:
        out["deepseek"] = analyze("openai", n=300)
    except Exception as e:
        out["deepseek"] = {"error": str(e)}

docs = Path(__file__).resolve().parent.parent / "docs"
docs.mkdir(exist_ok=True)
(docs / "snapshots.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
ds = out.get("deepseek")
print(f"stub 事件 {len(out['stub']['events'])} | deepseek "
      f"{'未生成(无key)' if ds is None else ('FAIL:'+ds['error'] if 'error' in ds else '事件'+str(len(ds['events'])))}")
print("已写 docs/snapshots.json")
