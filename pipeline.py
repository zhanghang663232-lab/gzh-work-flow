#!/usr/bin/env python3
"""网页、传统命令与手机 Codex 共用的三步写作引擎（仅 Python 标准库）。"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import unicodedata
import uuid

ROOT = Path(__file__).resolve().parent
# 默认使用仓库自带的三份核心提示词；需要团队共享一套提示词时，可用
# WECHAT_CORE_PROMPTS_DIR 指向另一个目录，不再依赖任何人的本机用户名或目录结构。
CORE = Path(os.environ.get('WECHAT_CORE_PROMPTS_DIR', str(ROOT / 'prompts' / 'core'))).expanduser().resolve()
CORE_FILES = {'rewrite': CORE / '仿写提示词_优化版v2.0.md',
              'titles': CORE / '公众号爆款标题生成器_v3.md',
              'human': CORE / '人味写作增强提示词_v2.1.md'}
MODES = ('通用', '数码', '汽车', '体制内', '监狱')
MODEL_EFFORTS = {
    'gpt-5.6-sol': ['low', 'medium', 'high', 'xhigh', 'max', 'ultra'],
    'gpt-5.6-terra': ['low', 'medium', 'high', 'xhigh', 'max', 'ultra'],
    'gpt-5.6-luna': ['low', 'medium', 'high', 'xhigh', 'max'],
    'gpt-5.5': ['low', 'medium', 'high', 'xhigh'],
    'gpt-5.4': ['low', 'medium', 'high', 'xhigh'],
    'gpt-5.4-mini': ['low', 'medium', 'high', 'xhigh'],
}
STAGES = ('rewrite', 'titles', 'human')
LABELS = {'rewrite': '仿写与联网素材', 'titles': '标题 M1–M7', 'human': '人味终稿与自检'}
OUTPUTS = {'rewrite': '1_仿写稿.md', 'titles': '2_备选标题.md', 'human': '3_人味终稿.md'}
STATUS_LABELS = {'queued': '已提交', 'running': '执行中', 'completed': '已完成',
                 'failed': '失败，可重试', 'interrupted': '进程中断，可续跑'}


def output_root():
    return Path(os.environ.get('WECHAT_OUTPUT_DIR', str(ROOT / 'output'))).resolve()


def now():
    return dt.datetime.now().astimezone().isoformat(timespec='microseconds')


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def atomic_text(path, text):
    """写完后一次替换，避免网页读到半份 JSON。"""
    path = Path(path)
    fd, name = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def save_json(path, value):
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2) + '\n')


@contextlib.contextmanager
def lock(path, blocking=True):
    with Path(path).open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        try:
            yield handle
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def settings():
    # config.sh 仅解析本项目的简单赋值，不执行任意 shell 语句。
    values = {}
    for line in (ROOT / 'config.sh').read_text().splitlines():
        match = re.fullmatch(r'([A-Z_]+)=(.*)', line.strip())
        if match:
            values[match[1]] = match[2].strip().strip('"\'')
    codex_config = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'config.toml'
    config = tomllib.loads(codex_config.read_text()) if codex_config.exists() else {}
    # 只保留两个非敏感默认项，绝不把账号配置或令牌写进任务。
    return {'model': values.get('CODEX_MODEL') or config.get('model', ''),
            'reasoning_effort': values.get('CODEX_REASONING_EFFORT') or config.get('model_reasoning_effort', ''),
            'timeout': int(values.get('CODEX_TIMEOUT_SECONDS', 900)),
            'attempts': int(values.get('MAX_RETRIES', 2))}


def writing_only_overrides():
    """仅对这一条写作调用禁用无关插件/MCP，不修改用户全局配置。"""
    path = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'config.toml'
    config = tomllib.loads(path.read_text()) if path.exists() else {}
    overrides = ['features.hooks=false', 'features.plugins=false', 'features.unbounded_connection_retries=false']
    for category in ('plugins', 'mcp_servers'):
        for name in config.get(category, {}):
            # Codex 的 -c 左侧按点分段，不解析 TOML 引号；带引号会创建错误的新工具条目。
            if '.' not in name:
                overrides.append(category + '.' + name + '.enabled=false')
    return overrides


def writing_environment():
    """让终端与网页worker沿用Mac已配置的代理；不更改系统设置，也不覆盖用户环境变量。"""
    env = os.environ.copy()
    if sys.platform != 'darwin':
        return env
    try:
        proxy = subprocess.check_output(['/usr/sbin/scutil', '--proxy'], text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return env
    values = dict(re.findall(r'^\s*(HTTP\w+)\s*:\s*(\S+)', proxy, re.MULTILINE))
    for prefix in ('HTTP', 'HTTPS'):
        host, port = values.get(prefix + 'Proxy'), values.get(prefix + 'Port')
        if values.get(prefix + 'Enable') == '1' and host and port and port.isdigit():
            if re.fullmatch(r'[A-Za-z0-9.:-]+', host):
                if ':' in host:
                    host = '[' + host + ']'
                env.setdefault(prefix + '_PROXY', 'http://' + host + ':' + port)
    return env


def resources(mode):
    if mode not in MODES:
        raise ValueError('必须手动选择赛道：通用、数码、汽车、体制内、监狱。')
    return {**CORE_FILES, 'references': ROOT / 'reference' / 'titles' / (mode + '.txt'),
            'persona': ROOT / 'prompts' / 'personas' / (mode + '.md'),
            'boundary': ROOT / 'prompts' / '00_执行边界.md',
            'adapter': ROOT / 'prompts' / '自动执行适配.md'}


def options():
    return {'modes': MODES, 'models': MODEL_EFFORTS, 'defaults': settings(),
            'core_prompts': {k: str(v) for k, v in CORE_FILES.items()},
            'resources': {mode: {k: str(v) for k, v in resources(mode).items()} for mode in MODES},
            'title_count': 40}


def validate_input(payload):
    if not isinstance(payload, dict):
        raise ValueError('输入必须是一份 JSON 对象。')
    result = {}
    for key in ('topic', 'source', 'mode', 'brief', 'positioning', 'model', 'reasoning_effort'):
        value = payload.get(key, '')
        if not isinstance(value, str):
            raise ValueError(key + ' 必须是文本。')
        result[key] = value.strip()
    if not result['topic'] or not result['source']:
        raise ValueError('请提供主题和完整仿写原文。')
    if '\ufffd' in result['source'] or re.search(r'[?？]{4,}', result['source']):
        raise ValueError('参考原文中发现乱码或连续问号。请检查原文后重新粘贴，任务尚未提交，也不会消耗模型额度。')
    resources(result['mode'])
    if result['model'] and result['model'] not in MODEL_EFFORTS:
        raise ValueError('模型不在可选列表中。')
    defaults = settings()
    effective_model = result['model'] or defaults['model']
    effort = result['reasoning_effort'] or defaults['reasoning_effort']
    allowed = MODEL_EFFORTS.get(effective_model, ['low', 'medium', 'high', 'xhigh', 'max', 'ultra'])
    if effort and effort not in allowed:
        raise ValueError(f'{effective_model} 不支持推理强度 {effort}；请明确选择兼容强度。')
    raw_count = payload.get('title_count', 40)
    if isinstance(raw_count, bool) or not re.fullmatch(r'\d+', str(raw_count)):
        raise ValueError('标题数量必须是 10–60 的整数。')
    count = min(int(raw_count), 60)
    if count < 10:
        raise ValueError('标题数量至少为 10。')
    result.update(title_count=count, effective_model=effective_model, effective_effort=effort,
                  timeout=max(30, defaults['timeout']), attempts=max(1, defaults['attempts']))
    return result


def job_dir(job_id):
    if not re.fullmatch(r'\d{8}-\d{6}-[a-f0-9]{12}', job_id):
        raise ValueError('任务编号格式不正确。')
    path = output_root() / 'jobs' / job_id
    if not (path / 'job.json').is_file():
        raise FileNotFoundError('找不到任务：' + job_id)
    return path


def create_job(payload):
    inputs = validate_input(payload)
    blobs = {key: path.read_bytes() for key, path in resources(inputs['mode']).items()}
    for key, content in blobs.items():
        if not content.strip():
            raise ValueError(f'提示词或资料为空：{resources(inputs["mode"])[key]}')
    references = [s for s in blobs['references'].decode().splitlines() if s.strip()]
    if len(references) < 5:
        raise ValueError('该赛道参考标题少于 5 条；请先补充标题库。')
    job_id = dt.datetime.now().strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:12]
    path = output_root() / 'jobs' / job_id
    (path / 'snapshot').mkdir(parents=True, mode=0o700)
    (path / 'attempts').mkdir()
    (path / 'workspace').mkdir()
    provenance = {}
    for key, blob in blobs.items():
        filename = key + '.md'
        (path / 'snapshot' / filename).write_bytes(blob)
        provenance[key] = {'original': str(resources(inputs['mode'])[key]),
                           'snapshot': str(path / 'snapshot' / filename),
                           'sha256': hashlib.sha256(blob).hexdigest()}
    save_json(path / 'input.json', inputs)
    save_json(path / 'snapshot' / 'sources.json', provenance)
    atomic_text(path / '0_原文.md', inputs['source'] + '\n')
    atomic_text(path / '_运行日志.log', f'{now()} 创建任务 {job_id}\n')
    save_json(path / 'job.json', {'id': job_id, 'topic': inputs['topic'], 'mode': inputs['mode'],
                               'status': 'queued', 'stage': '', 'completed_stages': [],
                               'created_at': now(), 'updated_at': now(), 'error': '',
                               'selected_index': None, 'worker_pid': None,
                               'warning': '参考标题不足 10 条，M1 不做统计，仅作主导动机判断。' if len(references) < 10 else ''})
    return job_id


def update_job(path, **changes):
    with lock(path / '.state.lock'):
        state = read_json(path / 'job.json')
        state.update(changes, updated_at=now())
        save_json(path / 'job.json', state)
    return state


def busy(path):
    try:
        with lock(path / '.run.lock', blocking=False):
            return False
    except BlockingIOError:
        return True


def state_of(job_id):
    path = job_dir(job_id)
    state = read_json(path / 'job.json')
    age = (dt.datetime.now().astimezone() - dt.datetime.fromisoformat(state['updated_at'])).total_seconds()
    if state['status'] in ('queued', 'running') and age > 15 and not busy(path):
        # 返回可恢复状态，不在 GET 中写盘，避免覆盖正在启动的 worker。
        state.update(status='interrupted', error='后台进程已退出。已完成步骤保留，可从未完成步骤续跑。')
    started = state.get('attempt_started_at')
    if started and state['status'] == 'running':
        state['elapsed_seconds'] = max(0, int(time.time() - dt.datetime.fromisoformat(started).timestamp()))
        last_event = state.get('last_event_at')
        if last_event and time.time() - dt.datetime.fromisoformat(last_event).timestamp() > 60:
            state['progress_note'] = '等待模型返回新内容；尚无新事件不等于已失败，超时会自动记录。'
    state['status_label'] = STATUS_LABELS[state['status']]
    state['stage_label'] = LABELS.get(state['stage'], '')
    state['directory'] = str(path)
    return state


def start_job(job_id):
    path = job_dir(job_id)
    with lock(path / '.start.lock'):
        state = state_of(job_id)
        # 写作 worker 会先把正文状态写成 completed，再收尾启动独立配图并释放 run.lock。
        # 用户恰好在这个很短的窗口点“重试”时，等收尾结束后再判断，避免把一次有效重试吞掉。
        if state['status'] == 'completed' and busy(path):
            deadline = time.monotonic() + 3
            while busy(path) and time.monotonic() < deadline:
                time.sleep(.05)
            state = state_of(job_id)
        if busy(path):
            return state
        if state['status'] == 'completed':
            try:
                for stage in STAGES:
                    validate_result(stage, read_json(path / (stage + '.json')), read_json(path / 'input.json'),
                                    validation_evidence(path, stage))
            except (OSError, ValueError) as error:
                update_job(path, status='failed', error='已保存产物未通过复检：' + str(error))
            else:
                return state
        # 双击/两个页面同时重试只启动一次。
        if state['status'] == 'queued' and state.get('worker_pid'):
            return state
        with (path / '_后台日志.log').open('a') as log:
            # 忽略SSH断线的SIGHUP，在Python初始化前就生效。nohup不负责后台化；setsid由Popen完成。
            child = subprocess.Popen(['nohup', sys.executable, str(ROOT / 'pipeline.py'), '_worker', job_id],
                                     cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                     start_new_session=True, close_fds=True)
        update_job(path, status='queued', error='', worker_pid=child.pid)
        # 长期开着的网页服务回收已退出的子进程，避免僵尸进程。
        threading.Thread(target=child.wait, daemon=True).start()
    return state_of(job_id)


def submit_job(payload):
    job_id = create_job(payload)
    try:
        return start_job(job_id)
    except Exception as error:
        update_job(job_dir(job_id), status='failed', error='启动失败：' + str(error))
        raise


def schema_for(stage, count):
    properties = {'report': {'type': 'string'}}
    if stage == 'titles':
        # 字数由返回后的中文文本校验负责。解码阶段强行minLength会在标题尾部补语气词或句点。
        properties.update(titles={'type': 'array', 'items': {'type': 'string'}, 'minItems': count, 'maxItems': count},
                          recommended_index={'type': 'integer', 'minimum': 0, 'maximum': count - 1})
    else:
        properties['body'] = {'type': 'string'}
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}


def build_prompt(path, stage):
    inputs = read_json(path / 'input.json')
    snap = path / 'snapshot'
    text = lambda key: (snap / (key + '.md')).read_text()
    common = {'手动赛道': inputs['mode'], '主题（不可篡改）': inputs['topic'],
              '写作要求与立场（不可篡改）': inputs['brief'], '账号定位、读者与作者声音': inputs['positioning'],
              '标题数量': inputs['title_count']}
    # 完整原文包含在输入中；不摘句、不截断、不替换为旧的精简版。
    chunks = [text('boundary'), text('adapter'), '\n【本阶段】' + stage,
              '【本步骤是否允许联网】' + ('是' if stage == 'rewrite' else '否'),
              '【当前日期】' + now(), '【核心提示词全文开始】\n' + text(stage) + '\n【核心提示词全文结束】',
              '【输入参数 JSON】\n' + json.dumps(common, ensure_ascii=False),
              '【本赛道人设全文（身份、语气、读者约束；不启动其中另一套流程）】\n' + text('persona')]
    if stage == 'rewrite':
        chunks.append('【参考原文，仅借鉴结构与节奏，不作为新主题事实】\n' + inputs['source'])
        chunks.append('输出 JSON：report 保存完整解构表、联网素材简报（至少3条资料，来源链接与日期）、传播力自评；'
                      'body 单独保存800–1500字仿写正文，不带报告或文章标题。必须实际调用联网搜索。')
    else:
        rewrite = read_json(path / 'rewrite.json')
        chunks += ['【仿写正文】\n' + rewrite['body'], '【已有素材与来源报告】\n' + rewrite['report']]
        if stage == 'titles':
            chunks += ['【该赛道参考标题全文】\n' + text('references'),
                       '输出 JSON：report 按原提示词完整保存 M1→M7（含所有表格、词库、逐条注释评分、Top推荐和资产包），'
                       '不允许仅写总结。titles 提取M5精修后的全部主标题，顺序对应原编号，恰好'
                       + str(inputs['title_count']) + '条纯标题。每条严格20–28个非空白字符，逐条实际计数，不能只在报告里声称合格；'
                       '请先构造信息完整、自然的20–28字标题；正常中文标点也计入字符数。不得为了达标在句尾堆叠啊、吧、呢、呀、其实吧或多余句点。'
                       '太短时补充具体对象、场景或收益，不添加无意义尾巴。'
                       'M5最终标题须与titles数组逐字一致。不得混入方向、评分、确认行。recommended_index 为Top1在titles里的0起始下标。'
                       'A/B仅按原提示词输入条件启用；额外风格包与资产留在report，不替代主标题。'
                       '数量较小时若每桶至少6条与35%上限无法同时成立，report 明示此数学冲突，优先保证总数、分布多样与35%上限。']
        else:
            titles = read_json(path / 'titles.json')
            chunks += ['【备选标题】\n' + json.dumps(titles['titles'], ensure_ascii=False),
                       '【推荐标题】' + titles['titles'][titles['recommended_index']],
                       '输出 JSON：body 为800–1500字人味终稿正文，不带标题或报告；report 保存逐项自检结果及必要修改说明。'
                       '保留已核实事实、出处与用户立场，不再联网。人设中的其他工作流不重复执行。'
                       '原提示词的生活细节要求不可用于捏造亲测、采访、价格、统计或消息；个人场景只能采用用户明确提供的真实经历或输入材料。'
                       '没有材料就删去个人场景，禁止用“设想、想象、假设”等标签包装虚构经历，也不得用随意具体数字替换模糊事实。'
                       '人味提示词全程第一人称要求用于本次终稿，原文结构借鉴不得反转用户立场。']
    return '\n\n'.join(chunks)


def codex_command(inputs, stage, raw_path, schema_path, workspace):
    command = ['codex', '--ask-for-approval', 'never']
    if inputs['effective_model']:
        command += ['-m', inputs['effective_model']]
    if inputs['effective_effort']:
        command += ['-c', 'model_reasoning_effort=' + json.dumps(inputs['effective_effort'])]
    gzh_skill = Path(os.environ.get('WECHAT_GZH_SKILL', str(Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'skills' / 'gzh'))).expanduser()
    command += ['-c', 'web_search=' + json.dumps('live' if stage == 'rewrite' else 'disabled'),
                '-c', 'project_doc_max_bytes=0',
                '-c', 'skills.config=[{path=' + json.dumps(str(gzh_skill)) + ',enabled=false}]']
    for override in writing_only_overrides():
        command += ['-c', override]
    command += ['exec', '--skip-git-repo-check', '-C', str(workspace), '--sandbox', 'read-only',
                '--output-schema', str(schema_path), '--output-last-message', str(raw_path), '--json', '-']
    return command


def validation_evidence(path, stage):
    """人味步骤只能重写已有材料，不得凭空增加可核验数字或场景。"""
    if stage != 'human':
        return ''
    chunks = []
    rewrite = Path(path) / 'rewrite.json'
    if rewrite.exists():
        saved = read_json(rewrite)
        chunks.extend([saved.get('body', ''), saved.get('report', '')])
    return '\n'.join(chunks)


def validate_result(stage, result, inputs, evidence=''):
    if not isinstance(result, dict) or not isinstance(result.get('report'), str) or not result['report'].strip():
        raise ValueError('缺少完整报告。')
    for field, value in result.items():
        values = value if isinstance(value, list) else [value]
        for item in values:
            if not isinstance(item, str):
                continue
            bad = [f'U+{ord(char):04X}' for char in item
                   if ord(char) in {0xFEFF, 0x200B, 0x200C, 0x200D, 0x2060, 0xFFFD}
                   or (unicodedata.category(char) in {'Cc', 'Cs', 'Co'} and char not in '\n\r\t')]
            if bad:
                raise ValueError(f'{field} 含乱码或隐藏异常字符：{sorted(set(bad))}。')
    if stage == 'titles':
        titles = result.get('titles')
        if not isinstance(titles, list) or len(titles) != inputs['title_count']:
            raise ValueError(f'必须提供 {inputs["title_count"]} 个主标题。')
        if not all(isinstance(t, str) and t.strip() and '\n' not in t and len(t) < 120 for t in titles):
            raise ValueError('备选标题必须是一行一个纯标题。')
        if len({t.strip() for t in titles}) != len(titles):
            duplicates = {t.strip(): [i+1 for i, candidate in enumerate(titles) if candidate.strip() == t.strip()]
                          for t in titles if sum(candidate.strip() == t.strip() for candidate in titles) > 1}
            raise ValueError(f'备选标题有完全重复项，标题与序号：{duplicates}。只保留其中一条，其余改写并同步报告。')
        wrong_length = [(i+1, len(re.sub(r'\s', '', title))) for i, title in enumerate(titles)
                        if not 20 <= len(re.sub(r'\s', '', title)) <= 28]
        if wrong_length:
            raise ValueError(f'标题核心默认20–28字，以下标题序号及长度不符：{wrong_length}。修正titles并同步报告中的最终版。')
        padding = [(i+1,t) for i,t in enumerate(titles) if re.search(r'(?:[啊呀呢吧]{2,}|好吗[呢吧])$',t)]
        if padding:
            raise ValueError(f'标题尾部有机械凑字数语气词：{padding}。用具体对象、场景或收益自然扩写，勿堆语气词。')
        missing = [i+1 for i,t in enumerate(titles) if t not in result['report']]
        if missing:
            raise ValueError(f'标题序号{missing}在完整报告中找不到对应原句；titles必须从M5最终版逐字提取，不能另加字符凑数。')
        banned = '绝了|炸裂|yyds|神操作|顶流|杀疯了|破防了|泪目|离谱|必买|必看|亲测|种草|安利|闭眼入|不看后悔|扎心|内卷|躺平|打工人|社畜|摆烂|显眼包|100%|一定|必须|最强|史上最|没有之一|颠覆'.split('|')
        hits = [(i+1, word) for i, title in enumerate(titles) for word in banned if word.lower() in title.lower()]
        if hits:
            raise ValueError(f'标题命中核心禁用词：{hits}。按核心要求替换。')
        if type(result.get('recommended_index')) is not int or not 0 <= result['recommended_index'] < len(titles):
            raise ValueError('推荐标题编号无效。')
        for number in range(1, 8):
            if not re.search(r'M\s*' + str(number), result['report']):
                raise ValueError(f'标题报告缺少 M{number}。')
    else:
        body = result.get('body')
        if not isinstance(body, str):
            raise ValueError('缺少正文。')
        size = len(re.sub(r'\s', '', body))
        if not 800 <= size <= 1500:
            raise ValueError(f'正文为 {size} 个非空白字符，应为800–1500。')
        if re.search(r'(?:虽然|尽管)[^。！？\n]{0,35}(?:尚无|没有|未有)[^。！？\n]{0,12}消息[^。！？\n]{0,12}(?:但|还是|仍然)|消息未确认[，,]?但', body):
            raise ValueError('出现用户禁止的“消息式否定”句式。')
        if stage == 'human':
            source_material = '\n'.join(str(inputs.get(key, '')) for key in
                                        ('topic', 'source', 'brief', 'positioning')) + '\n' + evidence
            if re.search(r'(?:我设想(?:过|了)?|我想象(?:过|了)?|假设(?:一下)?我)', body) and not re.search(
                    r'(?:我设想(?:过|了)?|我想象(?:过|了)?|假设(?:一下)?我)', source_material):
                raise ValueError('人味终稿新增了设想式个人场景；没有用户真实材料时应删除，不能用标签包装虚构经历。')
            introduced = sorted({token for token in re.findall(r'\d+(?:[.,]\d+)*(?:%|％)?', body)
                                 if token not in source_material})
            if introduced:
                raise ValueError(f'人味终稿新增了上一步材料中不存在的具体数字：{introduced}。请删除或改回原材料表述。')


def used_search(event_path):
    """启用搜索不等于实际搜索；核对Codex已完成的工具事件。"""
    for line in event_path.read_text(errors='replace').splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        item = event.get('item', {})
        if event.get('type') == 'item.completed' and item.get('type') == 'web_search':
            if item.get('query') or item.get('action', {}).get('type') == 'search':
                return True
    return False


def event_error(event_path):
    """从Codex事件流提取用户能看懂的最后一条真正错误。"""
    messages = []
    for line in event_path.read_text(errors='replace').splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get('type') in ('error', 'turn.failed'):
            value = event.get('message') or event.get('error', {}).get('message')
            if value:
                messages.append(value)
    if messages:
        return messages[-1]
    lines = event_path.read_text(errors='replace').splitlines()
    return next((line for line in reversed(lines) if line.startswith('Error:')), '')


def should_retry(error_message):
    """内容或短暂网络问题可重试；额度、登录、模型和配置错误立即停止。"""
    terminal = ('usage limit', 'purchase more credits', 'not logged', 'unauthorized',
                'invalid model', 'does not support', 'Error loading config', 'invalid transport')
    return not any(word.lower() in error_message.lower() for word in terminal)


def render_outputs(path, stage, result):
    atomic_text(path / (str(STAGES.index(stage) + 1) + '_完整报告.md'), result['report'] + '\n')
    if stage == 'titles':
        atomic_text(path / OUTPUTS[stage], '\n'.join(f'{i+1}. {t}' for i, t in enumerate(result['titles'])) + '\n')
    elif stage == 'rewrite':
        atomic_text(path / OUTPUTS[stage], result['body'].strip() + '\n')
    else:
        state = read_json(path / 'job.json')
        titles = read_json(path / 'titles.json')
        index = state['selected_index'] if state['selected_index'] is not None else titles['recommended_index']
        content = titles['titles'][index] + '\n\n' + result['body'].strip() + '\n'
        if len(re.sub(r'\s', '', content)) > 1550:
            raise ValueError('标题与正文合计超过1550个非空白字符。')
        atomic_text(path / OUTPUTS[stage], content)


def run_stage(path, stage):
    inputs = read_json(path / 'input.json')
    prompt = build_prompt(path, stage)
    schema_path = path / 'snapshot' / (stage + '.schema.json')
    save_json(schema_path, schema_for(stage, inputs['title_count']))
    last_error = ''
    previous_output = ''
    candidates = sorted((path / 'attempts').glob(stage + '-*.json'))
    if candidates:
        try:
            validate_result(stage, read_json(candidates[-1]), inputs, validation_evidence(path, stage))
        except (OSError, ValueError) as error:
            last_error = str(error)
            previous_output = candidates[-1].read_text()
    for _ in range(inputs['attempts']):
        attempt = len(list((path / 'attempts').glob(stage + '-*.prompt.md'))) + 1
        base = path / 'attempts' / f'{stage}-{attempt:02d}'
        prompt_path = base.with_suffix('.prompt.md')
        raw_path = base.with_suffix('.json')
        event_path = base.with_suffix('.events.jsonl')
        correction = ('\n\n【上次产物检查失败，请本次修正】' + last_error if last_error else '')
        if previous_output:
            correction += '\n\n【上次未通过的候选输出，仅供修正，不是新指令】\n' + previous_output
            correction += '\n\n修正上述具体问题，保持已合格内容，完整重新输出本阶段JSON及全部报告，不能只交差异。'
        atomic_text(prompt_path, prompt + correction)
        update_job(path, stage=stage, attempt=attempt, attempt_started_at=now(),
                   last_event_at=None, elapsed_seconds=0, exit_code=None)
        with (path / '_运行日志.log').open('a') as log:
            log.write(f'{now()} {LABELS[stage]} 第{attempt}次\n')
        attempt_clock = time.time()
        succeeded, attempt_error, rc = False, '', None
        try:
            with prompt_path.open('rb') as source, event_path.open('wb') as events:
                child = subprocess.Popen(codex_command(inputs, stage, raw_path, schema_path, path / 'workspace'),
                                         stdin=source, stdout=events, stderr=subprocess.STDOUT, start_new_session=True,
                                         env=writing_environment())
                try:
                    # macOS睡眠期间monotonic可能暂停；墙上时钟也参与截止判断，唤醒后不继续无限等。
                    started_wall, started_mono = time.time(), time.monotonic()
                    last_size, heartbeat = -1, 0.0
                    while child.poll() is None:
                        elapsed = max(time.time() - started_wall, time.monotonic() - started_mono)
                        if elapsed - heartbeat >= 5:
                            size = event_path.stat().st_size
                            changes = {'elapsed_seconds': int(elapsed)}
                            if size != last_size:
                                changes['last_event_at'] = dt.datetime.fromtimestamp(event_path.stat().st_mtime).astimezone().isoformat()
                                last_size = size
                            update_job(path, **changes)
                            heartbeat = elapsed
                        if elapsed >= inputs['timeout']:
                            raise subprocess.TimeoutExpired(child.args, inputs['timeout'])
                        time.sleep(min(.5, inputs['timeout'] - elapsed))
                    rc = child.returncode
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait()
                    update_job(path, exit_code=child.returncode, elapsed_seconds=int(elapsed))
                    raise ValueError(f'本步骤超过 {inputs["timeout"]} 秒；最后事件见 {event_path.name}。')
            update_job(path, exit_code=rc, elapsed_seconds=int(time.time() - started_wall),
                       last_event_at=dt.datetime.fromtimestamp(event_path.stat().st_mtime).astimezone().isoformat())
            if rc:
                detail = event_error(event_path)
                raise ValueError((detail or f'Codex 退出码 {rc}') + f'；详情：{event_path}')
            result = read_json(raw_path)
            validate_result(stage, result, inputs, validation_evidence(path, stage))
            if stage == 'rewrite' and not used_search(event_path):
                raise ValueError('没有发现已完成的联网搜索调用；不能把仅凭已有知识生成当作完成素材搜索。')
            render_outputs(path, stage, result)
            # 完整 JSON 为该步骤的提交点；崩溃后仅重跑未提交步骤。
            save_json(path / (stage + '.json'), result)
            with (path / '_运行日志.log').open('a') as log:
                log.write(f'{now()} {LABELS[stage]} 完成，耗时 {time.time() - started_wall:.1f} 秒，退出码 {rc}\n')
            succeeded = True
            return
        except (OSError, ValueError) as error:
            last_error = str(error)
            attempt_error = last_error
            if raw_path.exists():
                previous_output = raw_path.read_text()
            with (path / '_运行日志.log').open('a') as log:
                log.write(f'{now()} 检查未通过：{last_error}\n')
            if not should_retry(last_error):
                break
        finally:
            latest = read_json(path / 'job.json')
            save_json(base.with_suffix('.meta'), {'stage':stage,'attempt':attempt,
                      'status':'completed' if succeeded else 'failed','error':attempt_error,
                      'started_at':dt.datetime.fromtimestamp(attempt_clock).astimezone().isoformat(),
                      'ended_at':now(),'elapsed_seconds':round(time.time()-attempt_clock,1),
                      'exit_code':latest.get('exit_code',rc),'last_event_at':latest.get('last_event_at')})
    raise ValueError(last_error)


def worker(job_id):
    path = job_dir(job_id)
    try:
        with lock(path / '.run.lock', blocking=False):
            # 启动者先登记PID，再开始写状态。
            with lock(path / '.start.lock'):
                update_job(path, status='running', worker_pid=os.getpid(), error='')
            caffeinate = None
            if sys.platform == 'darwin' and shutil.which('caffeinate'):
                caffeinate = subprocess.Popen(['caffeinate', '-i', '-w', str(os.getpid())],
                                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                completed = []
                for stage in STAGES:
                    if (path / (stage + '.json')).exists():
                        saved = read_json(path / (stage + '.json'))
                        try:
                            validate_result(stage, saved, read_json(path / 'input.json'),
                                            validation_evidence(path, stage))
                        except ValueError:
                            # 用户显式重试时发现旧产物不合格，保留旧版再重做该步及下游。
                            for dependent in STAGES[STAGES.index(stage):]:
                                old = path / (dependent + '.json')
                                if old.exists():
                                    old.rename(path / 'attempts' / ('rejected-' + dependent + '-' + uuid.uuid4().hex[:8] + '.json'))
                            update_job(path, completed_stages=completed)
                        else:
                            render_outputs(path, stage, saved)
                    if not (path / (stage + '.json')).exists():
                        run_stage(path, stage)
                    completed.append(stage)
                    update_job(path, completed_stages=completed)
                update_job(path, status='completed', error='')
                try:
                    import image_workflow
                    image_workflow.start_search(job_id)
                except Exception as error:
                    # Even a missing/broken optional image module cannot roll back text completion.
                    with (path / '_运行日志.log').open('a') as log:
                        log.write(f'{now()} 配图启动失败（正文已完成）：{error}\n')
                    save_json(path / 'images.json', {'status':'failed','candidates':[],
                              'selected_ids':[],'downloads':{},'provider_errors':[],'error':str(error)})
            except Exception as error:
                update_job(path, status='failed', error=str(error))
            finally:
                if caffeinate:
                    caffeinate.terminate()
                    caffeinate.wait()
    except BlockingIOError:
        return


def job_result(job_id):
    path = job_dir(job_id)
    state = state_of(job_id)
    state['inputs'] = read_json(path / 'input.json')
    state['sources'] = read_json(path / 'snapshot' / 'sources.json')
    state['reports'] = {}
    for stage in STAGES:
        file = path / (stage + '.json')
        if file.exists():
            state['reports'][stage] = read_json(file)['report']
    state['titles'] = []
    if (path / 'titles.json').exists():
        titles = read_json(path / 'titles.json')
        state['titles'] = titles['titles']
        state['recommended_index'] = titles['recommended_index']
        index = state['selected_index'] if state['selected_index'] is not None else titles['recommended_index']
        state['final_title'] = titles['titles'][index]
    if state.get('final_title') and (path / 'human.json').exists():
        state['final_body'] = read_json(path / 'human.json')['body'].strip()
        state['final_content'] = state['final_title'] + '\n\n' + state['final_body']
        state['final_path'] = str(path / OUTPUTS['human'])
    try:
        import image_workflow
        state['images'] = image_workflow.state_of(path)
    except (ImportError, OSError, ValueError) as error:
        state['images'] = {'status':'failed','status_label':'配图暂不可用，正文可正常复制',
                           'candidates':[],'selected_ids':[],'downloads':{},'error':str(error)}
    state['console'] = (path / '_运行日志.log').read_text()[-8000:]
    return state


def select_title(job_id, index):
    path = job_dir(job_id)
    # 只在完成后选择，避免后台写终稿与用户改标题抢写。
    with lock(path / '.select.lock'):
        if state_of(job_id)['status'] != 'completed':
            raise ValueError('请等人味终稿完成后再选择标题。')
        titles = read_json(path / 'titles.json')['titles']
        if type(index) is not int or not 0 <= index < len(titles):
            raise ValueError('标题编号超出范围。')
        body = read_json(path / 'human.json')['body']
        if len(re.sub(r'\s', '', titles[index] + body)) > 1550:
            raise ValueError('此标题与正文合计超过1550字符。')
        update_job(path, selected_index=index)
        render_outputs(path, 'human', read_json(path / 'human.json'))
    return job_result(job_id)


def list_jobs():
    directory = output_root() / 'jobs'
    jobs = [state_of(p.name) for p in directory.glob('*') if (p / 'job.json').exists()]
    return sorted(jobs, key=lambda state: (state['created_at'], state['id']), reverse=True)


def wait_job(job_id):
    while True:
        state = state_of(job_id)
        if state['status'] not in ('queued', 'running'):
            return state
        time.sleep(1)


def emit(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description='公众号三步写作：网页和手机共用的后台任务')
    sub = parser.add_subparsers(dest='command', required=True)
    for cmd in ('submit', 'run'):
        p = sub.add_parser(cmd, help='提交JSON任务；run额外等待结果')
        p.add_argument('--input', required=True, help='JSON文件绝对路径，或 - 从标准输入读入')
    sub.add_parser('list', help='列出已提交任务')
    for cmd in ('status', 'show', 'retry', '_worker'):
        p = sub.add_parser(cmd)
        p.add_argument('id')
        if cmd == 'show':
            p.add_argument('--body', action='store_true')
    p = sub.add_parser('select', help='选择标题，编号从1开始')
    p.add_argument('id')
    p.add_argument('number', type=int)
    sub.add_parser('options')
    p = sub.add_parser('prompts', help='列出真实调用路径，或导出三份完整核心提示词')
    p.add_argument('--export', action='store_true')
    p = sub.add_parser('legacy')
    p.add_argument('args', nargs='+')
    args = parser.parse_args()
    if args.command == '_worker':
        worker(args.id)
    elif args.command == 'options':
        emit(options())
    elif args.command == 'prompts':
        emit(options()['resources'])
        if args.export:
            path = output_root() / '核心提示词_三份原文合辑.md'
            path.parent.mkdir(parents=True, exist_ok=True)
            content = '# 三份核心提示词原文合辑\n\n以下为原文件全文，无删减。新任务直接读取原文件，本合辑仅供查阅。\n'
            for key, source in CORE_FILES.items():
                content += '\n\n---\n\n来源：' + str(source) + '\n\n' + source.read_text()
            atomic_text(path, content)
            print('合辑位置：' + str(path))
    elif args.command in ('submit', 'run', 'legacy'):
        if args.command == 'legacy':
            values = args.args + [''] * 6
            source = values[1]
            try:
                if source and Path(source).is_file():
                    source = Path(source).read_text()
            except OSError:
                pass  # 长原文不是文件名。
            if not source and shutil.which('pbpaste'):
                source = subprocess.check_output(['pbpaste'], text=True)
            payload = dict(zip(('topic', 'source', 'mode', 'model', 'reasoning_effort', 'brief'),
                               [values[0], source, *values[2:6]]))
        else:
            payload = json.load(sys.stdin) if args.input == '-' else read_json(args.input)
        state = submit_job(payload)
        emit(state)
        if args.command != 'submit':
            final = wait_job(state['id'])
            if final['status'] != 'completed':
                raise ValueError(final['error'])
            print('✅ 完成。成稿位置：' + str(job_dir(state['id']) / OUTPUTS['human']))
    elif args.command == 'list':
        emit(list_jobs())
    elif args.command == 'status':
        emit(state_of(args.id))
    elif args.command == 'show':
        result = job_result(args.id)
        print(result.get('final_body', '正文尚未完成。')) if args.body else emit(result)
    elif args.command == 'retry':
        emit(start_job(args.id))
    elif args.command == 'select':
        emit(select_title(args.id, args.number - 1))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError) as error:
        print('错误：' + str(error), file=sys.stderr)
        sys.exit(1)
