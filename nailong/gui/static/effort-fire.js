// Native browser lifecycle adapter for the licensed effort-slider shaders.
import {VERT,FRAG_SIM,FRAG_BLUR,FRAG_COMP} from './effort-shaders.js';
export function createEffortFire(canvas){
  let gl,programs=[],buffers=[],vao,vbo,frame=0,ratio=0,spring=0,velocity=0,previous=0,started=performance.now(),disposed=false,lost=false;
  const reduced=matchMedia('(prefers-reduced-motion: reduce)');
  const available=()=>!disposed&&!lost&&canvas.isConnected&&canvas.closest(':popover-open')&&!document.hidden;
  const halt=()=>{cancelAnimationFrame(frame);frame=0;previous=0;};
  const clearBuffers=()=>{for(const {fbo,tex} of buffers){gl.deleteFramebuffer(fbo);gl.deleteTexture(tex);}buffers=[];};
  const clearPrograms=()=>{programs.forEach(program=>gl.deleteProgram(program));programs=[];if(vao)gl.deleteVertexArray(vao);if(vbo)gl.deleteBuffer(vbo);};
  function compile(type,source){const shader=gl.createShader(type);gl.shaderSource(shader,source);gl.compileShader(shader);if(!gl.getShaderParameter(shader,gl.COMPILE_STATUS)){gl.deleteShader(shader);throw new Error('Shader unavailable');}return shader;}
  function program(source){const vertex=compile(gl.VERTEX_SHADER,VERT),fragment=compile(gl.FRAGMENT_SHADER,source),program=gl.createProgram();gl.attachShader(program,vertex);gl.attachShader(program,fragment);gl.linkProgram(program);gl.deleteShader(vertex);gl.deleteShader(fragment);if(!gl.getProgramParameter(program,gl.LINK_STATUS)){gl.deleteProgram(program);throw new Error('Program unavailable');}return program;}
  function init(){
    for(const source of [FRAG_SIM,FRAG_BLUR,FRAG_COMP])programs.push(program(source));vao=gl.createVertexArray();gl.bindVertexArray(vao);vbo=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,vbo);gl.bufferData(gl.ARRAY_BUFFER,new Float32Array([-1,-1,1,-1,-1,1,-1,1,1,-1,1,1]),gl.STATIC_DRAW);gl.enableVertexAttribArray(0);gl.vertexAttribPointer(0,2,gl.FLOAT,false,0,0);
    canvas.classList.remove('fire-fallback');resize();
  }
  function resize(){
    if(disposed||lost)return;
    const rect=canvas.getBoundingClientRect(),dpr=Math.min(2,devicePixelRatio||1),width=Math.max(1,Math.round(rect.width*dpr)),height=Math.max(1,Math.round(rect.height*dpr));
    if(canvas.width===width&&canvas.height===height&&buffers.length)return;
    canvas.width=width;canvas.height=height;clearBuffers();
    for(let index=0;index<4;index++){
      const tex=gl.createTexture(),fbo=gl.createFramebuffer();gl.bindTexture(gl.TEXTURE_2D,tex);gl.texImage2D(gl.TEXTURE_2D,0,gl.RGBA,width,height,0,gl.RGBA,gl.UNSIGNED_BYTE,null);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,gl.LINEAR);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MAG_FILTER,gl.LINEAR);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_S,gl.CLAMP_TO_EDGE);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_T,gl.CLAMP_TO_EDGE);gl.bindFramebuffer(gl.FRAMEBUFFER,fbo);gl.framebufferTexture2D(gl.FRAMEBUFFER,gl.COLOR_ATTACHMENT0,gl.TEXTURE_2D,tex,0);gl.clearColor(0,0,0,1);gl.clear(gl.COLOR_BUFFER_BIT);buffers.push({fbo,tex});
    }
  }
  const uniform=(program,name)=>gl.getUniformLocation(program,name);
  function draw(now){
    if(!available()||!programs.length)return halt();
    resize();const dt=Math.min(.05,(now-(previous||now))/1000);previous=now;
    if(reduced.matches){spring=ratio;velocity=0;}else if(spring<ratio){velocity+=(ratio-spring)*7*dt;velocity*=1-.55*dt*6;spring=Math.min(ratio,spring+velocity*dt);}else{spring=ratio;velocity=0;}
    gl.bindVertexArray(vao);gl.viewport(0,0,canvas.width,canvas.height);
    const [sim,blur,composite]=programs,[a,b,h,v]=buffers;
    gl.bindFramebuffer(gl.FRAMEBUFFER,b.fbo);gl.useProgram(sim);gl.uniform1f(uniform(sim,'u_time'),reduced.matches?1:now/1000);gl.uniform1f(uniform(sim,'u_elapsed'),reduced.matches?4:(now-started)/1000);gl.uniform1f(uniform(sim,'u_slider'),.15+spring*.85);gl.activeTexture(gl.TEXTURE0);gl.bindTexture(gl.TEXTURE_2D,a.tex);gl.uniform1i(uniform(sim,'u_back'),0);gl.drawArrays(gl.TRIANGLES,0,6);
    gl.useProgram(blur);gl.uniform2f(uniform(blur,'u_res'),canvas.width,canvas.height);gl.uniform1i(uniform(blur,'u_tex'),0);
    for(const [target,texture,x,y,ext] of [[h,b.tex,1,0,1],[v,h.tex,0,1,0]]){gl.bindFramebuffer(gl.FRAMEBUFFER,target.fbo);gl.uniform2f(uniform(blur,'u_dir'),x,y);gl.uniform1f(uniform(blur,'u_ext'),ext);gl.bindTexture(gl.TEXTURE_2D,texture);gl.drawArrays(gl.TRIANGLES,0,6);}
    gl.bindFramebuffer(gl.FRAMEBUFFER,null);gl.useProgram(composite);gl.activeTexture(gl.TEXTURE0);gl.bindTexture(gl.TEXTURE_2D,b.tex);gl.uniform1i(uniform(composite,'u_scene'),0);gl.activeTexture(gl.TEXTURE1);gl.bindTexture(gl.TEXTURE_2D,v.tex);gl.uniform1i(uniform(composite,'u_glow'),1);gl.drawArrays(gl.TRIANGLES,0,6);[buffers[0],buffers[1]]=[buffers[1],buffers[0]];
    frame=reduced.matches?0:requestAnimationFrame(draw);
  }
  const kick=()=>{halt();if(available()&&programs.length)frame=requestAnimationFrame(draw);};
  const onLost=event=>{event.preventDefault();lost=true;halt();canvas.classList.add('fire-fallback');};
  const onRestored=()=>{lost=false;programs=[];buffers=[];try{init();kick();}catch{canvas.classList.add('fire-fallback');}};
  let observer;
  try{gl=canvas.getContext('webgl2',{antialias:false,preserveDrawingBuffer:false});if(!gl)throw new Error('WebGL unavailable');init();observer=new ResizeObserver(kick);observer.observe(canvas);canvas.addEventListener('webglcontextlost',onLost);canvas.addEventListener('webglcontextrestored',onRestored);document.addEventListener('visibilitychange',kick);reduced.addEventListener('change',kick);kick();}
  catch{if(gl){clearBuffers();clearPrograms();}canvas.classList.add('fire-fallback');}
  return {update(value){ratio=Math.max(0,Math.min(1,value));canvas.style.setProperty('--effort-fill',ratio*100+'%');if(!frame)kick();},dispose(){if(disposed)return;disposed=true;halt();observer?.disconnect();canvas.removeEventListener('webglcontextlost',onLost);canvas.removeEventListener('webglcontextrestored',onRestored);document.removeEventListener('visibilitychange',kick);reduced.removeEventListener('change',kick);if(gl){clearBuffers();clearPrograms();gl.getExtension('WEBGL_lose_context')?.loseContext();}}};
}
