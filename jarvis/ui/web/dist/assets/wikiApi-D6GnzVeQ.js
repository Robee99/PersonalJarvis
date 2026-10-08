import{p as n}from"./index-DR2Pfk2Z.js";/**
 * @license lucide-react v0.445.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const o=n("Notebook",[["path",{d:"M2 6h4",key:"aawbzj"}],["path",{d:"M2 10h4",key:"l0bgd4"}],["path",{d:"M2 14h4",key:"1gsvsf"}],["path",{d:"M2 18h4",key:"1bu2t1"}],["rect",{width:"16",height:"20",x:"4",y:"2",rx:"2",key:"1nb95v"}],["path",{d:"M16 2v20",key:"rotuqe"}]]);async function a(t){const e=await fetch(t);if(!e.ok)throw new Error(`HTTP ${e.status} ${e.statusText}`);return await e.json()}async function r(){return a("/api/wiki/tree")}async function s(t){return a(`/api/wiki/page/${encodeURIComponent(t)}`)}async function c(t){return a(`/api/wiki/backlinks/${encodeURIComponent(t)}`)}async function h(){try{const t=await fetch("/api/wiki/health");if(!t.ok)return null;const e=await t.json();return e.ok&&e.health?e.health:null}catch{return null}}async function k(){const t=await fetch("/api/wiki/reindex",{method:"POST"});if(!t.ok)throw new Error(`HTTP ${t.status} ${t.statusText}`);return await t.json()}export{o as N,s as a,c as b,h as c,r as f,k as r};
