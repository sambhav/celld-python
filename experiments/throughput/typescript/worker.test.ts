import assert from 'node:assert/strict';
import test from 'node:test';
import {createWorker} from './worker';

function request(value: unknown, id = 'typescript-call-0001', headers = {}) {
  return new Request('http://localhost/hello', {method: 'POST',
    headers: {'x-celld-call-id': id, ...headers}, body: JSON.stringify(value)});
}
const price = (customer: string) => ({customer, unit_price_cents: 1999, currency: 'USD', stock: 100});

test('hello validates arguments and routing', async () => {
  const worker = createWorker();
  assert.deepEqual(await (await worker.fetch(request({name: 'Sam'}))).json(), {result: 'Hello, Sam'});
  for (const args of [{name: 1}, {}, {name: 'Sam', extra: 1}, []])
    assert.equal((await worker.fetch(request(args))).status, 422);
  assert.equal((await worker.fetch(new Request('http://localhost/hello'))).status, 405);
  assert.equal((await worker.fetch(new Request('http://localhost/missing'))).status, 404);
});

test('quote rejects invalid input before I/O and isolates concurrent traces', async () => {
  let calls = 0;
  const worker = createWorker('http://upstream/price', async (_url, init) => {
    calls++;
    const {name} = JSON.parse(init!.body as string);
    await new Promise(resolve => setTimeout(resolve, 2));
    return Response.json(price(name));
  });
  for (const args of [{name: ''}, {name: 'x'.repeat(129)}, {name: 'Sam', quantity: 0},
    {name: 'Sam', quantity: 11}, {name: 'Sam', quantity: 1.5}, {name: 'Sam', unknown: 1}])
    assert.equal((await worker.fetch(request(args))).status, 422);
  assert.equal((await worker.fetch(request({name: 'Sam'}, undefined, {'x-celld-context': '[]'}))).status, 422);
  assert.equal(calls, 0);
  const replies = await Promise.all(Array.from({length: 64}, (_, i) =>
    worker.fetch(request({name: `customer-${i}`}, `typescript-call-${String(i).padStart(4, '0')}`))));
  for (const [i, response] of replies.entries()) {
    assert.equal(response.status, 200);
    assert.equal(response.headers.get('x-celld-replayed'), null);
    assert.deepEqual(await response.json(), {result: {customer: `customer-${i}`, total_cents: 3998,
      currency: 'USD', trace: `typescript-call-${String(i).padStart(4, '0')}`}});
  }
  assert.equal(calls, 64);
  const value = await (await worker.fetch(request({name: 'Sam', quantity: 3}))).json();
  assert.equal(value.result.total_cents, 5997);
});

test('quote validates upstream data and does not replay repeated IDs', async () => {
  for (const result of [{...price('Sam'), unit_price_cents: 'bad'}, {...price('Sam'), stock: 0}, price('wrong')]) {
    const worker = createWorker('http://upstream/price', async () => Response.json(result));
    assert.equal((await worker.fetch(request({name: 'Sam'}))).status, 500);
  }
  let calls = 0;
  const worker = createWorker('http://upstream/price', async () => {calls++; return Response.json(price('Sam'));});
  for (let i = 0; i < 2; i++) assert.equal((await worker.fetch(request({name: 'Sam'}))).status, 200);
  assert.equal(calls, 2);
});
