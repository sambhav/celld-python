// Diagnostic controls: identical request/response content, no Python.
const MODE = '__MODE__';
const UPSTREAM = '__UPSTREAM_URL__';
async function hello(request) {
  const {name} = await request.json();
  if (UPSTREAM.startsWith('http')) {
    const response = await fetch(UPSTREAM,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({name})});
    if(!response.ok)throw new Error('upstream failed');
    const price = await response.json();
    if(price.customer!==name || price.stock<2)throw new Error('invalid upstream response');
    return Response.json({result:{customer:name,total_cents:price.unit_price_cents*2,currency:price.currency,trace:request.headers.get('x-celld-call-id')}});
  }
  return Response.json({result: `Hello, ${name}`});
}
export class BenchCell {
  constructor(ctx) { this.ctx = ctx; this.initialized = false; }
  fetch(request) { return this.ctx.blockConcurrencyWhile(async () => {
    if (MODE === 'write') {
      if (!this.initialized) {
        this.ctx.storage.sql.exec('CREATE TABLE marker (id INTEGER PRIMARY KEY, value TEXT)');
        this.initialized = true;
      }
      this.ctx.storage.sql.exec('INSERT INTO marker VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET value=excluded.value', request.headers.get('x-celld-call-id'));
    }
    return hello(request);
  }); }
}
export default {
  fetch(request, env) {
    if (MODE === 'stateless') return hello(request);
    let slot = 0;
    for (const c of request.headers.get('x-celld-call-id')) slot = (slot*31+c.charCodeAt(0))%16;
    return env.CELLS.getByName(String(slot)).fetch(request);
  }
};
