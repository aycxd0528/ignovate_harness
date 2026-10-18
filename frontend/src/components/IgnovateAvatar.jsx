/*! bloub — Copyright (c) 2026 Jérémy Perret. MIT; see BLOUB_LICENSE.md. */
import React, {useEffect, useId, useRef, useState, useSyncExternalStore} from 'react';
import {BotEngine} from '../vendor/bloub/engine';
import {DEMI_VIEWBOX as VB, RAYON as R} from '../vendor/bloub/repere';
import './IgnovateAvatar.css';

// The upstream engine stays framework-free. This adapter binds its poses to
// actual Harness state; polling/timer updates must never restart its clock.
function poseFor(status, activity) {
  if(status==='waiting_approval')return 'notify';
  if(status==='failed')return 'exclaim';
  if(status==='running')return activity==='tools'?'orbit':activity==='thinking'?'thinking':'idle';
  return 'idle';
}
const LIVE=new Set(['running','queued','waiting_approval','stopping']);
const motionQuery='(prefers-reduced-motion: reduce)';
const subscribeMotion=listener=>{
  const query=window.matchMedia(motionQuery);query.addEventListener('change',listener);
  return ()=>query.removeEventListener('change',listener);
};
const reducedMotion=()=>window.matchMedia(motionQuery).matches;

export default function IgnovateAvatar({status='idle',activity,size=40,follow=false,decorative=false,frozenAt}) {
  const pose=poseFor(status,activity),reduce=useSyncExternalStore(subscribeMotion,reducedMotion),svg=useRef(null);
  const id='ignovate-'+useId().replace(/:/g,'');
  const runtime=useRef(null);
  if(!runtime.current){
    const engine=new BotEngine(R,pose);
    runtime.current={engine,clock:1};
  }
  const [frame,setFrame]=useState(()=>runtime.current.engine.sample(frozenAt??1));
  const moving=(status==='idle'||LIVE.has(status))&&frozenAt===undefined;

  useEffect(()=>{
    const rt=runtime.current,engine=rt.engine;
    let raf=0,last=0,visible=true,disposed=false;
    const frozen=reduce||!moving||frozenAt!==undefined;
    if(frozen){
      engine.reset(pose,0);rt.clock=frozenAt??1;
    }else if(engine.state===pose)engine.reset(pose,rt.clock);
    else engine.setState(pose,rt.clock);
    let nextToolAt=rt.clock+3.4,toolOrbit=true;
    setFrame(engine.sample(rt.clock));
    const tick=ms=>{
      const dt=last?Math.min((ms-last)/1000,.064):0;last=ms;
      rt.clock+=dt;
      // Orbit is a finite upstream entrance. Alternate with its hexagon pose
      // through the engine's morphs so long tool calls keep showing activity.
      if(pose==='orbit'&&rt.clock>=nextToolAt){
        toolOrbit=!toolOrbit;engine.setState(toolOrbit?'orbit':'hexagon',rt.clock);
        nextToolAt=rt.clock+(toolOrbit?3.4:1.4);
      }
      setFrame(engine.sample(rt.clock));raf=requestAnimationFrame(tick);
    };
    const schedule=()=>{
      cancelAnimationFrame(raf);last=0;
      if(!disposed&&!frozen&&visible&&!document.hidden)raf=requestAnimationFrame(tick);
    };
    const observer=new IntersectionObserver(entries=>{visible=entries[0].isIntersecting;schedule();});
    observer.observe(svg.current);
    document.addEventListener('visibilitychange',schedule);
    const look=event=>{
      if(event.pointerType==='touch'||pose!=='idle')return;
      const rect=svg.current.getBoundingClientRect();if(!rect.width||!rect.height)return;
      const clamp=n=>Math.max(-1,Math.min(1,n));
      engine.setLook({yaw:clamp((event.clientX-rect.x-rect.width/2)/Math.max(1,innerWidth/2))*28,
        // Screen Y increases downwards; the engine's positive pitch looks up.
        pitch:-clamp((event.clientY-rect.y-rect.height/2)/Math.max(1,innerHeight/2))*22,
        mix:1,spin:0,wander:0},rt.clock);
    };
    const release=()=>engine.setLook(null,rt.clock);
    if(follow&&!frozen){window.addEventListener('pointermove',look);document.addEventListener('pointerleave',release);}
    else release();
    return ()=>{
      disposed=true;cancelAnimationFrame(raf);observer.disconnect();
      document.removeEventListener('visibilitychange',schedule);
      window.removeEventListener('pointermove',look);document.removeEventListener('pointerleave',release);
    };
  },[pose,moving,reduce,follow,frozenAt]);

  const dots=frame.dots.map((dot,i)=>dot.d?
    <path key={i} d={dot.d} transform={`translate(${dot.x} ${dot.y}) rotate(${dot.rot??0}) scale(${R})`} fill="var(--brand-blue, #315cff)" opacity={dot.opacity}/>:
    <circle key={i} cx={dot.x} cy={dot.y} r={dot.r} fill="var(--brand-blue, #315cff)" opacity={dot.opacity}/>);
  const arcs=side=><g fill="none" strokeLinecap="round">{frame.arcs.map(arc=><path key={arc.id} d={arc[side]} stroke={`url(#${id}-${arc.id})`} strokeWidth={arc.width} opacity={arc.opacity}/>)}</g>;
  return <svg ref={svg} className="ignovate-avatar" width={size} height={size} viewBox={`${-VB} ${-VB} ${VB*2} ${VB*2}`}
    role={decorative?undefined:'img'} aria-label={decorative?undefined:'Ignovate 机器人头像'} aria-hidden={decorative?'true':undefined}
    data-avatar-state={pose} data-avatar-motion={reduce||!moving?'still':'animated'} focusable="false">
    <defs>
      <mask id={id+'-mask'} maskUnits="userSpaceOnUse" x={-VB} y={-VB} width={VB*2} height={VB*2}>
        <path d={frame.bodyPath} fill="#fff"/>
        {frame.eyes.map((eye,i)=><path key={i} d={eye.d} transform={eye.matrix} opacity={eye.alpha} fill="#000"/>)}
        {frame.notch&&<circle cx={frame.notch.x} cy={frame.notch.y} r={frame.notch.r} fill="#000"/>}
      </mask>
      {frame.arcs.map(arc=><linearGradient key={arc.id} id={`${id}-${arc.id}`} gradientUnits="userSpaceOnUse" x1={arc.grad.x1} y1={arc.grad.y1} x2={arc.grad.x2} y2={arc.grad.y2}>
        {arc.grad.stops.map((_,i)=><stop key={i} offset={i/(arc.grad.stops.length-1)} stopColor="var(--brand-blue, #315cff)" stopOpacity={.22+.78*i/(arc.grad.stops.length-1)}/>)}</linearGradient>)}
    </defs>
    {arcs('back')}
    {frame.dotsBehind&&dots}
    <g opacity={frame.bodyAlpha}>
      <path d={frame.bodyPath} fill="#fff"/>
      <g mask={`url(#${id}-mask)`}><rect x={-VB} y={-VB} width={VB*2} height={VB*2} fill="var(--brand-blue, #315cff)"/></g>
    </g>
    {!frame.dotsBehind&&dots}
    {frame.notif&&<circle cx={frame.notif.x} cy={frame.notif.y} r={frame.notif.r} fill="#f0b429"/>}
    {arcs('front')}
  </svg>;
}
