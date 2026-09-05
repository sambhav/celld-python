import { loadPyodide } from './pyodide.mjs';
import createPyodideModule from './pyodide.asm.mjs';
import { apps, sdk } from './manifest.js';

const MAX_BYTES = 1024 * 1024;
const RECEIPT_TTL = 24 * 60 * 60 * 1000;
const MAX_RECEIPTS = 4096;
const STATELESS_SLOTS = 16; // Stable across deployments: retries must find the same receipt.
const decoder = new TextDecoder();
const encoder = new TextEncoder();
const decode = value => Uint8Array.from(atob(value), c => c.charCodeAt(0));
function encode(value) {
  let result = '';
  for (let i=0;i<value.length;i+=8192) result += String.fromCharCode(...value.subarray(i,i+8192));
  return btoa(result);
}
async function hash(value) {
  return [...new Uint8Array(await crypto.subtle.digest('SHA-256', encoder.encode(value)))].map(x=>x.toString(16).padStart(2,'0')).join('');
}
function error(code, message, status=400) {
  return Response.json({error:{code,message}}, {status});
}
async function readBody(request) {
  if (!request.body) return new Uint8Array();
  const reader=request.body.getReader(), chunks=[];
  let size=0;
  try {
    while (true) {
      const {done,value}=await reader.read();
      if (done) break;
      size+=value.byteLength;
      if(size>MAX_BYTES) {await reader.cancel();throw new Error('Body exceeds 1 MiB');}
      chunks.push(value);
    }
  } finally {reader.releaseLock();}
  const bytes=new Uint8Array(size);
  let offset=0;
  for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.length;}
  return bytes;
}
function select(request) {
  const url=new URL(request.url);
  const app=apps.find(a=>a.mount==='/' || url.pathname===a.mount || url.pathname.startsWith(a.mount+'/'));
  if(!app) return null;
  const path=app.mount==='/'?url.pathname:url.pathname.slice(app.mount.length)||'/';
  return {app,url,path};
}
function match(app,method,path) {
  for(const route of app.routes){
    const found=new RegExp(route.pattern).exec(path);
    if(route.method===method && found) return {route,match:found};
  }
  return null;
}
async function boot(app) {
  const py=await loadPyodide({indexURL:'https://celld-python.invalid/runtime/',
    packageBaseUrl:`https://celld-python.invalid/apps/${app.name}/`,lockFileContents:app.lock,
    packages:app.packages,createPyodideModule,enableRunUntilComplete:false,
    stdout:line=>console.log(`[${app.name}] ${line}`),stderr:line=>console.error(`[${app.name}] ${line}`)});
  for(const [name,source] of Object.entries({...sdk,...app.sources})){
    const path='/app/'+name;py.FS.mkdirTree(path.slice(0,path.lastIndexOf('/')));py.FS.writeFile(path,source);
  }
  py.runPython("import sys; sys.path.insert(0,'/app')");
  const module=py.pyimport('celld_python');const worker=module.load_worker(app.entrypoint);module.destroy();
  return {py,worker};
}
function response(result) {
  return new Response([204,205,304].includes(result.status)?null:decode(result.body), {status:result.status,headers:result.headers});
}
function failure(exception) {
  console.error(exception.stack||String(exception));
  if(exception.message==='Body exceeds 1 MiB') return error('payload_too_large',exception.message,413);
  return error('execution_error','Worker execution failed',500);
}

