"""模型配置、脱敏展示和本地密钥管理。"""
from __future__ import annotations

import getpass
import json
import os
from pathlib import Path
import shutil
import stat
import tomllib


ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / 'config'
EXAMPLE = CONFIG_DIR / 'providers.example.toml'
LOCAL = CONFIG_DIR / 'providers.local.toml'
SECRETS = CONFIG_DIR / 'secrets.local.toml'
STAGES = ('rewrite', 'titles', 'human')
ADAPTERS = {'codex_cli', 'anthropic', 'volcengine_ark', 'openai_compatible'}


def _read_toml(path: Path) -> dict:
    return tomllib.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}


def _deep_merge(base: dict, override: dict) -> dict:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config() -> dict:
    data = _deep_merge(_read_toml(EXAMPLE), _read_toml(LOCAL))
    workflow = data.setdefault('workflow', {})
    profiles = data.setdefault('profiles', {})
    if not profiles:
        raise ValueError('没有任何模型配置。')
    for profile_id, profile in profiles.items():
        adapter = profile.get('adapter', '')
        if adapter not in ADAPTERS:
            raise ValueError(f'模型配置 {profile_id} 的 adapter 无效：{adapter}')
        if not profile.get('model'):
            raise ValueError(f'模型配置 {profile_id} 缺少 model。')
        profile['id'] = profile_id
        profile.setdefault('label', profile_id)
        profile.setdefault('native_search', False)
        profile.setdefault('strict_json_schema', False)
        profile.setdefault('reasoning_effort', '')
        profile.setdefault('supports_reasoning_effort', adapter == 'codex_cli')
        profile.setdefault('supported_efforts', [])
        profile.setdefault('max_output_tokens', 32000)
    default = workflow.get('default_profile', 'codex')
    fallback = workflow.get('search_fallback_profile', 'codex')
    if default not in profiles:
        raise ValueError('默认模型配置不存在：' + default)
    if fallback and fallback not in profiles:
        raise ValueError('搜索回退模型配置不存在：' + fallback)
    workflow['default_profile'] = default
    workflow['search_fallback_profile'] = fallback
    return data


def load_secrets() -> dict:
    if SECRETS.exists():
        if SECRETS.is_symlink():
            raise ValueError('密钥文件不能是符号链接：' + str(SECRETS))
        mode = stat.S_IMODE(SECRETS.stat().st_mode)
        if mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise ValueError('密钥文件权限过宽。请运行：chmod 600 ' + str(SECRETS))
    values = _read_toml(SECRETS).get('secrets', {})
    return {str(k): str(v) for k, v in values.items() if isinstance(v, str) and v}


def profile_is_configured(profile: dict, secrets: dict | None = None) -> bool:
    if profile['adapter'] == 'codex_cli':
        return shutil.which('codex') is not None
    secrets = load_secrets() if secrets is None else secrets
    key_name = profile.get('api_key_name', '')
    if not profile.get('base_url') or not key_name or not secrets.get(key_name):
        return False
    return not str(profile.get('model', '')).startswith('请在')


def public_options() -> dict:
    config, secrets = load_config(), load_secrets()
    profiles = {}
    for profile_id, profile in config['profiles'].items():
        profiles[profile_id] = {
            'id': profile_id,
            'label': profile['label'],
            'adapter': profile['adapter'],
            'model': profile['model'],
            'reasoning_effort': profile.get('reasoning_effort', ''),
            'native_search': bool(profile.get('native_search')),
            'strict_json_schema': bool(profile.get('strict_json_schema')),
            'supported_efforts': list(profile.get('supported_efforts', [])),
            'configured': profile_is_configured(profile, secrets),
        }
    return {'profiles': profiles, 'routing': dict(config['workflow'])}


def resolve_profile(profile_id: str) -> tuple[dict, str]:
    config, secrets = load_config(), load_secrets()
    if profile_id not in config['profiles']:
        raise ValueError('模型配置不存在：' + profile_id)
    profile = dict(config['profiles'][profile_id])
    if not profile_is_configured(profile, secrets):
        raise ValueError('模型配置尚未完成：' + profile_id + '。请运行 bash wechat.sh configure。')
    key = secrets.get(profile.get('api_key_name', ''), '')
    return profile, key


def resolve_stage_profiles(payload: dict) -> dict:
    config = load_config()
    profiles = config['profiles']
    default = str(payload.get('default_profile') or config['workflow']['default_profile'])
    if default not in profiles:
        raise ValueError('默认模型配置不存在：' + default)
    raw = payload.get('stage_profiles') or {}
    if not isinstance(raw, dict):
        raise ValueError('stage_profiles 必须是对象。')
    unknown = set(raw) - set(STAGES)
    if unknown:
        raise ValueError('未知阶段模型配置：' + ', '.join(sorted(unknown)))
    selected = {}
    for stage in STAGES:
        chosen = str(raw.get(stage) or default)
        if chosen not in profiles:
            raise ValueError(f'{stage} 的模型配置不存在：{chosen}')
        selected[stage] = chosen
    if not profiles[selected['rewrite']].get('native_search'):
        fallback = config['workflow'].get('search_fallback_profile', '')
        if not fallback or fallback not in profiles or not profiles[fallback].get('native_search'):
            raise ValueError('仿写模型不支持原生联网，且没有可用的搜索回退模型。')
        selected['rewrite'] = fallback
    return {'default_profile': default, 'stage_profiles': selected}


def _toml_quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def configure_interactive() -> None:
    config = load_config()
    existing = load_secrets()
    print('密钥只写入 config/secrets.local.toml，不会显示在网页、任务或Git中。')
    for profile in config['profiles'].values():
        key_name = profile.get('api_key_name')
        if not key_name:
            continue
        current = '已配置' if existing.get(key_name) else '未配置'
        value = getpass.getpass(f'{profile["label"]} API Key（{current}，直接回车保持不变）：').strip()
        if value:
            existing[key_name] = value
    lines = ['[secrets]'] + [f'{key} = {_toml_quote(value)}' for key, value in sorted(existing.items())]
    CONFIG_DIR.mkdir(exist_ok=True)
    fd = os.open(SECRETS, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(fd, 'w', encoding='utf-8') as handle:
        handle.write('\n'.join(lines) + '\n')
    os.chmod(SECRETS, stat.S_IRUSR | stat.S_IWUSR)
    print('已保存本地密钥文件：' + str(SECRETS))


def doctor_rows() -> list[dict]:
    options = public_options()
    return [options['profiles'][key] for key in options['profiles']]
