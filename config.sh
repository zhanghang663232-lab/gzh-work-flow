#!/usr/bin/env bash
# Codex 工作流的非敏感配置。这里不要填写 API Key 或账号信息。

# 工作流默认生产模型。网页或任务里手动选择时，以手动选择为准。
CODEX_MODEL="gpt-5.6-sol"

# 工作流默认推理强度。网页里手动选择后，会优先使用网页选择。
# 可选：low、medium、high、xhigh、max、ultra。
CODEX_REASONING_EFFORT="high"

# 单个 Codex 步骤的最长等待时间（秒）和失败重试次数。
CODEX_TIMEOUT_SECONDS=900
MAX_RETRIES=2

# 仿写和人味终稿的最低有效字节数，防止错误信息被当成文章。
MIN_ARTICLE_BYTES=900
MIN_TITLES_BYTES=180

# 正文800–1500字符、标题加正文最多1550字符，由完整核心提示词与项目约定决定。
# 不再使用旧版1300字上限。长度校验在 pipeline.py 中统一执行。
