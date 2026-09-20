"""Offline picture-state/provenance/download regressions; no real original images fetched."""
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch, Mock
from urllib.request import Request, urlopen
from urllib.error import HTTPError
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pipeline as p
import image_workflow as im
from web.server import AppHandler, ThreadingHTTPServer
from PIL import Image


class ImageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ,{'WECHAT_OUTPUT_DIR':self.temp.name})
        self.env.start()
        self.id = p.create_job({'topic':'手表','source':'参考原文','mode':'数码'})
        self.path = p.job_dir(self.id)
        p.update_job(self.path,status='completed')
        self.item = {'id':'a'*20,'url':'https://upload.wikimedia.org/example.png','attributionLine':'作者 · CC BY 4.0'}
        im.update(self.path,status='ready',candidates=[self.item])

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_selection_validation_idempotence_and_isolation(self):
        for ids in ([],['https://example.com/a.jpg'],['b'*20],None):
            with self.assertRaises(ValueError): im.start_download(self.id,ids)
        (self.path/'images').mkdir()
        (self.path/'images/test.png').write_bytes(b'test fixture')
        im.update(self.path,downloads={self.item['id']:{'status':'completed','filename':'test.png'}})
        with patch('subprocess.Popen') as launch:
            self.assertEqual(im.start_download(self.id,[self.item['id']])['status'],'completed')
            launch.assert_not_called()
        self.assertEqual(len(list((self.path/'images').iterdir())),1)
        other = p.create_job({'topic':'另一篇','source':'原文','mode':'汽车'})
        p.update_job(p.job_dir(other),status='completed')
        with self.assertRaises(ValueError): im.start_download(other,[self.item['id']])
        with self.assertRaises(ValueError): im.downloaded_file(self.id,'../input.json')

    def test_strict_license_proof_dedup_and_provider_errors(self):
        self.assertEqual(im.normalize_license_url('http://creativecommons.org/publicdomain/zero/1.0/deed.en'),'https://creativecommons.org/publicdomain/zero/1.0/')
        self.assertEqual(im.normalize_license_url('https://creativecommons.org/licenses/by-nc/4.0/deed.zh'),'')
        self.assertEqual(im.normalize_license_url('https://creativecommons.org.evil.example/licenses/by/4.0'),'')
        url='https://upload.wikimedia.org/a.jpg'
        source='https://commons.wikimedia.org/wiki/File:A.jpg'
        item={**self.item,'candidateId':self.item['id'],'url':url,'title':'血压测量示意','sourcePageUrl':source,'mime':'image/jpeg'}
        records=[{'name':'search_images','args':{'query':q},'result':{'structuredContent':{'results':[item],'providerReports':[{'provider':'openverse','ok':False,'error':'timeout'}]}}} for q in ['a','b','c']]
        selected=[{'id':self.item['id'],'kind':'illustration','reason':'正文涉及血压管理，不代表新型号'}]*2
        self.assertEqual(im.licensed_candidates(records,selected)[0],[])
        proof={'url':url,'width':800,'height':600,'extmetadata':{'LicenseUrl':{'value':'https://creativecommons.org/licenses/by-sa/3.0'},'LicenseShortName':{'value':'CC BY-SA 3.0'},'Artist':{'value':'<a>作者</a>'}}}
        records.append({'name':'fetch_with_license','args':{'url':source},'result':{'structuredContent':{'fileLicenseEvidence':proof}}})
        accepted,errors=im.licensed_candidates(records,selected)
        self.assertEqual(len(accepted),1)
        self.assertIn('CC BY-SA 3.0',accepted[0]['attributionLine'])
        self.assertNotIn('4.0',accepted[0]['attributionLine'])
        self.assertEqual(errors,['openverse: timeout'])
        proof['extmetadata']['LicenseUrl']['value']='https://creativecommons.org/licenses/by-nc/4.0'
        self.assertEqual(im.licensed_candidates(records,selected)[0],[])
        with self.assertRaises(ValueError): im.licensed_candidates(records[:1],selected)

    def test_download_content_validation_and_http_image(self):
        buffer=io.BytesIO()
        Image.new('RGB',(12,12),'blue').save(buffer,format='PNG')
        data=buffer.getvalue()
        class Response(io.BytesIO):
            headers=Mock()
        def response(payload=data):
            r=Response(payload)
            r.headers.get_content_type.return_value='image/png'
            r.headers.get.return_value='0'
            return r
        opener=Mock()
        opener.open.side_effect=lambda *a,**k: response()
        with patch.object(im,'public_url'),patch.object(im.p,'writing_environment',return_value={}),patch('urllib.request.build_opener',return_value=opener):
            result=im.download_one(self.path,self.item)
            self.assertEqual(Path(result['path']).read_bytes(),data)
            opener.open.side_effect=lambda *a,**k:response(b'<html>not an image</html>')
            with self.assertRaises(Exception): im.download_one(self.path,self.item)
        im.update(self.path,downloads={self.item['id']:result},status='completed')
        server=ThreadingHTTPServer(('127.0.0.1',0),AppHandler)
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        base=f'http://127.0.0.1:{server.server_port}/api/jobs/{self.id}/images/'
        try:
            with urlopen(base+self.item['id']) as r:
                self.assertEqual(r.read(),data)
                self.assertEqual(r.headers.get_content_type(),'image/png')
            with self.assertRaises(HTTPError): urlopen(base+'b'*20)
            with patch.object(im,'start_search',return_value={'status':'queued'}):
                req=Request(base+'search',data=b'{}',headers={'Content-Type':'application/json'})
                with urlopen(req) as r: self.assertEqual(json.load(r)['images']['status'],'queued')
            req=Request(base+'download',data=json.dumps({'ids':[self.item['id']]}).encode(),headers={'Content-Type':'application/json'})
            with urlopen(req) as r: self.assertEqual(json.load(r)['images']['status'],'completed')
        finally:
            server.shutdown();server.server_close();thread.join()

    def test_partial_failure_retry_and_stale_worker_do_not_damage_article(self):
        article=self.path/'3_人味终稿.md'; article.write_text('保留正文')
        with patch.object(im,'download_one',side_effect=ValueError('来源连接失败')):
            im.download_worker(self.path,[self.item['id']])
        self.assertEqual(im.state_of(self.path)['status'],'partial')
        self.assertEqual(article.read_text(),'保留正文')
        im.update(self.path,status='searching',worker_pid=99999999)
        self.assertEqual(im.state_of(self.path)['status'],'interrupted')
        self.assertEqual(p.state_of(self.id)['status'],'completed')
        with patch.object(im,'download_one',return_value={'status':'completed'}):
            im.download_worker(self.path,[self.item['id']])
        self.assertEqual(im.state_of(self.path)['status'],'completed')

    def test_public_download_guard_and_agent_tool_scope(self):
        for url in ('http://example.com/a','https://127.0.0.1/a','https://[::1]/a','file:///etc/passwd','https://localhost/a'):
            with self.assertRaises(ValueError): im.public_url(url)
        command=im.image_command({'effective_model':'gpt-5.6-sol','effective_effort':'high'},self.path/'raw',self.path/'schema',self.path/'workspace',self.path/'evidence')
        self.assertIn('features.shell_tool=false',command)
        self.assertIn('mcp_servers.picture2.enabled=true',command)
        self.assertIn('web_search="disabled"',command)
        self.assertIn('features.multi_agent=false',command)


if __name__=='__main__': unittest.main(verbosity=2)
