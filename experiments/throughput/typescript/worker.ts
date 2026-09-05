// Matched application workload, not a proposed TypeScript SDK.
import { z } from 'zod';

const MAX_BYTES = 1024 * 1024;
const helloResult = z.string();
const helloArgs = z.strictObject({name: z.string()});
const quoteArgs = z.strictObject({
  name: z.string().min(1).max(128),
  quantity: z.number().int().min(1).max(10).default(2),
});
const priceSchema = z.object({
  customer: z.string(), unit_price_cents: z.number().int(),
  currency: z.string(), stock: z.number().int(),
});
const quoteSchema = z.object({
  customer: z.string(), total_cents: z.number().int(),
  currency: z.string(), trace: z.string(),
});
const callerSchema = z.object({});
const clientSchema = z.object({
  name: z.string().max(128).default('python'),
  version: z.string().max(128).default(''),
  metadata: z.record(z.string(), z.unknown()).default({}),
});
type Context = {
  data: z.infer<typeof callerSchema>; scope: string; function: string;
  callId: string; requestId: string; attempt: number;
  client: z.infer<typeof clientSchema>; host: Record<string, unknown>;
  local: Record<string, string>;
};
type Invocation = {context: Context; arguments: Record<string, unknown>};
type Next = (call: Invocation) => Promise<unknown>;
type Middleware = (call: Invocation, next: Next) => Promise<unknown>;

function dependencies() {
  const cache = new Map<() => unknown, Promise<unknown>>();
  return async <T>(provider: () => T | Promise<T>): Promise<T> => {
    if (!cache.has(provider)) cache.set(provider, Promise.resolve().then(provider));
    return await cache.get(provider) as T;
  };
}
const trace: Middleware = async (call, next) => {
  call.context.local.trace = call.context.callId;
  return await next(call);
};
function failure(code: string, message: string, status: number) {
  return Response.json({error: {code, message}}, {status});
}
async function body(request: Request) {
  if (!request.body) return {};
  const reader = request.body.getReader(), chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > MAX_BYTES) { await reader.cancel(); throw new RangeError('Body exceeds 1 MiB'); }
      chunks.push(value);
    }
  } finally { reader.releaseLock(); }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
  return size ? JSON.parse(new TextDecoder().decode(bytes)) : {};
}

export function createWorker(upstream = '', transport: typeof fetch = fetch) {
  const pricingUrl = () => upstream;
  const middlewares: Middleware[] = upstream ? [trace] : [];
  return {
    async fetch(request: Request) {
      if (new URL(request.url).pathname !== '/hello') return failure('not_found', 'Unknown function', 404);
      if (request.method !== 'POST') return failure('method_not_allowed', 'Method not allowed', 405);
      const callId = request.headers.get('x-celld-call-id') || crypto.randomUUID();
      if (!/^[a-zA-Z0-9_-]{16,128}$/.test(callId)) return failure('invalid_call_id', 'Invalid call ID', 400);
      const requestId = request.headers.get('x-celld-request-id') || callId;
      const rawContext = request.headers.get('x-celld-context') || '{}';
      const rawClient = request.headers.get('x-celld-client') || '{}';
      if (requestId.length > 128 || rawContext.length > 16384 || rawClient.length > 4096)
        return failure('context_too_large', 'Context exceeds its limit', 413);
      let call: Invocation;
      try {
        call = {arguments: (upstream ? quoteArgs : helloArgs).parse(await body(request)), context: {
          data: callerSchema.parse(JSON.parse(rawContext)),
          client: clientSchema.parse(JSON.parse(rawClient)),
          scope: 'default', function: 'hello', callId, requestId,
          attempt: Math.max(1, Math.min(100, Number(request.headers.get('x-celld-attempt') || 1))),
          host: {}, local: {},
        }};
      } catch (error) {
        return error instanceof RangeError ? failure('payload_too_large', error.message, 413)
          : failure('validation_error', 'Invalid arguments or context', 422);
      }
      const resolve = dependencies();
      let next: Next = async call => {
        const name = call.arguments.name as string;
        if (!upstream) return `Hello, ${name}`;
        const quantity = call.arguments.quantity as number;
        const response = await transport(await resolve(pricingUrl), {
          method: 'POST', headers: {'content-type': 'application/json'}, body: JSON.stringify({name}),
        });
        if (!response.ok) throw new Error('Upstream failed');
        const price = priceSchema.parse(await response.json());
        if (price.customer !== name || price.stock < quantity) throw new Error('Invalid price or stock');
        return {customer: name, total_cents: price.unit_price_cents * quantity,
          currency: price.currency, trace: call.context.local.trace};
      };
      for (const middleware of [...middlewares].reverse()) {
        const previous = next;
        next = call => middleware(call, previous);
      }
      try {
        const result = (upstream ? quoteSchema : helloResult).parse(await next(call));
        const encoded = JSON.stringify({result});
        if (new TextEncoder().encode(encoded).length > MAX_BYTES)
          return failure('payload_too_large', 'Result exceeds 1 MiB', 413);
        return new Response(encoded, {headers: {'content-type': 'application/json'}});
      } catch {
        return failure('execution_error', 'Worker execution failed', 500);
      }
    },
  };
}
