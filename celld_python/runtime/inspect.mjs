// Build-time schema extraction in the pinned WASM Python, never host CPython.
import fs from 'node:fs';
import { pathToFileURL } from 'node:url';

const spec = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const {loadPyodide} = await import(pathToFileURL(spec.runtime + '/pyodide.mjs'));
const py = await loadPyodide({indexURL:spec.runtime + '/',lockFileContents:spec.lock,
  packageCacheDir:spec.packages,packageBaseUrl:pathToFileURL(spec.packages + '/').href,
  enableRunUntilComplete:false,stdout:line=>console.error(line),stderr:line=>console.error(line)});
await py.loadPackage(Object.keys(spec.lock.packages), {messageCallback:()=>{},errorCallback:message=>{throw new Error(message)}});
for (const name of Object.keys(spec.lock.packages)) {
  if (!py.loadedPackages[name] && !py.loadedPackages[name.replaceAll('-', '_')])
    throw new Error(`Package not loaded: ${name}`);
}
for (const [name, source] of Object.entries({...spec.sdk,...spec.sources})) {
  const path='/app/'+name;
  py.FS.mkdirTree(path.slice(0,path.lastIndexOf('/')));
  py.FS.writeFile(path,source);
}
py.runPython("import sys; sys.path.insert(0,'/app')");
const sdk=py.pyimport('celld_python');
const app=sdk.load_worker(spec.entrypoint);
const result={routes:JSON.parse(app.describe()),schema:JSON.parse(app.schema())};
fs.writeFileSync(process.argv[3],JSON.stringify(result));
app.destroy();sdk.destroy();
