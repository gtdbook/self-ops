"""实验室风格静态站点构建器（纯标准库，零依赖）。

设计语言致敬 dilum.dev：纸感底色 + 单一橙色强调 + 等宽微标签 + 编号分区 + 暗色模式。
数据层不变：从 data/ 读取日报、进化、沉淀、心跳产物，构建成静态站输出到 _site/。

首页 = 「实验室」单页：
  Hero   五环轨道图（Canvas 动画，节点 = 五条自循环的真实运行状态）
  01 // 情报流    今日日报卡片墙 + 实时搜索
  02 // 进化面板  适应度条形 + 进化账本时间线
  03 // 沉淀库    双链力导向星图（可拖拽/悬停/点击）+ 条目列表
  04 // 运行面板  五环状态卡 + 六项自检（读 data/meta/heartbeat.json）
子页（day/*、evolution、weekly、sediment、404）沿用同一设计系统换装。
"""

import html
import json
import os
import sys
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape

sys.path.insert(0, os.path.dirname(__file__))
from common import (  # noqa: E402
    DATA_DIR,
    DIGEST_DIR,
    SITE_DIR,
    SITE_URL,
    atomic_write_text,
    load_config,
    load_json,
    now_tz,
)

# ================================================================ 设计系统 CSS

CSS = """
:root{
  --bg:#f3f1ea;--bg2:#ebe8df;--card:#fbfaf6;--text:#1c1a16;--muted:#7d786d;
  --accent:#e05a1e;--accent-soft:rgba(224,90,30,.12);--border:#e0ddd2;
  --ok:#2e7d32;--warn:#b26a00;--bad:#c62828;--rank:#a39d8f;
  --mono:ui-monospace,'JetBrains Mono','SF Mono',Menlo,Consolas,monospace;
  --sans:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Hiragino Sans GB',
  'Microsoft YaHei',sans-serif;--shadow:0 1px 2px rgba(28,26,22,.05),0 8px 28px rgba(28,26,22,.06)}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){
  --bg:#141310;--bg2:#0e0d0b;--card:#1c1a17;--text:#e9e5db;--muted:#8f8a7e;
  --accent:#ff7038;--accent-soft:rgba(255,112,56,.14);--border:#2b2823;
  --ok:#5fbf66;--warn:#e0a23c;--bad:#ef6b5f;--rank:#6e695e;
  --shadow:0 1px 2px rgba(0,0,0,.4),0 10px 32px rgba(0,0,0,.35)}}
:root[data-theme=dark]{--bg:#141310;--bg2:#0e0d0b;--card:#1c1a17;--text:#e9e5db;
  --muted:#8f8a7e;--accent:#ff7038;--accent-soft:rgba(255,112,56,.14);--border:#2b2823;
  --ok:#5fbf66;--warn:#e0a23c;--bad:#ef6b5f;--rank:#6e695e;
  --shadow:0 1px 2px rgba(0,0,0,.4),0 10px 32px rgba(0,0,0,.35)}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;font-family:var(--sans);background:var(--bg);color:var(--text);
  line-height:1.65;-webkit-font-smoothing:antialiased}
body::before{content:"";position:fixed;inset:0;pointer-events:none;z-index:0;
  background:linear-gradient(var(--bg2) 1px,transparent 1px),
  linear-gradient(90deg,var(--bg2) 1px,transparent 1px);
  background-size:44px 44px;opacity:.5;mask-image:radial-gradient(ellipse at 30% 0%,#000 30%,transparent 75%)}
a{color:var(--accent);text-decoration:none}
.wrap{position:relative;z-index:1;max-width:1060px;margin:0 auto;padding:0 20px 80px}

/* ---------- 顶栏 ---------- */
.topbar{position:sticky;top:0;z-index:50;backdrop-filter:blur(12px);
  background:color-mix(in srgb,var(--bg) 78%,transparent);border-bottom:1px solid var(--border)}
.topbar .in{max-width:1060px;margin:0 auto;padding:10px 20px;display:flex;align-items:center;gap:18px}
.logo{display:flex;align-items:center;gap:10px;font-family:var(--mono);font-weight:700;
  letter-spacing:.12em;font-size:14px;color:var(--text)}
.logo .dot{width:26px;height:26px;border-radius:50%;background:var(--accent);color:#fff;
  display:grid;place-items:center;font-size:12px}
.logo .sub{color:var(--muted);font-weight:400;letter-spacing:.05em}
nav.top{margin-left:auto;display:flex;gap:4px;flex-wrap:wrap}
nav.top a{font-family:var(--mono);font-size:12px;letter-spacing:.1em;color:var(--muted);
  padding:6px 11px;border-radius:999px}
nav.top a:hover{color:var(--accent);background:var(--accent-soft)}
.tbtn{border:1px solid var(--border);background:var(--card);color:var(--muted);width:34px;
  height:34px;border-radius:50%;cursor:pointer;font-size:14px}
.tbtn:hover{color:var(--accent);border-color:var(--accent)}

/* ---------- Hero ---------- */
.hero{display:grid;grid-template-columns:1.15fr .85fr;gap:26px;align-items:center;
  padding:58px 0 34px}
@media(max-width:820px){.hero{grid-template-columns:1fr}}
.kicker{font-family:var(--mono);font-size:11.5px;letter-spacing:.18em;color:var(--accent);
  text-transform:uppercase;display:flex;align-items:center;gap:9px}
.kicker::before{content:"";width:7px;height:7px;border-radius:50%;background:var(--accent)}
.hero h1{font-size:clamp(30px,4.6vw,44px);line-height:1.15;margin:12px 0 10px;letter-spacing:-.01em}
.hero .lede{color:var(--muted);font-size:16.5px;max-width:34em;margin:0 0 20px}
.hero .lede b{color:var(--text)}
.herostats{display:flex;flex-wrap:wrap;gap:8px;margin:6px 0 22px}
.hstat{font-family:var(--mono);font-size:12px;color:var(--muted);border:1px solid var(--border);
  background:var(--card);border-radius:10px;padding:7px 12px}
.hstat b{color:var(--accent);font-size:15px}
.orbitbox{position:relative;aspect-ratio:1/1;max-width:430px;margin:0 auto;width:100%}
.orbitbox canvas{width:100%;height:100%;display:block}
.orbitbox .cap{position:absolute;bottom:-4px;left:50%;transform:translateX(-50%);
  font-family:var(--mono);font-size:10.5px;letter-spacing:.16em;color:var(--muted);white-space:nowrap}
.ctarow{display:flex;gap:10px;flex-wrap:wrap}
.cta{display:inline-flex;align-items:center;gap:8px;font-family:var(--mono);font-size:13px;
  padding:10px 18px;border-radius:999px;border:1px solid var(--border);background:var(--card);color:var(--text)}
.cta:hover{border-color:var(--accent);color:var(--accent)}
.cta.primary{background:var(--accent);border-color:var(--accent);color:#fff}
.cta.primary:hover{filter:brightness(1.08);color:#fff}

/* ---------- 分区 ---------- */
section.lab{margin:30px 0;padding:26px 26px 30px;background:var(--card);border:1px solid var(--border);
  border-radius:18px;box-shadow:var(--shadow);scroll-margin-top:70px}
.sechead{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap;margin-bottom:4px}
.secnum{font-family:var(--mono);font-size:13px;color:var(--muted);border:1px solid var(--border);
  border-radius:9px;padding:3px 8px;background:var(--bg)}
.micro{font-family:var(--mono);font-size:11.5px;letter-spacing:.16em;color:var(--accent);text-transform:uppercase}
.sechead h2{font-size:22px;margin:0;letter-spacing:-.01em}
.sechead .go{margin-left:auto;font-family:var(--mono);font-size:12px;letter-spacing:.08em;color:var(--muted)}
.sechead .go:hover{color:var(--accent)}
.secdesc{color:var(--muted);font-size:13.5px;margin:2px 0 16px}

/* ---------- 情报流 ---------- */
.searchbar{display:flex;gap:10px;align-items:center;margin-bottom:14px}
.searchbar input{flex:1;font-family:var(--mono);font-size:13px;padding:10px 14px;border-radius:11px;
  border:1px solid var(--border);background:var(--bg);color:var(--text);outline:none}
.searchbar input:focus{border-color:var(--accent)}
.searchbar .count{font-family:var(--mono);font-size:12px;color:var(--muted);white-space:nowrap}
.grouphead{display:flex;align-items:center;gap:10px;margin:20px 0 4px;font-family:var(--mono);
  font-size:12px;letter-spacing:.1em;color:var(--muted);text-transform:uppercase}
.grouphead::after{content:"";flex:1;height:1px;background:var(--border)}
.item{display:flex;gap:12px;padding:11px 2px;border-bottom:1px dashed var(--border)}
.item:last-child{border-bottom:none}
.item .rank{color:var(--rank);font-variant-numeric:tabular-nums;min-width:22px;text-align:right;
  font-size:12.5px;font-family:var(--mono);padding-top:3px}
.item .body{min-width:0;flex:1}
.item a.title{color:var(--text);font-weight:600;text-decoration:none;font-size:15px}
.item a.title:hover{color:var(--accent)}
.item .meta{color:var(--muted);font-size:12.5px;margin-top:2px;font-family:var(--mono)}
.item .summary{color:var(--muted);font-size:13px;margin-top:3px}
.item .reason{color:var(--accent);font-size:12.5px;margin-top:3px}
.err{color:var(--bad);font-size:12.5px;margin:6px 0 0}

/* ---------- 进化面板 ---------- */
table.fit{width:100%;border-collapse:collapse;font-size:13px;margin:8px 0 4px}
table.fit th,table.fit td{text-align:left;padding:8px 8px;border-bottom:1px solid var(--border)}
table.fit td.num,table.fit th.num{text-align:right;font-variant-numeric:tabular-nums;font-family:var(--mono)}
.fitbar{color:var(--accent);letter-spacing:1px}
.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}
.ledger .item .ledgerts{color:var(--rank);font-family:var(--mono);font-size:12px;min-width:92px;white-space:nowrap}
.samples{color:var(--muted);font-size:12.5px}

/* ---------- 沉淀库 ---------- */
.gardenwrap{display:grid;grid-template-columns:1.1fr .9fr;gap:20px}
@media(max-width:860px){.gardenwrap{grid-template-columns:1fr}}
#gardenCanvas{width:100%;aspect-ratio:1/1;display:block;border:1px solid var(--border);
  border-radius:14px;background:var(--bg);cursor:grab}
#gardenCanvas:active{cursor:grabbing}
.gardenlist{min-width:0;max-height:430px;overflow:auto;padding-right:4px}
.bedchips{display:flex;flex-wrap:wrap;gap:7px;margin:8px 0 12px}
.chip{font-family:var(--mono);font-size:11.5px;background:var(--accent-soft);color:var(--accent);
  border-radius:999px;padding:3px 11px}
.chip.dim{background:var(--bg);color:var(--muted);border:1px solid var(--border)}

/* ---------- 运行面板 ---------- */
.loopgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(185px,1fr));gap:12px}
.loopcard{border:1px solid var(--border);border-radius:14px;padding:14px 15px;background:var(--bg)}
.loopcard .lk{display:flex;align-items:center;gap:8px;font-weight:700;font-size:14px}
.loopcard .state{width:9px;height:9px;border-radius:50%;background:var(--rank)}
.loopcard .state.ok{background:var(--ok);box-shadow:0 0 0 3px color-mix(in srgb,var(--ok) 22%,transparent)}
.loopcard .state.stale{background:var(--warn);box-shadow:0 0 0 3px color-mix(in srgb,var(--warn) 22%,transparent)}
.loopcard .cad{font-family:var(--mono);font-size:11px;color:var(--muted);letter-spacing:.08em;margin-top:6px}
.loopcard .detail{font-size:12.5px;color:var(--muted);margin-top:3px}
.checkline{font-family:var(--mono);font-size:12px;color:var(--muted);margin-top:14px;line-height:2}
.checkline b.ok{color:var(--ok)}.checkline b.bad{color:var(--bad)}

/* ---------- 通用 ---------- */
nav.days a{display:inline-block;margin:0 10px 8px 0;color:var(--accent);font-size:13px}
footer{color:var(--muted);font-size:12.5px;text-align:center;margin-top:44px;line-height:2;position:relative;z-index:1}
footer a{color:var(--muted);border-bottom:1px dotted var(--border)}
a.back{font-family:var(--mono);font-size:12.5px;letter-spacing:.08em;display:inline-block;margin:18px 0 6px}
/* ---------- 滚动显现（渐进增强：JS 可用才动画，默认永远可见） ---------- */
html.js .reveal{opacity:0;transform:translateY(14px);transition:opacity .5s ease,transform .5s ease}
html.js .reveal.in{opacity:1;transform:none}
@media(prefers-reduced-motion:reduce){html.js .reveal{transition:none;opacity:1;transform:none}}
.gnum{font-variant-numeric:tabular-nums}
"""

