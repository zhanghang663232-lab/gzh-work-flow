"""统一模型适配层：Codex CLI、Claude、豆包方舟与 OpenAI 兼容接口。"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import time
import tomllib
import urllib.error
import urllib.request


class ProviderError(ValueError):
    def __init__(self, message: str, *, retryable: bool):
        super().__init__(message)
        self.retryable = retryable


@dataclass
class GenerationRequest:
    profile: dict
    api_key: str
    stage: str
    prompt: str
    schema: dict
    timeout: float
    reasoning_effort: str
    raw_path: Path
    event_path: Path
    workspace: Path
    environment: dict
    heartbeat: object | None = None


@dataclass
class GenerationResult:
    output: dict
    metadata: dict
    raw_response: str


def _writing_only_overrides() -> list[str]:
    path = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'config.toml'
    config = tomllib.loads(path.read_text()) if path.exists() else {}
    overrides = ['features.hooks=false', 'features.plugins=false', 'features.unbounded_connection_retries=false']
    for category in ('plugins', 'mcp_servers'):
        for name in config.get(category, {}):
            if '.' not in name:
                overrides.append(category + '.' + name + '.enabled=false')
    return overrides


def codex_command(profile: dict, stage: str, raw_path: Path, schema_path: Path, workspace: Path,
                  reasoning_effort: str = '', model_override: str = '') -> list[str]:
    command = ['codex', '--ask-for-approval', 'never']
    model = model_override or profile.get('model', '')
    if model:
        command += ['-m', model]
    effort = reasoning_effort or profile.get('reasoning_effort', '')
    if effort:
        command += ['-c', 'model_reasoning_effort=' + json.dumps(effort)]
    codex_root = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex')))
    gzh_skill = Path(os.environ.get('WECHAT_GZH_SKILL', str(codex_root / 'skills' / 'gzh'))).expanduser()
    command += ['-c', 'web_search=' + json.dumps('live' if stage == 'rewrite' else 'disabled'),
                '-c', 'project_doc_max_bytes=0',
                '-c', 'skills.config=[{path=' + json.dumps(str(gzh_skill)) + ',enabled=false}]']
    for override in _writing_only_overrides():
        command += ['-c', override]
    command += ['exec', '--skip-git-repo-check', '-C', str(workspace), '--sandbox', 'read-only',
                '--output-schema', str(schema_path), '--output-last-message', str(raw_path), '--json', '-']
    return command


def _codex_search_used(path: Path) -> bool:
    for line in path.read_text(errors='replace').splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        item = event.get('item', {})
        if event.get('type') == 'item.completed' and item.get('type') == 'web_search':
            if item.get('query') or item.get('action', {}).get('type') == 'search':
                return True
    return False


def _codex_error(path: Path) -> str:
    messages = []
    for line in path.read_text(errors='replace').splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get('type') in ('error', 'turn.failed'):
            value = event.get('message') or event.get('error', {}).get('message')
            if value:
                messages.append(str(value))
    return messages[-1] if messages else ''


def _execute_codex(request: GenerationRequest) -> GenerationResult:
    schema_path = request.raw_path.with_suffix('.schema')
    schema_path.write_text(json.dumps(request.schema, ensure_ascii=False), encoding='utf-8')
    command = codex_command(request.profile, request.stage, request.raw_path, schema_path, request.workspace,
                            request.reasoning_effort, request.profile.get('model_override', ''))
    started = time.time()
    with request.event_path.open('wb') as events:
        child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=events, stderr=subprocess.STDOUT,
                                 start_new_session=True, env=request.environment)
        assert child.stdin is not None
        child.stdin.write(request.prompt.encode('utf-8'))
        child.stdin.close()
        last_size, last_heartbeat = -1, 0.0
        while child.poll() is None:
            elapsed = time.time() - started
            if elapsed - last_heartbeat >= 5:
                size = request.event_path.stat().st_size
                if request.heartbeat:
                    request.heartbeat(elapsed, size != last_size)
                last_size, last_heartbeat = size, elapsed
            if elapsed >= request.timeout:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
                raise ProviderError(f'Codex步骤超过 {int(request.timeout)} 秒。', retryable=True)
            time.sleep(.25)
    if child.returncode:
        detail = _codex_error(request.event_path) or f'Codex退出码 {child.returncode}'
        terminal = any(token in detail.lower() for token in ('usage limit', 'not logged', 'unauthorized', 'invalid model', 'error loading config'))
        raise ProviderError(detail, retryable=not terminal)
    try:
        output = json.loads(request.raw_path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        raise ProviderError('Codex没有返回有效JSON：' + str(error), retryable=True) from error
    searched = _codex_search_used(request.event_path)
    if request.stage == 'rewrite' and not searched:
        raise ProviderError('没有发现已完成的联网搜索调用。', retryable=True)
    return GenerationResult(output, {'profile': request.profile['id'], 'adapter': 'codex_cli',
                            'model': request.profile.get('model_override') or request.profile['model'],
                            'reasoning_effort': request.reasoning_effort or request.profile.get('reasoning_effort', ''),
                            'search_used': searched, 'elapsed_seconds': round(time.time() - started, 2)}, '')


def _endpoint(base: str, suffix: str) -> str:
    return base.rstrip('/') + '/' + suffix.lstrip('/')


def _json_from_text(text: str) -> dict:
    value = text.strip()
    fenced = re.fullmatch(r'```(?:json)?\s*(.*?)\s*```', value, re.DOTALL | re.IGNORECASE)
    if fenced:
        value = fenced.group(1)
    try:
        result = json.loads(value)
    except ValueError as error:
        raise ProviderError('模型返回的内容不是有效JSON：' + str(error), retryable=True) from error
    if not isinstance(result, dict):
        raise ProviderError('模型必须返回JSON对象。', retryable=True)
    return result


def _extract_text(data: dict, adapter: str) -> str:
    if adapter == 'openai_compatible':
        try:
            content = data['choices'][0]['message']['content']
            if isinstance(content, list):
                return ''.join(str(item.get('text', '')) for item in content if isinstance(item, dict))
            return str(content)
        except (KeyError, IndexError, TypeError) as error:
            raise ProviderError('兼容接口响应缺少 choices[0].message.content。', retryable=True) from error
    if adapter == 'anthropic':
        blocks = data.get('content', [])
        return ''.join(str(block.get('text', '')) for block in blocks
                       if isinstance(block, dict) and block.get('type') == 'text')
    if isinstance(data.get('output_text'), str):
        return data['output_text']
    texts = []
    for item in data.get('output', []):
        if not isinstance(item, dict) or item.get('type') != 'message':
            continue
        for block in item.get('content', []):
            if isinstance(block, dict) and block.get('type') in ('output_text', 'text'):
                texts.append(str(block.get('text', '')))
    if not texts:
        raise ProviderError('Responses接口响应中没有输出文本。', retryable=True)
    return ''.join(texts)


def _contains_search(value) -> bool:
    if isinstance(value, dict):
        kind = str(value.get('type', '')).lower()
        status = str(value.get('status', '')).lower()
        if 'web_search' in kind:
            if 'error' in kind or status in {'failed', 'error', 'cancelled', 'incomplete'}:
                return False
            # 声明工具或开始调用不算完成；只有结果块或 completed 调用才是可验证证据。
            if 'result' in kind or ('call' in kind and status == 'completed'):
                return True
        return any(_contains_search(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_search(item) for item in value)
    return False


def _api_payload(request: GenerationRequest) -> tuple[str, dict, dict]:
    profile, adapter = request.profile, request.profile['adapter']
    common_headers = {'Content-Type': 'application/json', 'User-Agent': 'gzh-work-flow/1.0'}
    effort = request.reasoning_effort or profile.get('reasoning_effort', '')
    max_tokens = int(profile.get('max_output_tokens', 32000))
    if adapter == 'anthropic':
        headers = {**common_headers, 'x-api-key': request.api_key, 'anthropic-version': '2023-06-01'}
        body = {'model': profile['model'], 'max_tokens': max_tokens,
                'messages': [{'role': 'user', 'content': request.prompt}],
                'output_config': {'format': {'type': 'json_schema', 'schema': request.schema}}}
        if effort in {'low', 'medium', 'high', 'xhigh', 'max'}:
            body['output_config']['effort'] = effort
            body['thinking'] = {'type': 'adaptive'}
        if request.stage == 'rewrite':
            body['tools'] = [{'type': profile.get('search_tool_type', 'web_search_20250305'),
                              'name': 'web_search', 'max_uses': int(profile.get('search_max_uses', 8))}]
        return _endpoint(profile['base_url'], 'v1/messages'), headers, body
    headers = {**common_headers, 'Authorization': 'Bearer ' + request.api_key}
    if adapter == 'volcengine_ark':
        body = {'model': profile['model'], 'input': request.prompt, 'max_output_tokens': max_tokens,
                'text': {'format': {'type': 'json_schema', 'name': request.stage + '_output', 'schema': request.schema}}}
        if effort and profile.get('supports_reasoning_effort'):
            body['reasoning_effort'] = effort
            body['thinking'] = True
        if request.stage == 'rewrite':
            body['tools'] = [{'type': profile.get('search_tool_type', 'web_search')}]
        return _endpoint(profile['base_url'], 'responses'), headers, body
    body = {'model': profile['model'], 'messages': [{'role': 'user', 'content': request.prompt}],
            'max_tokens': max_tokens, 'response_format': {'type': 'json_object'}}
    if effort and profile.get('supports_reasoning_effort'):
        body['reasoning_effort'] = effort
        if profile.get('thinking_enabled'):
            body['thinking'] = {'type': 'enabled'}
    return _endpoint(profile['base_url'], 'chat/completions'), headers, body


def _execute_api(request: GenerationRequest) -> GenerationResult:
    url, headers, payload = _api_payload(request)
    started = time.time()
    safe_event = {'type': 'provider.request.started', 'profile': request.profile['id'],
                  'adapter': request.profile['adapter'], 'model': request.profile['model'],
                  'native_search': bool(request.profile.get('native_search'))}
    request.event_path.write_text(json.dumps(safe_event, ensure_ascii=False) + '\n', encoding='utf-8')
    http_request = urllib.request.Request(url, data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                                          headers=headers, method='POST')
    try:
        with urllib.request.urlopen(http_request, timeout=request.timeout) as response:
            raw = response.read().decode('utf-8')
            request_id = response.headers.get('request-id') or response.headers.get('x-request-id') or ''
            status = response.status
    except urllib.error.HTTPError as error:
        body = error.read(8192).decode('utf-8', errors='replace')
        retryable = error.code == 429 or error.code >= 500
        detail = body.replace(request.api_key, '<redacted>') if request.api_key else body
        detail = re.sub(r'(?i)(api[_-]?key|authorization)[^,}\n]*', r'\1=<redacted>', detail)
        raise ProviderError(f'{request.profile["label"]} HTTP {error.code}：{detail[:1000]}', retryable=retryable) from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise ProviderError(f'{request.profile["label"]} 连接失败：{error}', retryable=True) from error
    try:
        data = json.loads(raw)
    except ValueError as error:
        raise ProviderError('服务商响应不是JSON。', retryable=True) from error
    text = _extract_text(data, request.profile['adapter'])
    output = _json_from_text(text)
    searched = _contains_search(data)
    if request.stage == 'rewrite' and not searched:
        raise ProviderError('服务商响应中没有可验证的原生联网搜索记录。', retryable=True)
    request.raw_path.write_text(json.dumps(output, ensure_ascii=False), encoding='utf-8')
    with request.event_path.open('a', encoding='utf-8') as events:
        events.write(json.dumps({'type': 'provider.request.completed', 'http_status': status,
                                 'request_id': request_id, 'search_used': searched}, ensure_ascii=False) + '\n')
    usage = data.get('usage', {}) if isinstance(data.get('usage'), dict) else {}
    metadata = {'profile': request.profile['id'], 'adapter': request.profile['adapter'],
                'model': request.profile['model'], 'search_used': searched,
                'reasoning_effort': request.reasoning_effort,
                'request_id': request_id, 'usage': usage, 'elapsed_seconds': round(time.time() - started, 2)}
    return GenerationResult(output, metadata, raw)


def generate(request: GenerationRequest) -> GenerationResult:
    if request.profile['adapter'] == 'codex_cli':
        return _execute_codex(request)
    return _execute_api(request)
