#!/usr/bin/env bash
set -euo pipefail
PIPELINE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
bash -n "$PIPELINE/wechat.sh"
python3 -m py_compile "$PIPELINE/pipeline.py" "$PIPELINE/terminal_ui.py" "$PIPELINE/web/server.py" "$PIPELINE/workflow/config.py" "$PIPELINE/workflow/prompts.py" "$PIPELINE/workflow/providers.py"
python3 "$PIPELINE/tests/test_pipeline.py"
python3 -m unittest -v tests.test_providers
echo '✅ 网页、终端、五赛道、模型适配与完整提示词回归检查通过。'