# ================================================================ 前端交互 JS（零依赖原生）

APP_JS = r"""(function(){
"use strict";
var $=function(s,el){return (el||document).querySelector(s)};
var $$=function(s,el){return Array.prototype.slice.call((el||document).querySelectorAll(s))};

/* ---------- 主题 ---------- */
var root=document.documentElement;
var saved=null;try{saved=localStorage.getItem("selfops-theme")}catch(e){}
if(saved)root.setAttribute("data-theme",saved);
var tb=$("#themeToggle");
if(tb)tb.addEventListener("click",function(){
  var cur=root.getAttribute("data-theme")||
    (window.matchMedia&&matchMedia("(prefers-color-scheme:dark)").matches?"dark":"light");
  var next=cur==="dark"?"light":"dark";
  root.setAttribute("data-theme",next);
  try{localStorage.setItem("selfops-theme",next)}catch(e){}
});

/* ---------- 滚动显现（超高分区用 rootMargin 触发；3s 兜底全亮） ---------- */
document.documentElement.classList.add("js"); // 渐进增强开关：JS 挂了内容也照常可见
var io=new IntersectionObserver(function(es){
  es.forEach(function(en){if(en.isIntersecting){en.target.classList.add("in");io.unobserve(en.target)}});
},{rootMargin:"0px 0px -60px 0px",threshold:0});
$$(".reveal").forEach(function(el){io.observe(el)});
setTimeout(function(){$$(".reveal").forEach(function(el){el.classList.add("in")})},3000);
function countUp(el){
  var target=parseFloat(el.getAttribute("data-count"))||0;var t0=null;
  function step(t){if(!t0)t0=t;var p=Math.min(1,(t-t0)/900);
    el.textContent=Math.round(target*(1-Math.pow(1-p,3))).toLocaleString();
    if(p<1)requestAnimationFrame(step)}
  requestAnimationFrame(step);
}
var cio=new IntersectionObserver(function(es){
  es.forEach(function(en){if(en.isIntersecting){countUp(en.target);cio.unobserve(en.target)}});
},{threshold:.4});
$$("[data-count]").forEach(function(el){cio.observe(el)});

/* ---------- 五环轨道图 ---------- */
function dpr(c){var d=window.devicePixelRatio||1;var r=c.getBoundingClientRect();
  c.width=r.width*d;c.height=r.height*d;var x=c.getContext("2d");x.setTransform(d,0,0,d,0,0);return x}
function orbit(){
  var c=$("#orbitCanvas");if(!c)return;
  var loops=window.LAB_DATA&&window.LAB_DATA.loops||[];if(!loops.length)return;
  var ctx=dpr(c),W=c.getBoundingClientRect().width,H=c.getBoundingClientRect().height;
  var cx=W/2,cy=H/2,colors={ok:getVar("--accent"),stale:getVar("--warn"),pending:getVar("--rank")};
  var t0=null;
  function draw(t){
    if(!t0)t0=t;var sec=(t-t0)/1000;
    ctx.clearRect(0,0,W,H);
    // 轨道
    loops.forEach(function(l,i){
      var r=Math.min(W,H)*0.16+i*Math.min(W,H)*0.085;
      ctx.beginPath();ctx.arc(cx,cy,r,0,7);ctx.strokeStyle=getVar("--border");ctx.lineWidth=1;ctx.stroke();
    });
    // 核心
    var pr=6+Math.sin(sec*2)*1.6;
    ctx.beginPath();ctx.arc(cx,cy,pr,0,7);ctx.fillStyle=getVar("--accent");ctx.fill();
    ctx.beginPath();ctx.arc(cx,cy,pr+7+Math.sin(sec*2)*2,0,7);
    ctx.strokeStyle=hexA(getVar("--accent"),.35);ctx.lineWidth=1.4;ctx.stroke();
    // 环上节点
    loops.forEach(function(l,i){
      var r=Math.min(W,H)*0.16+i*Math.min(W,H)*0.085;
      var sp=(0.25+i*0.075)*(i%2?-1:1);
      var a=sec*sp+i*1.25;
      var x=cx+Math.cos(a)*r,y=cy+Math.sin(a)*r*0.62; // 椭圆轨道更立体
      var col=colors[l.state]||colors.pending;
      ctx.beginPath();ctx.arc(x,y,5.5,0,7);ctx.fillStyle=col;ctx.fill();
      // 呼吸脉冲
      var ph=(sec*0.8+i*0.37)%1;
      ctx.beginPath();ctx.arc(x,y,5.5+ph*13,0,7);
      ctx.strokeStyle=hexA(col,(1-ph)*0.5);ctx.lineWidth=1.3;ctx.stroke();
      // 标签
      ctx.font="10px "+getVar("--mono");ctx.fillStyle=getVar("--muted");
      ctx.textAlign="center";ctx.fillText(l.label,x,y-13);
      ctx.fillStyle=hexA(col,.9);ctx.fillText(l.age,x,y+17);
    });
    requestAnimationFrame(draw);
  }
  requestAnimationFrame(draw);
}
function getVar(n){return getComputedStyle(document.documentElement).getPropertyValue(n).trim()||"#e05a1e"}
function hexA(c,a){ // 支持 #rgb/#rrggbb → rgba
  c=c.replace("#","");
  if(c.length===3)c=c[0]+c[0]+c[1]+c[1]+c[2]+c[2];
  var n=parseInt(c,16);if(isNaN(n))return c;
  return "rgba("+(n>>16&255)+","+(n>>8&255)+","+(n&255)+","+a+")";
}

/* ---------- 沉淀库力导向星图 ---------- */
function garden(){
  var c=$("#gardenCanvas");if(!c)return;
  var data=window.LAB_DATA&&window.LAB_DATA.sediment;
  if(!data||!data.nodes||!data.nodes.length){
    var p=c.parentNode;var m=document.createElement("div");m.className="samples";
    m.style.padding="40px 20px";m.style.textAlign="center";
    m.textContent="沉淀库尚未播种——每周一沉淀循环首跑后，这里会长出知识星图。";
    p.insertBefore(m,c);c.remove();return;
  }
  var ctx=dpr(c),W=c.getBoundingClientRect().width,H=c.getBoundingClientRect().height;
  var nodes=data.nodes,links=data.links,idx={};
  nodes.forEach(function(n,i){idx[n.token]=i;n.x=W*0.5+(Math.random()-0.5)*W*0.5;
    n.y=H*0.5+(Math.random()-0.5)*H*0.5;n.vx=0;n.vy=0;n.fixed=false});
  var bedColors={};var palette=["#e05a1e","#c29237","#4e8d6e","#5b7fb9","#a06bb5","#bf5b62","#7d8a4e"];
  var bi=0;nodes.forEach(function(n){if(!(n.bed in bedColors))bedColors[n.bed]=palette[bi++%palette.length]});
  var hover=-1,drag=-1;
  function step(){
    // 斥力
    var fx,fy;
    for(var i=0;i<nodes.length;i++)for(var j=i+1;j<nodes.length;j++){
      var a=nodes[i],b=nodes[j],dx=a.x-b.x,dy=a.y-b.y,d2=dx*dx+dy*dy+40;
      var f=1400/d2;var d=Math.sqrt(d2);fx=dx/d*f;fy=dy/d*f;
      a.vx+=fx;a.vy+=fy;b.vx-=fx;b.vy-=fy;}
    // 弹簧
    links.forEach(function(l){
      var a=nodes[idx[l.a]],b=nodes[idx[l.b]];if(!a||!b)return;
      var dx=b.x-a.x,dy=b.y-a.y,d=Math.sqrt(dx*dx+dy*dy)||1;
      var f=(d-95)*0.004;var fx=dx/d*f,fy=dy/d*f;
      a.vx+=fx;a.vy+=fy;b.vx-=fx;b.vy-=fy;});
    // 向心 + 阻尼
    nodes.forEach(function(n,i){
      n.vx+=(W/2-n.x)*0.0035;n.vy+=(H/2-n.y)*0.0035;
      if(i===drag)return;
      n.vx*=0.85;n.vy*=0.85;n.x+=Math.max(-14,Math.min(14,n.vx));n.y+=Math.max(-14,Math.min(14,n.vy));
      n.x=Math.max(18,Math.min(W-18,n.x));n.y=Math.max(18,Math.min(H-18,n.y));});
    render();requestAnimationFrame(step);
  }
  function render(){
    ctx.clearRect(0,0,W,H);
    links.forEach(function(l){
      var a=nodes[idx[l.a]],b=nodes[idx[l.b]];if(!a||!b)return;
      var hi=hover>=0&&(idx[l.a]===hover||idx[l.b]===hover);
      ctx.beginPath();ctx.moveTo(a.x,a.y);ctx.lineTo(b.x,b.y);
      ctx.strokeStyle=hexA(getVar("--accent"),hi?0.85:0.28);ctx.lineWidth=hi?1.8:1;ctx.stroke();});
    nodes.forEach(function(n,i){
      var r=4+Math.sqrt(n.hits||1)*0.9;
      var col=bedColors[n.bed]||getVar("--accent");
      var dim=hover>=0&&hover!==i&&!(links.some(function(l){
        return (idx[l.a]===hover&&idx[l.b]===i)||(idx[l.b]===hover&&idx[l.a]===i)}));
      ctx.beginPath();ctx.arc(n.x,n.y,r,0,7);
      ctx.globalAlpha=dim?0.25:1;ctx.fillStyle=col;ctx.fill();
      if(n.status==="常青"){ctx.beginPath();ctx.arc(n.x,n.y,r+3.5,0,7);
        ctx.strokeStyle=hexA(col,.55);ctx.lineWidth=1.2;ctx.stroke();}
      if(hover===i){ctx.beginPath();ctx.arc(n.x,n.y,r+7,0,7);
        ctx.strokeStyle=hexA(col,.9);ctx.lineWidth=1.4;ctx.stroke();}
      ctx.globalAlpha=1;
      if(r>6.5||hover===i){ctx.font="10px "+getVar("--mono");ctx.textAlign="center";
        ctx.fillStyle=hexA(getVar("--text"),dim?0.3:0.75);ctx.fillText(n.token,n.x,n.y-r-5);}
    });
  }
  function pos(e){var r=c.getBoundingClientRect();
    return{x:(e.touches?e.touches[0].clientX:e.clientX)-r.left,
           y:(e.touches?e.touches[0].clientY:e.clientY)-r.top}}
  function nearest(p){var best=-1,bd=1e9;
    nodes.forEach(function(n,i){var d=(n.x-p.x)*(n.x-p.x)+(n.y-p.y)*(n.y-p.y);
      if(d<bd){bd=d;best=i}});return bd<400?best:-1}
  c.addEventListener("mousemove",function(e){
    var p=pos(e);
    if(drag>=0){nodes[drag].x=p.x;nodes[drag].y=p.y;return}
    hover=nearest(p);c.style.cursor=hover>=0?"pointer":"grab";
    var tip=$("#gardenTip");
    if(hover>=0){var n=nodes[hover];
      tip.style.display="block";tip.style.left=(n.x+14)+"px";tip.style.top=(n.y-10)+"px";
      tip.innerHTML="<b>"+n.token+"</b> · "+n.bed+"<br>追踪 "+n.weeks+" 周 · "+n.hits+" 次 · "+n.status;
    }else tip.style.display="none";
  });
  c.addEventListener("mousedown",function(e){var p=pos(e);drag=nearest(p);
    if(drag>=0){nodes[drag].fixed=true;e.preventDefault()}});
  window.addEventListener("mouseup",function(){if(drag>=0){nodes[drag].fixed=false;drag=-1}});
  c.addEventListener("touchstart",function(e){var p=pos(e);drag=nearest(p);
    if(drag>=0)e.preventDefault()},{passive:false});
  c.addEventListener("touchmove",function(e){if(drag>=0){var p=pos(e);
    nodes[drag].x=p.x;nodes[drag].y=p.y}},{passive:true});
  c.addEventListener("touchend",function(){drag=-1});
  c.addEventListener("click",function(){if(hover>=0){
    var el=document.getElementById("entry-"+nodes[hover].token);
    if(el)el.scrollIntoView({behavior:"smooth",block:"center"});
    else window.location.href="sediment.html#"+nodes[hover].token;}});
  requestAnimationFrame(step);
}

/* ---------- 情报流搜索 ---------- */
function search(){
  var input=$("#intelSearch");if(!input)return;
  var count=$("#intelCount");var groups=$$("#intelGroups .igroup");
  input.addEventListener("input",function(){
    var q=input.value.trim().toLowerCase();var total=0;
    groups.forEach(function(g){
      var n=0;
      $$(".item",g).forEach(function(it){
        var hit=!q||it.textContent.toLowerCase().indexOf(q)>=0;
        it.style.display=hit?"":"none";if(hit)n++;});
      g.style.display=n?"":"none";total+=n;});
    count.textContent=q?("命中 "+total+" 条"):"";
  });
}

document.addEventListener("DOMContentLoaded",function(){orbit();garden();search();});
if(document.readyState!=="loading"){orbit();garden();search();}
})();
"""

