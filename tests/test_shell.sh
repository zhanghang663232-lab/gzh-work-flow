#!/usr/bin/env bash
set -euo pipefail
PIPELINE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
bash -n "$PIPELINE/wechat.sh"
python3 -m py_compile "$PIPELINE/pipeline.py" "$PIPELINE/terminal_ui.py" "$PIPELINE/web/server.py" "$PIPELINE/image_workflow.py"
node --check "$PIPELINE/picture2_bridge.mjs"
python3 "$PIPELINE/tests/test_pipeline.py"
python3 "$PIPELINE/tests/test_images.py"
echo '✅ 网页、终端、四赛道与完整提示词回归检查通过。'
