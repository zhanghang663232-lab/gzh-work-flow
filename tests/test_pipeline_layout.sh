#!/usr/bin/env bash
set -euo pipefail
PIPELINE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python3 "$PIPELINE/tests/test_pipeline.py"
