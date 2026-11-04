'use strict';
let state=null, request=null, viewSource=null, connected=false, posting=false, polling=false, timer=null;
let notice='', successUntil=0, lastAlignment=null;
let eventStream=null, lastPushAt=0, lastStatusAt=0, transportHealthy=false, previousConnectionKey=null;
const $=id=>document.getElementById(id), model=window.avControl, storageKey='pynq_unresolved_request_v3';
try {request=JSON.parse(localStorage.getItem(storageKey)||'null');}catch(_){notice='上次请求记录无法读取，已保留当前连接检查。';}
function persist(){try {if(request)localStorage.setItem(storageKey,JSON.stringify(request));else localStorage.removeItem(storageKey);}catch(_){notice='浏览器无法保存请求记录；请保持本窗口开启，避免重复发送。';}}
function element(tag,text,cls){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;}
function label(s){return s.label==='computer'?'电脑声源':s.label||'声源';}
function angle(v){return Number.isFinite(v)?v.toFixed(1)+'°':'—';}
function render(){
  const r=model.reconcile(state,request);
  if(r.completed){notice=r.completed.navigation_ready?'板端已确认本次任务完成。':model.failure(r.completed);if(r.completed.navigation_ready&&request.action==='select')viewSource=request.source_id;request=null;persist();}
  const gate=model.connectionGate(state,connected), phase=model.derivePhase(state,request,connected,Date.now(),notice||null);
  $('connection').textContent=gate.message;$('dot').style.background=gate.ready?'var(--accent)':'var(--warn)';
  const cm=state?.connection_monitor;
  $('runtimeState').textContent='定位服务：'+(connected?(cm?.message||gate.message):'连接检测中');
  $('boardInfo').textContent='PYNQ '+(cm?.board_address||'192.168.2.99')+' · '+(connected&&state?.board_connected?'已连接':'正在自动连接')+(cm?.system_version?' · 系统接口 '+cm.system_version:'')+(state?.boot_id?' · 本次开机 '+state.boot_id.slice(0,8):'');
  $('connectionNotice').textContent=transportHealthy?(cm?.message||gate.message)+'。状态持续更新；初始化只由手动按钮触发。':'正在恢复与本地连接程序的通信，保留原请求。';

  $('stepConnect').textContent='1 连接板子'+(connected&&state?.board_connected?' ✓':' …');
  $('stepReady').textContent='2 定位服务就绪'+(state?.runtime_ready===true&&!state?.closed&&connected&&state.full_turn_initialization_supported===true?' ✓':' …');
  $('stepInit').textContent='3 '+(state?.active_action==='initialize'?'板端执行初始化':'点击开始初始化');
  $('init').textContent=phase.button;$('init').disabled=!phase.allow||posting||!!request;
  $('nextAction').textContent=phase.next;$('initTitle').textContent=phase.title;$('initDetail').textContent=phase.detail;
  $('initLoader').hidden=!phase.running;document.body.classList.toggle('init-mode',phase.show);
  $('notice').textContent=notice;
  $('audioState').textContent=state?.pc_audio?.message||'正在检查第一段测试音';
  const aligned=connected&&state?.runtime_ready===true&&!state.closed&&state.full_turn_initialization_supported===true&&state.calibration==='RELATIVE_SINGLE_SOURCE';
  $('calibration').textContent=aligned?'坐标已对齐':'坐标尚未对齐';
  const initJob=(state?.jobs||[]).filter(j=>j.action==='initialize'&&j.navigation_ready).slice(-1)[0];
  const key=aligned?state.boot_id+':'+state.pid+':'+(initJob?.id||'aligned'):null;
  if(key&&key!==lastAlignment){lastAlignment=key;successUntil=Date.now()+1400;}
  if(!aligned)lastAlignment=null;
  const banner=aligned&&Date.now()<successUntil;$('initComplete').hidden=!banner;
  $('initScreen').hidden=!phase.show;$('overview').hidden=!aligned||banner||!!viewSource;
  $('view').hidden=!aligned||!viewSource;$('progress').hidden=true;
  const sources=state?.sources||[];$('count').textContent=sources.length+' / 3';$('cards').replaceChildren();$('markers').replaceChildren();
  if(!sources.length){const e=element('div',undefined,'empty');e.append(element('p','等待有效声源'),element('p','初始化通过后，由板端更新声音方向。','muted'));$('cards').append(e);}
  sources.forEach((s,i)=>{const b=element('button',undefined,'source'),left=element('span');left.append(element('b',String(i+1).padStart(2,'0')+'  '+label(s)),element('small',s.selectable?'查看目标画面 →':'等待声音确认'));b.append(left,element('span',angle(s.bearing_deg),'angle'));b.disabled=!aligned||!s.selectable||!!request||posting;b.onclick=()=>command('select',s.source_id);$('cards').append(b);if(Number.isFinite(s.bearing_deg)){const m=element('span',i+1,'marker'),a=s.bearing_deg*Math.PI/180;m.style.left=50+38*Math.sin(a)+'%';m.style.top=50-38*Math.cos(a)+'%';$('markers').append(m);}});
  $('history').replaceChildren();(state?.jobs||[]).slice(-4).reverse().forEach(j=>{const e=element('div',undefined,'job');e.append(element('span',j.action==='initialize'?'初始化坐标':j.action==='select'?'查看目标':'监听声音'),element('span',model.labels[j.status]||j.status),element('span',Number.isFinite(j.total_seconds)?j.total_seconds.toFixed(2)+' s':'—','duration'));if(model.terminal(j,state)&&!j.navigation_ready&&j.error)e.append(element('div',model.failure(j),'job-detail'));$('history').append(e);});
  const source=sources.find(s=>s.source_id===viewSource);
  if(viewSource&&source?.image_url&&aligned){$('frame').src=source.image_url;$('viewtitle').textContent=label(source);$('viewcaption').textContent='本次转向方位 '+angle(source.view_bearing_deg);}
  else {viewSource=null;$('view').hidden=true;}
  const preview=state?.preview;$('preview').hidden=!connected||!preview||!!state?.active||!!viewSource;
  if(preview&&connected){if($('previewImage').getAttribute('src')!==preview.image_url)$('previewImage').src=preview.image_url;$('previewImage').style.transform='rotate('+model.previewRotation(preview)+'deg)';$('previewCaption').textContent=(preview.caption||'历史相机快照')+' · '+Math.round(preview.age_seconds)+' 秒前（非实时）';}
}
async function fetchTimed(url,options={},milliseconds=3500){const c=new AbortController(),t=setTimeout(()=>c.abort(),milliseconds);try{const r=await fetch(url,{...options,signal:c.signal,cache:'no-store'});return {code:r.status,body:await r.json()};}finally{clearTimeout(t);}}
function acceptState(value,fromPush=false){
  const stamp=value?.connection_monitor?.pc_unix;
  if(!value||!Number.isFinite(stamp))throw new Error('连接状态缺少时间');
  if(stamp<lastStatusAt)return; // A slow poll must not overwrite a newer push.
  lastStatusAt=stamp;state=value;connected=true;transportHealthy=true;
  if(fromPush)lastPushAt=Date.now();
  $('updatedAt').textContent='状态更新：'+new Date(stamp*1000).toLocaleTimeString();
  const key=[value.boot_id,value.pid,value.calibration].join(':');
  if(previousConnectionKey&&key!==previousConnectionKey)viewSource=null;
  previousConnectionKey=key;render();
}
async function poll(){
  if(polling)return;polling=true;clearTimeout(timer);
  try{const r=await fetchTimed('/api/state',{},2000);if(r.code!==200)throw new Error(r.body.error||'本地连接程序暂不可用');acceptState(r.body);}
  catch(e){if(Date.now()-lastPushAt>3000){connected=false;transportHealthy=false;$('updatedAt').textContent='连接状态暂不可用，正在自动重连';render();}}
  finally{polling=false;timer=setTimeout(checkConnection,1000);}
}
function checkConnection(){
  if(Date.now()-lastPushAt>2500)poll();
  else {render();timer=setTimeout(checkConnection,1000);}
}
function startConnection(){
  // The desktop server is already probing even before this page opens.
  poll();
  if(typeof EventSource==='undefined')return;
  eventStream=new EventSource('/api/events');
  eventStream.addEventListener('state',event=>{
    try{acceptState(JSON.parse(event.data),true);}catch(_){poll();}
  });
  eventStream.onerror=()=>{lastPushAt=0;poll();};
}

