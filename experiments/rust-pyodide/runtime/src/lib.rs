//! Rust owns Python boot, module/proxy lifetimes and the invocation boundary.
//! Pyodide remains an Emscripten WASM module with its required JS loader.
use js_sys::{Object, Promise, Reflect};
use serde::Deserialize;
use std::collections::BTreeMap;
use wasm_bindgen::prelude::*;
use wasm_bindgen_futures::JsFuture;

mod pyodide;
use pyodide::{OwnedProxy, Pyodide};

pub const MAX_BYTES: usize = 1024 * 1024;

#[derive(Deserialize)]
struct Bundle {
    name: String,
    entrypoint: String,
    sources: BTreeMap<String, String>,
}

#[derive(Deserialize)]
struct Reply {
    status: u16,
    headers: Vec<(String, String)>,
    body: String,
    state: Option<String>,
}

fn js_error(message: impl std::fmt::Display) -> JsValue {
    js_sys::Error::new(&message.to_string()).into()
}

fn set(object: &JsValue, key: &str, value: &JsValue) -> Result<(), JsValue> {
    Reflect::set(object, &key.into(), value).map(|_| ())
}

fn validate_reply(raw: &str) -> Result<(), String> {
    let reply: Reply =
        serde_json::from_str(raw).map_err(|e| format!("Invalid Python reply: {e}"))?;
    if !(200..=599).contains(&reply.status) {
        return Err("Invalid Python response status".into());
    }
    if reply.body.len() > MAX_BYTES.div_ceil(3) * 4
        || reply.state.as_ref().is_some_and(|s| s.len() > MAX_BYTES)
        || reply
            .headers
            .iter()
            .map(|(k, v)| k.len() + v.len())
            .sum::<usize>()
            > 64 * 1024
    {
        return Err("Result or state exceeds its limit".into());
    }
    Ok(())
}

/// A worker and its interpreter, owned together for the lifetime of one cell.
#[wasm_bindgen]
pub struct PythonRuntime {
    // Keep the interpreter alive as long as any Python proxy belongs to it.
    _py: Pyodide,
    worker: OwnedProxy,
}

#[wasm_bindgen]
impl PythonRuntime {
    /// Boot exclusively from the build's locked, bundled sources and packages.
    pub async fn boot(app: JsValue, sdk: JsValue) -> Result<PythonRuntime, JsValue> {
        let bundle: Bundle = serde_wasm_bindgen::from_value(app.clone()).map_err(js_error)?;
        let sdk: BTreeMap<String, String> =
            serde_wasm_bindgen::from_value(sdk).map_err(js_error)?;
        let options = Object::new();
        set(
            &options,
            "indexURL",
            &"https://celld-python.invalid/runtime/".into(),
        )?;
        set(
            &options,
            "packageBaseUrl",
            &format!("https://celld-python.invalid/apps/{}/", bundle.name).into(),
        )?;
        set(
            &options,
            "lockFileContents",
            &Reflect::get(&app, &"lock".into())?,
        )?;
        set(
            &options,
            "packages",
            &Reflect::get(&app, &"packages".into())?,
        )?;
        set(&options, "createPyodideModule", &pyodide::module_factory())?;
        set(&options, "enableRunUntilComplete", &JsValue::FALSE)?;
        let py: Pyodide = JsFuture::from(pyodide::load_pyodide(&options)?)
            .await?
            .unchecked_into();
        let fs = py.fs();
        for (name, source) in sdk.into_iter().chain(bundle.sources) {
            // Only relative paths inside /app are accepted, even for embedding callers.
            if name.starts_with('/') || name.split('/').any(|p| p == ".." || p.is_empty()) {
                return Err(js_error("Invalid bundled source path"));
            }
            let path = format!("/app/{name}");
            fs.mkdir_tree(path.rsplit_once('/').unwrap().0)?;
            fs.write_file(&path, &source)?;
        }
        py.run_python("import sys; sys.path.insert(0, '/app')")?;
        let module = OwnedProxy::new(py.pyimport("celld_python")?);
        let worker = OwnedProxy::new(module.value().load_worker(&bundle.entrypoint)?);
        Ok(Self { _py: py, worker })
    }

    /// Invoke the Python SDK through a typed Rust boundary. The cell's native
    /// gate must serialize calls; it also covers boot and durable commit.
    pub async fn invoke(&self, payload: &str, state: Option<String>) -> Result<String, JsValue> {
        let state = state.map(JsValue::from).unwrap_or(JsValue::UNDEFINED);
        let coroutine = OwnedProxy::new(self.worker.value().handle_wire(payload, &state)?);
        let value = JsFuture::from(Promise::resolve(coroutine.value())).await?;
        let raw = value
            .as_string()
            .ok_or_else(|| js_error("Python returned a non-string reply"))?;
        validate_reply(&raw).map_err(js_error)?;
        Ok(raw)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reply_contract_and_resource_bounds() {
        assert!(validate_reply(r#"{"status":200,"headers":[],"body":"","state":null}"#).is_ok());
        assert!(validate_reply(r#"{"status":99,"headers":[],"body":"","state":null}"#).is_err());
        assert!(validate_reply(r#"{"status":200,"headers":[],"body":false}"#).is_err());
        let raw = serde_json::json!({"status":200,"headers":[],"body":"","state":"x".repeat(MAX_BYTES + 1)}).to_string();
        assert!(validate_reply(&raw).is_err());
    }
}
