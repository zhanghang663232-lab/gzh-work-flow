# 终端工作流

终端入口是普通 Python 脚本，不是 Agent、Skill 或插件。它只负责收集输入、创建任务和显示结果；写作阶段才调用 Codex CLI。

```bash
cd /绝对路径/gzh-work-flow
bash wechat.sh terminal
```

手机通过 SSH 输入同一条命令即可。手机不是文章保存位置：任务、文章、标题、报告和图片都保留在运行该命令的 Mac 的 `output/jobs/任务编号/` 内。

常用查看命令：

```bash
bash wechat.sh list
bash wechat.sh status 任务编号
bash wechat.sh show 任务编号
bash wechat.sh show 任务编号 --body
bash wechat.sh select 任务编号 3
bash wechat.sh retry 任务编号
```

网页和终端共用同一套任务目录。手机提交后，在 Mac 运行 `bash wechat.sh web`，即可查看同一任务、复制正文、换标题和选择图片。
