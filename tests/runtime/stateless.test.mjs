import assert from 'node:assert/strict';
import test from 'node:test';
import {createStatelessPool, PoolBusy} from '../../celld/runtime/stateless.js';

test('cold concurrency shares boot while every invocation keeps its own values', async () => {
  let boots=0;
  const pool=createStatelessPool(async app=>{boots++; return {app};});
  const results=await Promise.all(Array.from({length:64},(_,i)=>pool.run('scope/app','app',async runtime=>{
    await Promise.resolve(); return [runtime.app,i];
  })));
  assert.equal(boots,1);
  assert.deepEqual(results,Array.from({length:64},(_,i)=>['app',i]));
});

test('capacity cannot evict active interpreters and releases on failure', async () => {
  const pool=createStatelessPool(async ()=>({}),{maxRuntimes:1,maxConcurrency:1});
  let release;
  const waiting=new Promise(resolve=>{release=resolve;});
  const active=pool.run('a',null,async ()=>{await waiting; throw new Error('application failed');});
  await assert.rejects(pool.run('a',null,()=>{}),PoolBusy);
  await assert.rejects(pool.run('b',null,()=>{}),PoolBusy);
  release();
  await assert.rejects(active,/application failed/);
  assert.equal(await pool.run('b',null,()=>42),42);
});

test('scopes keep separate globals, idle LRU roots are released, boot failure retries', async () => {
  let boots=0;
  const disposed=[];
  const pool=createStatelessPool(async app=>{
    boots++;
    if(app==='broken') throw new Error('boot failed');
    return {count:0,worker:{destroy(){disposed.push(app);}}};
  },{maxRuntimes:2});
  const increment=runtime=>++runtime.count;
  assert.equal(await pool.run('scope1','one',increment),1);
  assert.equal(await pool.run('scope2','two',increment),1);
  assert.equal(await pool.run('scope1','one',increment),2);
  assert.equal(await pool.run('scope3','three',increment),1);
  assert.deepEqual(disposed,['two']);
  assert.equal(await pool.run('scope1','one',increment),3);
  await assert.rejects(pool.run('failure','broken',increment),/boot failed/);
  assert.equal(await pool.run('failure','repaired',increment),1);
  assert.equal(boots,5);
});