# ================================================================ 页面骨架


def esc(s):
    return html.escape(str(s or ""), quote=True)


def shell(title, body, css_path="style.css", updated=None, extra_head="", base=""):
    """实验室风格页面骨架（f-string 插值安全，模板中无字面花括号）。base 为子目录前缀。"""
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<link rel="alternate" type="application/rss+xml" title="self-ops RSS" href="{base}feed.xml">
<link rel="stylesheet" href="{css_path}">
{extra_head}
</head>
<body>
<div class="topbar"><div class="in">
  <a class="logo" href="{base}index.html" style="color:var(--text)"><span class="dot">◉</span>
  self-ops <span class="sub">// 实验室</span></a>
  <nav class="top">
    <a href="{base}index.html#intel">情报流</a>
    <a href="{base}index.html#evolve">进化</a>
    <a href="{base}index.html#garden">沉淀库</a>
    <a href="{base}index.html#ops">运行面板</a>
    <a href="{base}feed.xml">RSS</a>
  </nav>
  <button class="tbtn" id="themeToggle" title="切换明暗">◐</button>
</div></div>
<div class="wrap">
{body}
<footer>
  数据来源：Hacker News · GitHub Search · Product Hunt · 各官方博客 RSS ·
  <a href="https://sediment-garden.pages.dev/" target="_blank" rel="noopener">沉淀园</a>种子（版权归原作者所有）<br>
  由 <a href="https://github.com/gtdbook/self-ops">gtdbook/self-ops</a> 五条自循环驱动 ·
  <a href="{base}feed.xml">RSS 订阅</a> · 更新于 {esc(updated or now_tz().strftime("%Y-%m-%d %H:%M"))}