// Each scope/app/key (or stateless slot) owns a Python interpreter and a cell.
// celld manages the underlying V8 isolate pool; cells can share an isolate.
export class PythonCell {
  constructor(ctx){this.ctx=ctx;this.runtime=null;this.initialized=false;}
  fetch(request){return this.ctx.blockConcurrencyWhile(()=>this.execute(request)).catch(failure);}
  initialize(){
    if(this.initialized)return;
    this.ctx.storage.sql.exec('CREATE TABLE IF NOT EXISTS _python_state (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL)');
    this.ctx.storage.sql.exec('CREATE TABLE IF NOT EXISTS _python_calls (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, reply TEXT NOT NULL, expires_at INTEGER NOT NULL)');
    this.ctx.storage.sql.exec('CREATE INDEX IF NOT EXISTS _python_calls_expiry ON _python_calls(expires_at)');
    this.initialized=true;
  }
  async execute(request){
    const selected=select(request);
    if(!selected)return error('not_found','Unknown app',404);
    const bytes=await readBody(request);
    const callId=request.headers.get('x-celld-call-id');
    this.initialize();
    const fingerprint=await hash(JSON.stringify([request.method,selected.path,selected.url.search,encode(bytes),
      request.headers.get('x-celld-context')||'{}',request.headers.get('x-celld-client')||'{}',request.headers.get('x-celld-host')||'{}']));
    const now=Date.now();
    const receipt=this.ctx.storage.sql.exec('SELECT fingerprint, reply, expires_at FROM _python_calls WHERE id=?',callId).toArray()[0];
    if(receipt && receipt.expires_at>now){
      if(receipt.fingerprint!==fingerprint)return error('idempotency_conflict','Call ID was reused with different arguments or context',409);
      const result=JSON.parse(receipt.reply);result.headers.push(['x-celld-replayed','true']);return response(result);
    }
    // Never evict a live receipt to make room: that could duplicate a committed call.
    const count=this.ctx.storage.sql.exec('SELECT count(*) AS n FROM _python_calls WHERE expires_at>?',now).toArray()[0].n;
    if(count>=MAX_RECEIPTS)return error('capacity_exceeded','This worker has reached its retained-call limit',429);
    this.runtime??=boot(selected.app).catch(exception=>{this.runtime=null;throw exception;});
    const runtime=await this.runtime;
    const before=this.ctx.storage.sql.exec('SELECT value FROM _python_state WHERE id=1').toArray()[0]?.value??null;
    const payload=JSON.stringify({method:request.method,path:selected.path,query:selected.url.search.slice(1),headers:[...request.headers],body:encode(bytes)});
    const result=JSON.parse(await runtime.worker.handle_wire(payload,before===null?undefined:before));
    if(encoder.encode(result.state||'').length>MAX_BYTES || result.body.length>Math.ceil(MAX_BYTES*4/3))
      return error('payload_too_large','Result or state exceeds 1 MiB',413);
    // Commit state and receipt in one SQLite transaction. celld's output gate
    // keeps the response behind durable replication. External effects still
    // need their own idempotency key if a crash precedes this transaction.
    this.ctx.storage.transactionSync(()=>{
      if(result.state!==null && result.state!==before)
        this.ctx.storage.sql.exec('INSERT INTO _python_state(id,value) VALUES(1,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value',result.state);
      const reply={status:result.status,headers:result.headers,body:result.body};
      this.ctx.storage.sql.exec('DELETE FROM _python_calls WHERE expires_at<=?',now);
      this.ctx.storage.sql.exec('INSERT INTO _python_calls(id,fingerprint,reply,expires_at) VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET fingerprint=excluded.fingerprint,reply=excluded.reply,expires_at=excluded.expires_at',callId,fingerprint,JSON.stringify(reply),now+RECEIPT_TTL);
    });
    return response(result);
  }
}

// Platform policy is intentionally outside this module. The optional resolver
// can reject with a Response, or supply a stable scope and trusted attributes.
export function createHandler({resolveContext=async()=>({})}={}) { return {
  async fetch(request,env){
    try {
      const selected=select(request);
      if(!selected)return error('not_found','Unknown app',404);
      const host=await resolveContext(request,env,{app:selected.app.name,function:selected.path.slice(1)});
      if(host instanceof Response)return host;
      const scopeName=host.scope??'default';
      if(typeof scopeName!=='string' || !scopeName || scopeName.length>512)throw new Error('Invalid host scope');
      if(selected.path==='/__celld/schema' && request.method==='GET')return Response.json(selected.app.schema);
      const found=match(selected.app,request.method,selected.path);
      if(!found){
        const exists=selected.app.routes.some(r=>new RegExp(r.pattern).test(selected.path));
        return error(exists?'method_not_allowed':'not_found',exists?'Method not allowed':'Unknown function',exists?405:404);
      }
      const callId=request.headers.get('x-celld-call-id')||crypto.randomUUID();
      if(!/^[a-zA-Z0-9_-]{16,128}$/.test(callId))return error('invalid_call_id','Call IDs must have 16–128 URL-safe characters');
      const headers=new Headers(request.headers);
      headers.delete('authorization');
      headers.set('x-celld-scope',scopeName);
      headers.set('x-celld-host',JSON.stringify(host.attributes??{}));
      headers.set('x-celld-context',headers.get('x-celld-context')||headers.get('x-celld-metadata')||'{}');
      headers.delete('x-celld-metadata');
      headers.set('x-celld-call-id',callId);
      headers.set('x-celld-request-id',request.headers.get('x-celld-request-id')||callId);
      if((headers.get('x-celld-request-id')||'').length>128 || (headers.get('x-celld-client')||'').length>4096 || (headers.get('x-celld-context')||'').length>16384)
        return error('context_too_large','Context or client information exceeds its limit',413);
      const bytes=await readBody(request);
      let scope;
      if(found?.route.state_key){
        const {route,match}=found;let key;
        if(route.operation){
          let args;try{args=JSON.parse(decoder.decode(bytes));}catch{return error('validation_error','Arguments must be an object',422);}
          key=args?.[route.state_key];
          if(typeof key!=='string')return error('validation_error',`${route.state_key} must be a string`,422);
        }else key=decodeURIComponent(match[route.names.indexOf(route.state_key)+1]);
        if(!key || key.length>512)return error('validation_error','State keys must have 1–512 characters',422);
        scope=['state',route.namespace,key];
      }else{
        let slot=0;for(const ch of callId)slot=(slot*31+ch.charCodeAt(0))%STATELESS_SLOTS;
        scope=['stateless',slot];
      }
      const identity=JSON.stringify([scopeName,selected.app.name,...scope]);
      const forwarded=new Request(request.url,{method:request.method,headers,body:['GET','HEAD'].includes(request.method)?undefined:bytes});
      return env.PYTHON_CELLS.get(env.PYTHON_CELLS.idFromName(identity)).fetch(forwarded);
    }catch(exception){return failure(exception);}
  }
}; }

export default createHandler();