async function command(action,sid){
  if(posting||request||!model.connectionGate(state,connected).ready)return;
  const id='ui_'+Date.now()+'_'+Math.random().toString(16).slice(2,10);
  request={id,action,source_id:sid,boot_id:state.boot_id,pid:state.pid,sentAt:Date.now(),delivery:'SUBMITTING'};persist();posting=true;notice='';viewSource=null;render();
  const body={request_id:id};if(action==='select')body.source_id=sid;
  try{const response=await fetchTimed('/api/'+action,{method:'POST',headers:{'Content-Type':'application/json','X-AV-Request':'1'},body:JSON.stringify(body)},4000);
    const outcome=model.responseOutcome(response.code,response.body,id);
    // A terminal poll may already have resolved this request while POST was returning.
    if(request?.id===id){if(outcome==='REJECTED'){notice='板端未接受本次指令：'+(response.body.error||'请求被拒绝');request=null;}else{request.delivery=outcome;if(outcome==='UNKNOWN')notice='请求结果待核对，保留原编号并继续读取状态。';}persist();}
  }catch(e){if(request?.id===id){request.delivery='UNKNOWN';persist();notice='请求结果待核对：连接未返回确认。不会重复发送。';}}
  finally{posting=false;render();poll();}
}
$('init').onclick=()=>command('initialize');$('refresh').onclick=poll;
$('back').onclick=()=>{viewSource=null;$('frame').removeAttribute('src');render();};
$('frame').onerror=()=>{notice='本次确认图暂不可用，任务记录已保留。';render();};
window.avReceiver={...model};render();startConnection();
