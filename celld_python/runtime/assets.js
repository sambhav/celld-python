import { assets } from './asset-data.js';

// Lexically scoped to the Pyodide loader. Never patches global fetch, and
// never falls back to a CDN/PyPI: missing declared artifacts fail closed.
export async function assetFetch(input) {
  const url = new URL(typeof input === 'string' ? input : input.url);
  if (url.origin !== 'https://celld-python.invalid') throw new Error(`Unbundled runtime URL: ${url}`);
  const data = assets[url.pathname];
  if (data === undefined) throw new Error(`Unbundled runtime asset: ${url.pathname}`);
  return new Response(Uint8Array.from(atob(data), c => c.charCodeAt(0)), {
    headers: { 'content-type': url.pathname.endsWith('.wasm') ? 'application/wasm' : 'application/octet-stream' },
  });
}
