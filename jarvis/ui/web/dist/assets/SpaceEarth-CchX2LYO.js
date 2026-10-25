import{r as d,eY as ze,j as L,e as Se}from"./index-Cedgh9m9.js";import{ab as Ae,C as Re,Y as De,P as Ee,as as Ce,d as Pe,V as B,o as O,$ as V,b as $,A as ie,Z as _e,aJ as N,aD as W,i as F,F as q,aH as Ne,K as We,c as Fe,q as A}from"./three.module-BUbGbmXm.js";import{O as Te}from"./OrbitControls-BVNu0IdV.js";import{l as S,s as se}from"./OrbitalView-D1iJ6TQy.js";import"./OrbitGlobe-CePX4pzV.js";import"./orbit-Dn3ngcxB.js";import"./publicData-Ci18fuu1.js";const le={day:"/space/earth-day.jpg",night:"/space/earth-night.jpg",water:"/space/earth-water.jpg"},je=`
  varying vec2 vUv;
  varying vec3 vNormal;
  varying vec3 vWorld;
  void main() {
    vUv = uv;
    vNormal = normalize(mat3(modelMatrix) * normal);
    vec4 world = modelMatrix * vec4(position, 1.0);
    vWorld = world.xyz;
    gl_Position = projectionMatrix * viewMatrix * world;
  }
`,Ge=`
  uniform sampler2D dayMap;
  uniform sampler2D nightMap;
  uniform sampler2D waterMap;
  uniform float hasMaps;
  uniform vec3 sunDir;
  varying vec2 vUv;
  varying vec3 vNormal;
  varying vec3 vWorld;
  void main() {
    vec3 n = normalize(vNormal);
    float light = dot(n, sunDir);
    float day = smoothstep(-0.12, 0.18, light);
    vec3 ocean = vec3(0.04, 0.16, 0.34);
    vec3 dayColor = mix(ocean, texture2D(dayMap, vUv).rgb, hasMaps);
    vec3 nightColor = texture2D(nightMap, vUv).rgb * hasMaps;
    float water = texture2D(waterMap, vUv).r * hasMaps;
    vec3 viewDir = normalize(cameraPosition - vWorld);
    vec3 halfway = normalize(sunDir + viewDir);
    float spec = pow(max(dot(n, halfway), 0.0), 40.0) * water * 0.55 * day;
    vec3 lit = dayColor * (0.08 + 0.95 * max(light, 0.0)) + vec3(1.0, 0.92, 0.75) * spec;
    vec3 lights = nightColor * vec3(1.0, 0.82, 0.55) * 1.35 * (1.0 - day);
    vec3 color = mix(lights + dayColor * 0.025, lit, day);
    gl_FragColor = vec4(color, 1.0);
    #include <colorspace_fragment>
  }
`,Ue=`
  uniform vec3 sunDir;
  uniform float time;
  varying vec3 vNormal;
  varying vec3 vPos;
  float hash(vec3 p) { return fract(sin(dot(p, vec3(127.1, 311.7, 74.7))) * 43758.5453); }
  float noise(vec3 p) {
    vec3 i = floor(p);
    vec3 f = fract(p);
    f = f * f * (3.0 - 2.0 * f);
    return mix(
      mix(mix(hash(i), hash(i + vec3(1, 0, 0)), f.x), mix(hash(i + vec3(0, 1, 0)), hash(i + vec3(1, 1, 0)), f.x), f.y),
      mix(mix(hash(i + vec3(0, 0, 1)), hash(i + vec3(1, 0, 1)), f.x), mix(hash(i + vec3(0, 1, 1)), hash(i + vec3(1, 1, 1)), f.x), f.y),
      f.z);
  }
  float fbm(vec3 p) {
    float v = 0.0;
    float a = 0.5;
    for (int i = 0; i < 5; i++) { v += a * noise(p); p *= 2.07; a *= 0.5; }
    return v;
  }
  void main() {
    vec3 p = normalize(vPos);
    float c = fbm(p * 3.2 + vec3(time * 0.006, 0.0, time * 0.004));
    float band = 1.0 - abs(p.y) * 0.35;
    float alpha = smoothstep(0.47, 0.74, c * band) * 0.78;
    float light = dot(normalize(vNormal), sunDir);
    float day = smoothstep(-0.15, 0.25, light);
    gl_FragColor = vec4(vec3(0.92 + 0.08 * day) * (0.06 + 0.94 * day), alpha * (0.25 + 0.75 * day));
  }
`,ke=`
  varying vec3 vNormal;
  varying vec3 vPos;
  void main() {
    vPos = position;
    vNormal = normalize(mat3(modelMatrix) * normal);
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`,He=`
  varying vec3 vNormal;
  varying vec3 vWorld;
  void main() {
    vNormal = normalize(mat3(modelMatrix) * normal);
    vec4 world = modelMatrix * vec4(position, 1.0);
    vWorld = world.xyz;
    gl_Position = projectionMatrix * viewMatrix * world;
  }
`,Le=`
  uniform vec3 sunDir;
  varying vec3 vNormal;
  varying vec3 vWorld;
  void main() {
    vec3 viewDir = normalize(cameraPosition - vWorld);
    float rim = pow(1.0 - abs(dot(normalize(vNormal), viewDir)), 3.0);
    float day = smoothstep(-0.35, 0.4, dot(normalize(vNormal), sunDir));
    vec3 color = mix(vec3(0.08, 0.2, 0.55), vec3(0.35, 0.65, 1.0), day);
    gl_FragColor = vec4(color, rim * (0.25 + 0.75 * day));
  }
`;function Be(s){const r=new Float32Array(s*3),o=new Float32Array(s*3);let n=7;const v=()=>(n=n*16807%2147483647,n/2147483647);for(let u=0;u<s;u+=1){const x=v()*2-1,f=v()*Math.PI*2,h=60+v()*20,b=Math.sqrt(1-x*x);r.set([h*b*Math.cos(f),h*x,h*b*Math.sin(f)],u*3);const w=.35+v()**3*.65,m=v();o.set([w*(.85+m*.15),w*.9,w*(1-m*.15)],u*3)}const y=new F;return y.setAttribute("position",new A(r,3)),y.setAttribute("color",new A(o,3)),y}function Oe(){const r=document.createElement("canvas");r.width=r.height=64;const o=r.getContext("2d");if(o){const n=o.createRadialGradient(32,32,0,32,32,32);n.addColorStop(0,"rgba(255,255,255,1)"),n.addColorStop(.25,"rgba(255,255,255,0.9)"),n.addColorStop(.6,"rgba(255,255,255,0.25)"),n.addColorStop(1,"rgba(255,255,255,0)"),o.fillStyle=n,o.fillRect(0,0,64,64)}return new Fe(r)}function ce(s){const r=new Float32Array(s.length*3);return s.forEach((o,n)=>r.set(S(o.lat,o.lon,o.alt_km),n*3)),r}function Ke({iss:s,stations:r,track:o,follow:n,issLabel:v,label:y,onFail:u,className:x}){const f=d.useRef(null),h=d.useRef(null),b=d.useRef(null),{generation:w}=ze(f),m=d.useRef({iss:s,stations:r,track:o,follow:n});m.current={iss:s,stations:r,track:o,follow:n};const R=d.useRef(!0),[de,ve]=d.useState(!1);return d.useEffect(()=>{R.current=!0},[s,r,o]),d.useEffect(()=>{var re;const T=h.current,g=f.current;if(!T||!g)return;const M=((re=window.matchMedia)==null?void 0:re.call(window,"(prefers-reduced-motion: reduce)").matches)??!1;let p;try{p=new Ae({canvas:T,antialias:!0,alpha:!1,powerPreference:"low-power"})}catch(e){u(e);return}p.setPixelRatio(Math.min(window.devicePixelRatio||1,1.75)),p.setClearColor(new Re(66314),1);const c=new De,l=new Ee(40,1,.05,200),X=m.current.iss??{lat:20,lon:0};l.position.set(...S(X.lat,X.lon,0)).multiplyScalar(3.4);const i=new Te(l,T);i.enableDamping=!M,i.dampingFactor=.08,i.enablePan=!1,i.minDistance=1.25,i.maxDistance=9,i.rotateSpeed=.45,i.zoomSpeed=.7,i.autoRotate=!M,i.autoRotateSpeed=.18;const D=new B(...se(Date.now())),Y=[],a=e=>(Y.push(e),e),me=new Ce,j={value:null},G={dayMap:{...j},nightMap:{...j},waterMap:{...j},hasMaps:{value:0},sunDir:{value:D}};let I=0;["day","night","water"].forEach(e=>{me.load(le[e],t=>{e!=="water"&&(t.colorSpace=Pe),t.anisotropy=Math.min(8,p.capabilities.getMaxAnisotropy()),a(t),G[`${e}Map`].value=t,I+=1,I===3&&(G.hasMaps.value=1),R.current=!0},void 0,t=>{console.info(`Space view texture ${le[e]} did not load.`,t)})});const pe=a(new O(1,128,64)),ue=new V(pe,a(new $({uniforms:G,vertexShader:je,fragmentShader:Ge})));c.add(ue);const J={sunDir:{value:D},time:{value:0}},fe=new V(a(new O(1.008,96,48)),a(new $({uniforms:J,vertexShader:ke,fragmentShader:Ue,transparent:!0,depthWrite:!1})));c.add(fe);const he=new V(a(new O(1.045,96,48)),a(new $({uniforms:{sunDir:{value:D}},vertexShader:He,fragmentShader:Le,side:_e,transparent:!0,blending:ie,depthWrite:!1})));c.add(he);const E=a(Oe()),we=new N(a(Be(2600)),a(new W({map:E,size:.3,vertexColors:!0,transparent:!0,sizeAttenuation:!0,depthWrite:!1})));c.add(we);const U=a(new F);U.setAttribute("position",new q([],3));const ge=new N(U,a(new W({map:E,color:6281471,size:.05,sizeAttenuation:!0,transparent:!0,opacity:.95,depthWrite:!1})));c.add(ge);const C=a(new F);C.setAttribute("position",new q([0,0,0],3));const K=new N(C,a(new W({map:E,color:16756832,size:.07,sizeAttenuation:!0,transparent:!0,depthWrite:!1}))),Z=new N(C,a(new W({map:E,color:16751164,size:.22,sizeAttenuation:!0,transparent:!0,opacity:.25,blending:ie,depthWrite:!1})));c.add(K,Z);const k=a(new F);k.setAttribute("position",new q([],3));const ye=new Ne(k,a(new We({color:16751164,transparent:!0,opacity:.55})));c.add(ye);const xe=()=>{const{iss:e,stations:t,track:P}=m.current;U.setAttribute("position",new A(ce(t),3)),k.setAttribute("position",new A(ce(P),3)),K.visible=Z.visible=e!==null,e&&C.setAttribute("position",new A(new Float32Array(S(e.lat,e.lon,e.alt_km)),3)),R.current=!1},Q=()=>{const e=g.clientWidth||1,t=g.clientHeight||1;p.setSize(e,t,!1),l.aspect=e/t,l.updateProjectionMatrix()};Q();const ee=new ResizeObserver(Q);ee.observe(g);const z=new B,te=new B;let H=0,ae=0;const be=performance.now(),oe=()=>{if(H=window.requestAnimationFrame(oe),document.visibilityState!=="visible")return;const e=Date.now();e-ae>3e4&&(D.set(...se(e)),ae=e),R.current&&xe();const{iss:t,follow:P}=m.current;i.autoRotate=!M&&!P,P&&t&&(z.set(...S(t.lat,t.lon,t.alt_km)),te.copy(z).normalize().multiplyScalar(l.position.length()),l.position.lerp(te,M?1:.05)),M||(J.time.value=(performance.now()-be)/1e3),i.update(),p.render(c,l);const _=b.current;if(_)if(t){z.set(...S(t.lat,t.lon,t.alt_km));const Me=z.dot(l.position)>1,ne=z.clone().project(l);_.style.display=Me?"block":"none",_.style.transform=`translate(${(ne.x+1)/2*g.clientWidth+10}px, ${(1-ne.y)/2*g.clientHeight-8}px)`}else _.style.display="none"};return H=window.requestAnimationFrame(oe),ve(!0),()=>{window.cancelAnimationFrame(H),ee.disconnect(),i.dispose(),Y.forEach(e=>e.dispose()),p.dispose()}},[w]),L.jsxs("div",{ref:f,className:Se("relative overflow-hidden cursor-grab active:cursor-grabbing touch-none",x),role:"img","aria-label":y,"data-testid":"stark-space-earth","data-ready":de?"true":"false",children:[L.jsx("canvas",{ref:h,className:"block h-full w-full"},w),L.jsx("div",{ref:b,className:"pointer-events-none absolute left-0 top-0 hidden whitespace-nowrap font-mono text-[10px] font-semibold uppercase tracking-[0.18em] text-[hsl(32_100%_62%)] [text-shadow:0_0_6px_rgba(0,0,0,0.9)]","aria-hidden":!0,children:v})]})}export{Ke as default};