</footer>
</div>
<script src="{base}app.js"></script>
</body>
</html>"""


def group_head(title):
    return f'<div class="grouphead">{esc(title)}</div>'


def render_item(i, it):
    parts = [f'<div class="item"><div class="rank">{i}</div><div class="body">']
    if it.get("url"):
        parts.append(
            f'<a class="title" href="{esc(it["url"])}" target="_blank" rel="noopener">{esc(it["title"])}</a>'
        )
    else:
        parts.append(f'<span class="title">{esc(it["title"])}</span>')
    if it.get("meta"):
        parts.append(f'<div class="meta">{esc(it["meta"])}</div>')
    if it.get("summary"):
        parts.append(f'<div class="summary">{esc(it["summary"])}</div>')
    if it.get("reason"):
        parts.append(f'<div class="reason">💡 {esc(it["reason"])}</div>')
    parts.append("</div></div>")
    return "".join(parts)


def render_section_items(sec):
    if sec.get("errors"):
        last = sec["errors"][-1]
        return (
            f'<p class="err">⚠ 近期该源曾报错：{esc(last.get("error", "")[:160])}</p>'
            + "".join(render_item(i, it) for i, it in enumerate(sec["items"], 1))
        )
    return "".join(render_item(i, it) for i, it in enumerate(sec["items"], 1))


def render_digest(digest):
    """日报分组渲染（首页情报流 / day 归档页共用）。"""
    out = []
    for key, sec in digest["sources"].items():
        out.append(
            f'<div class="igroup">{group_head(sec["emoji"] + "  " + sec["title"])}'
            + render_section_items(sec)
            + "</div>"
        )
    return "".join(out)


def load_days(max_days):
    days = []
    for path in sorted(DIGEST_DIR.glob("*.json"), reverse=True)[:max_days]:
        try:
            days.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            continue
    return days


def build_feed(latest, max_items=80):
    """从最新日报生成 RSS 2.0。"""
    try:
        pub = format_datetime(
            datetime.fromisoformat(latest["generated_at"]).astimezone(timezone.utc)
        )
    except Exception:
        pub = format_datetime(datetime.now(timezone.utc))
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        "<rss version=\"2.0\"><channel>",
        "<title>self-ops 情报站</title>",
        f"<link>{SITE_URL}</link>",
        "<description>自动抓取的每日技术情报：Hacker News / GitHub 新星 / Product Hunt / 博客 RSS / 中文技术圈</description>",
        "<language>zh-CN</language>",
    ]
    count = 0
    for sec in latest["sources"].values():
        for it in sec["items"]:
            if count >= max_items:
                break
            desc = " · ".join(
                x for x in (sec["title"], it.get("meta", ""), it.get("summary", "")) if x
            )
            parts.append(
                f"<item><title>{escape(it['title'])}</title>"
                f"<link>{escape(it['url'])}</link>"
                f"<description>{escape(desc)}</description>"
                f"<pubDate>{pub}</pubDate>"
                f"<guid>{escape(it['url'])}</guid></item>"
            )
            count += 1
    parts.append("</channel></rss>")
    return "\n".join(parts)


# ================================================================ 五环状态


def loop_status(cfg):
    """五条自循环的真实运行状态（时间戳全部来自仓库内数据产物）。"""
    tz = cfg.get("timezone", "Asia/Shanghai")
    now = now_tz(tz)

    def age_h(iso):
        try:
            return (now - datetime.fromisoformat(iso)).total_seconds() / 3600
        except Exception:
            return None

    ledger = load_json(DATA_DIR / "meta" / "evolution.json", {}) or {}
    hb = load_json(DATA_DIR / "meta" / "heartbeat.json", {}) or {}
    sediment_idx = load_json(DATA_DIR / "sediment" / "index.json", {}) or {}
    seed = load_json(DATA_DIR / "sediment" / "seed.json", {}) or {}
    wfiles = sorted((DATA_DIR / "weekly").glob("*.json"))
    weekly_ts = (load_json(wfiles[-1], {}) or {}).get("generated_at") if wfiles else None
    dfiles = sorted(DIGEST_DIR.glob("*.json"))
    digest_ts = (load_json(dfiles[-1], {}) or {}).get("generated_at") if dfiles else None

    raw = [
        ("pipeline", "情报流水线", "每小时", "抓取 → 处理 → 部署", digest_ts, 6),
        ("evolve", "内生进化", "每日 07:10", "适应度 · 主题自学 · 账本", ledger.get("updated_at"), 30),
        ("heartbeat", "看门狗", "每日两次", "六项自检 · 漏跑自愈", hb.get("updated_at"), 30),
        ("weekly", "周度合成", "每周一 07:40", "周报 · 知识花园 · 月度回顾", weekly_ts, 9 * 24),
        ("sediment", "沉淀循环", "每周一", "跨周主题 → 永久条目", sediment_idx.get("updated_at") or seed.get("fetched_at"), 9 * 24),
    ]
    loops = []
    for key, label, cadence, desc, ts, stale in raw:
        a = age_h(ts) if ts else None
        loops.append({
            "key": key,
            "label": label,
            "cadence": cadence,
            "desc": desc,
            "ts": (ts or "")[:16].replace("T", " ") or None,
            "age_h": round(a, 1) if a is not None else None,
            "age": (f"{a:.0f}h 前" if a is not None and a < 48 else
                    (f"{a/24:.1f} 天前" if a is not None else "待首跑")),
            "state": ("ok" if a is not None and a < stale else
                      ("stale" if a is not None else "pending")),
        })
    return loops, hb


# ================================================================ 首页（实验室）


def build_index(cfg, days):
    loops, hb = loop_status(cfg)
    ledger = load_json(DATA_DIR / "meta" / "evolution.json", {}) or {}
    fitness = load_json(DATA_DIR / "meta" / "fitness.json", {}) or {}
    sediment_idx = load_json(DATA_DIR / "sediment" / "index.json", {}) or {}
    seed = load_json(DATA_DIR / "sediment" / "seed.json", {}) or {}
    prefs = load_json(DATA_DIR / "prefs.json", {}) or {}

    total_all = sum(sum(len(s["items"]) for s in d["sources"].values()) for d in days)
    n_entries = len(sediment_idx.get("entries") or {})
    n_links = sum(len(v) for v in (sediment_idx.get("links") or {}).values()) // 2
    updated = (days[0]["generated_at"] if days else now_tz().isoformat())[:16].replace("T", " ")

    # ---- 嵌入数据（f-string 插值安全：内容里的花括号只是普通字符）----
    sed = sediment_idx.get("entries") or {}
    nodes = [
        {"token": t, "bed": e.get("bed", ""), "hits": e.get("hits", 0),
         "weeks": e.get("weeks", 0), "status": e.get("status", "")}
        for t, e in sed.items()
    ]
    links = [
        {"a": a, "b": b, "w": w}
        for a, m in (sediment_idx.get("links") or {}).items()
        for b, w in m.items()
    ]
    lab_data = {
        "loops": [{"label": l["label"], "state": l["state"], "age": l["age"]} for l in loops],
        "sediment": {"nodes": nodes, "links": links},
    }
    data_script = (
        '<script>window.LAB_DATA='
        + json.dumps(lab_data, ensure_ascii=False)
        + ";</script>"
    )

    # ---- Hero ----
    hero = f"""
