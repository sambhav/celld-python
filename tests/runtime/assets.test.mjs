import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp, readFile, writeFile, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';

test('offline artifacts preserve every byte and reject undeclared URLs', async () => {
  const root = await mkdtemp(join(tmpdir(), 'celld-assets-'));
  try {
    const bytes = Uint8Array.from({length:100_000}, (_, i) => i % 256);
    await writeFile(join(root, 'package.json'), '{"type":"module"}');
    await writeFile(join(root, 'assets.js'), await readFile(new URL('../../celld/runtime/assets.js', import.meta.url)));
    await writeFile(join(root, 'asset-data.js'), 'export const assets=' + JSON.stringify({
      '/runtime/test.whl':Buffer.from(bytes).toString('base64'), '/runtime/empty.whl':''
    }) + ';');
    const {assetFetch} = await import(pathToFileURL(join(root, 'assets.js')));
    assert.deepEqual(new Uint8Array(await (await assetFetch('https://celld-python.invalid/runtime/test.whl')).arrayBuffer()), bytes);
    assert.equal((await (await assetFetch('https://celld-python.invalid/runtime/empty.whl')).arrayBuffer()).byteLength, 0);
    await assert.rejects(assetFetch('https://example.com/runtime/test.whl'), /Unbundled runtime URL/);
    await assert.rejects(assetFetch('https://celld-python.invalid/runtime/missing.whl'), /Unbundled runtime asset/);
  } finally { await rm(root, {recursive:true, force:true}); }
});
