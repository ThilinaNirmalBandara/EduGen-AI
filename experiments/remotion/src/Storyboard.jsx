import React from 'react';
import {AbsoluteFill, Img, staticFile, useCurrentFrame} from 'remotion';
import {objectState, sceneAt, activeNarration} from './state.mjs';

const ASSETS = new Set(['sun', 'cloud', 'lake', 'rain', 'river', 'mountain']);
const box = (x, y, w, h) => ({position:'absolute', left:`${100*x}%`, top:`${100*y}%`, width:`${100*w}%`, height:`${100*h}%`, transform:'translate(-50%, -50%)'});
function TextBox({text, x, y, w, h, size, color, theme, scale, panel=true, layout, objectTransform=''}) {
  if (layout?.geometry) ({x,y,w,h}=layout.geometry);
  return <div style={{...box(x,y,w,h), transform:`translate(-50%, -50%) ${objectTransform}`, color, background:panel ? theme.panel : 'transparent',
    borderRadius:10*scale, padding:`${4*scale}px ${8*scale}px`, boxSizing:'border-box',
    display:'flex', alignItems:'center', justifyContent:'center', fontSize:(layout?.font_size ?? size)*scale,
    lineHeight:1.22, textAlign:'center', whiteSpace:'pre-wrap', fontWeight:400}}>{layout?.text ?? text}</div>;
}
function Layer({board, scene, local, opacity, layouts}) {
  const scale = board.width / 1280;
  return <AbsoluteFill style={{opacity, background:scene.background==='sky' ? `linear-gradient(${board.theme.sky_top}, ${board.theme.background})` : board.theme.background}}>
    <TextBox text={board.title} x={.30} y={.065} w={.55} h={.085} size={40} color={board.theme.foreground} theme={board.theme} scale={scale} panel={false} layout={layouts?.heading}/>
    <TextBox text={scene.title} x={.80} y={.065} w={.32} h={.085} size={32} color={board.theme.foreground} theme={board.theme} scale={scale} layout={layouts?.badge}/>
    {[...scene.objects].sort((a,b)=>a.z-b.z).map(obj => {
      const state = objectState(obj,scene.motions,local);
      const style = {...box(state.x,state.y,obj.width,obj.height), opacity:state.opacity,
        transform:`translate(-50%, -50%) scale(${state.scale}) rotate(${state.rotate}rad)`};
      if (obj.kind==='label') return <div key={obj.id} style={{position:'absolute',inset:0,opacity:state.opacity}}>
        <TextBox text={obj.text} x={state.x} y={state.y} w={obj.width} h={obj.height} size={obj.font_size} color={obj.color} theme={board.theme} scale={scale} layout={layouts?.[obj.id]} objectTransform={`scale(${state.scale}) rotate(${state.rotate}rad)`}/></div>;
      if (obj.kind==='asset') {
        if (!ASSETS.has(obj.asset)) throw new Error(`Unknown SVG asset: ${obj.asset}`);
        return <Img key={obj.id} src={staticFile(`svg/${obj.asset}.svg`)} style={{...style,objectFit:'fill'}}/>;
      }
      if (obj.kind==='arrow') return <svg key={obj.id} style={style} viewBox="0 0 100 100" preserveAspectRatio="none"><path d="M5 95 L90 10 M65 10 L90 10 L90 35" fill="none" stroke={obj.color} strokeWidth="5"/></svg>;
      if (obj.kind==='polyline') return <svg key={obj.id} style={{position:'absolute',inset:0,width:'100%',height:'100%',opacity:state.opacity}} viewBox={`0 0 ${board.width} ${board.height}`}><polyline points={obj.points.map(p=>`${p.x*board.width},${p.y*board.height}`).join(' ')} fill="none" stroke={obj.color} strokeWidth={4*scale}/></svg>;
      return <div key={obj.id} style={{...style, background:obj.color, borderRadius:obj.kind==='circle' ? '50%' : 0}}/>;
    })}
  </AbsoluteFill>;
}
export function StoryboardVideo({board, textLayouts={}}) {
  const frame = useCurrentFrame();
  const {scene,index,local} = sceneAt(board, frame / board.fps);
  const fade = index && scene.transition.kind==='crossfade' ? Math.min(1,local / scene.transition.duration) : 1;
  const cue = activeNarration(scene,local);
  return <AbsoluteFill style={{fontFamily:'DejaVu Sans, sans-serif', background:board.theme.background}}>
    {fade<1 && <Layer board={board} scene={board.scenes[index-1]} local={board.scenes[index-1].duration} opacity={1-fade} layouts={textLayouts[board.scenes[index-1].id]}/>}
    <Layer board={board} scene={scene} local={local} opacity={fade} layouts={textLayouts[scene.id]}/>
    <TextBox text={cue.text} x={.5} y={.9} w={.91} h={.14} size={30} color={board.theme.foreground} theme={board.theme} scale={board.width/1280} layout={textLayouts[scene.id]?.[cue.id]}/>
  </AbsoluteFill>;
}