<section class="hero" id="top">
  <div>
    <div class="kicker">SELF-OPERATING INTELLIGENCE LAB</div>
    <h1>自循环情报实验室</h1>
    <p class="lede">五条<b>自循环</b>日夜运转：自动抓取、自动处理、自动部署、自动进化、
    自动沉淀——<b>不需要任何人值守</b>，它自己跑、自己修、自己长知识。</p>
    <div class="herostats">
      <span class="hstat"><b data-count="{total_all}">0</b> 条情报</span>
      <span class="hstat"><b data-count="{len(days)}">0</b> 天连续运行</span>
      <span class="hstat"><b data-count="{n_entries}">0</b> 条沉淀</span>
      <span class="hstat"><b data-count="{n_links}">0</b> 条双链</span>
      <span class="hstat"><b data-count="{len(ledger.get("entries") or [])}">0</b> 次进化决策</span>
    </div>
    <div class="ctarow">
      <a class="cta primary" href="#intel">看今日情报 →</a>
      <a class="cta" href="#garden">逛知识星图</a>
      <a class="cta" href="https://github.com/gtdbook/self-ops" target="_blank" rel="noopener">GitHub</a>
    </div>
  </div>
  <div class="orbitbox">
    <canvas id="orbitCanvas"></canvas>
    <div class="cap">FIVE SELF-RUNNING LOOPS · 实时状态来自仓库数据</div>
  </div>
</section>"""

    # ---- 01 情报流 ----
    if days:
        intel_body = render_digest(days[0])
        intel_date = days[0]["date"]
    else:
        intel_body = '<p class="samples">流水线尚未产出数据，请稍后再来。</p>'
        intel_date = "—"
    sec1 = f"""
<section class="lab reveal" id="intel">
  <div class="sechead"><span class="secnum">01</span><span class="micro">// 情报流</span>
    <h2>今日情报 · {esc(intel_date)}</h2>
    <a class="go" href="feed.xml">RSS 订阅 →</a></div>
  <p class="secdesc">每小时自动抓取 → 加权排序（{len(prefs.get("seed_tokens") or {})} 个沉淀园种子词 + 动态口味词）→ 多源共振聚类 → 飙升榜。</p>
  <div class="searchbar"><input id="intelSearch" type="search" placeholder="实时过滤：输入关键词，如 agent / 沉淀 / qwen …">
    <span class="count" id="intelCount"></span></div>
  <div id="intelGroups">{intel_body}</div>
