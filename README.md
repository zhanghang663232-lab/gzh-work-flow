# gzh-work-flow

一个可在 Mac 网页、终端或手机 SSH 入口使用的微信公众号写作工作流。

一次提交会依次完成：仿写与素材核对、40 个备选标题、人味终稿与自检、合规配图候选搜索。文章先完成，配图独立进行；配图失败不会影响正文、标题和复制。

## 它不是什么

它是一个普通的 Python 项目和一组命令，不是 Codex 插件，也不依赖 `gzh` Agent。每次模型调用都会显式禁用 `gzh`。它不会自动发布到公众号，也不会自动下载图片；只有你在网页中勾选图片并点击下载后，图片才会保存到任务目录。

## 安装

```bash
git clone https://github.com/你的 GitHub 用户名/gzh-work-flow.git
cd gzh-work-flow
python3 -m pip install -r requirements.txt
npm install
npx playwright install chromium
bash wechat.sh doctor
```

写作需要已安装并登录的 Codex CLI。配图还需要 Node.js、已安装的 `picture2` 技能和 `getwebfetch-mcp`。如果暂时没有这些配图依赖，文章仍可正常生成。

`npm install` 和 `npx playwright install chromium` 只用于网页自动化测试；正常使用网页工作台不需要它们。

三份完整核心提示词已经保存在 `prompts/core/`。如团队要临时换用另一套提示词，启动前设置：

```bash
export WECHAT_CORE_PROMPTS_DIR="/绝对路径/你的提示词目录"
```

该目录中必须含有同名的三份文件：仿写提示词、标题生成器、人味写作增强提示词。

## 使用

打开网页工作台：

```bash
bash wechat.sh web
```

浏览器打开终端显示的地址，填写主题、原文、赛道、立场和目标读者后提交。支持：通用、数码、汽车、体制内、监狱；每个赛道各有独立人设和参考标题。

在终端填写表单：

```bash
bash wechat.sh terminal
```

也可从 JSON 文件提交：

```bash
bash wechat.sh submit --input /绝对路径/任务.json
bash wechat.sh list
bash wechat.sh status 任务编号
bash wechat.sh show 任务编号 --body
bash wechat.sh select 任务编号 3
bash wechat.sh retry 任务编号
```

手机只是远程提交入口：用 SSH 在 Mac 上运行上述命令，任务实际仍在 Mac 完成；之后可在 Mac 网页按任务编号查看同一篇文章、选标题、选图片。

## 结果存放

默认每个任务都在本项目下保存，互不混淆：

```text
output/jobs/任务编号/
├── 1_仿写稿.md
├── 2_备选标题.md
├── 3_人味终稿.md
├── 4_配图报告.md
├── images/                 # 仅保存你手动选择下载的图片
├── images.json             # 候选、来源、授权和下载状态
└── attempts/               # 每一步的输入、输出和错误记录
```

如需改到另一块磁盘或共享目录，设置 `WECHAT_OUTPUT_DIR`。输出目录、历史文章、图片、日志和备份都已写入 `.gitignore`，不会被上传到 GitHub。

## 可配置项

默认模型是 `gpt-5.6-sol`、推理强度 `high`，可在网页/任务中覆盖，默认值位于 `config.sh`。

配图路径因每台电脑不同，可选地设置：

```bash
export WECHAT_PICTURE2_SKILL="/绝对路径/picture2/SKILL.md"
export PICTURE2_COMMAND="/绝对路径/getwebfetch-mcp"
export WECHAT_NODE="/绝对路径/node"
export WECHAT_WEB_PORT=8765
```

运行 `bash wechat.sh doctor` 可检查本机依赖。

## 开发与验证

```bash
bash wechat.sh test
node tests/test_web.cjs
python3 -m unittest tests.test_images
```

项目代码、核心提示词、赛道人设和参考标题会进入版本控制；个人文章、任务、图片、日志、私人对标原文和本机配置不会进入仓库。
