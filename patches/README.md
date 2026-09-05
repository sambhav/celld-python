# Optional upstream Rust patches

The [cell-density patch](celld-0.4.0-cell-density.patch) makes the native packing
ceiling configurable from 1 to 32, preserving the default and existing packing/
retirement rules. See the [packing experiment](../experiments/packing) for the
tests and same-binary GitHub comparison. The same native change is now in
[draft PR #1 on the user's celld fork](https://github.com/sambhav/celld/pull/1),
with focused Rust CI. Build that pinned revision or apply this patch to upstream
v0.4.0; the fork already contains it. It remains optional and has not been sent
to denoland/celld. The stock celld release remains the SDK's default requirement.

## Development watcher

`celld-0.4.0-watch-reads.patch` applies to upstream celld tag `v0.4.0`
(`a52f9905425bc41134d817694bdc2c50bcc5e856`). It excludes filesystem access
notifications from development rebuild triggers. On Linux, reading source
files during esbuild otherwise creates a rebuild loop.

```sh
cd /path/to/celld
# Review the patch, then:
git apply /path/to/celld-python/patches/celld-0.4.0-watch-reads.patch
cargo test -p celld --lib watcher_tests --locked
cargo build -p celld --release --locked
```

Two Rust regression tests check access/open/close-read events and preservation
of edit/create/delete/rename events. Both passed in an isolated Rust harness
using the exact helper/test source and notify 8.2.0. A full celld build in the
initial development environment was blocked by a jemalloc C configuration
failure; a binary including the watcher patch has not been validated here.

The Python SDK runs on the stock release. Its local supervisor works around the
watcher issue and uses celld's native preserve shutdown. This patch is optional,
and has not been sent upstream: celld's contribution process currently asks for
patches by email instead of GitHub pull requests.
