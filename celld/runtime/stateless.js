// Interpreter caches belong to one native isolate and one app generation.
// Never reuse an interpreter across app/scope identities or evict an active call.
export class PoolBusy extends Error {}

export function createStatelessPool(boot, {maxRuntimes=4, maxConcurrency=256}={}) {
  if (!Number.isSafeInteger(maxRuntimes) || maxRuntimes < 1 ||
      !Number.isSafeInteger(maxConcurrency) || maxConcurrency < 1)
    throw new TypeError('Stateless pool limits must be positive integers');
  const entries = new Map();
  return {
    async run(key, app, invoke) {
      let entry = entries.get(key);
      if (!entry) {
        if (entries.size >= maxRuntimes) {
          const idle = [...entries].find(([, value]) => value.active === 0);
          if (!idle) throw new PoolBusy('All stateless interpreters are busy');
          entries.delete(idle[0]);
          // Release roots; reclaiming WASM memory still depends on native GC.
          idle[1].value?.worker?.destroy();
          idle[1].value?.free?.(); // Optional Rust experiment's owner.
        }
        entry = {runtime: null, value: null, active: 0};
        entries.set(key, entry);
      }
      if (entry.active >= maxConcurrency) throw new PoolBusy('Stateless concurrency limit reached');
      entry.active++;
      // One boot, even when the first requests arrive concurrently.
      entry.runtime ??= Promise.resolve().then(() => boot(app)).then(value => {
        entry.value = value;
        return value;
      });
      try {
        return await invoke(await entry.runtime);
      } finally {
        entry.active--;
        entries.delete(key);
        if (entry.value || entry.active) entries.set(key, entry);
        // A failed cold boot is removable once every waiter has observed it.
      }
    },
  };
}
