// Real pinned Pyodide regression: restored Python must work after bootstrap
// input becomes unreachable from the runtime's retained configuration.
import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
const [originalDirectory, patchedDirectory] = process.argv.slice(2);
const original = await import(pathToFileURL(`${originalDirectory}/pyodide.mjs`));
const patched = await import(pathToFileURL(`${patchedDirectory}/pyodide.mjs`));
const initial = await original.loadPyodide({_makeSnapshot:true});
initial.runPython('import asyncio, json, random');
const bytes = initial.makeMemorySnapshot();
const retained = await original.loadPyodide({_loadSnapshot:bytes});
const released = await patched.loadPyodide({_loadSnapshot:bytes});
assert.equal(retained._api.config._loadSnapshot.byteLength, bytes.byteLength);
assert.equal('_loadSnapshot' in released._api.config, false);
for (const py of [retained, released]) {
  assert.equal(py.runPython('json.dumps({"answer": sum(range(10))})'), '{"answer": 45}');
  assert.equal(await py.runPythonAsync('await asyncio.sleep(0); 42'), 42);
}
console.log(JSON.stringify({retained_input_bytes:bytes.byteLength, released_input_bytes:0}));
