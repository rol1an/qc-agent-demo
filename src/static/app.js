// 前端：fetch 后端真实计算结果并渲染。零硬编码数据 —— 所有点/边/裁决都来自 /api/analyze。
let DATA = null, SEL = -1;
const $ = id => document.getElementById(id);
const COL = {A:'#3fb950','A→B':'#a371f7',B:'#a371f7',C:'#f85149'};
const TCOL = {process:'#4c9aff',kcc:'#e3b341',mechanism:'#f0883e',mode:'#f85149',
  kpc:'#3fb950',disposition:'#a371f7',sensor_cluster:'#9aa7b8'};
const LAYER = {sensor_cluster:0,process:1,kcc:2,mechanism:3,mode:4,kpc:5,disposition:6};

async function run(){
  $('run').disabled = true; $('status').textContent = '后端真实计算中…';
  try{
    const be = $('backend').value, n = $('n').value;
    const t0 = performance.now();
    DATA = await (await fetch(`/api/analyze?backend=${be}&n=${n}`)).json();
    if(DATA.error){ $('status').textContent = '错误: '+DATA.error; return; }
    $('status').textContent = `完成 (${Math.round(performance.now()-t0)}ms)`;
    render();
  }catch(e){ $('status').textContent = '请求失败: '+e; }
  finally{ $('run').disabled = false; }
}

function render(){
  const m = DATA.meta;
  $('meta').innerHTML =
    `真实数据集 UCI SECOM · ${m.rows} 批次 × ${m.cols} 传感器 · ${m.ts_start}→${m.ts_end}<br>`+
    `受监控过程变量: 传感器#${m.sensor}　|　LLM 后端: <b>${m.backend}</b>${m.backend!=='stub'?'（真模型）':'（确定性占位）'}<br>`+
    `<span style="color:#9aa7b8">选择理由: ${m.sensor_reason}</span>`;
  drawChart(); drawCpk();
  const tb = $('events'); tb.innerHTML = '';
  DATA.events.forEach((e,i)=>{
    const tr = document.createElement('tr'); tr.className='ev'; tr.onclick=()=>select(i);
    const br = e.gate.branch;
    tr.innerHTML = `<td>#${e.idx}</td>`+
      `<td><span class="tag ${e.proactive?'pro':'pas'}">${e.proactive?'前瞻':'被动'}</span></td>`+
      `<td style="font-size:12px">${e.rule.replace(/Nelson |：.*/g,'').slice(0,20)}</td>`+
      `<td>${e.cpk??'—'}</td>`+
      `<td><span class="pill ${br}">${br}</span></td>`;
    tb.appendChild(tr);
  });
  const t = DATA.tally;
  $('tally').innerHTML = `三态分布：`+Object.entries(t).map(([k,v])=>
    `<span class="pill ${k}">${k}</span>×${v}`).join('　')+
    `　·　前瞻立案 ${DATA.proactive}`;
  $('disc').innerHTML = `声明：SECOM 为真实<b>半导体</b>制造数据(传感器匿名)，用于证明引擎可跑通；`+
    `规格限由稳定基线派生(CL±6σ)仅供演示；stub 后端基于真实检索确定性产出假设，接真模型后由真 LLM 推理。`;
  if(DATA.events.length) select(0);
}

function cvCtx(cv, h){ const w=cv.clientWidth, r=devicePixelRatio||1;
  cv.width=w*r; cv.height=h*r; const c=cv.getContext('2d'); c.scale(r,r); return {c,w,h}; }

function drawChart(){
  const {c,w,h} = cvCtx($('chart'),300), s=DATA.spc, v=s.series, n=v.length, pad=42;
  const lo=Math.min(s.lsl,...v), hi=Math.max(s.usl,...v);
  const X=i=>pad+i*(w-pad-10)/(n-1), Y=val=>h-24-(val-lo)/(hi-lo)*(h-40);
  c.clearRect(0,0,w,h);
  [[s.usl,'#f85149','USL'],[s.ucl,'#f0883e','UCL'],[s.cl,'#4c9aff','CL'],
   [s.lcl,'#f0883e','LCL'],[s.lsl,'#f85149','LSL']].forEach(([y,col,lab])=>{
    c.strokeStyle=col; c.globalAlpha=y===s.cl?.9:.5; c.setLineDash(y===s.cl?[]:[4,4]);
    c.beginPath(); c.moveTo(pad,Y(y)); c.lineTo(w-10,Y(y)); c.stroke(); c.globalAlpha=1;
    c.setLineDash([]); c.fillStyle=col; c.font='10px monospace'; c.fillText(lab,4,Y(y)+3);
  });
  c.strokeStyle='#c9d4e0'; c.lineWidth=1; c.beginPath();
  v.forEach((val,i)=>i?c.lineTo(X(i),Y(val)):c.moveTo(X(i),Y(val))); c.stroke();
  DATA.events.forEach(e=>{
    if(e.idx>=n) return; const x=X(e.idx), y=Y(e.value);
    c.fillStyle = e.kind==='beyond_spec'?'#f85149':e.proactive?'#f0883e':'#e3b341';
    c.beginPath();
    if(e.proactive){ c.moveTo(x,y-5);c.lineTo(x-4,y+3);c.lineTo(x+4,y+3);c.closePath(); }
    else if(e.kind==='beyond_spec') c.rect(x-3,y-3,6,6);
    else c.arc(x,y,3.5,0,7);
    c.fill();
  });
}

