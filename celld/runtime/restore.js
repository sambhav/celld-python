import {assetFetch} from './assets.js';

// Pyodide validates its build ID before restoring memory. Compression keeps the
// offline deployment small; native celld implements DecompressionStream in Rust.
export async function snapshotOptions(enabled) {
  if (!enabled) return {};
  const response = await assetFetch('https://celld-python.invalid/runtime/baseline.snapshot.gz');
  const stream = response.body.pipeThrough(new DecompressionStream('gzip'));
  const bytes = new Uint8Array(await new Response(stream).arrayBuffer());
  return {_loadSnapshot:bytes};
}
