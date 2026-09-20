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
      await page.locator('#default_profile').selectOption('codex');
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
    assert.equal(await second.locator('#final-content').inputValue(),read.final_body);
    assert.ok(await second.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    assert.deepEqual(errors,[]);
    fs.mkdirSync(path.join(root,'output','verification'),{recursive:true});
    await second.screenshot({path:path.join(root,'output','verification','网页v2_手机验收.png'),fullPage:false});
    console.log('PASS: 两窗口隔离 / 提交后关页 / 40标题 / 手机布局 / 标题同步终端 / 两种复制 / 刷新恢复 / 无外部同步 / 模型配置');
  } finally {
    if(browser) await browser.close();
    server.kill('SIGTERM');
    await new Promise(resolve => server.exitCode!==null ? resolve() : server.once('exit',resolve));
    // 隔离测试目录含验证证据，不自动删除，方便失败排查。
    console.log('UI测试现场：'+tmp);
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
