"""多模型适配层离线测试，不访问真实服务、不消耗额度。"""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from workflow import config
from workflow import providers


SCHEMA = {'type':'object','properties':{'report':{'type':'string'},'body':{'type':'string'}},
          'required':['report','body'],'additionalProperties':False}
OUTPUT = {'report':'完整报告', 'body':'正文'}


class FakeAPI(BaseHTTPRequestHandler):
    last_headers = {}
    calls = []
    error_status = 0
    delay_seconds = 0

    def log_message(self, *args):
        pass

    def do_POST(self):
        size = int(self.headers.get('Content-Length', '0'))
        request = json.loads(self.rfile.read(size))
        FakeAPI.last_headers = dict(self.headers)
        FakeAPI.calls.append((self.path, request))
        if FakeAPI.delay_seconds:
            time.sleep(FakeAPI.delay_seconds)
        if FakeAPI.error_status:
            status = FakeAPI.error_status
            body = json.dumps({'error':'request rejected', 'authorization':self.headers.get('Authorization','')}).encode()
            self.send_response(status); self.send_header('Content-Length',str(len(body))); self.end_headers()
            self.wfile.write(body); return
        text = json.dumps(OUTPUT, ensure_ascii=False)
        if self.path.endswith('/v1/messages'):
            data = {'content':[{'type':'server_tool_use','name':'web_search'},
                               {'type':'web_search_tool_result','content':[{'type':'web_search_result','url':'https://example.test'}]},
                               {'type':'text','text':text}],
                    'usage':{'input_tokens':10,'output_tokens':20}}
        elif self.path.endswith('/responses'):
            data = {'output':[{'type':'web_search_call','status':'completed'},
                              {'type':'message','content':[{'type':'output_text','text':text}]}],
                    'usage':{'input_tokens':10,'output_tokens':20}}
        else:
            data = {'choices':[{'message':{'content':text}}], 'usage':{'total_tokens':30}}
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header('Content-Type','application/json')
        self.send_header('x-request-id','fixture-request')
        self.send_header('Content-Length',str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass


class ProviderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1',0), FakeAPI)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True); cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join()

    def call(self, adapter, stage='human'):
        suffix = {'anthropic':'', 'volcengine_ark':'/api/v3', 'openai_compatible':'/v1'}[adapter]
        profile = {'id':adapter,'label':adapter,'adapter':adapter,'base_url':self.base+suffix,
                   'model':'fixture-model','native_search':adapter!='openai_compatible',
                   'strict_json_schema':adapter!='openai_compatible','max_output_tokens':1000,
                   'supports_reasoning_effort':True}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            request = providers.GenerationRequest(profile=profile,api_key='secret-fixture-key',stage=stage,
                prompt='return json',schema=SCHEMA,timeout=5,reasoning_effort='high',
                raw_path=root/'result.json',event_path=root/'events.jsonl',workspace=root,environment=os.environ.copy())
            result = providers.generate(request)
            events = request.event_path.read_text()
        self.assertEqual(result.output, OUTPUT)
        self.assertNotIn('secret-fixture-key', events)
        return result

    def call_error(self, status):
        FakeAPI.error_status = status
        try:
            with self.assertRaises(providers.ProviderError) as raised:
                self.call('openai_compatible')
        finally:
            FakeAPI.error_status = 0
        self.assertNotIn('secret-fixture-key', str(raised.exception))
        return raised.exception

    def test_openai_compatible_json(self):
        result = self.call('openai_compatible')
        self.assertEqual(result.metadata['request_id'], 'fixture-request')
        self.assertEqual(FakeAPI.last_headers['Authorization'], 'Bearer secret-fixture-key')
        self.assertEqual(FakeAPI.calls[-1][1]['reasoning_effort'], 'high')

    def test_anthropic_structured_output_and_native_search(self):
        result = self.call('anthropic','rewrite')
        self.assertTrue(result.metadata['search_used'])
        path, body = FakeAPI.calls[-1]
        self.assertEqual(path, '/v1/messages')
        self.assertEqual(body['output_config']['format']['type'], 'json_schema')
        self.assertEqual(body['thinking']['type'], 'adaptive')
        self.assertEqual(body['tools'][0]['name'], 'web_search')

    def test_doubao_responses_and_native_search(self):
        result = self.call('volcengine_ark','rewrite')
        self.assertTrue(result.metadata['search_used'])
        path, body = FakeAPI.calls[-1]
        self.assertEqual(path, '/api/v3/responses')
        self.assertEqual(body['text']['format']['type'], 'json_schema')
        self.assertEqual(body['reasoning_effort'], 'high')

    def test_missing_search_evidence_fails(self):
        profile = {'id':'deepseek','label':'DeepSeek','adapter':'openai_compatible','base_url':self.base+'/v1',
                   'model':'fixture','native_search':False,'strict_json_schema':False,'max_output_tokens':1000}
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            request=providers.GenerationRequest(profile=profile,api_key='key',stage='rewrite',prompt='json',schema=SCHEMA,
                timeout=5,reasoning_effort='',raw_path=root/'result',event_path=root/'events',workspace=root,environment={})
            with self.assertRaisesRegex(providers.ProviderError,'联网搜索记录'):
                providers.generate(request)

    def test_failed_search_marker_is_not_accepted(self):
        self.assertFalse(providers._contains_search({'type':'web_search_tool_result_error','error':'timeout'}))
        self.assertFalse(providers._contains_search({'type':'web_search_call','status':'failed'}))
        self.assertTrue(providers._contains_search({'type':'web_search_call','status':'completed'}))

    def test_auth_error_is_terminal_and_redacted(self):
        self.assertFalse(self.call_error(401).retryable)

    def test_rate_limit_and_server_errors_are_retryable(self):
        self.assertTrue(self.call_error(429).retryable)
        self.assertTrue(self.call_error(503).retryable)

    def test_api_timeout_is_retryable(self):
        profile = {'id':'slow','label':'Slow API','adapter':'openai_compatible','base_url':self.base+'/v1',
                   'model':'fixture','native_search':False,'strict_json_schema':False,'max_output_tokens':1000}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            request = providers.GenerationRequest(profile=profile,api_key='key',stage='human',prompt='json',schema=SCHEMA,
                timeout=.05,reasoning_effort='',raw_path=root/'result',event_path=root/'events',workspace=root,environment={})
            FakeAPI.delay_seconds = .2
            try:
                with self.assertRaises(providers.ProviderError) as raised:
                    providers.generate(request)
            finally:
                FakeAPI.delay_seconds = 0
            self.assertTrue(raised.exception.retryable)

    def test_secret_file_must_be_private(self):
        with tempfile.TemporaryDirectory() as folder:
            secret = Path(folder) / 'secrets.local.toml'
            secret.write_text('[secrets]\nfixture = "top-secret"\n', encoding='utf-8')
            secret.chmod(0o644)
            with patch.object(config, 'SECRETS', secret), self.assertRaisesRegex(ValueError, '权限过宽'):
                config.load_secrets()
            secret.chmod(0o600)
            with patch.object(config, 'SECRETS', secret):
                self.assertEqual(config.load_secrets()['fixture'], 'top-secret')


if __name__ == '__main__':
    unittest.main()
