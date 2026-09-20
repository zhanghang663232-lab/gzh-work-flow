# 公众号三步写作：维护约定

- 正文保持三个 AI 动作：仿写、标题、人味终稿；当前版本不包含配图、发布或外部同步。
- 所有赛道共用用户指定的三份完整核心原文件（见 pipeline.py 的 CORE_FILES），禁止用旧精简版替换。不得使用 gzh。
- 网页与终端共用 pipeline.py；输入齐全后后台一次跑完，完整报告留存。标题默认40个。
- 只有用户要求生产文章、查任务或选标题时按 TERMINAL_WORKFLOW.md 操作；维护代码不触发写作。
- 文章类型必须由用户手动选择，不能通过主题或原文自动猜测。
- 不自动打开 VS Code、Finder 或浏览器。
- 第 2 步标题必须按手动赛道读取 `reference/titles/<赛道>.txt`。
- 第 3 步必须按手动赛道读取 `prompts/personas/<赛道>.md`。
- 每次任务都创建新目录，保存原文、仿写稿、备选标题、人味终稿和运行日志；不得覆盖历史稿。
- 所有文章去除空白后最多 1,550 个字符。
- 不在项目中写入 API Key、登录令牌或个人信息。
- 修改脚本后运行：`bash -n wechat_pipeline/wechat.sh` 与 `bash wechat_pipeline/tests/test_shell.sh`。

- 已删除 Obsidian 文章同步功能；所有新产物只保存在 Mac 任务目录，历史副本保留。
