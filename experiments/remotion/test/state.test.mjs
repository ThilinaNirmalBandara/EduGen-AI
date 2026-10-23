import test from 'node:test';
import assert from 'node:assert/strict';
import {pathPoint,objectState,sceneAt,activeNarration} from '../src/state.mjs';
test('Bezier and deterministic looping do not reorder events',()=>{
  assert.deepEqual(pathPoint([{x:0,y:0},{x:.5,y:1},{x:1,y:0}],.5),[.5,.5]);
  const object={id:'drop',x:0,y:0,opacity:1};
  const motion={object_id:'drop',action:'move',start:0,duration:2,loop:true,easing:'linear',path:[{x:0,y:0},{x:1,y:1}]};
  assert.equal(objectState(object,[motion],3).x,.5);
  const board={scenes:[{id:'a',duration:2},{id:'b',duration:3}]};
  assert.equal(sceneAt(board,2).scene.id,'b');
  assert.equal(sceneAt(board,1).scene.id,'a');
  assert.equal(activeNarration({narration:[{id:'a',start:0,duration:1},{id:'b',start:1,duration:2}]},1).id,'b');
});
test('scale, fade and rotation use the same easing and hold their final state',()=>{
  const object={id:'cloud',x:.5,y:.5,opacity:.6};
  const make=(action,from,to)=>({object_id:'cloud',action,start:1,duration:2,loop:false,easing:'smooth',from_value:from,to_value:to});
  const motions=[make('scale',.8,1.2),make('opacity',.6,1),make('rotate',0,Math.PI)];
  assert.equal(objectState(object,motions,0).scale,1);
  assert.equal(objectState(object,motions,2).opacity,.8);
  assert.equal(objectState(object,motions,100).scale,1.2);
  assert.equal(objectState(object,motions,2).rotate,Math.PI/2);
});
test('polyline endpoints remain ordered under out-of-order frame evaluation',()=>{
  const points=Array.from({length:5},(_,i)=>({x:i/4,y:i/4}));
  assert.deepEqual(pathPoint(points,1),[1,1]);
  assert.deepEqual(pathPoint(points,.25),[.25,.25]);
  assert.deepEqual(pathPoint(points,0),[0,0]);
});
