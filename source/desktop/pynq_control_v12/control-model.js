/* Pure presentation/request lifecycle. No angles, motion or hardware decisions. */
(function(root){
  'use strict';
  const waiting = new Set(['RUNNING','WAITING_FOR_AUDIO_CLEANUP','WAITING_FOR_WORKER_RETURN','WAITING_FOR_WORKER_COMPLETION','CANCELLING','DEADLINE_CLEANUP']);
  const labels = {NOT_VERIFIED:'本次初始化未通过',DEADLINE_EXCEEDED:'本次任务超过时限',FAILED:'执行失败',BLOCKED:'板端暂不能执行',CLEANUP_UNCONFIRMED:'资源释放状态待核对',DISCOVERY_REJECTED:'本段声音未采用',DISCOVERY_RECORDED:'声音方向已更新',RELATIVE_TARGET_CONFIRMED:'目标画面已确认',TARGET_CONFIRMED:'目标画面已确认',CANCELLED:'本次任务已取消'};
  const reasons = {keep_pattern_visible:'测轴时没有识别到屏幕图案，请保持 PYNQ Control 图案页面可见',stationary_probe_response_too_small:'静止小角度探测未测得足够的图像变化，测轴未通过',phase_model_mismatch:'本段声音的时延一致性未通过，继续监听',alternative_not_separated:'本段声音存在多个相近方向，继续监听',subwindow_vector_unstable:'本段声音在时间窗口内方向不稳定，继续监听',sweep_telemetry_discontinuity:'舵机反馈的位置变化未通过连续性检查，原始读数已保存',sweep_observation_bracket_invalid:'舵机读数的时间区间异常，已结束本轮并保存记录','Servo feedback guard':'舵机温度、电压或电流反馈触发保护，具体读数已保存',fresh_axis_response_model_rejected:'屏幕标识已识别，舵机角度与图像位置的测轴关系未通过',known_screen_not_found_in_full_sweep:'本次整圈处理的画面未确认屏幕标识',known_stimulus_not_detected:'未检测到第一段参考测试音',audio_reference_rejected:'转稳后的参考声音方向未通过，尚未建立坐标',screen_center_unconfirmed:'相机未确认屏幕位置',known_screen_not_in_current_view:'当前相机画面没有识别到屏幕图案',insufficient_view_deadline:'剩余时间不足以完成转向和取图',zero_filtered_energy:'麦克风未收到有效变化信号',fractional_direction_unstable:'本段声音的方向不稳定'};
  function failure(job){
    if(!job)return '';
    const raw=job.failure_reason||job.reason||job.error||job.status||'UNKNOWN';
    const found=Object.keys(reasons).find(k=>String(raw).includes(k));
    return found?reasons[found]:(labels[job.status]||'任务未完成')+'；板端原因：'+String(raw).slice(0,240);
  }
  function terminal(job,state){return !!job&&!waiting.has(job.status)&&state.active!==job.id;}
  function connectionGate(s,online){
    if(!online||!s||s.board_connected!==true)return {ready:false,message:s?.connection_monitor?.message||'正在自动连接板子'};
    if(s.connection_monitor?.service_available===false)return {ready:false,message:s.connection_monitor.message};
    if(typeof s.boot_id!=='string'||!s.boot_id)return {ready:false,message:'板端身份尚未确认'};
    if(s.closed)return {ready:false,message:'定位服务已结束'};
    if(s.service_lifecycle?.ready===false)return {ready:false,message:s.service_lifecycle.message||'正在核对定位服务'};
    if(s.runtime_ready!==true)return {ready:false,message:'板子已连接，定位服务未就绪'};
    if(s.full_turn_initialization_supported!==true)return {ready:false,message:'整圈初始化程序等待接续'};
    if(s.can_request!==true)return {ready:false,message:'板端正在完成当前任务'};
    return {ready:true,message:'连接成功，定位服务已就绪'};
  }
  function reconcile(s,pending){
    if(!pending)return {pending:null,completed:null};
    if(pending.boot_id&&s?.boot_id!==pending.boot_id)return {pending,completed:null};
    const job=(s?.jobs||[]).find(j=>j.id===pending.id);
    if(terminal(job,s))return {pending:null,completed:job};
    return {pending,completed:null};
  }
  function responseOutcome(code,body,id){
    // A transport/5xx failure may have happened after board acceptance.
    if(code===202&&body?.id===id)return 'ACCEPTED';
    if(code>=400&&code<500&&body?.outcome!=='UNKNOWN_DO_NOT_RESEND')return 'REJECTED';
    return 'UNKNOWN';
  }
  function derivePhase(s,pending,online,now=Date.now(),lastNotice=null){
    const base={show:true,running:false,title:'等待板端连接',detail:'正在读取板端状态。',next:'暂时无需点击；连接成功且定位服务就绪后，“开始初始化”会启用。',button:'等待连接',allow:false};
    if(!online||!s||s.board_connected!==true)return {...base,detail:pending?'本次请求结果待核对；连接恢复后继续读取同一请求，不重复发送。':'板端连接不可用，界面会自动重连。',next:'连接检测持续运行，恢复后自动更新；初始化需要手动点击。'};
    const gate=connectionGate(s,online), job=(s.jobs||[]).find(j=>j.id===s.active);
    if(s.active_action==='initialize'){
      const clock=s.observer_monotonic??s.monotonic;
      const elapsed=Number.isFinite(clock)&&Number.isFinite(job?.trigger_monotonic)?Math.max(0,clock-job.trigger_monotonic):null;
      const phases={REACQUIRING_STATIC_SCREEN:'正在用静止画面重新寻找标识',RETURNING_TO_STATIC_SCREEN_CENTER:'正在返回实测标识中心',CONFIRMING_STATIONARY_AUDIO:'转稳后确认声音方向',MOVING_TO_SCAN_START:'前往扫描起点',SCANNING_ONE_FULL_TURN:'正在扫描一圈',ANALYZING_SCAN_FRAMES:'正在分析扫描画面',RETURNING_TO_DETECTED_SCREEN:'正在返回已找到的屏幕',MEASURING_AXIS_WITH_STATIC_FRAMES:'正在用静止画面测定转轴',CONFIRMING_SCREEN_CENTER:'正在确认屏幕中心'};
      const step=phases[s.display_phase?.visual_step]||'板端正在初始化';
      return {...base,running:true,title:step,detail:'请不要关闭或遮挡界面。'+(elapsed===null?'板端已确认任务开始。':'已用 '+elapsed.toFixed(1)+' 秒。')+' 本次不设总时限。',next:'扫描完成后会返回目标并测轴；成功后显示“初始化完成”并进入雷达。',button:'初始化执行中'};
    }
    if(pending){
      const unknown=pending.delivery==='UNKNOWN'||now-pending.sentAt>4000;
      return {...base,title:unknown?'请求结果待核对':'正在等待板端接收确认',detail:'请不要关闭或遮挡界面。'+(unknown?'尚未取得本次任务的结束结果；界面继续查询，不重复发送。':'初始化指令已提交，尚未确认开始。'),next:'不用继续点击。“刷新连接状态”只更新显示，不重发任务。',button:'等待本次请求结果'};
    }
    if(s.full_turn_initialization_supported!==true&&s.runtime_ready===true){
      const previous=(s.jobs||[]).filter(j=>j.action==='initialize'&&terminal(j,s)).slice(-1)[0];
      return {...base,title:'整圈初始化程序等待接续',detail:(previous?failure(previous)+'。':'')+'当前服务尚不支持整圈初始化。',next:'程序持续接收服务状态；具备整圈能力且就绪后才启用初始化。',button:'等待整圈程序'};
    }
    const aligned=s.full_turn_initialization_supported===true&&s.runtime_ready===true&&!s.closed&&s.calibration==='RELATIVE_SINGLE_SOURCE';
    if(aligned)return {...base,show:false,title:'初始化完成',detail:'坐标已对齐。',next:gate.ready?'点击雷达旁的声源卡片查看目标；点“返回声源列表”可切换。':'板端正在更新方向，完成后可点选声源。',button:'重新初始化',allow:gate.ready};
    if(!gate.ready){
      const life=s.service_lifecycle,elapsed=Number.isFinite(life?.elapsed_seconds)?' 已等待 '+life.elapsed_seconds.toFixed(0)+' 秒。':'';
      return {...base,title:gate.message,detail:s.connection_monitor?.service_available===false?'自动连接正在运行；板子可达，定位服务尚未响应。界面会继续检测并显示恢复结果。':life?.message?life.message+elapsed:(s.runtime_error?'板端报告：'+s.runtime_error:(s.runtime_ready!==true?'当前没有可用的定位服务，初始化尚未开始。':'等待当前任务自然结束。')),next:s.runtime_ready!==true?'连接和服务状态会自动更新；服务就绪后，请手动点击“开始初始化”。':'当前无需再点击。',button:s.runtime_ready!==true?'等待定位服务':'板端任务进行中'};
    }
    if(s.pc_audio?.ready!==true)return {...base,title:'正在准备电脑测试音',detail:s.pc_audio?.message||'播放控制尚未就绪',next:'程序会自动检查；就绪后可点击初始化。',button:'等待测试音'};
    const last=(s.jobs||[]).filter(j=>j.action==='initialize'&&terminal(j,s)).slice(-1)[0];
    if(last||lastNotice)return {...base,title:'初始化未完成',detail:last?failure(last):lastNotice,next:'失败原因已保留。条件变化后可点“重新初始化”；刷新连接不会转动舵机。',button:'重新初始化',allow:true};
    return {...base,title:'准备就绪',detail:'连接成功，定位服务已就绪。',next:'下一步：点一次“开始初始化”，随后保持本窗口可见、无遮挡。',button:'开始初始化',allow:true};
  }
  function previewRotation(preview){return /[?&]source=completed_preview(?:&|$)/.test(preview?.image_url||'')?0:180;}
  const api={connectionGate,derivePhase,reconcile,responseOutcome,terminal,failure,labels,previewRotation};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  root.avControl=api;
})(typeof window==='undefined'?globalThis:window);
