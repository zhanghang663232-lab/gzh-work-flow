"""离线回归：真实三份核心与各赛道资料 + 假模型，不消耗模型额度。"""
import concurrent.futures
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import pipeline as p
import terminal_ui as terminal
spec = importlib.util.spec_from_file_location('webserver', ROOT / 'web/server.py')
web = importlib.util.module_from_spec(spec)
spec.loader.exec_module(web)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='wechat-v2-test-')
        self.directory = Path(self.temp.name)
        self.env = patch.dict(os.environ, {'WECHAT_OUTPUT_DIR': str(self.directory / 'output'),
                             'PATH': str(ROOT / 'tests/fake_bin') + ':' + os.environ['PATH']})
        self.env.start()

    def tearDown(self):
        # 不让后台测试跨越环境或临时目录的生命期。
        for job in p.list_jobs():
            if job['status'] in ('queued','running'):
                self.done(job['id'])
        end = time.monotonic() + 15
        while any(p.busy(p.job_dir(j['id'])) for j in p.list_jobs()) and time.monotonic() < end:
            time.sleep(.05)
        self.env.stop()
        self.temp.cleanup()

    def payload(self, mode='数码'):
        return {'topic':'固定主题：我的数码工具够用就好', 'source':'第一段原文\n\n第二段“引号”与$HOME、`字符`，不是命令。',
                'mode':mode, 'brief':'我的立场支持够用，不可改成劝人买新。',
                'positioning':'面向普通使用者，保持直白声音。', 'model':'gpt-5.6-terra','reasoning_effort':'medium'}

    def done(self, job_id):
        end = time.monotonic() + 25
        while time.monotonic() < end:
            state = p.state_of(job_id)
            if state['status'] not in ('queued','running'):
                return state
            time.sleep(.05)
        self.fail('后台任务未在测试时限内结束：' + job_id)

    def test_all_tracks_full_prompts_and_shared_selection(self):
        for mode in p.MODES:
            with self.subTest(mode=mode):
                payload = self.payload(mode)
                job = p.submit_job(payload)
                self.assertEqual(self.done(job['id'])['status'], 'completed')
                folder = p.job_dir(job['id'])
                for stage in p.STAGES:
                    original = p.CORE_FILES[stage].read_bytes()
                    self.assertEqual((folder / 'snapshot' / (stage+'.md')).read_bytes(), original)
                    prompt = (folder / 'attempts' / (stage+'-01.prompt.md')).read_text()
                    self.assertIn(original.decode(), prompt)
                    self.assertIn(payload['brief'], prompt)
                    self.assertIn(payload['positioning'], prompt)
                    self.assertIn(p.resources(mode)['persona'].read_text(), prompt)
                    self.assertIn('禁止调用、读取、引用、加载或遵循 `gzh` 技能', prompt)
                self.assertIn(p.resources(mode)['references'].read_text(), (folder/'attempts/titles-01.prompt.md').read_text())
                result = p.job_result(job['id'])
                self.assertEqual(len(result['titles']), 40)
                self.assertTrue(result['titles'][0].startswith('2.5倍'))
                result = p.select_title(job['id'], 0)
                self.assertEqual(result['final_title'], result['titles'][0])
                self.assertTrue((folder/'3_人味终稿.md').read_text().startswith('2.5倍'))
                cli = subprocess.check_output(['bash', str(ROOT/'wechat.sh'),'show',job['id'],'--body'], text=True)
                self.assertEqual(cli.strip(), result['final_body'])
                if mode == '汽车':
                    self.assertIn('不足 10', result['warning'])

    def test_input_validation_and_model_pair(self):
        for change in ({'mode':''}, {'source':''}, {'title_count':9}, {'title_count':True},
                       {'title_count':10.5}, {'model':'gpt-5.5','reasoning_effort':'ultra'}, {'topic':[]}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                p.validate_input({**self.payload(), **change})
        self.assertEqual(p.validate_input({**self.payload(),'title_count':99})['title_count'], 60)
        with self.assertRaises(ValueError):
            p.job_dir('../../etc')
        with self.assertRaisesRegex(ValueError, '乱码或连续问号'):
            p.validate_input({**self.payload(), 'source':'正常文字????????后续文字'})

    def test_search_and_gzh_flags(self):
        inp = p.validate_input(self.payload())
        for stage in p.STAGES:
            args = p.codex_command(inp,stage,Path('/tmp/result'),Path('/tmp/schema'),Path('/tmp/work'))
            self.assertIn('web_search="live"' if stage == 'rewrite' else 'web_search="disabled"',args)
            self.assertTrue(any(arg.startswith('skills.config=[{path=') and arg.endswith(',enabled=false}]') for arg in args))
            self.assertIn('--output-schema',args)
            self.assertFalse(any('mcp_servers."' in arg for arg in args))
            self.assertFalse(any('plugins."' in arg for arg in args))
        title_schema = p.schema_for('titles',40)['properties']['titles']
        self.assertNotIn('minLength',title_schema['items'])
        self.assertNotIn('maxLength',title_schema['items'])

    def test_profile_routing_and_public_options_are_secret_free(self):
        options = p.options()
        self.assertIn('codex', options['profiles'])
        self.assertNotIn('api_key', json.dumps(options))
        self.assertTrue(all(item['ok'] for item in p.environment_checks()))
        with patch.object(p.provider_config, 'resolve_profile', side_effect=lambda profile_id: (p.provider_config.load_config()['profiles'][profile_id], 'fixture')):
            routed = p.validate_input({**self.payload(), 'model':'', 'default_profile':'deepseek',
                                       'stage_profiles':{'titles':'deepseek','human':'deepseek'}})
        self.assertEqual(routed['stage_profiles']['rewrite'], 'codex')
        self.assertEqual(routed['stage_profiles']['titles'], 'deepseek')

    def test_failure_resume_only_unfinished(self):
        marker = self.directory/'fail'
        marker.touch()
        with patch.dict(os.environ, {'FAKE_CODEX_FAIL_MARKER':str(marker)}):
            job = p.submit_job(self.payload())
            self.assertEqual(self.done(job['id'])['status'],'failed')
            folder = p.job_dir(job['id'])
            self.assertTrue((folder/'rewrite.json').exists())
            marker.unlink()
            with concurrent.futures.ThreadPoolExecutor(3) as pool:
                list(pool.map(p.start_job, [job['id']]*3))
            self.assertEqual(self.done(job['id'])['status'],'completed')
            self.assertEqual(len(list((folder/'attempts').glob('rewrite-*.json'))),1)
            self.assertEqual(len(list((folder/'attempts').glob('human-*.json'))),1)

    def test_detached_after_submitter_exits_and_hangup(self):
        with patch.dict(os.environ, {'FAKE_CODEX_DELAY':'.15'}):
            # 提交命令退出后，另一个进程仍能完整产出；worker无控制终端。
            result = subprocess.run(['bash',str(ROOT/'wechat.sh'),'submit','--input','-'],
                                    input=json.dumps(self.payload()),text=True,capture_output=True,check=True)
            job = json.loads(result.stdout)
            os.kill(job['worker_pid'], signal.SIGHUP)
            self.assertEqual(self.done(job['id'])['status'],'completed')

    def test_http_async_multi_job_selection_and_server_disconnect(self):
        server = web.ThreadingHTTPServer(('127.0.0.1',0),web.AppHandler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        base = 'http://127.0.0.1:' + str(server.server_port)
        def request(path,payload=None):
            req = Request(base+path,data=json.dumps(payload).encode() if payload is not None else None,
                          headers={'Content-Type':'application/json'})
            with urlopen(req,timeout=5) as response:
                return response.status,json.load(response)
        try:
            self.assertEqual(request('/api/options')[1]['title_count'],40)
            jobs = [request('/api/jobs', self.payload(m))[1] for m in ('数码','汽车')]
            self.assertNotEqual(jobs[0]['id'],jobs[1]['id'])
            for job in jobs:
                self.assertEqual(self.done(job['id'])['status'],'completed')
            chosen = request('/api/jobs/'+jobs[0]['id']+'/select',{'index':1})[1]
            self.assertEqual(chosen['final_title'],p.job_result(jobs[0]['id'])['titles'][1])
            self.assertEqual(request('/api/jobs')[1]['jobs'][0]['mode'],'汽车')
            try:
                request('/api/jobs',[])
                self.fail('应拒绝数组输入')
            except HTTPError as error:
                self.assertEqual(error.code,400)
                error.close()
            with patch.dict(os.environ, {'FAKE_CODEX_DELAY':'.2'}):
                last = request('/api/jobs',self.payload())[1]
            server.shutdown()
            self.assertEqual(self.done(last['id'])['status'],'completed')
        finally:
            server.shutdown(); server.server_close(); thread.join()

    def test_legacy_command(self):
        result = subprocess.run(['bash',str(ROOT/'wechat.sh'),'兼容旧入口','原文','通用'],
                                text=True,capture_output=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('成稿位置：',result.stdout)

    def test_explicit_retry_revalidates_without_discarding_good_stage(self):
        job = p.submit_job(self.payload())
        self.assertEqual(self.done(job['id'])['status'],'completed')
        folder = p.job_dir(job['id'])
        titles = p.read_json(folder/'titles.json')
        titles['titles'][0] = '太短'
        p.save_json(folder/'titles.json',titles)
        p.start_job(job['id'])
        self.assertEqual(self.done(job['id'])['status'],'completed')
        self.assertEqual(len(list((folder/'attempts').glob('rewrite-*.json'))),1)
        self.assertEqual(len(list((folder/'attempts').glob('titles-*.json'))),2)
        self.assertEqual(len(list((folder/'attempts').glob('rejected-*.json'))),2)

    def test_model_timeout_keeps_failure_evidence(self):
        job_id = p.create_job(self.payload())
        folder = p.job_dir(job_id)
        inputs = p.read_json(folder/'input.json')
        inputs['timeout'] = .1
        inputs['attempts'] = 1
        p.save_json(folder/'input.json',inputs)
        with patch.dict(os.environ, {'FAKE_CODEX_DELAY':'1'}):
            p.start_job(job_id)
            result = self.done(job_id)
        self.assertEqual(result['status'],'failed')
        self.assertIn('超过',result['error'])
        self.assertFalse((folder/'rewrite.json').exists())
        self.assertTrue((folder/'attempts/rewrite-01.prompt.md').exists())

    def test_title_quality_and_report_consistency_guards(self):
        titles = [f'选择数码产品之前，先弄清楚日常需要的第{i}件事' for i in range(40)]
        result = {'titles':titles,'report':'M1 M2 M3 M4 M5 M6 M7\n'+'\n'.join(titles),'recommended_index':0}
        p.validate_result('titles',result,p.validate_input(self.payload()))
        original = titles[0]
        titles[0] = '选择数码产品之前先弄清楚每天真正需要什么啊吧呢'
        with self.assertRaisesRegex(ValueError,'凑字数'):
            p.validate_result('titles',result,p.validate_input(self.payload()))
        titles[0] = '换手机前先查实际使用卡点，别让参数替你做决定'
        with self.assertRaisesRegex(ValueError,'报告中找不到'):
            p.validate_result('titles',result,p.validate_input(self.payload()))

    def test_human_stage_rejects_hidden_characters_and_invented_details(self):
        inputs = p.validate_input(self.payload())
        body = '这是保持原主题和立场的自然正文。' * 65
        p.validate_result('human', {'report':'检查通过', 'body':body}, inputs)
        with self.assertRaisesRegex(ValueError, '乱码或隐藏异常字符'):
            p.validate_result('human', {'report':'检查通过', 'body':body + '\u200b'}, inputs)
        with self.assertRaisesRegex(ValueError, '设想式个人场景'):
            p.validate_result('human', {'report':'检查通过', 'body':'我设想过一个周三晚上。' + body}, inputs)
        with self.assertRaisesRegex(ValueError, '不存在的具体数字'):
            p.validate_result('human', {'report':'检查通过', 'body':'手机只剩18%的电。' + body}, inputs)

    def test_macos_proxy_is_scoped_and_preserves_existing_environment(self):
        system_proxy = 'HTTPEnable : 1\nHTTPProxy : 127.0.0.1\nHTTPPort : 7892\nHTTPSEnable : 1\nHTTPSProxy : 127.0.0.1\nHTTPSPort : 7892\n'
        with patch.object(p.sys,'platform','darwin'), patch.object(p.subprocess,'check_output',return_value=system_proxy):
            with patch.dict(os.environ,{},clear=True):
                self.assertEqual(p.writing_environment()['HTTPS_PROXY'],'http://127.0.0.1:7892')
                self.assertNotIn('HTTPS_PROXY',os.environ)
            with patch.dict(os.environ,{'HTTPS_PROXY':'http://existing-proxy:9000'}):
                self.assertEqual(p.writing_environment()['HTTPS_PROXY'],'http://existing-proxy:9000')

    def test_phone_entry_is_deterministic_script_not_llm(self):
        result = subprocess.run(['bash',str(ROOT/'wechat.sh'),'chat','--help'],
                                text=True,capture_output=True,check=True)
        self.assertIn('wx                 新建一篇文章',result.stdout)
        self.assertNotIn('TERMINAL_WORKFLOW.md',result.stdout)
        self.assertNotIn('skills.config',result.stdout)

    def test_phone_end_marker_is_case_insensitive(self):
        for marker in ('END', 'end', 'End', '  eNd  '):
            with self.subTest(marker=marker), patch('builtins.input', side_effect=['正文内容', marker]):
                self.assertEqual(terminal.multiline_source(), '正文内容')

    def test_article_stays_in_task_directory_on_complete_retry_or_title_selection(self):
        job = p.submit_job(self.payload())
        self.assertEqual(self.done(job['id'])['status'], 'completed')
        p.start_job(job['id'])
        result = p.select_title(job['id'],1)
        self.assertTrue(Path(result['final_path']).is_file())
        self.assertTrue(Path(result['final_path']).resolve().is_relative_to((self.directory/'output'/'jobs').resolve()))

    def test_terminal_errors_do_not_consume_retries(self):
        events = self.directory/'events.jsonl'
        events.write_text('{"type":"error","message":"You have hit your usage limit"}\n')
        self.assertIn('usage limit',p.event_error(events))
        self.assertFalse(p.should_retry(p.event_error(events)))
        self.assertTrue(p.should_retry('备选标题有完全重复项'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
