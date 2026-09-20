#!/usr/bin/env python3
"""手机友好的纯脚本入口。它只收集输入和调用 pipeline，自身不调用 LLM。"""
from __future__ import annotations

import sys

import pipeline


def required(label):
    while True:
        value = input(label).strip()
        if value:
            return value
        print('这一项必须填写。')


def choose_mode():
    print('\n选择赛道：1 通用  2 数码  3 汽车  4 体制内  5 监狱')
    mapping = {'1': '通用', '2': '数码', '3': '汽车', '4': '体制内', '5': '监狱',
               '通用': '通用', '数码': '数码', '汽车': '汽车', '体制内': '体制内', '监狱': '监狱'}
    while True:
        value = input('赛道：').strip()
        if value in mapping:
            return mapping[value]
        print('请输入1、2、3、4，或直接输入赛道名称。')


def multiline_source():
    print('\n粘贴完整参考原文。粘贴完后，换到新的一行输入 END（大小写均可），再按回车：')
    lines = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        # 手机键盘经常自动输入小写；end、End、END 都表示粘贴结束。
        if line.strip().upper() == 'END':
            break
        lines.append(line)
    source = '\n'.join(lines).strip()
    if not source:
        raise ValueError('参考原文不能为空。')
    return source


def new_job():
    defaults = pipeline.settings()
    print('公众号写作·手机终端')
    print(f'默认模型：{defaults["model"] or "Codex默认"}；推理强度：{defaults["reasoning_effort"] or "Codex默认"}；标题：40个')
    mode = choose_mode()
    topic = required('\n新主题：')
    brief = input('你的写法和态度（可留空）：').strip()
    positioning = input('账号定位/目标读者（可留空）：').strip()
    source = multiline_source()
    state = pipeline.submit_job({'topic': topic, 'source': source, 'mode': mode,
                                 'brief': brief, 'positioning': positioning,
                                 'model': '', 'reasoning_effort': '', 'title_count': 40})
    print('\n✅ 已提交，可以断开手机连接。')
    print('任务编号：' + state['id'])
    print('电脑目录：' + str(pipeline.job_dir(state['id'])))
    print('以后查看：wx status ' + state['id'])
    print('查看结果：wx show ' + state['id'])


def resolve_job(args):
    if args:
        return args.pop(0)
    jobs = pipeline.list_jobs()
    if not jobs:
        raise ValueError('还没有任务。')
    return jobs[0]['id']


def list_jobs():
    jobs = pipeline.list_jobs()
    if not jobs:
        print('还没有任务。')
        return
    for job in jobs[:20]:
        print(f'{job["id"]}  {job["status_label"]}  {job["mode"]}  {job["topic"]}')


def status(args):
    state = pipeline.state_of(resolve_job(args))
    print(f'任务：{state["id"]}')
    print(f'状态：{state["status_label"]}')
    if state['stage_label']:
        print(f'当前：{state["stage_label"]}')
    if state.get('error'):
        print('问题：' + state['error'])
    print('电脑目录：' + state['directory'])
    if state.get('elapsed_seconds') is not None:
        print('本次尝试已用时：' + str(state['elapsed_seconds']) + '秒')
    import image_workflow
    pictures = image_workflow.state_of(pipeline.job_dir(state['id']))
    print('配图：' + pictures['status_label'])
    if pictures.get('error'):
        print('配图问题：' + pictures['error'])
    if state.get('last_event_at'):
        print('最后事件时间：' + state['last_event_at'])
    if state.get('progress_note'):
        print(state['progress_note'])


def show(args):
    result = pipeline.job_result(resolve_job(args))
    if result['status'] != 'completed':
        print('任务还没完成：' + result['status_label'])
        return
    print('\n【备选标题】')
    for number, title in enumerate(result['titles'], 1):
        marker = ' ← 当前' if title == result['final_title'] else ''
        print(f'{number}. {title}{marker}')
    print('\n【终稿】\n')
    print(result['final_content'])
    print('\n【电脑终稿】\n' + result['final_path'])
    print('配图请在 Mac 网页的同一任务中选择。')


def select(args):
    job_id = resolve_job(args)
    result = pipeline.job_result(job_id)
    if result['status'] != 'completed':
        raise ValueError('任务还没完成。')
    if args:
        raw = args.pop(0)
    else:
        for number, title in enumerate(result['titles'], 1):
            print(f'{number}. {title}')
        raw = required('选择第几个标题：')
    if not raw.isdigit():
        raise ValueError('标题编号必须是数字。')
    result = pipeline.select_title(job_id, int(raw) - 1)
    print('✅ 已选择：' + result['final_title'])
    print('电脑终稿：' + result['final_path'])


def usage():
    print('wx                 新建一篇文章')
    print('wx list            查看最近任务')
    print('wx status [编号] 查看进度（不写编号默认最新）')
    print('wx show [编号]   查看标题和终稿')
    print('wx select [编号] [序号]  选择标题')
    print('wx retry [编号]  从失败处继续')


def main():
    args = sys.argv[1:]
    command = args.pop(0) if args else 'new'
    if command in ('new', '新建'):
        new_job()
    elif command == 'list':
        list_jobs()
    elif command == 'status':
        status(args)
    elif command == 'show':
        show(args)
    elif command == 'select':
        select(args)
    elif command == 'retry':
        state = pipeline.start_job(resolve_job(args))
        print('✅ 已继续：' + state['id'])
    elif command in ('help', '-h', '--help'):
        usage()
    else:
        raise ValueError('不认识这个操作：' + command)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\n已取消。')
    except (EOFError, OSError, ValueError) as error:
        print('错误：' + str(error), file=sys.stderr)
        sys.exit(1)
