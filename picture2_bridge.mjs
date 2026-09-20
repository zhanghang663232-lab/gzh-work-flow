// Restricted picture2 MCP: search/metadata only. Never exposes downloading to the Agent.
import fs from 'node:fs';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import crypto from 'node:crypto';
import {spawnSync} from 'node:child_process';
const requestedCommand = process.env.PICTURE2_COMMAND || 'getwebfetch-mcp';
const locatedCommand = requestedCommand.includes('/')
  ? requestedCommand
  : spawnSync('which', [requestedCommand], {encoding:'utf8'}).stdout.trim();
if (!locatedCommand) throw new Error('未找到 getwebfetch-mcp；请安装 picture2 依赖或设置 PICTURE2_COMMAND。');
const executable = fs.realpathSync(locatedCommand);
const sdk = path.join(path.dirname(executable), '../node_modules/@modelcontextprotocol/sdk/dist/esm');
const load = name => import(pathToFileURL(path.join(sdk, name)).href);
const {Client} = await load('client/index.js');
const {StdioClientTransport} = await load('client/stdio.js');
const client = new Client({name:'wechat-picture2',version:'1.0'}, {capabilities:{}});
await client.connect(new StdioClientTransport({command:process.execPath,args:[executable],env:{...process.env,NODE_USE_ENV_PROXY:'1'},stderr:'inherit'}));
const allowed = new Set(['search_images','fetch_with_license']);
let queue = Promise.resolve(), nextAllowed = 0;
function call(name, args) {
  const pending = queue.then(async()=>{
    await new Promise(resolve=>setTimeout(resolve,Math.max(0,nextAllowed-Date.now())));
    try { return await performCall(name,args); }
    finally { nextAllowed=Date.now()+1500; }
  });
  queue=pending.catch(()=>{});
  return pending;
}
async function performCall(name, args) {
  if (!allowed.has(name)) throw new Error('Only picture2 search and license inspection are permitted');
  if (name === 'search_images') {
    const providers = (Array.isArray(args.providers) ? args.providers : ['wikimedia','openverse']).filter(p=>['wikimedia','openverse'].includes(p));
    args = {...args,safeSearch:'strict',licensePolicy:'open-only',providers:providers.length ? providers : ['wikimedia','openverse'],maxPerProvider:Math.min(args.maxPerProvider || 10,20),timeoutMs:45000};
  }
  if (name === 'fetch_with_license') {
    const page = new URL(args.url);
    if (page.protocol !== 'https:' || page.hostname !== 'commons.wikimedia.org' || !decodeURIComponent(page.pathname).startsWith('/wiki/File:') || page.username || page.password || page.port) {
      throw new Error('Only Wikimedia file source pages are supported for verifiable per-file licenses. Exclude other candidates.');
    }
    args = {...args,probe:false};
  }
  const result = await client.callTool({name,arguments:args},undefined,{timeout:120000});
  if (name === 'fetch_with_license') {
    // Wikimedia's file-specific API, not the footer license of the whole website.
    const page = new URL(args.url);
    if (page.hostname === 'commons.wikimedia.org' && decodeURIComponent(page.pathname).startsWith('/wiki/File:')) {
      const api = new URL('https://commons.wikimedia.org/w/api.php');
      api.search = new URLSearchParams({action:'query',format:'json',prop:'imageinfo',iiprop:'extmetadata|size|url',titles:decodeURIComponent(page.pathname.slice(6))});
      try {
        const response = await fetch(api,{signal:AbortSignal.timeout(45000)});
        if (!response.ok) throw new Error('source metadata HTTP '+response.status);
        const data = await response.json();
        const info = Object.values(data.query?.pages || {})[0]?.imageinfo?.[0];
        result.structuredContent = {...result.structuredContent,fileLicenseEvidence:info || null,evidenceUrl:api.href};
      } catch (error) { result.structuredContent = {...result.structuredContent,evidenceError:String(error)}; }
      result.content = [{type:'text',text:JSON.stringify(result.structuredContent)}];
    }
  }
  if (result.structuredContent?.results) {
    result.structuredContent.results = result.structuredContent.results.slice(0,5);
    result.structuredContent.resultCount = result.structuredContent.results.length;
    for (const item of result.structuredContent.results) item.candidateId = crypto.createHash('sha256').update(item.url).digest('hex').slice(0,20);
    result.content = [{type:'text',text:JSON.stringify(result.structuredContent)}];
  }
  if (process.env.PICTURE2_PROVENANCE) fs.appendFileSync(process.env.PICTURE2_PROVENANCE,JSON.stringify({time:new Date().toISOString(),name,args,result})+'\n');
  return result;
}
if (process.argv[2] === 'call') {
  try { process.stdout.write(JSON.stringify(await call(process.argv[3],JSON.parse(process.argv[4])))); }
  finally { await client.close(); }
} else {
  const {Server} = await load('server/index.js');
  const {StdioServerTransport} = await load('server/stdio.js');
  const {ListToolsRequestSchema,CallToolRequestSchema} = await load('types.js');
  const server = new Server({name:'picture2-search-only',version:'1.0'}, {capabilities:{tools:{}}});
  server.setRequestHandler(ListToolsRequestSchema,async()=>({tools:(await client.listTools()).tools.filter(t=>allowed.has(t.name)).map(t=>({...t,annotations:{...t.annotations,readOnlyHint:true,destructiveHint:false,idempotentHint:true,openWorldHint:true}}))}));
  server.setRequestHandler(CallToolRequestSchema,async req=>call(req.params.name,req.params.arguments || {}));
  await server.connect(new StdioServerTransport());
  process.stdin.on('end',()=>client.close().finally(()=>process.exit(0)));
}
