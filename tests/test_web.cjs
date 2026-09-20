// Headless browser acceptance; fake model, real HTTP server and background engine.
const { chromium } = require('playwright');
const { spawn, execFileSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const net = require('node:net');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..');

(async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'wechat-ui-'));
  const reserve = net.createServer();
  await new Promise(resolve => reserve.listen(0, '127.0.0.1', resolve));
  const port = reserve.address().port;
  await new Promise(resolve => reserve.close(resolve));
  const env = {...process.env, WECHAT_WEB_PORT:String(port), WECHAT_OUTPUT_DIR:path.join(tmp,'output'),
    PATH:path.join(root,'tests/fake_bin')+':'+process.env.PATH, FAKE_CODEX_DELAY:'.4'};
  const server = spawn('python3',[path.join(root,'web/server.py')],{env,stdio:['ignore','pipe','pipe']});
  const base = 'http://127.0.0.1:'+port;
  let browser;
  try {
    await new Promise((resolve,reject) => { server.stdout.once('data',resolve); server.once('error',reject); });
    browser = await chromium.launch({headless:true,channel:'chrome'});
    const desktop = await browser.newContext({permissions:['clipboard-read','clipboard-write']});
    const mobile = await browser.newContext({viewport:{width:390,height:844},permissions:['clipboard-read','clipboard-write']});
    const first = await desktop.newPage(), second = await mobile.newPage();
    const errors=[];
    for (const page of [first,second]) page.on('pageerror', error=>errors.push(error.message));
    await Promise.all([first.goto(base),second.goto(base)]);
    for (const [page,mode,topic] of [[first,'数码','网页甲主题'],[second,'汽车','手机乙主题']]) {
            await page.locator('#submit').waitFor({state:'visible'});
      await page.locator('#topic').fill(topic);
      await page.locator('#source').fill('跨主题参考原文。\n第二段保留换行和“引号”。');
      await page.locator('#brief').fill('保持我支持够用的立场。');
      await page.locator('#positioning').fill('普通用户，直白声音');
      await page.locator('#mode').selectOption(mode);
      assert.equal(await page.locator('#title_count').inputValue(),'40');
      await page.locator('#model').selectOption('gpt-5.6-terra');
      await page.locator('#reasoning_effort').selectOption('medium');
    }
    assert.equal(await first.locator('#topic').inputValue(),'网页甲主题');
    assert.equal(await second.locator('#topic').inputValue(),'手机乙主题');
    assert.match(await first.locator('#source-paths').textContent(),/数码\.txt/);
    assert.match(await second.locator('#source-paths').textContent(),/汽车\.txt/);
    const response = first.waitForResponse(r=>r.url()===base+'/api/jobs' && r.request().method()==='POST');
    await first.locator('#submit').click();
    const submitted=await (await response).json();
    await first.close(); // close the submitting page before completion
    const response2=second.waitForResponse(r=>r.url()===base+'/api/jobs' && r.request().method()==='POST');
    await second.locator('#submit').click();
    const submitted2=await (await response2).json();
    assert.notEqual(submitted.id,submitted2.id);
    await second.waitForFunction(()=>document.getElementById('job-status').textContent.includes('已完成'),{},{timeout:30000});
    assert.equal(await second.locator('.title-option').count(),40);
    assert.doesNotMatch(await second.locator('#path').textContent(),/Obsidian/);
    await second.waitForFunction(()=>document.getElementById('image-status').textContent.includes('没有合格候选'),{},{timeout:30000});
    assert.equal(await second.locator('#download-images').isDisabled(),true);
    await second.locator('#search-images').click();
    await second.waitForFunction(()=>document.getElementById('image-status').textContent.includes('没有合格候选'),{},{timeout:30000});
    await second.locator('.title-option').nth(39).click();
    await second.waitForFunction(()=>document.getElementById('status').textContent.includes('标题选择已保存'));
    const read = JSON.parse(execFileSync('bash',[path.join(root,'wechat.sh'),'show',submitted2.id],{env,encoding:'utf8'}));
    assert.equal(read.selected_index,39);
    await second.locator('#copy-body').click();
    assert.equal(await second.evaluate(()=>navigator.clipboard.readText()),read.final_body);
    await second.locator('#copy-final').click();
    assert.equal(await second.evaluate(()=>navigator.clipboard.readText()),read.final_content);
    await second.reload();
    await second.waitForFunction(()=>document.getElementById('selected-title').value.includes('测试标题40'));
    assert.equal(await second.locator('#topic').inputValue(),'手机乙主题');
    const reopened = await desktop.newPage();
    await reopened.goto(base);
    await reopened.locator('#jobs button').filter({hasText:'网页甲主题'}).click();
    await reopened.waitForFunction(()=>document.getElementById('job-status').textContent.includes('已完成'));
    assert.equal(await reopened.locator('.title-option').count(),40);
    // Fixture-only image selection: previews are remote, originals never requested before selection.
    const jobDir=path.join(tmp,'output/jobs',submitted2.id), imageStatePath=path.join(jobDir,'images.json');
    while (['queued','searching'].includes(JSON.parse(fs.readFileSync(imageStatePath)).status)) await new Promise(r=>setTimeout(r,100));
    const imageId='a'.repeat(20), png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=','base64');
    const picture={id:imageId,title:'测试场景示意图',kind:'illustration',url:'https://fixture.example/original.png',thumbnailUrl:'https://fixture.example/thumbnail.png',sourcePageUrl:'https://fixture.example/source',author:'测试作者',width:800,height:600,license:'CC BY 4.0',licenseUrl:'https://creativecommons.org/licenses/by/4.0/',attributionLine:'测试作者 · CC BY 4.0',attributionRequired:true,relevance:'仅测试，不是产品实拍'};
    let imgState={status:'ready',candidates:[picture],selected_ids:[],downloads:{},error:'',provider_errors:[]};
    const saveImages=()=>{fs.writeFileSync(imageStatePath+'.tmp',JSON.stringify(imgState));fs.renameSync(imageStatePath+'.tmp',imageStatePath);};
    saveImages();
    const requested=[];
    await second.route('https://fixture.example/**',async route=>{requested.push(route.request().url());await route.fulfill({contentType:'image/png',body:png});});
    await second.reload();
    await second.locator('.image-card input').waitFor();
    assert.equal(requested.filter(url=>url.includes('original')).length,0);
    assert.equal(fs.existsSync(path.join(jobDir,'images')),false);
    await second.locator('.image-card input').check();
    await second.reload();
    await second.locator('.image-card input:checked').waitFor();
    assert.equal(await reopened.locator('.image-card').count(),0);
    await second.route(base+'/api/jobs/'+submitted2.id+'/images/download',async route=>{
      assert.deepEqual(route.request().postDataJSON(),{ids:[imageId]});
      imgState.status='downloading'; saveImages();
      await route.fulfill({status:202,contentType:'application/json',body:JSON.stringify({ok:true,images:imgState})});
      // Simulated completed download; real HTTP decoding is covered in test_images.py.
      fs.mkdirSync(path.join(jobDir,'images'),{recursive:true});
      const file=path.join(jobDir,'images',imageId+'.png');fs.writeFileSync(file,png);
      imgState.status='completed';imgState.downloads[imageId]={status:'completed',filename:imageId+'.png',path:file,mime:'image/png',attributionLine:picture.attributionLine};saveImages();
    });
    await second.locator('#download-images').click();
    await second.waitForFunction(()=>document.querySelector('.image-card img')?.getAttribute('src')?.startsWith('/api/jobs/'));
    await second.unroute(base+'/api/jobs/'+submitted2.id+'/images/download');
    await second.locator('.image-card button').click();
    assert.equal(await second.evaluate(()=>navigator.clipboard.readText()),picture.attributionLine);
    await second.locator('#download-images').click();
    assert.equal(fs.readdirSync(path.join(jobDir,'images')).length,1);
    await second.reload();
    await second.waitForFunction(()=>document.querySelector('.image-card img')?.getAttribute('src')?.startsWith('/api/jobs/'));
    assert.equal(await second.locator('#final-content').inputValue(),read.final_body);
    assert.ok(await second.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    assert.deepEqual(errors,[]);
    fs.mkdirSync(path.join(root,'output','verification'),{recursive:true});
    await second.screenshot({path:path.join(root,'output','verification','网页v2_手机验收.png'),fullPage:false});
    console.log('PASS: 两窗口隔离 / 提交后关页 / 40标题 / 手机布局 / 标题同步终端 / 两种复制 / 刷新恢复 / 无同步库写入 / 候选勾选与本机显示 / 署名复制 / 重复点击');
  } finally {
    if(browser) await browser.close();
    server.kill('SIGTERM');
    await new Promise(resolve => server.exitCode!==null ? resolve() : server.once('exit',resolve));
    // 隔离测试目录含验证证据，不自动删除，方便失败排查。
    console.log('UI测试现场：'+tmp);
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