</section>"""

    # ---- 02 进化面板 ----
    fit_rows = []
    for key, m in sorted((fitness.get("sources") or {}).items(), key=lambda x: -x[1].get("fitness", 0)):
        f = m.get("fitness", 0)
        n = max(0, min(5, round(f * 5)))
        bar = "▰" * n + "▱" * (5 - n)
        if m.get("enabled") is False:
            status, cls = f"⛔ 禁用 · 探针 {m.get('probe_at', '?')}", "bad"
        elif m.get("weight_hint", 1.0) < 1.0:
            status, cls = f"📉 降权 ×{m.get('weight_hint')}", "warn"
        else:
            status, cls = "✅ 启用", "ok"
        fit_rows.append(
            f'<tr><td>{esc(key)}</td><td class="num">{f:.2f} <span class="fitbar">{bar}</span></td>'
            f'<td class="num">{m.get("mean_items", 0)}/天</td><td class="{cls}">{status}</td></tr>'
        )
    fit_html = (
        '<table class="fit"><tr><th>数据源</th><th class="num">适应度</th><th class="num">产量</th><th>状态</th></tr>'
        + "".join(fit_rows)
        + "</table>"
        if fit_rows
        else '<p class="samples">适应度评估待 evolve 首跑。</p>'
    )
    led_items = []
    for e in list(reversed(ledger.get("entries") or []))[:8]:
        led_items.append(
            f'<div class="item"><div class="ledgerts">{esc((e.get("ts") or "")[5:16].replace("T", " "))}</div>'
            f'<div class="body"><a class="title" style="font-size:13.5px">{esc(e.get("kind", ""))} · {esc(e.get("target", ""))}</a>'
            f'<div class="summary">{esc(e.get("detail", ""))}</div></div></div>'
        )
    led_html = "".join(led_items) or '<p class="samples">暂无进化决策。</p>'
    sec2 = f"""
<section class="lab reveal" id="evolve">
  <div class="sechead"><span class="secnum">02</span><span class="micro">// 进化面板</span>
    <h2>体系在自我调整</h2>
    <a class="go" href="evolution.html">完整面板 →</a></div>
  <p class="secdesc">不需要任何人投票：数据源按 7 日适应度自动降权/禁用/探针复活；高频主题自动提升偏好权重；每一次决策留痕账本。</p>
  <div class="gardenwrap"><div>{fit_html}</div>
  <div class="ledger">{group_head("最近进化决策")}<div class="ledgerts-list">{led_html}</div></div></div>
</section>"""

    # ---- 03 沉淀库 ----
    beds = sediment_idx.get("beds") or {}
    bed_chips = "".join(
        f'<span class="chip">{esc(b)} {len(t)}</span>' for b, t in beds.items()
    ) or '<span class="chip dim">尚未播种</span>'
    top_entries = sorted(
        sed.items(), key=lambda x: -x[1].get("hits", 0)
    )[:8]
    entry_list = "".join(
        f'<div class="item" id="entry-{esc(t)}"><div class="body">'
        f'<a class="title" href="sediment.html#{esc(t)}" style="font-family:var(--mono);font-size:13.5px">{esc(t)}</a>'
        f'<span class="{"ok" if e.get("status") == "常青" else "warn"}"> {esc(e.get("status", ""))}</span>'
        f'<div class="meta">{esc(e.get("bed", ""))} · 追踪 {e.get("weeks", 0)} 周 · 累计 {e.get("hits", 0)} 次 · {esc(e.get("last_week", ""))}</div>'
        f"</div></div>"
        for t, e in top_entries
    )
    sec3 = f"""
<section class="lab reveal" id="garden">
  <div class="sechead"><span class="secnum">03</span><span class="micro">// 沉淀库</span>
    <h2>知识星图</h2>
    <a class="go" href="sediment.html">全部条目 →</a></div>
  <p class="secdesc">流动的情报按「跨 ≥2 周重现」沉淀为永久知识条目（方法论源自
  <a href="https://sediment-garden.pages.dev/" target="_blank" rel="noopener">沉淀园</a>）。
  星图可拖拽：节点越大 = 累计热度越高，光环 = 常青主题，连线 = 真实共同出现。</p>
  <div class="bedchips">{bed_chips}<span class="chip dim">双链 {n_links}</span>
    <span class="chip dim">种子 {seed.get("articles", 0)} 篇</span></div>
  <div class="gardenwrap">
    <div style="position:relative"><canvas id="gardenCanvas"></canvas>
      <div id="gardenTip" style="display:none;position:absolute;pointer-events:none;font-family:var(--mono);
        font-size:11.5px;background:var(--card);border:1px solid var(--border);border-radius:9px;
        padding:7px 10px;box-shadow:var(--shadow);z-index:5"></div></div>
    <div class="gardenlist">{entry_list or '<p class="samples">条目待周一沉淀循环产出。</p>'}</div>
  </div>
</section>"""

    # ---- 04 运行面板 ----
    loop_cards = "".join(
        f'<div class="loopcard"><div class="lk"><span class="state {l["state"]}"></span>{esc(l["label"])}</div>'
        f'<div class="cad">{esc(l["cadence"])} · {esc(l["age"])}</div>'
        f'<div class="detail">{esc(l["desc"])}</div></div>'
        for l in loops
    )
    checks = hb.get("checks") or {}
    check_lines = " ".join(
        f'<b class="{"ok" if c.get("ok") else "bad"}">{"✅" if c.get("ok") else "❌"} {esc(name)}</b>'
        for name, c in checks.items()
    ) or '<span class="dim">六项自检待看门狗首跑后点亮</span>'
    heal = "".join(f"<div>🛠 {esc(n)}</div>" for n in (hb.get("heal_notes") or []))
    sec4 = f"""
<section class="lab reveal" id="ops">
  <div class="sechead"><span class="secnum">04</span><span class="micro">// 运行面板</span>
    <h2>它自己照顾自己</h2>
    <a class="go" href="https://github.com/gtdbook/self-ops/actions" target="_blank" rel="noopener">Actions →</a></div>
  <p class="secdesc">GitHub 定时任务常跳度——看门狗每日两跑，流水线安静 6 小时即自动补触发，26 小时才升级告警；六项自检全过则告警自动关闭。</p>
  <div class="loopgrid">{loop_cards}</div>
  <div class="checkline">最近自检 {esc((hb.get("updated_at") or "")[:16].replace("T", " ") or "—")}：{check_lines}{heal}</div>
