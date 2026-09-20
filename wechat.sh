#!/usr/bin/env bash
# 网页与终端统一入口；写作由脱离终端的 Python worker 完成。
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
command -v python3 >/dev/null || { echo '缺少 python3（需要3.11及以上）。'; exit 1; }
case "${1:-}" in
    web) exec python3 "$SCRIPT_DIR/web/server.py" ;;
    terminal|chat) exec python3 "$SCRIPT_DIR/terminal_ui.py" "${@:2}" ;;
    submit|run|list|status|show|select|retry|options|prompts|configure)
        exec python3 "$SCRIPT_DIR/pipeline.py" "$@" ;;
    test) exec bash "$SCRIPT_DIR/tests/test_shell.sh" ;;
    doctor) exec python3 "$SCRIPT_DIR/pipeline.py" doctor "${@:2}" ;;
    clean)
        echo '本次升级保留全部历史稿、旧提示词和备份；自动清理已停用，避免误删可续跑任务。' ;;
    ''|-h|--help)
        echo '网页：bash wechat.sh web'
        echo '手机一键入口：wx（纯脚本，不启动Codex操作员）'
        echo '手机完整入口：bash wechat.sh terminal'
        echo '后台提交：bash wechat.sh submit --input /绝对路径/任务.json'
        echo '查看任务：bash wechat.sh list / status 编号 / show 编号'
        echo '选择标题：bash wechat.sh select 编号 标题序号（从1开始）'
        echo '失败续跑：bash wechat.sh retry 编号'
        echo '环境检查：bash wechat.sh doctor'
        echo '配置API密钥：bash wechat.sh configure'
        echo '旧命令：bash wechat.sh "主题" "原文或文件" "赛道" [模型] [推理强度] [立场]'
        ;;
    *) exec python3 "$SCRIPT_DIR/pipeline.py" legacy "$@" ;;
esac
