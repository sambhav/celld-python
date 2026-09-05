# Third-party components

celld-python is Apache-2.0. Dependencies keep their own licenses.

- **celld 0.4.0:** Apache-2.0, Deno Land Inc. Installed separately. Source:
  https://github.com/denoland/celld/tree/v0.4.0
- **Pyodide 314.0.6:** Mozilla Public License 2.0. Source, including the
  Emscripten runtime build configuration:
  https://github.com/pyodide/pyodide/tree/314.0.6
  `celld_python/build.py:port_runtime` records the changes made to the generated
  loader at build time; original artifacts are pinned in `celld.lock.json`.
  The modified loader and assembly glue are available as source in each build.
- **CPython 3.14.2:** Python Software Foundation license and accompanying notices.
  Source: https://github.com/python/cpython/tree/v3.14.2
- **Pydantic and package wheels:** License files and metadata remain inside the
  original, hash-pinned wheel archives. Review the licenses of packages you add.
- **esbuild 0.25.12:** MIT, installed separately at build/deploy time.

Build outputs include runtime license texts. The Pyodide catalog and supplied
wheels may include further libraries under their respective licenses.