</section>"""

    body = hero + sec1 + sec2 + sec3 + sec4
    page = shell(
        "self-ops · 自循环情报实验室",
        body,
        css_path="style.css",
        updated=updated,
        extra_head=data_script,
    )
    atomic_write_text(SITE_DIR / "index.html", page)


# ================================================================ 子页


def build_day_pages(days):
    for d in days:
        body = (
            f'<p><a class="back" href="index.html#intel">← 返回实验室</a></p>'
            f'<div class="sechead"><span class="micro">// 情报归档</span><h2>每日情报 · {esc(d["date"])}</h2></div>'
            + render_digest(d)
        )
        page = shell(f"self-ops 情报 · {d['date']}", body, css_path="../style.css",
                     updated=d["generated_at"][:16].replace("T", " "), base="../")
        atomic_write_text(SITE_DIR / "day" / f"{d['date']}.html", page)


FIT_BAR_FULL = 5
LEDGER_EMOJI = {
    "source_demote": "📉", "source_disable": "⛔", "source_probe": "🧪",
    "source_reenable": "✅", "source_heal": "🌱", "token_promote": "🏷️",
    "sediment_seed": "🌿", "sediment_new": "🪴", "sediment_update": "🌿",
}
SOURCE_NAMES = {
    "hackernews": "Hacker News", "github_search": "GitHub 新星", "blog_rss": "博客 RSS",
    "cn_rss": "中文技术圈", "producthunt": "Product Hunt", "aihot": "AIHOT",
}


def fit_bar(v):
    n = max(0, min(FIT_BAR_FULL, round(v * FIT_BAR_FULL)))
    return "▰" * n + "▱" * (FIT_BAR_FULL - n)


def build_evolution_page():
    fitness = load_json(DATA_DIR / "meta" / "fitness.json", {}) or {}
    prefs = load_json(DATA_DIR / "prefs.json", {}) or {}
    garden = load_json(DATA_DIR / "garden.json", {}) or {}
    ledger = load_json(DATA_DIR / "meta" / "evolution.json", {}) or {}

    rows = []
    for key, m in sorted((fitness.get("sources") or {}).items(), key=lambda x: -x[1].get("fitness", 0)):
        f = m.get("fitness", 0)
        if m.get("enabled") is False:
            status, cls = f'⛔ 禁用 · 探针 {m.get("probe_at", "?")}', "bad"
        elif m.get("weight_hint", 1.0) < 1.0:
            status, cls = f'📉 降权 ×{m.get("weight_hint")}', "warn"
        else:
            status, cls = "✅ 启用", "ok"
        rows.append(
            f'<tr><td>{esc(SOURCE_NAMES.get(key, key))}</td>'
            f'<td class="num">{f:.2f} <span class="fitbar">{fit_bar(f)}</span></td>'
            f'<td class="num">{m.get("mean_items", 0)}/天</td>'
            f'<td class="num">{m.get("days_present", 0)}/{m.get("window_days", 7)} 天</td>'
            f'<td class="num">{m.get("error_days", 0)}</td><td class="{cls}">{status}</td></tr>'
        )
    fit_html = (
        '<table class="fit"><tr><th>数据源</th><th class="num">适应度</th><th class="num">产量</th>'
        '<th class="num">在场</th><th class="num">报错</th><th>状态</th></tr>' + "".join(rows) + "</table>"
        if rows
        else "<p class='samples'>适应度评估尚未产出。</p>"
    )
    tokens = sorted((prefs.get("tokens") or {}).items(), key=lambda x: -abs(x[1]))[:15]
    seed_tokens = sorted((prefs.get("seed_tokens") or {}).items(), key=lambda x: -x[1])[:20]
    chips_dyn = "".join(f'<span class="chip">{esc(t)} {v:+.1f}</span>' for t, v in tokens) or \
        '<span class="chip dim">动态偏好库尚空</span>'
    chips_seed = "".join(f'<span class="chip dim">{esc(t)} {v:+.1f}</span>' for t, v in seed_tokens) or ""

    themes = garden.get("themes") or {}
    g_items = []
    for tok, v in sorted(themes.items(), key=lambda x: -x[1].get("hits", 0))[:24]:
        weeks = len(v.get("weeks", []))
        flower = "🌸 " if weeks >= 3 else ""
        samples = v.get("samples") or []
        rep = " / ".join(
            f'<a href="{esc(s.get("url", ""))}" target="_blank" rel="noopener">{esc(s.get("title", ""))[:40]}</a>'
            for s in samples[:1] if s.get("url")
        ) or esc((samples[0].get("title", "") if samples else "")[:44])
        g_items.append(
            f'<div class="item"><div class="body"><a class="title">{flower}{esc(tok)}</a>'
            f'<div class="meta">追踪 {weeks} 周 · 累计 {v.get("hits", 0)} 次 · 首见 {esc(v.get("first_week", "?"))}</div>'
            f'<div class="samples">{rep}</div></div></div>'
        )
    garden_html = "".join(g_items) or "<p class='samples'>花园尚未播种。</p>"

    entries = list(reversed(ledger.get("entries") or []))[:60]
    led_rows = "".join(
        f'<div class="item"><div class="ledgerts">{esc((e.get("ts") or "")[:16].replace("T", " "))}</div>'
        f'<div class="body"><a class="title" style="font-size:13.5px">{LEDGER_EMOJI.get(e.get("kind"), "⚙")} {esc(e.get("target", ""))}</a>'
        f'<div class="summary">{esc(e.get("detail", ""))}</div></div></div>'
        for e in entries
    )

    body = (
        '<p><a class="back" href="index.html#evolve">← 返回实验室</a></p>'
        '<div class="sechead"><span class="secnum">02</span><span class="micro">// 进化面板</span>'
        f'<h2>完整进化档案</h2></div>'
        f'<section class="lab"><div class="grouphead">数据源适应度（7 日窗口）</div>{fit_html}</section>'
        f'<section class="lab"><div class="grouphead">动态偏好词（逐日衰减）</div><div class="bedchips">{chips_dyn}</div>'
        + (f'<div class="grouphead">沉淀园种子先验（稳定不衰减）</div><div class="bedchips">{chips_seed}</div>' if chips_seed else "")
        + "</section>"
        f'<section class="lab"><div class="grouphead">知识花园 · {len(themes)} 主题</div>{garden_html}</section>'
        f'<section class="lab"><div class="grouphead">进化日志 · 最新 {len(entries)} / 共 {len(ledger.get("entries") or [])} 条</div>{led_rows}</section>'
    )
    atomic_write_text(SITE_DIR / "evolution.html", shell("进化面板 · self-ops 实验室", body))


STATUS_CLASS = {"常青": "ok", "活跃": "warn", "休眠": ""}
_sediment_links = {}


def render_sediment_entry(token, e):
    cls = STATUS_CLASS.get(e.get("status", ""), "")
    obs_html = []
    for o in (e.get("observations") or [])[-3:]:
        obs_html.append(
            f'<div class="samples">· <a href="{esc(o.get("url", ""))}" target="_blank" rel="noopener">'
            f'{esc(o.get("title", ""))[:64]}</a>（{esc(o.get("source", ""))} · {esc(o.get("date", ""))}）</div>'
        )
    related = "、".join(
        f'<a href="sediment.html#{esc(t)}">{esc(t)}</a>'
        for t in sorted(_sediment_links.get(token, {}), key=lambda x: -_sediment_links[token][x])[:4]
    ) or "（暂无——不为孤立页强行连线）"
    md_url = f"https://github.com/gtdbook/self-ops/blob/main/data/sediment/{token}.md"
    return (
        f'<div class="item" id="{esc(token)}"><div class="body">'
        f'<a class="title" href="{md_url}" target="_blank" rel="noopener" style="font-family:var(--mono)">{esc(token)}</a> '
        f'<span class="{cls}">{esc(e.get("status", ""))}</span>'
        f'<div class="meta">{esc(e.get("bed", ""))} · 追踪 {e.get("weeks", 0)} 周 · '
        f'累计 {e.get("hits", 0)} 次 · {esc(e.get("first_week", ""))} → {esc(e.get("last_week", ""))}</div>'
        + "".join(obs_html)
        + f'<div class="samples">双链：{related}</div>'
        f"</div></div>"
    )


def render_sediment_section(title, tokens, entries):
    if not tokens:
        return ""
    items = "".join(render_sediment_entry(t, entries[t]) for t in tokens if t in entries)
    return f'<div class="grouphead">{esc(title)}</div>{items}' if items else ""


def build_sediment_page():
    global _sediment_links
    idx = load_json(DATA_DIR / "sediment" / "index.json", {}) or {}
    seed = load_json(DATA_DIR / "sediment" / "seed.json", {}) or {}
    entries = idx.get("entries") or {}
    _sediment_links = idx.get("links") or {}
    n_links = sum(len(v) for v in _sediment_links.values()) // 2

    chips = (
        f'<span class="chip">沉淀 {len(entries)} 条</span>'
        f'<span class="chip">双链 {n_links}</span>'
        f'<span class="chip dim">新芽 {len(idx.get("pending") or [])}</span>'
        f'<span class="chip dim">种子 {seed.get("articles", 0)} 篇</span>'
    )
    parts = [f'<div class="bedchips">{chips}</div>']
    beds = idx.get("beds") or {}
    for bed, toks in sorted(beds.items(), key=lambda x: -len(x[1])):
        sec = render_sediment_section(f"🪴 {bed}", toks, entries)
        if sec:
            parts.append(sec)
    pending = idx.get("pending") or []
    if pending:
        chips_pending = "".join(
            f'<span class="chip dim">{esc(p["token"])}（{p["weight"]}）</span>' for p in pending[:20]
        )
        parts.append(f'<div class="grouphead">🌱 新芽圃（未达沉淀判据，不硬造条目）</div><div class="bedchips">{chips_pending}</div>')
    seed_tokens = sorted((seed.get("seed_tokens") or {}).items(), key=lambda x: -x[1])[:20]
    if seed_tokens:
        chips_seed = "".join(f'<span class="chip dim">{esc(t)} {v:+.1f}</span>' for t, v in seed_tokens)
        parts.append(f'<div class="grouphead">🧭 沉淀园兴趣先验（稳定不衰减）</div><div class="bedchips">{chips_seed}</div>')

    body = (
        '<p><a class="back" href="index.html#garden">← 返回实验室</a></p>'
        '<div class="sechead"><span class="secnum">03</span><span class="micro">// 沉淀库</span>'
        '<h2>全部沉淀条目</h2></div>'
        '<p class="secdesc">观察记录只进不改 · 矛盾并存 · 每条带出处 · 双链只连真实共同出现 ≥2 条 · 条目永不删除。</p>'
        + "".join(f'<section class="lab">{p}</section>' for p in parts)
    )
    atomic_write_text(SITE_DIR / "sediment.html", shell("沉淀库 · self-ops 实验室", body))


def render_stats_table(stats):
    if not stats:
        return ""
    rows = []
    for key, s in stats.items():
        arrow = "↑" if s.get("delta", 0) > 0 else ("↓" if s.get("delta", 0) < 0 else "→")
        rows.append(
            f'<tr><td>{esc(SOURCE_NAMES.get(key, key))}</td>'
            f'<td class="num">{s.get("mean", 0)}/天</td><td class="num">{s.get("prev_mean", 0)}/天</td>'
            f'<td class="num">{arrow}{abs(s.get("delta", 0))}</td><td class="num">{s.get("error_days", 0)}</td></tr>'
        )
    return (
        '<table class="fit"><tr><th>源</th><th class="num">本周日均</th><th class="num">上周日均</th>'
        '<th class="num">变化</th><th class="num">报错天数</th></tr>' + "".join(rows) + "</table>"
    )


def render_weekly_section(rep, full=True):
    g = rep.get("garden") or {}
    chips = (
        f'<span class="chip">主题 {len(rep.get("themes") or [])} 个</span>'
        f'<span class="chip">Top {len(rep.get("top_items") or [])} 条</span>'
        + (f'<span class="chip dim">花园 {g.get("total_themes", 0)}（常青 {g.get("evergreen", 0)}）</span>' if g else "")
    )
    parts = [f'<div class="bedchips">{chips}</div>']
    if rep.get("themes"):
        for th in rep["themes"]:
            rep_links = " / ".join(
                f'<a href="{esc(s.get("url", ""))}" target="_blank" rel="noopener">{esc(s.get("title", ""))}</a>'
                if s.get("url") else esc(s.get("title", ""))
                for s in th.get("samples", [])[:2]
            )
            parts.append(
                f'<div class="item"><div class="body"><a class="title">🌱 {esc(th["token"])}</a>'
                f'<div class="meta">权重 {th.get("weight", 0)} · 出现 {th.get("days", 0)} 天</div>'
                + (f'<div class="samples">{rep_links}</div>' if rep_links else "")
                + "</div></div>"
            )
    if rep.get("top_items"):
        items = rep["top_items"] if full else rep["top_items"][:3]
        parts.append("".join(render_item(i, it) for i, it in enumerate(items, 1)))
    stats_html = render_stats_table(rep.get("stats") or {})
    if stats_html:
        parts.append(stats_html)
    if rep.get("evolution_lines"):
        parts.append('<div class="samples">🧬 ' + esc("；".join(rep["evolution_lines"][:5])) + "</div>")
    return "".join(parts)


def build_weekly_page():
    weekly_dir = DATA_DIR / "weekly"
    files = sorted(weekly_dir.glob("*.json"), reverse=True) if weekly_dir.exists() else []
    monthly_dir = DATA_DIR / "monthly"
    mfiles = sorted(monthly_dir.glob("*.json"), reverse=True) if monthly_dir.exists() else []
    if not files and not mfiles:
        body = '<section class="lab"><h2>🗓 每周洞察</h2><p class="samples">周报尚未产出。</p></section>'
    else:
        parts = []
        for i, f in enumerate(files[:12]):
            rep = load_json(f, {}) or {}
            parts.append(
                f'<section class="lab"><div class="sechead"><span class="micro">// 每周洞察</span>'
                f'<h2 style="font-size:19px">{esc(rep.get("week", f.stem))}</h2></div>'
                + render_weekly_section(rep, full=(i == 0))
                + "</section>"
            )
        for f in mfiles[:6]:
            rep = load_json(f, {}) or {}
            parts.append(
                f'<section class="lab"><div class="sechead"><span class="micro">// 月度回顾</span>'
                f'<h2 style="font-size:19px">{esc(rep.get("month", f.stem))}</h2></div>'
                + render_weekly_section(rep, full=False)
                + "</section>"
            )
        body = '<p><a class="back" href="index.html#intel">← 返回实验室</a></p>' + "".join(parts)
    atomic_write_text(SITE_DIR / "weekly.html", shell("每周洞察 · self-ops 实验室", body))


# ================================================================ 总装


def build():
    cfg = load_config()
    max_days = cfg.get("site_max_days", 60)
    days = load_days(max_days)
    if not days:
        print("[warn] 没有日报数据，站点只生成骨架")
        body = (
            '<section class="hero"><div><div class="kicker">SELF-OPERATING LAB</div>'
            "<h1>自循环情报实验室</h1>"
            '<p class="lede">流水线尚未产出数据，请稍后再来。</p></div></section>'
        )
        atomic_write_text(SITE_DIR / "index.html", shell("self-ops · 实验室", body))
        return

    updated = days[0]["generated_at"][:16].replace("T", " ")

    # 首页（实验室）
    build_index(cfg, days)

    # 每日归档
    build_day_pages(days)

    # 子页
    build_evolution_page()
    build_weekly_page()
    build_sediment_page()

    # 404
    atomic_write_text(
        SITE_DIR / "404.html",
        shell(
            "404 · self-ops",
            '<section class="lab" style="text-align:center;padding:60px 20px">'
            '<div class="kicker" style="justify-content:center">404</div>'
            '<h2 style="font-size:26px">这个页面还没被自动生成出来</h2>'
            '<p class="samples"><a href="index.html">返回实验室</a></p></section>',
        ),
    )

    # RSS
    atomic_write_text(SITE_DIR / "feed.xml", build_feed(days[0]))

    # 静态资产
    atomic_write_text(SITE_DIR / "style.css", CSS)
    atomic_write_text(SITE_DIR / "app.js", APP_JS)

    print(f"实验室站点构建完成：{len(days)} 天日报 + 四分区首页 + 3 个子页 + feed.xml，输出到 _site/")


if __name__ == "__main__":
    build()
