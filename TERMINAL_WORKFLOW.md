# 终端工作流

终端入口是普通 Python 程序，不是 Agent、Skill 或插件。它负责收集输入、建立后台任务、查看进度和选择标题；真正需要语言理解的三个步骤才交给所选模型。

```text
wechat.sh
  └─ terminal_ui.py（收集表单）
       └─ pipeline.py（任务编排与断点续跑）
            ├─ workflow/prompts.py（完整提示词）
            ├─ workflow/providers.py（模型适配）
            └─ output/jobs/任务编号（结果和证据）
```

开始填写：

```bash
cd /绝对路径/gzh-work-flow
bash wechat.sh terminal
```

终端会先列出已经配置好的模型。可以只选一个默认模型，也可以分别指定仿写、标题和人味终稿模型。仿写模型不支持原生联网时，编排层自动使用已配置的搜索回退模型。

手机通过 SSH 输入相同命令即可。提交后可以断开手机，任务由电脑上的独立后台进程继续完成。手机不保存文章，也不会触发 Obsidian 或其他同步。

常用命令：

```bash
bash wechat.sh list
bash wechat.sh status 任务编号
bash wechat.sh show 任务编号
bash wechat.sh show 任务编号 --body
bash wechat.sh select 任务编号 3
bash wechat.sh retry 任务编号
```

网页和终端共用 `output/jobs/`。网页提交的任务可以在终端查看，手机提交的任务也可以在 Mac 网页中打开。
