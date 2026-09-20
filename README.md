# gzh-work-flow

可复用的微信公众号三步写作工作流。Mac 网页、终端和手机 SSH 共用同一个任务引擎：

1. 仿写与联网素材核对
2. 生成 40 个备选标题
3. 人味终稿与自检

它是普通的 Python 项目，不是 Codex 插件，也不依赖 `gzh` Agent。当前版本不包含配图、自动发布或 Obsidian 同步。历史任务和历史图片不会被升级脚本删除，但新任务不再读取或生成图片状态。

## 核心设计

项目按职责分成五层：

- `pipeline.py`：任务编排、断点续跑、结果校验与历史兼容。
- `workflow/prompts.py`：装配三份完整核心提示词、赛道人设和参考标题。
- `workflow/providers.py`：Codex CLI、Claude、豆包火山方舟和 OpenAI 兼容接口。
- `workflow/config.py`：模型路由、配置检查和本机密钥管理。
- `web/`、`terminal_ui.py`、`wechat.sh`：网页、终端和手机入口。

五个赛道为：通用、数码、汽车、体制内、监狱。每个赛道都有独立人设和参考标题。主题、写法与立场是硬约束，不允许模型擅自改题或反转态度。

## 安装

支持 macOS、Linux 和 Windows WSL，需要 Python 3.11 或更高版本。

```bash
git clone https://github.com/你的用户名/gzh-work-flow.git
cd gzh-work-flow
bash wechat.sh doctor
```

生产运行只使用 Python 标准库。`npm install` 仅用于开发者运行浏览器自动化验收，正常写文章不需要 Node.js。

## 配置模型

默认使用已登录的 Codex CLI：`gpt-5.6-sol`，推理强度 `high`。可配置四类适配器：

- `codex_cli`：本机 Codex CLI。
- `anthropic`：Claude Anthropic API。
- `volcengine_ark`：豆包火山方舟 Responses API。
- `openai_compatible`：DeepSeek、OpenRouter、通义、Ollama、LM Studio 等兼容接口。

复制示例配置再修改非密钥参数：

```bash
cp config/providers.example.toml config/providers.local.toml
```

API Key 不写入上述配置。运行：

```bash
bash wechat.sh configure
```

密钥只保存到 Git 忽略的 `config/secrets.local.toml`，文件权限为 `0600`。网页、任务 JSON 和日志只显示“已配置/未配置”，不会返回密钥。

默认模型和三个阶段可以分别路由。仿写阶段强制要求可验证的原生联网搜索；如果选择 DeepSeek 等无原生搜索的配置，会自动改用 `search_fallback_profile`。没有可用的联网模型时任务会明确拒绝启动，不会假装完成搜索。

检查配置：

```bash
bash wechat.sh doctor
bash wechat.sh doctor --live  # 对每个已配置服务发起一次最小真实调用
```

## 使用

网页入口：

```bash
bash wechat.sh web
```

终端入口：

```bash
bash wechat.sh terminal
```

JSON 和任务管理：

```bash
bash wechat.sh submit --input /绝对路径/任务.json
bash wechat.sh list
bash wechat.sh status 任务编号
bash wechat.sh show 任务编号 --body
bash wechat.sh select 任务编号 3
bash wechat.sh retry 任务编号
```

手机仅通过 SSH 向 Mac 提交命令。模型运行、任务文件和最终文章仍保存在运行服务的电脑上；手机断开不会停止后台任务。

## 文件位置

每个新任务保存在：

```text
output/jobs/任务编号/
├── 0_原文.md
├── 1_仿写稿.md
├── 2_备选标题.md
├── 3_人味终稿.md
├── 1_完整报告.md
├── 2_完整报告.md
├── 3_完整报告.md
├── snapshot/              # 本任务实际使用的完整提示词、人设、标题库及校验值
└── attempts/              # 各次提示词、结构化响应、事件、错误和耗时
```

可用 `WECHAT_OUTPUT_DIR` 改变输出根目录。输出、历史文章、日志、备份、本机配置和私人材料均已加入 `.gitignore`，不会上传 GitHub。

三份完整核心提示词在 `prompts/core/`。如需切换为另一套同名文件，可设置 `WECHAT_CORE_PROMPTS_DIR`。每个任务创建时会保存快照，因此提示词日后变动不会改变旧任务的审计记录。

## 测试与安全

```bash
bash wechat.sh test
node tests/test_web.cjs       # 需要 npm install 和可用的 Chrome
```

离线测试使用假 API，覆盖结构化输出、联网证据、超时、错误密钥、429、5xx、阶段路由和密钥泄漏。提交前还应运行 Git 文件扫描，确认仓库中没有文章、日志、任务、私人绝对路径或 API Key。

适配新的服务时，在 `config/providers.local.toml` 增加配置即可；若协议不同，再在 `workflow/providers.py` 实现新的适配分支。编排、提示词和前端不需要跟着重写。
