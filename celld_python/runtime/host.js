import { loadPyodide } from './pyodide.mjs';
import createPyodideModule from './pyodide.asm.mjs';
import { apps, sdk } from './manifest.js';

const MAX_BODY = 1024 * 1024;
const MAX_STATE = 1024 * 1024;
const stateless = new Map();

function decode(value) {
  return Uint8Array.from(atob(value), c => c.charCodeAt(0));
}
function encode(value) {
  let result = '';
  for (let i = 0; i < value.length; i += 8192)
    result += String.fromCharCode(...value.subarray(i, i + 8192));
  return btoa(result);
}

async function readBody(request) {
  if (!request.body) return new Uint8Array();
  const reader = request.body.getReader();
  const chunks = [];
  let size = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > MAX_BODY) {
        await reader.cancel();
        throw new Error('Request body exceeds 1 MiB');
      }
      chunks.push(value);
    }
  } finally { reader.releaseLock(); }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
  return bytes;
}

function select(request) {
  const url = new URL(request.url);
  // Longest prefix wins; /one never matches /onerous.
  const app = apps.find(a => a.mount === '/' || url.pathname === a.mount || url.pathname.startsWith(a.mount + '/'));
  if (!app) return null;
  return { app, url, path: app.mount === '/' ? url.pathname : url.pathname.slice(app.mount.length) || '/' };
}

async function boot(app) {
  const py = await loadPyodide({
    indexURL: 'https://celld-python.invalid/runtime/',
    packageBaseUrl: `https://celld-python.invalid/apps/${app.name}/`,
    lockFileContents: app.lock,
    packages: app.packages,
    createPyodideModule,
    enableRunUntilComplete: false,
    stdout: line => console.log(`[${app.name}] ${line}`),
    stderr: line => console.error(`[${app.name}] ${line}`),
  });
  py.FS.mkdirTree('/app/celld_python');
  for (const [name, source] of Object.entries({ ...sdk, ...app.sources })) {
    const path = '/app/' + name;
    py.FS.mkdirTree(path.slice(0, path.lastIndexOf('/')));
    py.FS.writeFile(path, source);
  }
  py.runPython("import sys; sys.path.insert(0, '/app')");
  const [module, attribute] = app.entrypoint.split(':');
  const imported = py.pyimport(module);
  const worker = imported[attribute];
  imported.destroy();
  const routes = JSON.parse(worker.describe()).map(r => ({ ...r, regex: new RegExp(r.pattern) }));
  return { py, worker, routes };
}

function warm(app) {
  if (!stateless.has(app.name)) {
    const pending = boot(app).catch(error => { stateless.delete(app.name); throw error; });
    stateless.set(app.name, pending);
  }
  return stateless.get(app.name);
}

function match(runtime, method, path) {
  for (const route of runtime.routes) {
    const match = route.regex.exec(path);
    if (route.method === method && match) return { route, match };
  }
  return null;
}

async function invoke(runtime, request, selected, state = null) {
  const body = await readBody(request);
  const payload = JSON.stringify({ method: request.method, path: selected.path,
    query: selected.url.search.slice(1), headers: [...request.headers], body: encode(body) });
  // JS undefined maps to Python None across supported Pyodide versions.
  return JSON.parse(await runtime.worker.handle_wire(payload, state === null ? undefined : state));
}

function response(result) {
  return new Response([204, 205, 304].includes(result.status) ? null : decode(result.body), {
    status: result.status, headers: result.headers,
  });
}

function failure(error) {
  console.error(error.stack || String(error));
  return Response.json({ detail: error.message === 'Request body exceeds 1 MiB' ? error.message : 'Worker execution failed' },
    { status: error.message === 'Request body exceeds 1 MiB' ? 413 : 500 });
}

// celld owns the cell, its SQLite storage, fencing and response durability gate.
// Promise chaining serializes the entire request, including async middleware/DI.
export class PythonCell {
  constructor(ctx) {
    this.ctx = ctx;
    this.pending = Promise.resolve();
    this.runtime = null;
  }
  fetch(request) {
    const result = this.pending.then(() => this.execute(request));
    this.pending = result.catch(() => {});
    return result.catch(failure);
  }
  async execute(request) {
    const selected = select(request);
    if (!selected) return new Response('Not found', { status: 404 });
    this.runtime ??= boot(selected.app).catch(error => { this.runtime = null; throw error; });
    const runtime = await this.runtime;
    const before = (await this.ctx.storage.get('state')) ?? null;
    const result = await invoke(runtime, request, selected, before);
    if (result.state !== null && result.state !== before) {
      if (new TextEncoder().encode(result.state).length > MAX_STATE) throw new Error('State exceeds 1 MiB');
      await this.ctx.storage.put('state', result.state);
    }
    return response(result);
  }
}

export default {
  async fetch(request, env) {
    try {
      const selected = select(request);
      if (!selected) return new Response('Not found', { status: 404 });
      const runtime = await warm(selected.app);
      const found = match(runtime, request.method, selected.path);
      if (found?.route.state_key) {
        const { route, match } = found;
        const key = decodeURIComponent(match[route.names.indexOf(route.state_key) + 1]);
        if (key.length > 512) return Response.json({ detail: 'State key exceeds 512 characters' }, { status: 400 });
        // Revision deliberately excluded: state survives code deployments.
        const identity = JSON.stringify([selected.app.name, route.namespace, key]);
        return env.PYTHON_CELLS.get(env.PYTHON_CELLS.idFromName(identity)).fetch(request);
      }
      return response(await invoke(runtime, request, selected));
    } catch (error) { return failure(error); }
  },
};
