#!/usr/bin/env python3
"""本机网页 v2：HTTP 只提交/查看任务，关闭网页不终止写作。"""
from __future__ import annotations
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import os
import sys
from urllib.parse import urlsplit

PIPELINE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE_DIR))
import pipeline
import image_workflow
MAX_BODY_BYTES = 1_000_000


class AppHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass  # 客户端断开，后台任务已独立启动。

    def safe_request(self):
        if urlsplit('http://' + self.headers.get('Host', '')).hostname not in {'127.0.0.1', 'localhost', '::1'}:
            raise ValueError('请使用 localhost 或127.0.0.1访问。')
        origin = self.headers.get('Origin')
        if origin and urlsplit(origin).netloc != self.headers.get('Host'):
            raise ValueError('不接受来自其他网页的写作请求。')

    def do_GET(self):
        try:
            self.safe_request()
            route = urlsplit(self.path).path
            if route in ('/', '/index.html'):
                body = (PIPELINE_DIR / 'web' / 'index.html').read_bytes()
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(body)
            elif route == '/api/options':
                self.send_json(200, {'ok': True, **pipeline.options()})
            elif route == '/api/jobs':
                self.send_json(200, {'ok': True, 'jobs': pipeline.list_jobs()})
            elif len(route.split('/')) == 6 and route.split('/')[4] == 'images':
                file, mime = image_workflow.downloaded_file(route.split('/')[3], route.split('/')[5])
                body = file.read_bytes()
                self.send_response(200)
                self.send_header('Content-Type', mime)
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'private, max-age=3600')
                self.end_headers()
                self.wfile.write(body)
            elif route.startswith('/api/jobs/'):
                self.send_json(200, {'ok': True, **pipeline.job_result(route.removeprefix('/api/jobs/'))})
            else:
                self.send_json(404, {'ok': False, 'error': '接口不存在。'})
        except (OSError, ValueError) as error:
            self.send_json(400, {'ok': False, 'error': str(error)})

    def do_POST(self):
        try:
            self.safe_request()
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= MAX_BODY_BYTES:
                raise ValueError('提交内容为空或超过1MB。')
            if self.headers.get_content_type() != 'application/json':
                raise ValueError('请提交 JSON。')
            payload = json.loads(self.rfile.read(size).decode('utf-8'))
            if not isinstance(payload, dict):
                raise ValueError('输入必须是JSON对象。')
            route = urlsplit(self.path).path
            if route in ('/api/jobs', '/api/generate'):
                state = pipeline.submit_job(payload)
                if route == '/api/generate':
                    # 保留旧同步接口；客户端断线不停止任务。
                    state = pipeline.wait_job(state['id'])
                    if state['status'] != 'completed':
                        self.send_json(500, {'ok': False, **state})
                        return
                    state = pipeline.job_result(state['id'])
                self.send_json(202 if route == '/api/jobs' else 200, {'ok': True, **state})
            elif len(route.split('/')) == 6 and route.split('/')[4] == 'images':
                job_id, action = route.split('/')[3], route.split('/')[5]
                if action == 'search':
                    state = image_workflow.start_search(job_id)
                elif action == 'download':
                    state = image_workflow.start_download(job_id, payload.get('ids'))
                else:
                    raise ValueError('图片操作不存在。')
                self.send_json(202, {'ok':True, 'images':state})
            elif route.startswith('/api/jobs/') and route.endswith('/select'):
                self.send_json(200, {'ok': True, **pipeline.select_title(route.split('/')[3], payload.get('index'))})
            elif route.startswith('/api/jobs/') and route.endswith('/retry'):
                self.send_json(202, {'ok': True, **pipeline.start_job(route.split('/')[3])})
            else:
                self.send_json(404, {'ok': False, 'error': '接口不存在。'})
        except (OSError, ValueError) as error:
            self.send_json(400, {'ok': False, 'error': str(error)})


def main():
    port = int(os.environ.get('WECHAT_WEB_PORT', '8765'))
    if not 1 <= port <= 65535:
        raise SystemExit('端口必须为1–65535。')
    server = ThreadingHTTPServer(('127.0.0.1', port), AppHandler)
    server.daemon_threads = True
    print(f'公众号写作网页 v2：http://127.0.0.1:{port}', flush=True)
    print('关闭网页或页面服务不影响已提交任务；Mac须保持开机联网。', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