function drawCpk(){
  const {c,w,h}=cvCtx($('cpk'),120), cp=DATA.spc.cpk, n=cp.length, pad=42;
  const mx=Math.max(2.2,...cp.filter(x=>x!=null)), X=i=>pad+i*(w-pad-10)/(n-1), Y=v=>h-16-(v/mx)*(h-26);
  c.clearRect(0,0,w,h);
  c.fillStyle='rgba(248,81,73,.10)';
  cp.forEach((v,i)=>{ if(v!=null&&v<1.33) c.fillRect(X(i)-1,Y(1.33),3,h-16-Y(1.33)); });
  c.strokeStyle='#f85149'; c.setLineDash([4,4]); c.beginPath();c.moveTo(pad,Y(1.33));c.lineTo(w-10,Y(1.33));c.stroke();c.setLineDash([]);
  c.fillStyle='#f85149';c.font='10px monospace';c.fillText('Cpk 1.33',4,Y(1.33)+3);
  c.strokeStyle='#3fb950';c.lineWidth=1.2;c.beginPath();let st=0;
  cp.forEach((v,i)=>{ if(v==null)return; (st++?c.lineTo:c.moveTo).call(c,X(i),Y(v)); }); c.stroke();
}

function select(i){
  SEL=i; document.querySelectorAll('tr.ev').forEach((t,j)=>t.classList.toggle('sel',j===i));
  const e=DATA.events[i], H=e.hypothesis, G=e.gate;
  $('detail').innerHTML =
    `<h2>③ 诊断闭环 · 批次 #${e.idx}（${e.proactive?'前瞻立案':'被动告警'}）</h2>`+
    `<div class="kv" style="color:#9aa7b8;font-size:12px">${e.rule} · Cpk=${e.cpk??'—'}</div>`+
    `<div style="margin:10px 0"><b style="color:#4c9aff;font-size:13px">Graph RAG 检索到的证据子图</b>`+
    `<div class="meta" style="margin:3px 0">${e.retrieval.summary}</div>`+
    svgGraph(e.retrieval.graph, H.evidence)+`</div>`+
    `<div style="margin:10px 0"><b style="color:#f0883e;font-size:13px">Agent 根因假设</b>`+
    `<span class="src">${H.source}</span>`+
    `<div class="kv"><b>根因</b>${H.cause}　<b style="min-width:auto">证据</b>${H.evidence.length}节点　`+
    `<b style="min-width:auto">置信</b>${H.confidence}　${H.conflict?'<span style="color:#a371f7">证据冲突</span>':'<span style="color:#3fb950">自洽</span>'}</div>`+
    `<div class="meta">${H.rationale}</div></div>`+
    `<div style="margin:10px 0"><b style="color:#3fb950;font-size:13px">确定性三态 gate 裁决</b>　`+
    `<span class="pill ${G.branch}">${G.branch}</span>`+
    `<div class="kv" style="margin-top:6px"><b>处置</b>${G.action}</div>`+
    `<div class="meta">${G.reason}${G.recheck_ok===false?' · 复测未恢复→转人审':G.recheck_ok?' · 复测已恢复':''}</div></div>`;
}

// 按 ntype 分层画真实子图(SVG)，证据路径节点高亮
function svgGraph(g, evid){
  if(!g.nodes.length) return '<div class="meta">（实体不在本体内 → 见 gate 拦截）</div>';
  const cols={}; g.nodes.forEach(nd=>{ const L=LAYER[nd.type]??0; (cols[L]=cols[L]||[]).push(nd); });
  const W=Math.max(...Object.keys(cols).map(Number))+1, colW=150, rowH=44, H=Math.max(...Object.values(cols).map(a=>a.length))*rowH+20;
  const pos={};
  Object.entries(cols).forEach(([L,arr])=>arr.forEach((nd,r)=>{ pos[nd.id]={x:+L*colW+70,y:r*rowH+26}; }));
  const es=evid||[];
  let s=`<svg width="100%" viewBox="0 0 ${W*colW+20} ${H}" style="background:#0a0e14;border:1px solid #2b3444;border-radius:8px;margin-top:6px">`;
  s+=`<defs><marker id="ar" markerWidth="7" markerHeight="7" refX="6" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 z" fill="#4b5568"/></marker></defs>`;
  g.edges.forEach(e=>{ const a=pos[e.s],b=pos[e.t]; if(!a||!b)return;
    s+=`<line x1="${a.x+50}" y1="${a.y}" x2="${b.x-50}" y2="${b.y}" stroke="#3a4556" stroke-width="1" marker-end="url(#ar)"/>`;
    s+=`<text x="${(a.x+b.x)/2}" y="${(a.y+b.y)/2-3}" fill="#6b7688" font-size="9" text-anchor="middle">${e.rel||''}</text>`; });
  g.nodes.forEach(nd=>{ const p=pos[nd.id], hot=es.includes(nd.id)||nd.root, col=TCOL[nd.type]||'#9aa7b8';
    s+=`<rect x="${p.x-50}" y="${p.y-13}" width="100" height="26" rx="6" fill="${hot?col:'#161b22'}" `+
       `fill-opacity="${hot?0.22:1}" stroke="${col}" stroke-width="${hot?2:1}"/>`;
    s+=`<text x="${p.x}" y="${p.y+4}" fill="${col}" font-size="10" text-anchor="middle">${nd.id.slice(0,9)}</text>`; });
  return s+'</svg>';
}

$('run').onclick = run;
run();
