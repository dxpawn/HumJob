# Vendored verovio (browser WASM toolkit)

`verovio-toolkit-wasm.js` is the prebuilt, self-contained browser build of
[verovio](https://www.verovio.org/) - the same music engraver the Python side uses
(`mouthtranscriber/export.py`), here compiled to WebAssembly so Manual mode can
re-engrave the edited score in the browser with no server round trip.

- Version: **6.2.0** (from the npm `verovio` package, `dist/verovio-toolkit-wasm.js`).
- This is the **light** build (non-Humdrum): 7 MB, with the wasm inlined as base64,
  so it is a single self-contained file. No separate `.wasm` and no network fetch.
- License: **LGPL-3.0-or-later** (verovio). Kept unmodified and vendored so the app
  stays fully local (no CDN), per the project's local-first constraint.

Loaded lazily on first entry to Manual mode by `server/static/manual.js`
(`loadVerovio()`), which injects this script and waits for the Emscripten runtime
(`window.verovio.module`) to be ready before constructing `new verovio.toolkit()`.

To update: `npm pack verovio`, unpack, and copy `dist/verovio-toolkit-wasm.js` here.
