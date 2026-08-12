#!/usr/bin/env bash
# 一键演示 —— 录屏 / 现场演示时用，避免手敲长命令出错。
#
#   ./demo.sh              跑完整链路（不派单，最快，适合先自检）
#   ./demo.sh --feishu     跑完整链路并把工单真实派发到飞书多维表格
#   ./demo.sh --check      只验证飞书 webhook 通不通（录制前先跑这个）
#   ./demo.sh --contrast   跑对照实验 + 门限敏感性（出方案文档要的数字）
#
# webhook 地址从环境变量读，不写进仓库：
#   export QC_FEISHU_WEBHOOK='https://…/base/automation/webhook/event/xxx'
# 接真模型（可选）：
#   export QC_LLM_BACKEND=openai OPENAI_BASE_URL=https://api.deepseek.com \
#          OPENAI_API_KEY=sk-… QC_MODEL=deepseek-chat
set -euo pipefail
cd "$(dirname "$0")"

PY=".venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"

hr() { printf '\n\033[36m%s\033[0m\n' "────────────────────────────────────────────────────────"; }
note() { printf '\033[90m%s\033[0m\n' "$1"; }
fail() { printf '\033[31m%s\033[0m\n' "$1"; exit 1; }

# ── 环境自检 ────────────────────────────────────────────────
"$PY" -c 'import numpy, pandas, networkx' 2>/dev/null || fail \
  "缺依赖。先装：uv venv && uv pip install numpy pandas networkx matplotlib"
[ -s data/secom.data ] || [ -s data/uci-secom.csv ] || note \
  "提示：data/ 下没看到 SECOM 数据文件，若加载失败请按 README 重新拉取数据。"

case "${1:-}" in
  --check)
    [ -n "${QC_FEISHU_WEBHOOK:-}" ] || fail \
      "没设 QC_FEISHU_WEBHOOK。先 export，再跑 ./demo.sh --check"
    hr; echo "验证飞书 webhook 是否可用（会在工单表里产生一条测试记录）"
    code=$(curl -s -o /tmp/qc_webhook_resp.txt -w '%{http_code}' \
      -X POST "$QC_FEISHU_WEBHOOK" -H 'Content-Type: application/json' \
      -d '{"工单标题":"【连通性测试】录制前自检","分支":"B","批次号":"0","触发规则":"连通性测试"}' || true)
    echo "HTTP $code"; cat /tmp/qc_webhook_resp.txt 2>/dev/null; echo
    if [ "$code" = "200" ]; then
      printf '\033[32m%s\033[0m\n' "✓ webhook 通。去工单表确认那条测试记录已落表、AI 处置摘要已生成，然后把它删掉再开录。"
    else
      printf '\033[31m%s\033[0m\n' "✗ webhook 不通。按 07-demo视频分镜 里的应急预案处理：先看自动化是不是被停用了。"
    fi
    ;;
  --contrast)
    hr; echo "对照实验：纯 LLM 直批 vs 三态 gate"
    (cd src && "../$PY" baseline_compare.py)
    hr; echo "门限敏感性 + Cpk 正态性前提校验"
    (cd src && "../$PY" threshold_sensitivity.py)
    ;;
  --feishu)
    [ -n "${QC_FEISHU_WEBHOOK:-}" ] || fail \
      "没设 QC_FEISHU_WEBHOOK。先 export，或用 ./demo.sh 跑不派单的版本"
    hr; echo "完整链路 + 工单真实派发到飞书"
    note "留意终端里的「派单」行，然后切到飞书工单表刷新——记录会当场出现。"
    (cd src && "../$PY" run.py)
    ;;
  ""|--local)
    hr; echo "完整链路（不派单）"
    note "看四件事：前瞻立案、双层检索选层、三态裁决、影子放权台账（含 #634 收权）。"
    (cd src && "../$PY" run.py)
    ;;
  *)
    grep '^#' "$0" | tail -n +2 | sed 's/^# \{0,1\}//' | head -17; exit 1;;
esac
