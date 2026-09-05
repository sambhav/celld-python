//! Narrow, typed imports of Pyodide's JS API. No JS worker implementation is called.
use js_sys::{Function, Object, Promise};
use wasm_bindgen::prelude::*;

#[wasm_bindgen(raw_module = "./pyodide.mjs")]
extern "C" {
    #[wasm_bindgen(catch, js_name = loadPyodide)]
    pub fn load_pyodide(options: &Object) -> Result<Promise, JsValue>;
}

#[wasm_bindgen(raw_module = "./pyodide.asm.mjs")]
extern "C" {
    #[wasm_bindgen(thread_local_v2, js_name = default)]
    static CREATE_MODULE: Function;
}

pub fn module_factory() -> JsValue {
    CREATE_MODULE.with(Clone::clone).into()
}

#[wasm_bindgen]
extern "C" {
    pub type Pyodide;
    #[wasm_bindgen(method, getter, js_name = FS)]
    pub fn fs(this: &Pyodide) -> FileSystem;
    #[wasm_bindgen(method, catch, js_name = runPython)]
    pub fn run_python(this: &Pyodide, source: &str) -> Result<JsValue, JsValue>;
    #[wasm_bindgen(method, catch)]
    pub fn pyimport(this: &Pyodide, name: &str) -> Result<PyProxy, JsValue>;

    pub type FileSystem;
    #[wasm_bindgen(method, catch, js_name = mkdirTree)]
    pub fn mkdir_tree(this: &FileSystem, path: &str) -> Result<(), JsValue>;
    #[wasm_bindgen(method, catch, js_name = writeFile)]
    pub fn write_file(this: &FileSystem, path: &str, source: &str) -> Result<(), JsValue>;

    pub type PyProxy;
    #[wasm_bindgen(method, catch)]
    pub fn destroy(this: &PyProxy) -> Result<(), JsValue>;
    #[wasm_bindgen(method, catch)]
    pub fn load_worker(this: &PyProxy, entrypoint: &str) -> Result<PyProxy, JsValue>;
    #[wasm_bindgen(method, catch)]
    pub fn handle_wire(this: &PyProxy, payload: &str, state: &JsValue) -> Result<PyProxy, JsValue>;
}

/// Release Python references on success and error, including failed awaits.
pub struct OwnedProxy(PyProxy);

impl OwnedProxy {
    pub fn new(value: PyProxy) -> Self {
        Self(value)
    }
    pub fn value(&self) -> &PyProxy {
        &self.0
    }
}

impl Drop for OwnedProxy {
    fn drop(&mut self) {
        // Awaiting Pyodide coroutines can already destroy their proxy. Cleanup
        // must never replace the original invocation/boot error.
        let _ = self.0.destroy();
    }
}
