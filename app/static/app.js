


  const tg = window.Telegram?.WebApp; if (tg) { tg.ready(); tg.expand(); }
  const initData = tg?.initData || ""; let me = null; let profiles = []; let currentProfileId = null; let todayRows = []; let todayFilter = 'pending'; let addMedicineOpen=false; let addAssignmentOpen=false; let addInventoryOpen=false; let aiEnabled=false; localStorage.setItem('todayFilter','pending');
  const savedTheme = localStorage.getItem('theme') || (tg?.colorScheme === 'dark' ? 'dark' : 'light');
  setTheme(savedTheme);
  function setTheme(t){document.documentElement.dataset.theme=t;localStorage.setItem('theme',t);document.getElementById('themeBtn').textContent=t==='dark'?'☀️':'🌙'}
  function toggleTheme(){setTheme(document.documentElement.dataset.theme==='dark'?'light':'dark')}
  function toast(text){const el=document.getElementById('toast');el.textContent=text;el.style.display='block';setTimeout(()=>el.style.display='none',3000)}
  async function api(path, options={}){const headers={'X-Telegram-Init-Data':initData,'X-Profile-Id':currentProfileId?String(currentProfileId):'',...(options.headers||{})}; if(!(options.body instanceof FormData) && !headers['Content-Type']) headers['Content-Type']='application/json'; const res=await fetch(path,{...options,headers});if(!res.ok)throw new Error(await res.text());const txt=await res.text();return txt?JSON.parse(txt):{}}
  let modalResolver=null;
  function closeModal(value=null){document.getElementById('modal').classList.add('hidden');if(modalResolver){modalResolver(value);modalResolver=null}}
  function closeModalByBackdrop(e){if(e.target.id==='modal')closeModal(null)}
  function modalSubmit(){if(window.__modalSubmit)window.__modalSubmit();else closeModal(true)}
  function openModal(title, bodyHtml, submitFn, okText='Сохранить'){document.getElementById('modalTitle').textContent=title;document.getElementById('modalBody').innerHTML=bodyHtml;document.getElementById('modalOk').textContent=okText;document.getElementById('modal').classList.remove('hidden');window.__modalSubmit=submitFn;return new Promise(resolve=>{modalResolver=resolve})}
  async function chooseTime(title, current){
    const value=(current || new Date().toTimeString().slice(0,5));
    return await openModal(title, `<label>Время</label><input id="modalTime" type="time" value="${escapeHtml(value)}">`, ()=>closeModal(document.getElementById('modalTime').value));
  }
  function selectStatusOption(value){
    document.querySelectorAll('.statusOption').forEach(el=>el.classList.toggle('selected', el.dataset.status===value));
    const hidden=document.getElementById('modalStatus');
    if(hidden) hidden.value=value;
  }
  async function chooseStatus(current){
    const option=(value, label)=>`<button type="button" class="statusOption ${current===value?'selected':''}" data-status="${value}" onclick="selectStatusOption('${value}')"><span class="statusDot"></span><span>${label}</span></button>`;
    return await openModal('Изменить статус', `<input id="modalStatus" type="hidden" value="${escapeHtml(current||'pending')}"><div class="statusChoice">
      ${option('pending','⏳ Не принято')}
      ${option('taken','✅ Принято')}
      ${option('skipped','⏭️ Пропущено')}
    </div>`, ()=>closeModal(document.getElementById('modalStatus').value));
  }
  async function confirmAction(title, text, warn=''){
    const html = `<div class="confirmText">${escapeHtml(text)}</div>${warn?`<div class="confirmWarn">${escapeHtml(warn)}</div>`:''}`;
    return await openModal(title, html, ()=>closeModal(true), 'Подтвердить');
  }

  function profileLabel(p){return (p.kind==='personal'?'👤 ':'👶 ') + p.name}
  function renderProfileChips(){
    const root=document.getElementById('profileChips');
    root.innerHTML=profiles.map(p=>`<button class="profileChip ${p.id===currentProfileId?'active':''}" onclick="changeProfile(${p.id})">${escapeHtml(profileLabel(p))}</button>`).join('');
    document.getElementById('profileBar').classList.toggle('hidden', profiles.length<1);
  }
  async function loadProfiles(){
    profiles = await api('/api/profiles');
    if(!profiles.length)return;
    currentProfileId = (profiles.find(p=>p.active)?.id) || Number(localStorage.getItem('activeProfileId')) || profiles[0].id;
    if(!profiles.some(p=>p.id===currentProfileId)) currentProfileId = profiles[0].id;
    renderProfileChips();
  }

  async function changeProfile(profileId){
    currentProfileId=Number(profileId);
    localStorage.setItem('activeProfileId', String(currentProfileId));
    renderProfileChips();
    try{ await api('/api/active-profile',{method:'POST',body:JSON.stringify({profile_id:currentProfileId})}); }catch(e){}
    editSchedules.clear();
    await loadToday();
    if(!document.getElementById('pageStats').classList.contains('hidden')) await loadStats();
    if(!document.getElementById('pageHistory').classList.contains('hidden')) await loadMedicines();
    if(!document.getElementById('pageAdmin').classList.contains('hidden')) { showAdminTab(currentAdminTab||'assignments'); }
  }

  function pageId(name){return 'page'+name[0].toUpperCase()+name.slice(1)} function tabId(name){return 'tab'+name[0].toUpperCase()+name.slice(1)}
  let currentAdminTab='assignments';
  let assignmentAiDraft=null;
  let assignmentAiFile=null;
  function showAdminTab(name){
    currentAdminTab=name;
    document.getElementById('adminTabAssignments')?.classList.toggle('active', name==='assignments');
    document.getElementById('adminTabMeds')?.classList.toggle('active', name==='meds');
    document.getElementById('adminTabInventory')?.classList.toggle('active', name==='inventory');
    document.getElementById('adminTabProfiles')?.classList.toggle('active', name==='profiles');
    document.getElementById('adminPanelAssignments')?.classList.toggle('hidden', name!=='assignments');
    document.getElementById('adminPanelMeds')?.classList.toggle('hidden', name!=='meds');
    document.getElementById('adminPanelInventory')?.classList.toggle('hidden', name!=='inventory');
    document.getElementById('adminPanelProfiles')?.classList.toggle('hidden', name!=='profiles');
    if(name==='assignments'){loadCourses();}
    if(name==='meds'){loadCourses();loadSchedules();loadAudit();}
    if(name==='inventory'){loadInventory();}
    if(name==='profiles'){loadProfileAdmin();}
  }
  function showTab(name){['today','stats','history','admin'].forEach(t=>{document.getElementById(pageId(t))?.classList.add('hidden');document.getElementById(tabId(t))?.classList.remove('active')});document.getElementById(pageId(name)).classList.remove('hidden');document.getElementById(tabId(name)).classList.add('active');location.hash=name==='today'?'':'#'+name;if(name==='today')loadToday();if(name==='stats')loadStats();if(name==='history')loadMedicines();if(name==='admin'){showAdminTab(currentAdminTab||'assignments');}window.scrollTo({top:0,behavior:'smooth'});}
  function setTodayFilter(f){todayFilter=f;localStorage.setItem('todayFilter',f);renderToday()}
  function applyFilter(rows){if(todayFilter==='all')return rows;if(todayFilter==='skipped')return rows.filter(r=>r.status==='skipped');if(todayFilter==='taken')return rows.filter(r=>r.status==='taken');if(todayFilter==='snoozed')return rows.filter(r=>r.status==='pending'&&r.postponed_until);return rows.filter(r=>r.status==='pending')}
  function statusText(r){if(r.status==='taken')return '✅ принято в '+(r.taken_at||'—');if(r.status==='skipped')return '⏭️ пропущено '+(r.skipped_at||'');if(r.postponed_until)return '😴 отложено до '+r.postponed_until;return '⏳ ждет отметки'}
  function escapeAttr(v){return String(v??'').replace(/&/g,'&amp;').replace(/'/g,'&#39;').replace(/"/g,'&quot;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}
  function escapeHtml(s){return String(s??'').replace(/[&<>\"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
  function fmtNum(v){
    const n=Number(v || 0);
    if(!Number.isFinite(n)) return '0';
    if(Math.abs(n - Math.round(n)) < 0.000001) return String(Math.round(n));
    return String(Math.round(n * 100) / 100).replace('.', ',');
  }

  window.fmtNum = fmtNum;
  function groupKey(r){return `${r.time}|${r.label||''}`}
  function groupRows(rows){
    const groups=[]; const map=new Map();
    rows.forEach(r=>{const k=groupKey(r); if(!map.has(k)){map.set(k,{time:r.time,label:r.label||'',rows:[]});groups.push(map.get(k));} map.get(k).rows.push(r);});
    return groups;
  }
  function renderToday(){
    ['pending','snoozed','skipped','taken','all'].forEach(f=>document.getElementById('filter'+f[0].toUpperCase()+f.slice(1))?.classList.toggle('active',todayFilter===f));
    const root=document.getElementById('today');const rows=applyFilter(todayRows);
    if(!rows.length){root.innerHTML='<div class="empty">По этому фильтру приемов нет</div>';return}
    root.innerHTML=groupRows(rows).map(g=>{
      const pending=g.rows.filter(r=>r.status==='pending');
      const allPending=pending.length===g.rows.length && pending.length>1;
      const groupActions=allPending?`<div class="groupActions"><button class="ok" onclick="takeGroup('${g.time}','${escapeHtml(g.label)}')">Принял все</button><button class="warn" onclick="snoozeGroup('${g.time}','${escapeHtml(g.label)}')">+30 все</button><button class="danger" onclick="skipGroup('${g.time}','${escapeHtml(g.label)}')">Пропустить все</button></div>`:'';
      const items=g.rows.map(r=>{
        let controls=''; let st=statusText(r);
        if(r.status==='pending' && !allPending){controls=`<div class="pendingActions"><button class="ok" onclick="take(${r.id})">Выпил</button><button class="warn" onclick="snooze(${r.id})">+30 мин</button><button class="danger" onclick="skip(${r.id})">Пропустил</button></div>`}
        else if(r.status==='taken'){controls=`<div class="doneActions"><div class="resultPill"><strong>✅ Принято</strong><span>факт ${r.taken_at||'—'}</span></div><button class="editTimeBtn" onclick="editTakenTime(${r.id}, '${r.taken_at||''}', '${r.time||''}')">Изменить время</button><button class="changeStatusBtn" onclick="changeStatus(${r.id}, '${r.status}', '${r.taken_at||''}', '${r.time||''}')">Изменить статус</button></div>`}
        else if(r.status==='skipped'){controls=`<div class="doneActions skipOnly"><div class="resultPill"><strong>⏭️ Пропущено</strong><span>${r.skipped_at||''}</span></div><button class="changeStatusBtn" onclick="changeStatus(${r.id}, '${r.status}', '${r.taken_at||''}', '${r.time||''}')">Изменить статус</button></div>`}
        return `<div class="groupDose"><div class="groupDoseMain"><div class="med">${escapeHtml(r.medicine)}</div><div class="meta">${escapeHtml(r.dose)}</div></div><div class="groupDoseStatus">${escapeHtml(st)}</div><div style="grid-column:1/-1">${controls}</div></div>`;
      }).join('');
      return `<div class="dayGroup"><div class="groupHead"><div class="groupTime">${g.time}</div><div class="groupMeta">${escapeHtml(g.label||'Прием')}</div></div>${items}${groupActions}</div>`
    }).join('')
  }
  async function loadToday(){todayRows=await api('/api/today');renderToday()}
  async function take(id){
    const now=new Date().toTimeString().slice(0,5);
    const actual=await chooseTime('Фактическое время приема', now);
    if(actual===null)return;
    if(!/^\d{2}:\d{2}$/.test(actual)){toast('Выберите время');return}
    const data=await api(`/api/events/${id}/take`,{method:'POST',body:JSON.stringify({actual_time:actual})});
    toast(data.message||'Сохранено');
    await loadToday();
    if(tg?.HapticFeedback)tg.HapticFeedback.notificationOccurred('success')
  }
  async function editTakenTime(id,current,planned){const value=await chooseTime('Фактическое время приема', current || planned);if(value===null)return;if(!/^\d{2}:\d{2}$/.test(value)){toast('Выберите время');return}const data=await api(`/api/events/${id}/taken-time`,{method:'PATCH',body:JSON.stringify({actual_time:value})});toast(data.message||'Время обновлено');await loadToday()}

  async function changeStatus(id,current,currentTime,planned){
    const status=await chooseStatus(current);
    if(!status)return;
    let actual_time=null;
    if(status==='taken'){
      actual_time=await chooseTime('Фактическое время приема', currentTime || planned);
      if(actual_time===null)return;
      if(!/^\d{2}:\d{2}$/.test(actual_time)){toast('Выберите время');return}
    }
    const data=await api(`/api/events/${id}/status`,{method:'PATCH',body:JSON.stringify({status, actual_time})});
    toast(data.message||'Статус обновлен');
    await loadToday();
  }

  async function skip(id){await api(`/api/events/${id}/skip`,{method:'POST',body:JSON.stringify({})});toast('Пропуск сохранен');loadToday()}
  async function snooze(id){const data=await api(`/api/events/${id}/snooze`,{method:'POST',body:JSON.stringify({})});toast(data.message||'Отложено');loadToday()}
  function eventsForGroup(time,label){return todayRows.filter(r=>r.time===time && (r.label||'')===label && r.status==='pending')}
  async function takeGroup(time,label){
    const rows=eventsForGroup(time,label); if(!rows.length)return;
    const actual=await chooseTime('Фактическое время группового приема', new Date().toTimeString().slice(0,5));
    if(actual===null)return;
    const data=await api('/api/events/batch/take',{method:'POST',body:JSON.stringify({ids:rows.map(r=>r.id), actual_time:actual})});
    toast(data.message||`Отмечено приемов: ${rows.length}`); await loadToday(); loadAudit();
  }
  async function skipGroup(time,label){
    const rows=eventsForGroup(time,label); if(!rows.length)return;
    const ok=await confirmAction('Пропустить все?', `Отметить как пропущенные ${rows.length} приема на ${time}?`, 'Действие попадет в журнал изменений.');
    if(!ok)return;
    const data=await api('/api/events/batch/skip',{method:'POST',body:JSON.stringify({ids:rows.map(r=>r.id)})});
    toast(data.message||`Пропущено приемов: ${rows.length}`); await loadToday(); loadAudit();
  }
  async function snoozeGroup(time,label){
    const rows=eventsForGroup(time,label); if(!rows.length)return;
    const data=await api('/api/events/batch/snooze',{method:'POST',body:JSON.stringify({ids:rows.map(r=>r.id)})});
    toast(data.message||`Отложено приемов: ${rows.length}`); await loadToday(); loadAudit();
  }
  function setCollapseButton(btnId, open, openText, closedText){
    const btn=document.getElementById(btnId); if(!btn)return;
    const label=btn.querySelector('span:first-child'); const chev=btn.querySelector('.chev');
    if(label) label.textContent=open?openText:closedText;
    if(chev) chev.textContent=open?'−':'＋';
  }
  function toggleAddMedicineForm(){
    addMedicineOpen=!addMedicineOpen;
    document.getElementById('addMedicineForm')?.classList.toggle('hidden', !addMedicineOpen);
    setCollapseButton('addMedicineToggle', addMedicineOpen, 'Скрыть форму добавления', 'Добавить лекарство');
  }
  function toggleAddAssignmentForm(){
    addAssignmentOpen=!addAssignmentOpen;
    document.getElementById('courseAddForm')?.classList.toggle('hidden', !addAssignmentOpen);
    setCollapseButton('courseAddToggle', addAssignmentOpen, 'Скрыть форму добавления', 'Добавить назначение');
  }
  function toggleAddInventoryForm(){
    addInventoryOpen=!addInventoryOpen;
    document.getElementById('addInventoryForm')?.classList.toggle('hidden', !addInventoryOpen);
    setCollapseButton('addInventoryToggle', addInventoryOpen, 'Скрыть форму добавления', 'Добавить в аптечку');
  }
  function populateMedicineFilter(selectId, rows, getName){
    const sel=document.getElementById(selectId); if(!sel)return '';
    const current=sel.value || '';
    const names=[...new Set(rows.map(getName).filter(Boolean))].sort((a,b)=>a.localeCompare(b,'ru'));
    sel.innerHTML='<option value="">Все лекарства</option>'+names.map(n=>`<option value="${escapeAttr(n)}">${escapeHtml(n)}</option>`).join('');
    if(current && names.includes(current)) sel.value=current;
    else sel.value='';
    return sel.value || '';
  }
  async function populateInventoryMedicineSelect(){
    const sel=document.getElementById('invName'); if(!sel)return;
    const current=sel.value || '';
    let rows=[];
    try{ rows=await api('/api/medicine-options'); }catch(e){ rows=[]; }
    const names=[...new Set(rows.map(r=>r.name).filter(Boolean))].sort((a,b)=>a.localeCompare(b,'ru'));
    sel.innerHTML='<option value="">Выберите лекарство</option>'+names.map(n=>`<option value="${escapeAttr(n)}">${escapeHtml(n)}</option>`).join('');
    if(current && names.includes(current)) sel.value=current;
  }
  function attachmentUrl(id){return `/api/attachments/${id}?profile_id=${currentProfileId||''}`}
  function inventoryPhotoUrl(i){return `${i.photo_url}?profile_id=${currentProfileId||''}&t=${Date.now()}`}
  function openFilePreview(url, filename='', isImage=false){
    const safeUrl=escapeAttr(url); const safeName=escapeHtml(filename||'Файл');
    const body=isImage
      ? `<img class="mediaPreview" src="${safeUrl}" alt="${safeName}">`
      : `<div class="filePreviewBox"><div style="font-weight:900;margin-bottom:8px">${safeName}</div><a href="${safeUrl}" target="_blank" rel="noopener">Открыть файл</a></div>`;
    return openModal(safeName, body, ()=>closeModal(true), 'Закрыть');
  }
  function isImageFilename(name=''){return /\.(png|jpe?g|gif|webp|heic|heif)$/i.test(name)}

  function aiDisabledHtml(){return '<div class="empty">ИИ-помощник выключен. Добавьте OPENAI_API_KEY и OPENAI_ENABLED=true в Railway.</div>'}
  function aiMedicineSummary(m){return `${escapeHtml(m.name||'—')} · ${escapeHtml(m.dose||'—')} · ${escapeHtml(m.comment||'')}`}
  function frequencyValueFromAI(m){
    const c=Number(m.frequency_count||1);
    const u=m.frequency_unit||'day';
    if(u==='week') return 'weekly:7:1';
    if(u==='2weeks') return 'weekly:14:1';
    if(u==='month') return 'monthly:30:1';
    if(c===2) return 'daily:1:2';
    if(c===3) return 'daily:1:3';
    return 'daily:1:1';
  }
  function applyAIMedicine(m){
    if(!m)return;
    const name=document.getElementById('name'), dose=document.getElementById('dose');
    if(name) name.value=m.name||'';
    if(dose) dose.value=m.dose||'';
    const start=document.getElementById('medStart'), end=document.getElementById('medEnd');
    if(start && m.start_date) start.value=m.start_date;
    if(end && m.end_date) end.value=m.end_date;
    const tpl=document.getElementById('timingTemplate'); if(tpl && m.timing_template) tpl.value=m.timing_template;
    const freq=document.getElementById('frequency'); if(freq) freq.value=frequencyValueFromAI(m);
    renderFrequencySlots();
    const meals=Array.isArray(m.meals)?m.meals:[]; const times=Array.isArray(m.times)?m.times:[];
    const f=parseFrequency();
    for(let i=0;i<f.count;i++){
      const mealEl=document.getElementById('slotMeal'+i); if(mealEl && meals[i]) mealEl.value=meals[i];
      const timeEl=document.getElementById('slotTime'+i); if(timeEl && times[i]) timeEl.value=times[i];
      const labelEl=document.getElementById('slotLabel'+i); if(labelEl && (m.comment||'')) labelEl.value=m.comment||'';
    }
    toast('Форма заполнена. Проверьте данные перед сохранением.');
    if(!addMedicineOpen) toggleAddMedicineForm();
  }
  async function aiParseMedicineText(){
    if(!aiEnabled){toast('ИИ-помощник выключен');return}
    const text=document.getElementById('aiMedicineText')?.value.trim();
    if(!text){toast('Опишите назначение текстом');return}
    const res=await api('/api/ai/parse-medicine',{method:'POST',body:JSON.stringify({text})});
    const meds=res.medicines||[];
    if(!meds.length){toast('Не удалось распознать назначение');return}
    if(meds.length===1){applyAIMedicine(meds[0]);return}
    const body=`<div class="aiDraftList">${meds.map((m,i)=>`<button class="statusOption" onclick="closeModal(${i})"><span>${aiMedicineSummary(m)}</span></button>`).join('')}</div><div class="muted" style="font-size:12px;margin-top:8px">Выберите препарат, которым заполнить форму. Остальные можно добавить по очереди.</div>`;
    const idx=await openModal('Распознано несколько препаратов', body, ()=>closeModal(null), 'Закрыть');
    if(idx!==null && meds[idx]) applyAIMedicine(meds[idx]);
  }
  async function aiParsePrescriptionFile(){
    if(!aiEnabled){toast('ИИ-помощник выключен');return}
    const file=document.getElementById('aiPrescriptionFile')?.files?.[0];
    if(!file){toast('Выберите фото или файл назначения');return}
    const fd=new FormData(); fd.append('file', file);
    const res=await api('/api/ai/parse-prescription',{method:'POST',body:fd,headers:{}});
    const meds=res.medicines||[];
    if(!meds.length){toast('Не удалось распознать препараты');return}
    const warn=(res.warnings||[]).map(w=>`<div class="confirmWarn">${escapeHtml(w)}</div>`).join('');
    const body=`${warn}<div class="aiDraftList">${meds.map((m,i)=>`<button class="statusOption" onclick="closeModal(${i})"><span>${aiMedicineSummary(m)}</span></button>`).join('')}</div><div class="muted" style="font-size:12px;margin-top:8px">Выберите препарат, которым заполнить форму. Перед сохранением обязательно проверьте данные по назначению врача.</div>`;
    const idx=await openModal('Черновик из назначения', body, ()=>closeModal(null), 'Закрыть');
    if(idx!==null && meds[idx]) applyAIMedicine(meds[idx]);
  }
  async function aiRecognizeInventoryPhoto(){
    if(!aiEnabled){toast('ИИ-помощник выключен');return}
    const file=document.getElementById('invPhoto')?.files?.[0];
    if(!file){toast('Сначала выберите фото лекарства');return}
    const fd=new FormData(); fd.append('file', file);
    const res=await api('/api/ai/recognize-inventory-photo',{method:'POST',body:fd,headers:{}});
    const name=(res.name||'').trim();
    if(name){
      const sel=document.getElementById('invName'); const manual=document.getElementById('invNameManual');
      if(sel && [...sel.options].some(o=>o.value===name)) sel.value=name; else { if(sel)sel.value=''; if(manual)manual.value=name; document.getElementById('invManualLine')?.classList.remove('hidden'); }
    }
    if(res.unit_name && document.getElementById('invUnit')) document.getElementById('invUnit').value=res.unit_name;
    toast(name?'Название распознано. Проверьте перед сохранением.':'Не удалось уверенно распознать название');
  }
  async function aiDoctorReportDraft(){
    if(!aiEnabled){toast('ИИ-помощник выключен');return}
    const days=Number(document.getElementById('aiReportDays')?.value||30);
    const res=await api('/api/ai/report-draft',{method:'POST',body:JSON.stringify({days})});
    const body=`<div class="reportDraft"><h4>${escapeHtml(res.title||'Черновик отчета')}</h4><p>${escapeHtml(res.summary||'')}</p><b>Факты:</b><ul>${(res.bullets||[]).map(x=>`<li>${escapeHtml(x)}</li>`).join('')}</ul><b>Вопросы врачу:</b><ul>${(res.questions_for_doctor||[]).map(x=>`<li>${escapeHtml(x)}</li>`).join('')}</ul></div>`;
    await openModal('Черновик отчета для врача', body, ()=>closeModal(true), 'Закрыть');
  }


  function todayIso(){return new Date().toISOString().slice(0,10)}
  function aiAssignmentName(d){return (d.assignment_name||'').trim() || (d.doctor?`Назначение: ${d.doctor}`:'Назначение врача')}
  function aiMedicinePlainSummary(m){
    const parts=[m.name||'—', m.dose||'—'];
    const f=frequencyValueFromAI(m);
    const fm={"daily:1:1":"1 раз в день","daily:1:2":"2 раза в день","daily:1:3":"3 раза в день","weekly:7:1":"1 раз в неделю","weekly:14:1":"1 раз в 2 недели","monthly:30:1":"1 раз в месяц","daily:2:1":"1 раз в 2 дня","daily:3:1":"1 раз в 3 дня"}[f]||f;
    parts.push(fm);
    const tpl={fixed:'фиксированное время',before_meal:'за 30 мин до еды',with_meal:'во время еды',after_meal:'после еды'}[m.timing_template||'fixed']||'';
    if(tpl) parts.push(tpl);
    if(Array.isArray(m.times)&&m.times.length) parts.push(m.times.join(', '));
    if(Array.isArray(m.meals)&&m.meals.length) parts.push(m.meals.map(x=>({breakfast:'завтрак',lunch:'обед',dinner:'ужин'}[x]||x)).join(', '));
    if(m.start_date||m.end_date) parts.push(`${m.start_date||'—'} — ${m.end_date||'—'}`);
    return parts.filter(Boolean).join(' · ');
  }
  function selectedDraftMedIndexes(){return [...document.querySelectorAll('.aiDraftCheck:checked')].map(x=>Number(x.value)).filter(n=>!Number.isNaN(n));}
  function renderAssignmentAiPreview(data){
    assignmentAiDraft=data||{};
    const root=document.getElementById('assignmentAiPreview');
    if(!root)return;
    const meds=assignmentAiDraft.medicines||[];
    const warnings=(assignmentAiDraft.warnings||[]).map(w=>`<div class="confirmWarn">${escapeHtml(w)}</div>`).join('');
    root.innerHTML=`<div class="aiAssignmentPreview">
      <div class="aiBlock"><h3>1. Общие данные назначения</h3>
        ${warnings}
        <div class="fieldLine"><label>Название</label><input id="aiAssignName" value="${escapeAttr(aiAssignmentName(assignmentAiDraft))}"></div>
        <div class="fieldLine"><label>Дата назначения</label><input id="aiAssignDate" type="date" value="${escapeAttr(assignmentAiDraft.assignment_date||todayIso())}"></div>
        <div class="fieldLine"><label>Врач</label><input id="aiAssignDoctor" value="${escapeAttr(assignmentAiDraft.doctor||'')}"></div>
        <div class="fieldLine"><label>Комментарий</label><input id="aiAssignComment" value="${escapeAttr(assignmentAiDraft.comment||'')}"></div>
      </div>
      <div class="aiBlock"><h3>2. Распознанные лекарства</h3>
        ${meds.length?meds.map((m,i)=>`<div class="aiMedCard"><div class="aiMedTop"><input class="aiDraftCheck" type="checkbox" value="${i}" checked><div><div class="aiMedName">${escapeHtml(m.name||'Без названия')}</div><div class="aiMedMeta">${escapeHtml(aiMedicinePlainSummary(m))}</div>${m.comment?`<div class="aiMedMeta">${escapeHtml(m.comment)}</div>`:''}</div></div><div class="aiMedActions"><button class="gray" onclick="applyAIMedicineFromDraft(${i})">В форму</button><button onclick="addSingleDraftMedicine(${i})">Добавить</button></div></div>`).join(''):'<div class="empty">Препараты не распознаны</div>'}
      </div>
      <div class="aiBlock"><h3>3. Действия</h3>
        <div class="aiPreviewActions"><button onclick="createAssignmentFromDraft(true)" ${meds.length?'':'disabled'}>Создать назначение и добавить выбранные</button><button class="gray" onclick="createAssignmentFromDraft(false)">Сохранить только назначение</button></div>
        <button class="danger wide" style="margin-top:8px" onclick="clearAssignmentDraft()">Отмена</button>
        <div class="muted" style="font-size:12px;margin-top:8px">Проверьте распознанные данные перед сохранением. ИИ может ошибиться в названиях, дозах, датах или привязке ко времени еды.</div>
      </div>
    </div>`;
  }
  function clearAssignmentDraft(){assignmentAiDraft=null;assignmentAiFile=null;const root=document.getElementById('assignmentAiPreview');if(root)root.innerHTML='';const f=document.getElementById('aiAssignmentFile');if(f)f.value='';}
  async function aiRecognizeAssignment(){
    if(!aiEnabled){toast('ИИ-помощник выключен');return}
    const file=document.getElementById('aiAssignmentFile')?.files?.[0];
    if(!file){toast('Выберите фото назначения');return}
    assignmentAiFile=file;
    const fd=new FormData();fd.append('file',file);
    toast('Распознаю назначение...');
    const res=await api('/api/ai/parse-prescription',{method:'POST',body:fd,headers:{}});
    renderAssignmentAiPreview(res);
  }
  function applyAIMedicineFromDraft(i){
    const m=(assignmentAiDraft?.medicines||[])[i];
    if(!m)return;
    showAdminTab('meds');
    applyAIMedicine(m);
  }
  function buildSchedulePayloadFromAIMed(m, courseId){
    const fval=frequencyValueFromAI(m);
    const [recType,interval,countRaw]=fval.split(':');
    const count=Number(countRaw)||1;
    const timingTemplate=m.timing_template||'fixed';
    const meals=Array.isArray(m.meals)?m.meals:[];
    const times=Array.isArray(m.times)?m.times:[];
    const defaultTimes=['09:00','15:00','21:00'];
    const defaultMeals=count===3?['breakfast','lunch','dinner']:(count===2?['breakfast','dinner']:['breakfast']);
    const entries=[];
    for(let idx=0;idx<count;idx++){
      let meal=meals[idx]||defaultMeals[idx]||'breakfast';
      const offset=timingTemplate==='before_meal'?-30:(timingTemplate==='after_meal'?10:0);
      const time=timingTemplate==='fixed'?(times[idx]||defaultTimes[idx]||'09:00'):({breakfast:'08:00',lunch:'13:30',dinner:'19:30'}[meal]||'09:00');
      const label=(m.comment||'').trim() || (timingTemplate==='fixed'?time:templateLabel(timingTemplate,meal));
      entries.push({time_local:time,label,timing_template:timingTemplate,meal_name:timingTemplate==='fixed'?'':meal,meal_offset_minutes:offset});
    }
    return {name:(m.name||'').trim(),dose:(m.dose||'').trim(),course_id:courseId,start_date:m.start_date||null,end_date:m.end_date||null,recurrence_type:recType||'daily',recurrence_interval_days:Number(interval)||1,weekdays:Array.isArray(m.weekdays)?m.weekdays.join(','):'',specific_dates:Array.isArray(m.specific_dates)?m.specific_dates.join(','):'',dosage_form:m.dosage_form||m.form||'',administration_route:m.administration_route||m.route||m.comment||'',analogs:Array.isArray(m.analogs)?m.analogs.join(', '):(m.analogs||''),entries};
  }
  async function addAIMedicineToCourse(m, courseId){
    const payload=buildSchedulePayloadFromAIMed(m, courseId);
    if(!payload.name||!payload.dose||!payload.entries.length) throw new Error('Недостаточно данных по препарату');
    return await api('/api/schedules',{method:'POST',body:JSON.stringify(payload)});
  }
  async function createCourseFromDraft(){
    const p={name:document.getElementById('aiAssignName')?.value.trim()||'Назначение врача',assignment_date:document.getElementById('aiAssignDate')?.value||null,doctor:document.getElementById('aiAssignDoctor')?.value.trim()||'',comment:document.getElementById('aiAssignComment')?.value.trim()||''};
    const created=await api('/api/courses',{method:'POST',body:JSON.stringify(p)});
    if(assignmentAiFile) await uploadFileToCourse(created.id, assignmentAiFile);
    return created;
  }
  async function createAssignmentFromDraft(withMeds){
    if(!assignmentAiDraft){toast('Сначала распознайте назначение');return}
    const course=await createCourseFromDraft();
    let added=0;
    if(withMeds){
      const meds=assignmentAiDraft.medicines||[];
      for(const i of selectedDraftMedIndexes()){
        try{await addAIMedicineToCourse(meds[i], course.id);added++;}catch(e){console.warn(e)}
      }
    }
    toast(withMeds?`Назначение создано, лекарств добавлено: ${added}`:'Назначение сохранено');
    clearAssignmentDraft();
    await loadCourses();await loadSchedules();await loadToday();await loadAudit();
  }
  async function addSingleDraftMedicine(i){
    if(!assignmentAiDraft){return}
    const m=(assignmentAiDraft.medicines||[])[i];
    if(!m)return;
    const course=await createCourseFromDraft();
    await addAIMedicineToCourse(m, course.id);
    toast('Назначение и препарат добавлены');
    clearAssignmentDraft();
    await loadCourses();await loadSchedules();await loadToday();await loadAudit();
  }


  function assignmentNeedHtml(courseId, rows){
    if(!rows || !rows.length) return '<div class="needBox muted">К назначению пока не привязаны лекарства.</div>';
    const totalShortage = rows.reduce((sum,r)=>sum+Number(r.shortage_units||0),0);
    return `<div class="needBox"><b>Лекарства по назначению</b>${rows.map(r=>`<div class="needLine"><b>${escapeHtml(r.name)}</b>: нужно ${Number(r.planned_units_total||0)} ${escapeHtml(r.consume_unit_name||'шт')} · осталось нужно ${Number(r.remaining_need_units||0)}${r.inventory_quantity!==null&&r.inventory_quantity!==undefined?` · в аптечке ${Number(r.inventory_quantity||0)}`:''}${Number(r.shortage_units||0)>0?` · ⚠️ не хватает ${Number(r.shortage_units||0)}`:''}</div>`).join('')}${totalShortage>0?`<div class="confirmWarn">По назначению есть дефицит лекарств: ${totalShortage}</div>`:''}</div>`;
  }

  async function loadCourses(){
    if(!me?.can_manage_current_profile)return;
    const rows=await api('/api/courses');
    coursesCache = rows || [];
    let schedRows=[]; try{ schedRows=await api('/api/schedules'); }catch(e){ schedRows=[]; }
    const needsByCourse = {};
    schedRows.forEach(r=>{ if(!r.course_id)return; (needsByCourse[r.course_id] ||= []).push(r); });
    const sel=document.getElementById('courseSelect');
    if(sel) sel.innerHTML=courseOptionsHtml(sel.value);
    const root=document.getElementById('coursesBox');
    root.innerHTML=`<div class="formrow aiBox"><b>Распознать назначение</b><div class="muted" style="font-size:12px;margin-top:4px">Загрузите фото назначения врача. Приложение покажет общие данные, список препаратов и действия для подтверждения.</div><div class="grid2" style="margin-top:8px"><input id="aiAssignmentFile" type="file" accept="image/*"><button class="gray" onclick="aiRecognizeAssignment()" ${aiEnabled?'':'disabled'}>Распознать назначение</button></div>${aiEnabled?'':'<div class="muted" style="font-size:12px;margin-top:6px">ИИ выключен в настройках Railway</div>'}</div><div id="assignmentAiPreview"></div><button id="courseAddToggle" class="collapseToggle" onclick="toggleAddAssignmentForm()"><span>${addAssignmentOpen?'Скрыть форму добавления':'Добавить назначение вручную'}</span><span class="chev">${addAssignmentOpen?'−':'＋'}</span></button><div id="courseAddForm" class="courseAddForm ${addAssignmentOpen?'':'hidden'}"><div class="fieldLine"><label>Название</label><input id="courseName" placeholder="Например, назначение гастроэнтеролога"></div><div class="fieldLine"><label>Дата назначения</label><input id="courseDate" type="date"></div><div class="fieldLine"><label>Врач</label><input id="courseDoctor" placeholder="опционально"></div><div class="fieldLine"><label>Комментарий</label><input id="courseComment" placeholder="опционально"></div><div class="fieldLine"><label>Фото / файл</label><input id="courseFile" type="file" accept="image/*,.pdf,.doc,.docx,.txt"></div><button class="wide" onclick="addCourse()" style="margin-top:8px">Сохранить назначение</button></div><div class="adminList" style="margin-top:14px">${rows.length?rows.map(c=>`<div class="formrow"><b>${escapeHtml(c.name)}</b><div class="muted" style="font-size:12px;margin-top:3px">Дата назначения: ${escapeHtml(c.assignment_date||'—')}${c.doctor?' · '+escapeHtml(c.doctor):''}</div>${c.comment?`<div class="muted" style="font-size:12px;margin-top:3px">${escapeHtml(c.comment)}</div>`:''}<div class="attachments">${(c.attachments||[]).map(a=>`<button class="fileLinkBtn" onclick="openFilePreview('${attachmentUrl(a.id)}', '${escapeAttr(a.filename)}', ${isImageFilename(a.filename)})">📎 ${escapeHtml(a.filename)}</button>`).join('')}</div>${assignmentNeedHtml(c.id, needsByCourse[c.id]||[])}<div class="adminActions"><button class="gray" onclick="editCourse(${c.id}, '${escapeAttr(c.name)}', '${c.assignment_date||''}', '${escapeAttr(c.doctor||'')}', '${escapeAttr(c.comment||'')}')">Изменить</button><button class="danger" onclick="deleteCourse(${c.id})">Удалить назначение</button></div><div class="fieldLine"><label>Добавить файл</label><input id="courseFile_${c.id}" type="file" accept="image/*,.pdf,.doc,.docx,.txt" onchange="uploadCourseFile(${c.id})"></div></div>`).join(''):'<div class="empty">Назначений пока нет</div>'}</div>`;
  }
  async function addCourse(){const p={name:document.getElementById('courseName').value.trim(),assignment_date:document.getElementById('courseDate').value||null,doctor:document.getElementById('courseDoctor').value.trim(),comment:document.getElementById('courseComment').value.trim()}; if(!p.name){toast('Укажите название назначения');return} const created=await api('/api/courses',{method:'POST',body:JSON.stringify(p)}); const file=document.getElementById('courseFile').files[0]; if(file){await uploadFileToCourse(created.id,file)} toast('Назначение добавлено'); addAssignmentOpen=false; await loadCourses(); await loadAudit()}
  async function editCourse(id,name,assignmentDate,doctor,comment){const body=`<label>Название</label><input id="cName" value="${name}"><label>Дата назначения</label><input id="cDate" type="date" value="${assignmentDate}"><label>Врач</label><input id="cDoctor" value="${doctor}"><label>Комментарий</label><input id="cComment" value="${comment}">`; const ok=await openModal('Изменить назначение', body, ()=>closeModal({name:document.getElementById('cName').value.trim(),assignment_date:document.getElementById('cDate').value||null,doctor:document.getElementById('cDoctor').value.trim(),comment:document.getElementById('cComment').value.trim()})); if(!ok)return; await api('/api/courses/'+id,{method:'PUT',body:JSON.stringify(ok)}); toast('Назначение обновлено'); await loadCourses(); await loadAudit()}
  async function deleteCourse(id){const ok=await confirmAction('Удалить назначение?', 'Назначение будет отключено вместе с лекарствами внутри него.', 'Действие попадет в журнал.'); if(!ok)return; await api('/api/courses/'+id,{method:'DELETE'}); toast('Назначение удалено'); await loadCourses(); await loadSchedules(); await loadToday(); await loadAudit()}
  async function uploadFileToCourse(id,file){const fd=new FormData();fd.append('file',file);await api('/api/courses/'+id+'/attachments',{method:'POST',body:fd,headers:{}})}
  async function uploadCourseFile(id){const el=document.getElementById('courseFile_'+id);if(!el?.files?.[0])return;await uploadFileToCourse(id,el.files[0]);toast('Файл добавлен');await loadCourses();await loadAudit()}
  const editSchedules = new Set();
  const scheduleDrafts = new Map();
  let coursesCache = [];
  function courseNameById(id){
    if(!id) return 'Без назначения';
    const c=(coursesCache||[]).find(x=>String(x.id)===String(id));
    return c ? (c.name || ('Назначение #' + id)) : ('Назначение #' + id);
  }
  function courseOptionsHtml(selected){
    return '<option value="">Без назначения</option>' + (coursesCache||[]).map(c=>`<option value="${c.id}" ${String(selected||'')===String(c.id)?'selected':''}>${escapeHtml(c.name)}</option>`).join('');
  }
  function stockLine(r){
    const unit = r.consume_unit_name || 'шт';
    const planned = Number(r.planned_units_total || 0);
    if(!planned) return '<div class="needLine muted">Потребность на курс пока не рассчитана: проверьте даты начала/окончания и дозу.</div>';
    const parts = [`нужно на курс: ${planned} ${escapeHtml(unit)}`];
    parts.push(`уже принято: ${Number(r.taken_units||0)} ${escapeHtml(unit)}`);
    parts.push(`осталось нужно: ${Number(r.remaining_need_units||0)} ${escapeHtml(unit)}`);
    if(r.inventory_quantity !== null && r.inventory_quantity !== undefined){
      parts.push(`в аптечке: ${Number(r.inventory_quantity||0)} ${escapeHtml(unit)}`);
      if(Number(r.shortage_units||0) > 0) parts.push(`⚠️ не хватает: ${Number(r.shortage_units||0)} ${escapeHtml(unit)}`);
    }
    return `<div class="needLine">${parts.join(' · ')}</div>`;
  }
  function fieldHtml(id, value, editable, type='text'){
    const safe = escapeHtml(value || '');
    if(editable) return `<input id="${id}" ${type!=='text'?`type="${type}"`:''} value="${safe}">`;
    return `<div id="${id}" class="valueBox ${safe?'':'isEmpty'}" data-value="${safe}">${safe || '—'}</div>`;
  }
  function scheduleForm(r){const edit=editSchedules.has(r.id);const slot=scheduleSlotText(r);const courseName=assignmentGroupName(r);if(edit)return `<div class="scheduleCard pretty"><div class="fieldLine"><label>Назначение</label>${courseSelectHtml(r.id,r.course_id)}</div><div class="scheduleGrid"><input id="s_name_${r.id}" value="${escapeAttr(r.name)}"><input id="s_dose_${r.id}" value="${escapeAttr(r.dose||'')}" placeholder="Доза"><input id="s_time_${r.id}" type="time" value="${r.time_local}"></div><input id="s_label_${r.id}" value="${escapeAttr(r.label||'')}" placeholder="Комментарий"><div class="dateLine"><div><label>Дата начала</label><input id="s_start_${r.id}" type="date" value="${r.start_date||''}"></div><div><label>Дата окончания</label><input id="s_end_${r.id}" type="date" value="${r.end_date||''}"></div></div><div class="adminActions"><button onclick="saveSchedule(${r.id})">Сохранить</button><button class="gray" onclick="cancelScheduleEdit(${r.id})">Отменить</button></div></div>`;const detailKey='sd_'+r.id;return `<div class="scheduleCard pretty"><div class="scheduleMain scheduleCourseToggle" onclick="toggleScheduleDetails('${detailKey}')"><div><div class="scheduleName">${escapeHtml(r.name)}</div><div class="scheduleMetaLine">Назначение: ${escapeHtml(courseName)} · Курс: ${escapeHtml(r.name)}</div></div><div class="scheduleSlot">${escapeHtml(slot)}</div></div><div id="${detailKey}" class="scheduleDetails hidden"><div class="muted" style="font-size:12px">${escapeHtml(r.dose||'')}${r.label?' · '+escapeHtml(r.label):''}<br>Период: ${r.start_date||'—'} — ${r.end_date||'—'}</div><div class="adminActions"><button class="gray" onclick="editSchedules.add(${r.id});loadSchedules()">Изменить</button><button class="danger" onclick="deleteSchedule(${r.id})">Удалить</button></div></div></div>`;}


  function enableScheduleEdit(id){editSchedules.add(id);loadSchedules()}
  function cancelScheduleEdit(id){editSchedules.delete(id);scheduleDrafts.delete(id);loadSchedules()}
  function toggleScheduleDetails(id){document.getElementById(id)?.classList.toggle('hidden')}
  function assignmentGroupName(r){const c=(coursesCache||[]).find(x=>Number(x.id)===Number(r.course_id));return c?.name || 'Без назначения';}
  async function loadSchedules(){if(!me?.can_manage_current_profile)return;if(!coursesCache.length){try{coursesCache=await api('/api/courses')}catch(e){coursesCache=[]}}let rows=await api('/api/schedules');const selected=populateMedicineFilter('scheduleSearch', rows, r=>r.name||'');if(selected)rows=rows.filter(r=>(r.name||'')===selected);const root=document.getElementById('schedules');const grouped=document.getElementById('scheduleGroupByAssignment')?.checked!==false;if(!rows.length){root.innerHTML='<div class="empty">Активное расписание пустое. Запустите курс в назначении.</div>';return}if(!grouped){root.innerHTML=`<div class="adminList">${rows.map(scheduleForm).join('')}</div>`;return}const map=new Map();rows.forEach(r=>{const k=assignmentGroupName(r);if(!map.has(k))map.set(k,[]);map.get(k).push(r)});root.innerHTML=[...map.entries()].map(([name,list])=>`<div class="scheduleGroup"><div class="scheduleGroupTitle">${escapeHtml(name)}</div><div class="adminList">${list.map(scheduleForm).join('')}</div></div>`).join('');}

  function readSchedule(id){const c=document.getElementById('s_course_'+id);return {name:document.getElementById('s_name_'+id).value.trim(),dose:document.getElementById('s_dose_'+id).value.trim(),time_local:document.getElementById('s_time_'+id).value,label:document.getElementById('s_label_'+id).value.trim(),course_id:c&&c.value?Number(c.value):null,start_date:document.getElementById('s_start_'+id).value||null,end_date:document.getElementById('s_end_'+id).value||null}}
  async function saveSchedule(id){const p=readSchedule(id);if(!p.name||!p.dose||!p.time_local){toast('Заполните лекарство, дозу и время');return}await api('/api/schedules/'+id,{method:'PUT',body:JSON.stringify(p)});editSchedules.delete(id);scheduleDrafts.delete(id);toast('Расписание обновлено');loadSchedules();loadToday();loadAudit()}
  function parseFrequency(){const [type,interval,count]=document.getElementById('frequency').value.split(':');return {recurrence_type:type, recurrence_interval_days:Number(interval), count:Number(count)}}
  function mealForIndex(i){return ['breakfast','lunch','dinner'][i] || 'breakfast'}
  function templateLabel(t, meal){const m={breakfast:'завтрак',lunch:'обед',dinner:'ужин'}[meal]||'еда'; return t==='before_meal'?`за 30 мин до: ${m}`:t==='with_meal'?`во время: ${m}`:t==='after_meal'?`после: ${m}`:''}
  function renderFrequencySlots(){const {count}=parseFrequency();const root=document.getElementById('frequencySlots');const t=document.getElementById('timingTemplate')?.value||'fixed';let defaults=['09:00','15:00','21:00'];root.innerHTML=Array.from({length:count},(_,i)=>{const meal=mealForIndex(i); const templateControls=t==='fixed'?`<div class="slotRow"><label>Время</label><input id="slotTime${i}" type="time" value="${defaults[i]||'09:00'}"></div><div class="slotRow"><label>Комментарий</label><input id="slotLabel${i}" placeholder="например, после еды"></div>`:`<div class="slotRow"><label>Еда</label><select id="slotMeal${i}"><option value="breakfast" ${meal==='breakfast'?'selected':''}>Завтрак</option><option value="lunch" ${meal==='lunch'?'selected':''}>Обед</option><option value="dinner" ${meal==='dinner'?'selected':''}>Ужин</option></select></div><div class="slotRow"><label>Комментарий</label><input id="slotLabel${i}" placeholder="${templateLabel(t,meal)}"></div>`; return `<div class="slotGroup"><div class="slotGroupTitle">Прием ${i+1}</div>${templateControls}</div>`}).join('')}
  let addScheduleInProgress=false;
  async function addSchedule(){
    if(!me?.can_manage_current_profile){toast('Нет доступа к управлению этим профилем');return}
    if(addScheduleInProgress){toast('Уже добавляю лекарство...');return}
    const f=parseFrequency();
    const entries=[];
    const seen=new Set();
    const timingTemplate=document.getElementById('timingTemplate').value;
    for(let i=0;i<f.count;i++){
      const mealEl=document.getElementById('slotMeal'+i);
      const meal=mealEl?mealEl.value:'';
      const offset=timingTemplate==='before_meal'?-30:(timingTemplate==='after_meal'?10:0);
      const time=timingTemplate==='fixed'?document.getElementById('slotTime'+i).value:({breakfast:'08:00',lunch:'13:30',dinner:'19:30'}[meal]||'09:00');
      const label=(document.getElementById('slotLabel'+i).value.trim() || (timingTemplate==='fixed'?time:templateLabel(timingTemplate,meal)));
      const key=`${time}|${label}|${timingTemplate}|${meal}|${offset}`;
      if(time && !seen.has(key)){seen.add(key);entries.push({time_local:time,label:label||time,timing_template:timingTemplate,meal_name:meal,meal_offset_minutes:offset})}
    }
    const weekdays=[...document.getElementById('weekdays').selectedOptions].map(o=>o.value).join(',');
    const payload={name:document.getElementById('name').value.trim(),dose:document.getElementById('dose').value.trim(),course_id:document.getElementById('courseSelect').value?Number(document.getElementById('courseSelect').value):null,start_date:document.getElementById('medStart').value||null,end_date:document.getElementById('medEnd').value||null,recurrence_type:f.recurrence_type,recurrence_interval_days:f.recurrence_interval_days,weekdays,specific_dates:'',entries};
    if(!payload.name||!payload.dose||!entries.length){toast('Заполните лекарство, дозу и хотя бы одно время');return}
    addScheduleInProgress=true;
    try{
      const res=await api('/api/schedules',{method:'POST',body:JSON.stringify(payload)});
      toast(`Лекарство добавлено: ${res.count||entries.length} прием(а)`);
      ['name','dose','medStart','medEnd'].forEach(id=>{const el=document.getElementById(id); if(el) el.value='';});
      document.getElementById('frequency').value='daily:1:1';
      renderFrequencySlots();
      addMedicineOpen=false;
      document.getElementById('addMedicineForm')?.classList.add('hidden');
      setCollapseButton('addMedicineToggle', false, 'Скрыть форму добавления', 'Добавить лекарство');
      await loadSchedules();
      await loadToday();
      await loadAudit();
    } finally { addScheduleInProgress=false; }
  }
  async function deleteSchedule(id){const ok=await confirmAction('Удалить прием?', 'Удалить этот прием из расписания?', 'Будущие напоминания по нему больше не будут создаваться.'); if(!ok)return; await api('/api/schedules/'+id,{method:'DELETE'});toast('Прием удален');loadSchedules();loadToday();loadAudit()}
  async function loadProfileAdmin(){
    const root=document.getElementById('profileAdmin');
    if(!me?.is_parent){root.innerHTML='<div class="empty">Управление профилями доступно только родителям</div>';return}
    const rows=await api('/api/profiles');
    root.innerHTML=`<div class="sectionTools"><button class="smallBtn" onclick="createChildProfile()">+ Ребенок</button></div><div class="profileList">${rows.map(p=>`<div class="profileItem"><div class="profileName">${escapeHtml(profileLabel(p))}</div><button class="smallBtn gray" onclick="renameProfile(${p.id}, '${escapeHtml(p.name)}')">Имя</button>${p.kind==='child'?`<button class="smallBtn danger" onclick="deleteProfile(${p.id})">Удалить</button>`:'<span></span>'}</div>`).join('')}</div>`;
  }
  async function createChildProfile(){const name=await openModal('Новый детский профиль', '<label>Имя</label><input id="profileNameInput" placeholder="Например, Саша">', ()=>closeModal(document.getElementById('profileNameInput').value.trim())); if(!name)return; await api('/api/profiles',{method:'POST',body:JSON.stringify({name})}); toast('Профиль создан'); await loadProfiles(); await loadProfileAdmin();}
  async function renameProfile(id,current){const name=await openModal('Переименовать профиль', `<label>Имя</label><input id="profileNameInput" value="${current}">`, ()=>closeModal(document.getElementById('profileNameInput').value.trim())); if(!name)return; await api('/api/profiles/'+id,{method:'PUT',body:JSON.stringify({name})}); toast('Профиль обновлен'); await loadProfiles(); await loadProfileAdmin();}
  async function deleteProfile(id){const ok=await confirmAction('Удалить профиль?', 'Удалить детский профиль из списка?', 'Расписание профиля будет скрыто вместе с профилем.'); if(!ok)return; await api('/api/profiles/'+id,{method:'DELETE'}); toast('Профиль удален'); localStorage.removeItem('activeProfileId'); await loadProfiles(); await loadProfileAdmin(); await loadToday();}
  function actionLabel(a){return {event_taken:'Принято',event_time_changed:'Изменено время',event_status_changed:'Изменен статус',event_skipped:'Пропуск',event_snoozed:'Отложено',schedule_created:'Добавлено расписание',schedule_updated:'Изменено расписание',schedule_deleted:'Удалено расписание',profile_created:'Создан профиль',profile_renamed:'Переименован профиль',profile_deleted:'Удален профиль',course_created:'Создано назначение',course_updated:'Изменено назначение',course_deleted:'Удалено назначение',assignment_file_added:'Добавлен файл',inventory_created:'Аптечка: добавлено',inventory_updated:'Аптечка: обновлено',inventory_deleted:'Аптечка: удалено',inventory_photo_added:'Аптечка: фото'}[a]||a}
  async function loadAudit(){const root=document.getElementById('auditLog');try{const rows=await api('/api/audit'); if(!rows.length){root.innerHTML='<div class="empty">Журнал пока пуст</div>';return} root.innerHTML=`<div class="auditList">${rows.map(r=>`<div class="auditItem"><div class="auditTop"><span>${escapeHtml(r.created_at)}</span><span>${escapeHtml(actionLabel(r.action))}</span></div><div class="auditText">${escapeHtml(r.details||'—')}</div></div>`).join('')}</div>`;}catch(e){root.innerHTML='<div class="empty">Журнал недоступен</div>'}}

  async function loadInventory(){
    if(!me?.can_manage_current_profile)return;
    await populateInventoryMedicineSelect();
    let rows=await api('/api/inventory');
    const selected=populateMedicineFilter('inventorySearch', rows, i=>i.name||'');
    if(selected)rows=rows.filter(i=>(i.name||'')===selected);
    const root=document.getElementById('inventoryBox');
    if(!rows.length){root.innerHTML='<div class="empty">Аптечка пустая</div>';return}
    root.innerHTML=`<div class="adminList">${rows.map(i=>`<div class="formrow"><div><div class="med">${escapeHtml(i.name)}</div><div class="meta">Осталось: ${i.quantity} ${escapeHtml(i.unit_name||'шт')} · напомнить при ${i.low_threshold}</div></div><div class="attachments">${i.photo_url?`<button class="fileLinkBtn" onclick="openFilePreview('${inventoryPhotoUrl(i)}', 'Фото: ${escapeAttr(i.name)}', true)">📷 Фото лекарства</button>`:''}</div><div class="adminActions"><button class="gray" onclick="editInventory(${i.id}, '${escapeAttr(i.name)}', ${i.quantity}, '${escapeAttr(i.unit_name||'шт')}', ${i.low_threshold})">Изменить</button><button class="danger" onclick="deleteInventory(${i.id})">Удалить</button></div><div class="fieldLine"><label>Заменить фото</label><input type="file" accept="image/*" onchange="uploadInventoryPhoto(${i.id}, this.files[0])"></div></div>`).join('')}</div>`;
  }
  async function addInventory(){const name=document.getElementById('invName').value;if(!name){toast('Выберите лекарство из списка');return}const res=await api('/api/inventory',{method:'POST',body:JSON.stringify({name,quantity:Number(document.getElementById('invQty').value||0),unit_name:document.getElementById('invUnit').value||'шт',low_threshold:Number(document.getElementById('invThreshold').value||0)})});const file=document.getElementById('invPhoto').files[0];if(file)await uploadInventoryPhoto(res.id,file,false);document.getElementById('invName').value='';document.getElementById('invPhoto').value='';document.getElementById('invQty').value='0';toast('Добавлено в аптечку');addInventoryOpen=false;document.getElementById('addInventoryForm')?.classList.add('hidden');setCollapseButton('addInventoryToggle', false, 'Скрыть форму добавления', 'Добавить в аптечку');await loadInventory();await loadAudit()}
  async function editInventory(id,name,qty,unit,thr){const body=`<label>Лекарство</label><input id="eiName" value="${name}" readonly><label>Остаток</label><input id="eiQty" type="number" min="0" value="${qty}"><label>Ед. изм.</label><input id="eiUnit" value="${unit}"><label>Напомнить при</label><input id="eiThr" type="number" min="0" value="${thr}">`;const ok=await openModal('Изменить запас', body, ()=>closeModal({name:document.getElementById('eiName').value.trim(),quantity:Number(document.getElementById('eiQty').value||0),unit_name:document.getElementById('eiUnit').value||'шт',low_threshold:Number(document.getElementById('eiThr').value||0)}));if(!ok)return;await api('/api/inventory/'+id,{method:'PUT',body:JSON.stringify(ok)});toast('Запас обновлен');await loadInventory();await loadAudit()}
  async function deleteInventory(id){const ok=await confirmAction('Удалить из аптечки?', 'Запись о запасе лекарства будет скрыта.');if(!ok)return;await api('/api/inventory/'+id,{method:'DELETE'});toast('Удалено из аптечки');await loadInventory();await loadAudit()}
  async function uploadInventoryPhoto(id,file,reload=true){if(!file)return;const fd=new FormData();fd.append('file',file);await api('/api/inventory/'+id+'/photo',{method:'POST',body:fd,headers:{}});toast('Фото сохранено');if(reload)await loadInventory()}
  async function downloadReport(kind){const res=await fetch(`/api/reports/doctor.${kind}?days=30`,{headers:{'X-Telegram-Init-Data':initData,'X-Profile-Id':currentProfileId?String(currentProfileId):''}});if(!res.ok){toast('Не удалось сформировать отчет');return}const blob=await res.blob();const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=kind==='pdf'?'doctor_report.pdf':'doctor_report.xlsx';document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000)}

  async function loadStats(){const rows=await api('/api/stats');const root=document.getElementById('stats');const report=`<div class="formrow" style="margin-bottom:12px"><b>ИИ-черновик отчета для врача</b><div class="fieldLine"><label>Период</label><select id="aiReportDays"><option value="7">7 дней</option><option value="30" selected>30 дней</option><option value="90">90 дней</option></select></div><button class="wide gray" onclick="aiDoctorReportDraft()" ${aiEnabled?'':'disabled'}>Подготовить черновик</button>${aiEnabled?'':'<div class="muted" style="font-size:12px;margin-top:6px">ИИ выключен в настройках Railway</div>'}</div>`;if(!rows.length){root.innerHTML=report+'<div class="empty">Статистики пока нет</div>';return}root.innerHTML=report+`<div class="tableWrap"><table class="miniTable statsTable"><tr><th>Препарат</th><th>✅</th><th>⏭️</th><th>⏳</th><th>%</th></tr>${rows.map(r=>`<tr><td>${escapeHtml(r.medicine)}</td><td>${r.taken}</td><td>${r.skipped}</td><td>${r.pending}</td><td>${r.taken_percent}%</td></tr>`).join('')}</table></div>`}
  async function loadMedicines(){const select=document.getElementById('medicineSelect');const meds=await api('/api/medicines');select.innerHTML=meds.map(m=>`<option value="${m.id}">${escapeHtml(m.name)}</option>`).join('');if(meds.length)loadHistory();else document.getElementById('history').innerHTML='<div class="empty">Препаратов пока нет</div>'}
  async function loadHistory(){const id=document.getElementById('medicineSelect').value;const root=document.getElementById('history');if(!id){root.textContent='Выберите препарат';return}const rows=await api(`/api/medicines/${id}/history`);if(!rows.length){root.innerHTML='<div class="empty">Истории пока нет</div>';return}root.innerHTML=`<div class="tableWrap"><table class="miniTable historyTable"><tr><th>Дата</th><th>План</th><th>Статус</th></tr>${rows.map(r=>`<tr><td>${r.date}</td><td>${r.due_time}<br>${escapeHtml(r.dose)}</td><td>${r.status==='taken'?'✅ '+(r.taken_at||''):r.status==='skipped'?'⏭️ '+(r.skipped_at||''):'⏳'}</td></tr>`).join('')}</table></div>`}



  // ===== v35: assignments/courses/schedule model =====
  let recognizeAssignmentOpen=false;
  window.courseGroups={};
  function renderFrequencySlots(){ const root=document.getElementById('frequencySlots'); if(!root) return; }
  function freqFromValue(value){const [type,interval,count]=(value||'daily:1:1').split(':');return {recurrence_type:type||'daily',recurrence_interval_days:Number(interval||1),count:Number(count||1)} }
  function freqValueFromGroup(g){const count=(g.entries||[]).length||1; return `${g.recurrence_type||'daily'}:${g.recurrence_interval_days||1}:${Math.min(3,Math.max(1,count))}`;}
  function mealNameRu(meal){return {breakfast:'Завтрак',lunch:'Обед',dinner:'Ужин'}[meal]||'Еда'}
  function templateText(t, meal){const m=mealNameRu(meal); return t==='before_meal'?`за 30 мин до: ${m}`:t==='with_meal'?`во время: ${m}`:t==='after_meal'?`после: ${m}`:'фиксированное время'}
  function slotText(row){const t=row.timing_template||'fixed'; if(t==='fixed'||!row.meal_name)return row.display_time||row.time_local||'—'; return templateText(t,row.meal_name);}
  function scheduleSlotText(r){return slotText(r)}
  function doseParts(dose){const m=String(dose||'').match(/([0-9]+(?:[\.,][0-9]+)?|\d+\/\d+)\s*([^0-9]*)/); return {amount:m?m[1]:'', unit:m?(m[2]||'').trim():''};}
  function durationUnitRu(unit,n){unit=unit||'days'; if(unit==='months')return n==1?'месяц':(n>=2&&n<=4?'месяца':'месяцев'); if(unit==='weeks')return n==1?'неделя':(n>=2&&n<=4?'недели':'недель'); return n==1?'день':(n>=2&&n<=4?'дня':'дней');}
  function durationText(start,end,dv=null,du=''){
    if(dv){return `${dv} ${durationUnitRu(du,dv)}`;}
    if(!start&&!end)return 'длительность не указана';
    if(start&&end){const a=new Date(start+'T00:00:00'),b=new Date(end+'T00:00:00');const days=Math.max(1,Math.round((b-a)/86400000)+1); if(days%30===0)return `${days/30} мес`; if(days===14)return '14 дней'; if(days===7)return '7 дней'; return `${days} дней`;}
    if(start&&!end)return `с ${start}`; return `до ${end}`;
  }
  function courseFreqText(g){const cnt=(g.entries||[]).length||1; const base=g.recurrence_type==='weekly'?'еженедельно':g.recurrence_type==='monthly'?'ежемесячно':(g.recurrence_interval_days>1?`раз в ${g.recurrence_interval_days} дн.`:'ежедневно'); return `${cnt} раз(а), ${base}`;}
  function courseApplyText(g){const t=g.timing_template||'fixed'; const times=[...new Set((g.entries||[]).map(slotText))].join(', '); if(t==='fixed')return times||'по времени'; return times||templateText(t,g.meal_name);}
  function courseNeedsStatus(g){const unit=escapeHtml(g.consume_unit_name||doseParts(g.dose).unit||'шт'); const stock=g.inventory_quantity==null?'—':fmtNum(g.inventory_quantity); const shortage=Number(g.shortage_units||0); const ok=g.inventory_quantity==null?'аптечка не заполнена':(shortage>0?`⚠️ не хватает ${fmtNum(shortage)} ${unit}`:'✅ хватает'); return {unit,stock,shortage,ok};}
  function courseNeedPanel(g){const x=courseNeedsStatus(g);return `<div class="needPanel"><div><b>Расчет на курс</b></div><div class="needLine">Нужно: <b>${fmtNum(g.planned_units_total||0)} ${x.unit}</b> · уже принято: <b>${fmtNum(g.taken_units||0)} ${x.unit}</b></div><div class="needLine">Осталось нужно: <b>${fmtNum(g.remaining_need_units||0)} ${x.unit}</b> · в аптечке: <b>${x.stock} ${x.unit}</b></div><div class="courseNeedStatus">${x.ok}</div></div>`;}
  function courseCourseHeader(g,key){const dur=durationText(g.start_date,g.end_date,g.duration_value,g.duration_unit);const meta=[escapeHtml(g.dose||''), escapeHtml(courseFreqText(g)), escapeHtml(courseApplyText(g))].filter(Boolean).join(' · ');return `<div class="courseGroupHeader" onclick="toggleCourseGroup('${key}')"><div><div class="courseTitle">${escapeHtml(g.name||'Лекарство')} ${dur&&dur!=='длительность не указана'?`<span class="muted">· ${escapeHtml(dur)}</span>`:''}</div><div class="courseDuration">${meta}</div><div class="courseMetaLine"><span class="courseChip">${g.active_all?'активен':(g.active_any?'частично активен':'не начат')}</span>${g.analogs?'<span class="courseChip">есть аналоги</span>':''}</div></div><div class="chev" id="chev_${key}">＋</div></div>`}
  function toggleCourseGroup(key){const el=document.getElementById('courseBody_'+key); const ch=document.getElementById('chev_'+key); if(!el)return; const hid=el.classList.toggle('hidden'); if(ch)ch.textContent=hid?'＋':'−';}
  function courseGroupBody(g,key){
    const dp=doseParts(g.dose);
    const period=`${g.start_date||'—'} — ${g.end_date||'—'}`;
    const analogs=(g.analogs||'').trim();
    const route=(g.administration_route||'').trim();
    const form=(g.dosage_form||dp.unit||'').trim();
    const inactiveActions = g.active_all ? '' : `<button onclick="startScheduleGroup('${g.ids.join(',')}')">Начать курс</button>`;
    const rows=[
      ['Формат', form||'—'],
      ['Дозировка', g.dose||'—'],
      ['Периодичность', courseFreqText(g)],
      ['Прием', courseApplyText(g)],
      ['Способ применения', route||'—'],
      ['Длительность', durationText(g.start_date,g.end_date,g.duration_value,g.duration_unit)],
      ['Период', period]
    ];
    const details=rows.map(([k,v])=>`<div class="detailRow"><span>${escapeHtml(k)}</span><b>${escapeHtml(v)}</b></div>`).join('');
    return `<div id="courseBody_${key}" class="courseGroupBody hidden"><div class="courseDetailList">${details}${analogs?`<div class="detailRow analogsRow"><span>Аналоги</span><b>${escapeHtml(analogs)}</b></div>`:''}</div>${courseNeedPanel(g)}<div class="adminActions">${inactiveActions}<button class="gray" onclick="openHistoryImport('${key}')">Внести историю</button><button class="gray" onclick="openCourseMedicineForm(${g.course_id}, null, null, '${key}')">Изменить</button><button class="danger" onclick="deleteScheduleGroup('${g.ids.join(',')}')">Удалить</button></div></div>`;
  }
  function courseGroupCard(g){const key='cg_'+g.course_id+'_'+g.ids.join('_'); window.courseGroups[key]=g; return `<div class="courseMedCard">${courseCourseHeader(g,key)}${courseGroupBody(g,key)}</div>`;}
  function groupCourseItems(items){const map=new Map();(items||[]).forEach(it=>{const key=[it.course_id||'',(it.name||'').toLowerCase(),it.dose||'',it.start_date||'',it.end_date||'',it.recurrence_type||'',it.recurrence_interval_days||'',it.weekdays||'',it.specific_dates||'',it.timing_template||''].join('|');if(!map.has(key)) map.set(key,{...it, ids:[], entries:[], planned_units_total:0, remaining_need_units:0, taken_units:0, active_any:false, active_all:true});const g=map.get(key);g.ids.push(it.id); g.entries.push(it);g.planned_units_total += Number(it.planned_units_total||0);g.remaining_need_units += Number(it.remaining_need_units||0);g.taken_units += Number(it.taken_units||0);if(g.inventory_quantity==null && it.inventory_quantity!=null)g.inventory_quantity=it.inventory_quantity;g.consume_unit_name = g.consume_unit_name || it.consume_unit_name || doseParts(it.dose).unit || 'шт';g.active_any = g.active_any || !!it.active; g.active_all = g.active_all && !!it.active;g.shortage_units = Math.max(0, Number(g.remaining_need_units||0) - Number(g.inventory_quantity||0));g.duration_value = g.duration_value || it.duration_value; g.duration_unit = g.duration_unit || it.duration_unit;});return [...map.values()];}
  function courseItemsBlock(course){const groups=groupCourseItems(course.items||[]);return `<div class="courseItems"><div class="courseItemsTitle">Курсы лекарств</div>${groups.length?groups.map(courseGroupCard).join(''):'<div class="empty compactEmpty">Курсов лекарств пока нет</div>'}</div>`;}
  function recognizeBoxHtml(){return `<button class="collapseToggle" onclick="recognizeAssignmentOpen=!recognizeAssignmentOpen;loadCourses()"><span>✨ Распознать назначение</span><span class="chev">${recognizeAssignmentOpen?'−':'＋'}</span></button><div class="recognizeBox ${recognizeAssignmentOpen?'':'collapsed'}"><div class="recognizeHead"><div><div class="recognizeTitle">📷 Распознать назначение по фото</div><div class="muted" style="font-size:12px;margin-top:4px">Загрузите фото листа назначения. ИИ подготовит черновик назначения и курсов лекарств, а вы проверите и подтвердите.</div></div></div><div class="recognizeContent"><div class="grid2" style="margin-top:10px"><input id="aiAssignmentFile" type="file" accept="image/*"><button onclick="aiRecognizeAssignment()" ${aiEnabled?'':'disabled'}>Распознать</button></div>${aiEnabled?'':'<div class="muted" style="font-size:12px;margin-top:6px">ИИ выключен в настройках Railway</div>'}</div></div><div id="assignmentAiPreview"></div>`}
  async function loadCourses(){const root=document.getElementById('coursesBox'); if(!root)return; root.innerHTML='<div class="empty">Загружаю назначения...</div>';try{const raw=await api('/api/courses'); const rows=Array.isArray(raw)?raw:(Array.isArray(raw?.items)?raw.items:[]); coursesCache=rows; window.courseGroups={};const formHtml=`${recognizeBoxHtml()}<button id="courseAddToggle" class="collapseToggle" onclick="toggleAddAssignmentForm()"><span>${addAssignmentOpen?'Скрыть форму добавления':'Добавить назначение вручную'}</span><span class="chev">${addAssignmentOpen?'−':'＋'}</span></button><div id="courseAddForm" class="courseAddForm ${addAssignmentOpen?'':'hidden'}"><div class="fieldLine"><label>Название</label><input id="courseName" placeholder="Например, назначение гастроэнтеролога"></div><div class="fieldLine"><label>Дата назначения</label><input id="courseDate" type="date"></div><div class="fieldLine"><label>Врач</label><input id="courseDoctor" placeholder="опционально"></div><div class="fieldLine"><label>Комментарий</label><input id="courseComment" placeholder="опционально"></div><div class="fieldLine"><label>Фото / файл</label><input id="courseFile" type="file" accept="image/*,.pdf,.doc,.docx,.txt"></div><button class="wide" onclick="addCourse()" style="margin-top:8px">Сохранить назначение</button></div>`;const listHtml=rows.length?rows.map(c=>{const safeName=escapeAttr(c.name||''),safeDoctor=escapeAttr(c.doctor||''),safeComment=escapeAttr(c.comment||'');return `<div class="formrow assignmentCard"><b>${escapeHtml(c.name||'Без названия')}</b><div class="muted" style="font-size:12px;margin-top:3px">Дата назначения: ${escapeHtml(c.assignment_date||'—')}${c.doctor?' · '+escapeHtml(c.doctor):''}</div>${c.comment?`<div class="muted" style="font-size:12px;margin-top:3px">${escapeHtml(c.comment)}</div>`:''}<div class="attachments">${(c.attachments||[]).map(a=>`<button class="fileLinkBtn" onclick="openFilePreview('${attachmentUrl(a.id)}', '${escapeAttr(a.filename)}', ${isImageFilename(a.filename)})">📎 ${escapeHtml(a.filename)}</button>`).join('')}</div>${courseItemsBlock(c)}<div class="adminActions"><button onclick="openCourseMedicineForm(${c.id})">Добавить курс лекарства</button><button class="gray" onclick="aiAddMedicineToCourse(${c.id})" ${aiEnabled?'':'disabled'}>ИИ из текста</button><button class="gray" onclick="editCourse(${c.id}, '${safeName}', '${c.assignment_date||''}', '${safeDoctor}', '${safeComment}')">Изменить назначение</button><button class="danger" onclick="deleteCourse(${c.id})">Удалить назначение</button></div><div class="fieldLine"><label>Добавить файл</label><input id="courseFile_${c.id}" type="file" accept="image/*,.pdf,.doc,.docx,.txt" onchange="uploadCourseFile(${c.id})"></div></div>`}).join(''):'<div class="empty">Назначений пока нет</div>';root.innerHTML=formHtml+`<div class="adminList" style="margin-top:14px">${listHtml}</div>`;}catch(e){console.error('loadCourses failed',e);root.innerHTML=`<div class="empty">Не удалось загрузить назначения.<br><br><small>${escapeHtml(e.message||String(e))}</small></div>`;toast('Ошибка загрузки назначений');}}
  function courseMedicineModalHtml(courseId,item={}){return `<div class="fieldLine"><label>Препарат</label><input id="cm_name" value="${escapeAttr(item.name||'')}" placeholder="Например, Панкреатин"></div><div class="fieldLine"><label>Формат</label><input id="cm_form" value="${escapeAttr(item.dosage_form||doseParts(item.dose).unit||'')}" placeholder="таблетка, капсула, сироп, мл"></div><div class="fieldLine"><label>Дозировка</label><input id="cm_dose" value="${escapeAttr(item.dose||'')}" placeholder="1 капс, 1 таб, 5 мл"></div><div class="fieldLine"><label>Периодичность</label><select id="cm_template" onchange="renderCourseMedicineSlots()"><option value="fixed">В определенное время</option><option value="before_meal">За 30 мин до еды</option><option value="with_meal">Во время еды</option><option value="after_meal">После еды</option></select></div><div class="fieldLine"><label>Способ применения</label><input id="cm_route" value="${escapeAttr(item.administration_route||'')}" placeholder="внутрь, натощак, в нос, рассасывать"></div><div class="durationLine"><div><label>Длительность</label><input id="cm_duration_value" type="number" min="1" value="${escapeAttr(item.duration_value||'')}" placeholder="14"></div><div><label>Ед.</label><select id="cm_duration_unit"><option value="days">дней</option><option value="weeks">недель</option><option value="months">месяцев</option></select></div></div><div class="fieldLine"><label>Аналоги</label><input id="cm_analogs" value="${escapeAttr(item.analogs||'')}" placeholder="если указаны в назначении"></div><div class="dateLine"><div><label>Дата начала</label><input id="cm_start" type="date" value="${escapeAttr(item.start_date||'')}"></div><div><label>Дата окончания</label><input id="cm_end" type="date" value="${escapeAttr(item.end_date||'')}" placeholder="рассчитается"></div></div><div class="fieldLine"><label>Частота</label><select id="cm_frequency" onchange="renderCourseMedicineSlots()"><option value="daily:1:1">1 раз в день</option><option value="daily:1:2">2 раза в день</option><option value="daily:1:3">3 раза в день</option><option value="weekly:7:1">1 раз в неделю</option><option value="weekly:14:1">1 раз в 2 недели</option><option value="monthly:30:1">1 раз в месяц</option><option value="daily:2:1">1 раз в 2 дня</option><option value="daily:3:1">1 раз в 3 дня</option></select></div><div class="fieldLine"><label>Дни недели</label><select id="cm_weekdays" multiple size="3"><option value="0">Пн</option><option value="1">Вт</option><option value="2">Ср</option><option value="3">Чт</option><option value="4">Пт</option><option value="5">Сб</option><option value="6">Вс</option></select></div><div id="cm_slots" class="frequencySlots"></div><div class="muted" style="font-size:12px;margin-top:8px">При старте курса дата окончания будет рассчитана из даты начала и длительности, если не указана вручную.</div>`;}
  function renderCourseMedicineSlots(item={}){const root=document.getElementById('cm_slots');if(!root)return;const f=freqFromValue(document.getElementById('cm_frequency')?.value);const t=document.getElementById('cm_template')?.value||'fixed';const defaults=['09:00','15:00','21:00'];const entries=item.entries||[];root.innerHTML=Array.from({length:f.count},(_,i)=>{const e=entries[i]||{};const meal=e.meal_name||mealForIndex(i);const controls=t==='fixed'?`<div class="slotRow"><label>Время</label><input id="cm_time${i}" type="time" value="${e.time_local||defaults[i]||'09:00'}"></div><div class="slotRow"><label>Комментарий</label><input id="cm_label${i}" placeholder="например, после еды" value="${escapeAttr(e.label||'')}"></div>`:`<div class="slotRow"><label>Прием пищи</label><select id="cm_meal${i}"><option value="breakfast" ${meal==='breakfast'?'selected':''}>Завтрак</option><option value="lunch" ${meal==='lunch'?'selected':''}>Обед</option><option value="dinner" ${meal==='dinner'?'selected':''}>Ужин</option></select></div><div class="slotRow"><label>Комментарий</label><input id="cm_label${i}" placeholder="${templateLabel(t,meal)}" value="${escapeAttr(e.label||'')}"></div>`;return `<div class="slotGroup"><div class="slotGroupTitle">Прием ${i+1}</div>${controls}</div>`}).join('');}
  function readCourseMedicinePayload(courseId){const f=freqFromValue(document.getElementById('cm_frequency')?.value);const timingTemplate=document.getElementById('cm_template')?.value||'fixed';const entries=[];const seen=new Set();for(let i=0;i<f.count;i++){const mealEl=document.getElementById('cm_meal'+i);const meal=mealEl?mealEl.value:'';const offset=timingTemplate==='before_meal'?-30:(timingTemplate==='after_meal'?10:0);const time=timingTemplate==='fixed'?document.getElementById('cm_time'+i)?.value:({breakfast:'08:00',lunch:'13:30',dinner:'19:30'}[meal]||'09:00');const label=(document.getElementById('cm_label'+i)?.value.trim()||(timingTemplate==='fixed'?time:templateLabel(timingTemplate,meal)));const key=`${time}|${label}|${timingTemplate}|${meal}|${offset}`;if(time&&!seen.has(key)){seen.add(key);entries.push({time_local:time,label:label||time,timing_template:timingTemplate,meal_name:meal,meal_offset_minutes:offset});}}const weekdays=[...document.getElementById('cm_weekdays').selectedOptions].map(o=>o.value).join(',');return {name:document.getElementById('cm_name').value.trim(),dose:document.getElementById('cm_dose').value.trim(),course_id:courseId,start_date:document.getElementById('cm_start').value||null,end_date:document.getElementById('cm_end').value||null,duration_value:document.getElementById('cm_duration_value').value?Number(document.getElementById('cm_duration_value').value):null,duration_unit:document.getElementById('cm_duration_unit').value||'',recurrence_type:f.recurrence_type,recurrence_interval_days:f.recurrence_interval_days,weekdays,specific_dates:'',dosage_form:document.getElementById('cm_form').value.trim(),administration_route:document.getElementById('cm_route').value.trim(),analogs:document.getElementById('cm_analogs').value.trim(),entries};}
  async function openCourseMedicineForm(courseId,itemId=null,draft=null,groupKey=null){const course=(coursesCache||[]).find(c=>Number(c.id)===Number(courseId));let item=draft||{};let ids=[];if(groupKey&&window.courseGroups[groupKey]){item=window.courseGroups[groupKey];ids=item.ids||[];}else if(itemId&&course){const found=(course.items||[]).find(x=>Number(x.id)===Number(itemId))||{};item=found;ids=[itemId];}const body=courseMedicineModalHtml(courseId,item);setTimeout(()=>{if(item.timing_template)document.getElementById('cm_template').value=item.timing_template;if(item.duration_unit)document.getElementById('cm_duration_unit').value=item.duration_unit;if(item.recurrence_type){document.getElementById('cm_frequency').value=groupKey?freqValueFromGroup(item):`${item.recurrence_type}:${item.recurrence_interval_days||1}:1`;}renderCourseMedicineSlots(item);},0);const ok=await openModal(ids.length?'Изменить курс лекарства':'Добавить курс лекарства',body,()=>closeModal(readCourseMedicinePayload(courseId)),ids.length?'Сохранить':'Добавить');if(!ok)return;if(!ok.name||!ok.dose||!ok.entries.length){toast('Заполните препарат, дозировку и схему приема');return}if(!ids.length&&course&&(course.items||[]).some(x=>(x.name||'').toLowerCase()===ok.name.toLowerCase())){toast('В этом назначении это лекарство уже есть');return}if(ids.length){const entries=ok.entries||[];for(let i=0;i<entries.length;i++){const p={...ok,time_local:entries[i].time_local,label:entries[i].label,entries:[entries[i]]};if(ids[i]) await api('/api/schedules/'+ids[i],{method:'PUT',body:JSON.stringify(p)});else await api('/api/schedules',{method:'POST',body:JSON.stringify(p)});}for(let i=entries.length;i<ids.length;i++){await api('/api/schedules/'+ids[i],{method:'DELETE'});}}else await api('/api/schedules',{method:'POST',body:JSON.stringify(ok)});toast(ids.length?'Курс обновлен':'Курс лекарства добавлен');await loadCourses();await loadSchedules();await loadToday();await loadAudit();}
  async function startSchedule(id){ await api('/api/schedules/'+id+'/start',{method:'POST'}); toast('Курс начат'); await loadCourses(); await loadSchedules(); await loadToday(); await loadAudit(); }
  async function startScheduleGroup(idsCsv){const ids=idsCsv.split(',').map(Number).filter(Boolean); for(const id of ids){await api('/api/schedules/'+id+'/start',{method:'POST'});} toast('Курс начат'); await loadCourses(); await loadSchedules(); await loadToday(); await loadAudit();}
  async function deleteScheduleGroup(idsCsv){const ok=await confirmAction('Удалить курс лекарства?', 'Будут удалены все приемы этого лекарства в назначении.'); if(!ok)return; const ids=idsCsv.split(',').map(Number).filter(Boolean); for(const id of ids){await api('/api/schedules/'+id,{method:'DELETE'});} toast('Курс лекарства удален'); await loadCourses(); await loadSchedules(); await loadToday(); await loadAudit();}
  function courseSelectHtml(id, value){return `<select id="s_course_${id}"><option value="">Без назначения</option>${(coursesCache||[]).map(c=>`<option value="${c.id}" ${Number(value)===Number(c.id)?'selected':''}>${escapeHtml(c.name||'Назначение')}</option>`).join('')}</select>`}
  function toggleScheduleDetails(id){document.getElementById(id)?.classList.toggle('hidden')}
  function assignmentGroupName(r){const c=(coursesCache||[]).find(x=>Number(x.id)===Number(r.course_id));return c?.name || 'Без назначения';}
  function scheduleForm(r){const edit=editSchedules.has(r.id);const slot=scheduleSlotText(r);const courseName=assignmentGroupName(r);if(edit)return `<div class="scheduleCard pretty"><div class="fieldLine"><label>Назначение</label>${courseSelectHtml(r.id,r.course_id)}</div><div class="scheduleGrid"><input id="s_name_${r.id}" value="${escapeAttr(r.name)}"><input id="s_dose_${r.id}" value="${escapeAttr(r.dose||'')}" placeholder="Доза"><input id="s_time_${r.id}" type="time" value="${r.time_local}"></div><input id="s_label_${r.id}" value="${escapeAttr(r.label||'')}" placeholder="Комментарий"><div class="dateLine"><div><label>Дата начала</label><input id="s_start_${r.id}" type="date" value="${r.start_date||''}"></div><div><label>Дата окончания</label><input id="s_end_${r.id}" type="date" value="${r.end_date||''}"></div></div><div class="adminActions"><button onclick="saveSchedule(${r.id})">Сохранить</button><button class="gray" onclick="cancelScheduleEdit(${r.id})">Отменить</button></div></div>`;const detailKey='sd_'+r.id;return `<div class="scheduleCard pretty"><div class="scheduleMain scheduleCourseToggle" onclick="toggleScheduleDetails('${detailKey}')"><div><div class="scheduleName">${escapeHtml(r.name)}</div><div class="scheduleMetaLine">Назначение: ${escapeHtml(courseName)} · Курс: ${escapeHtml(r.name)}</div></div><div class="scheduleSlot">${escapeHtml(slot)}</div></div><div id="${detailKey}" class="scheduleDetails hidden"><div class="muted" style="font-size:12px">${escapeHtml(r.dose||'')}${r.label?' · '+escapeHtml(r.label):''}<br>Период: ${r.start_date||'—'} — ${r.end_date||'—'}</div><div class="adminActions"><button class="gray" onclick="editSchedules.add(${r.id});loadSchedules()">Изменить</button><button class="danger" onclick="deleteSchedule(${r.id})">Удалить</button></div></div></div>`;}
  async function loadSchedules(){if(!me?.can_manage_current_profile)return;if(!coursesCache.length){try{coursesCache=await api('/api/courses')}catch(e){coursesCache=[]}}let rows=await api('/api/schedules');const selected=populateMedicineFilter('scheduleSearch', rows, r=>r.name||'');if(selected)rows=rows.filter(r=>(r.name||'')===selected);const root=document.getElementById('schedules');const grouped=document.getElementById('scheduleGroupByAssignment')?.checked!==false;if(!rows.length){root.innerHTML='<div class="empty">Активное расписание пустое. Запустите курс в назначении.</div>';return}if(!grouped){root.innerHTML=`<div class="adminList">${rows.map(scheduleForm).join('')}</div>`;return}const map=new Map();rows.forEach(r=>{const k=assignmentGroupName(r);if(!map.has(k))map.set(k,[]);map.get(k).push(r)});root.innerHTML=[...map.entries()].map(([name,list])=>`<div class="scheduleGroup"><div class="scheduleGroupTitle">${escapeHtml(name)}</div><div class="adminList">${list.map(scheduleForm).join('')}</div></div>`).join('');}
  function readSchedule(id){return {name:document.getElementById('s_name_'+id).value.trim(),dose:document.getElementById('s_dose_'+id).value.trim(),time_local:document.getElementById('s_time_'+id).value,label:document.getElementById('s_label_'+id).value.trim(),start_date:document.getElementById('s_start_'+id).value||null,end_date:document.getElementById('s_end_'+id).value||null,course_id:document.getElementById('s_course_'+id)?.value?Number(document.getElementById('s_course_'+id).value):null,entries:[{time_local:document.getElementById('s_time_'+id).value,label:document.getElementById('s_label_'+id).value.trim(),timing_template:'fixed',meal_name:'',meal_offset_minutes:0}]}}
  async function saveSchedule(id){const p=readSchedule(id);if(!p.name||!p.dose||!p.time_local){toast('Заполните лекарство, дозу и время');return}await api('/api/schedules/'+id,{method:'PUT',body:JSON.stringify(p)});editSchedules.delete(id);toast('Расписание обновлено');loadSchedules();loadCourses();loadToday();loadAudit()}

  // ===== v36: physical MedicineCourse table + tree UI overrides =====
  let expandedAssignments = window.expandedAssignments || new Set();
  let expandedScheduleGroups = window.expandedScheduleGroups || new Set();
  let scheduleGroupByAssignmentUi = true;
  function assignmentHeaderMeta(c){return `Дата: ${escapeHtml(c.assignment_date||'—')}${c.doctor?' · '+escapeHtml(c.doctor):''}`}
  function toggleAssignmentTree(id){
    const key=Number(id);
    if(expandedAssignments.has(key)) expandedAssignments.delete(key); else expandedAssignments.add(key);
    const body=document.getElementById('assignmentBody_'+key);
    const chev=document.getElementById('assignmentChev_'+key);
    if(body){const isHidden=body.classList.toggle('hidden'); if(chev)chev.textContent=isHidden?'＋':'−';}
  }
  function toggleScheduleTreeGroup(key){
    if(expandedScheduleGroups.has(key)) expandedScheduleGroups.delete(key); else expandedScheduleGroups.add(key);
    const body=document.getElementById('scheduleTreeBody_'+key);
    const chev=document.getElementById('scheduleTreeChev_'+key);
    if(body){const isHidden=body.classList.toggle('hidden'); if(chev)chev.textContent=isHidden?'＋':'−';}
  }
  function setScheduleGroupsOpen(open){
    document.querySelectorAll('.scheduleTreeBody').forEach(el=>{el.classList.toggle('hidden',!open);});
    document.querySelectorAll('.scheduleTreeHead').forEach(head=>{const key=head.dataset.key; if(!key)return; if(open)expandedScheduleGroups.add(key); else expandedScheduleGroups.delete(key); const chev=document.getElementById('scheduleTreeChev_'+key); if(chev)chev.textContent=open?'−':'＋';});
  }
  function assignmentCourseGroups(course){ return groupCourseItems(course.items||[]); }
  function assignmentCoursesTree(course){
    const groups=assignmentCourseGroups(course);
    if(!groups.length) return '<div class="empty compactEmpty">Курсов лекарств пока нет</div>';
    return groups.map(g=>{
      const key='mc_'+(g.medicine_course_id||g.id||g.ids.join('_'));
      window.courseGroups[key]=g;
      const dur=durationText(g.start_date,g.end_date,g.duration_value,g.duration_unit);
      const open=!!document.getElementById('courseBody_'+key) && !document.getElementById('courseBody_'+key)?.classList.contains('hidden');
      return `<div class="courseTreeCard">${courseCourseHeader(g,key)}${courseGroupBody(g,key)}</div>`;
    }).join('');
  }
  async function loadCourses(){
    const root=document.getElementById('coursesBox'); if(!root)return; root.innerHTML='<div class="empty">Загружаю назначения...</div>';
    try{
      const raw=await api('/api/courses'); const rows=Array.isArray(raw)?raw:(Array.isArray(raw?.items)?raw.items:[]); coursesCache=rows; window.courseGroups={};
      const rootActions=`<div class="treeActions"><button onclick="recognizeAssignmentOpen=!recognizeAssignmentOpen;loadCourses()">✨ Распознать назначение</button><button class="gray" onclick="toggleAddAssignmentForm()">${addAssignmentOpen?'Скрыть форму':'Добавить назначение'}</button></div>`;
      const recognize=`<div class="recognizeBox ${recognizeAssignmentOpen?'':'collapsed'}" style="margin-top:10px"><div class="recognizeContent"><div class="recognizeTitle">📷 Распознать назначение по фото</div><div class="muted" style="font-size:12px;margin-top:4px">ИИ создаст черновик назначения и курсов. Перед сохранением проверьте данные.</div><div class="grid2" style="margin-top:10px"><input id="aiAssignmentFile" type="file" accept="image/*"><button onclick="aiRecognizeAssignment()" ${aiEnabled?'':'disabled'}>Распознать</button></div>${aiEnabled?'':'<div class="muted" style="font-size:12px;margin-top:6px">ИИ выключен в настройках Railway</div>'}</div></div><div id="assignmentAiPreview"></div>`;
      const addForm=`<div id="courseAddForm" class="courseAddForm ${addAssignmentOpen?'':'hidden'}" style="margin-top:10px"><div class="fieldLine"><label>Название</label><input id="courseName" placeholder="Например, назначение гастроэнтеролога"></div><div class="fieldLine"><label>Дата назначения</label><input id="courseDate" type="date"></div><div class="fieldLine"><label>Врач</label><input id="courseDoctor" placeholder="опционально"></div><div class="fieldLine"><label>Комментарий</label><input id="courseComment" placeholder="опционально"></div><div class="fieldLine"><label>Фото / файл</label><input id="courseFile" type="file" accept="image/*,.pdf,.doc,.docx,.txt"></div><button class="wide" onclick="addCourse()" style="margin-top:8px">Сохранить назначение</button></div>`;
      const list=rows.length?rows.map(c=>{
        const open=expandedAssignments.has(c.id);
        const safeName=escapeAttr(c.name||''),safeDoctor=escapeAttr(c.doctor||''),safeComment=escapeAttr(c.comment||'');
        return `<div class="assignmentTreeCard"><div class="assignmentTreeHead" onclick="toggleAssignmentTree(${c.id})"><div><div class="assignmentTreeTitle">${escapeHtml(c.name||'Без названия')}</div><div class="assignmentTreeMeta">${assignmentHeaderMeta(c)} · курсов: ${(c.items||[]).length?assignmentCourseGroups(c).length:0}</div></div><div class="chev" id="assignmentChev_${c.id}">${open?'−':'＋'}</div></div><div id="assignmentBody_${c.id}" class="assignmentTreeBody ${open?'':'hidden'}">${c.comment?`<div class="muted" style="font-size:12px;margin-bottom:6px">${escapeHtml(c.comment)}</div>`:''}<div class="attachments">${(c.attachments||[]).map(a=>`<button class="fileLinkBtn" onclick="openFilePreview('${attachmentUrl(a.id)}', '${escapeAttr(a.filename)}', ${isImageFilename(a.filename)})">📎 ${escapeHtml(a.filename)}</button>`).join('')}</div><div class="treeActions"><button onclick="openCourseMedicineForm(${c.id})">Добавить курс</button><button class="gray" onclick="aiAddMedicineToCourse(${c.id})" ${aiEnabled?'':'disabled'}>Распознать курс из текста</button><button class="gray" onclick="editCourse(${c.id}, '${safeName}', '${c.assignment_date||''}', '${safeDoctor}', '${safeComment}')">Изменить назначение</button><button class="danger" onclick="deleteCourse(${c.id})">Удалить</button></div><div class="fieldLine"><label>Добавить файл</label><input id="courseFile_${c.id}" type="file" accept="image/*,.pdf,.doc,.docx,.txt" onchange="uploadCourseFile(${c.id})"></div><div class="courseItemsTitle">Курсы лекарств</div>${assignmentCoursesTree(c)}</div></div>`;
      }).join(''):'<div class="empty">Назначений пока нет</div>';
      root.innerHTML=rootActions+recognize+addForm+`<div class="adminList" style="margin-top:14px">${list}</div>`;
    }catch(e){console.error('loadCourses failed',e);root.innerHTML=`<div class="empty">Не удалось загрузить назначения.<br><br><small>${escapeHtml(e.message||String(e))}</small></div>`;toast('Ошибка загрузки назначений');}
  }
  function groupCourseItems(items){
    const map=new Map();(items||[]).forEach(it=>{const key=it.medicine_course_id?('mc:'+it.medicine_course_id):[it.course_id||'',(it.name||'').toLowerCase(),it.dose||'',it.start_date||'',it.end_date||'',it.recurrence_type||'',it.recurrence_interval_days||'',it.weekdays||'',it.specific_dates||'',it.timing_template||''].join('|');if(!map.has(key)) map.set(key,{...it, ids:[], entries:[], planned_units_total:0, remaining_need_units:0, taken_units:0, active_any:false, active_all:true});const g=map.get(key);g.ids.push(it.id); g.entries.push(it);g.medicine_course_id=g.medicine_course_id||it.medicine_course_id;g.planned_units_total += Number(it.planned_units_total||0);g.remaining_need_units += Number(it.remaining_need_units||0);g.taken_units += Number(it.taken_units||0);if(g.inventory_quantity==null && it.inventory_quantity!=null)g.inventory_quantity=it.inventory_quantity;g.consume_unit_name = g.consume_unit_name || it.consume_unit_name || doseParts(it.dose).unit || 'шт';g.active_any = g.active_any || !!it.active; g.active_all = g.active_all && !!it.active;g.shortage_units = Math.max(0, Number(g.remaining_need_units||0) - Number(g.inventory_quantity||0));g.duration_value = g.duration_value || it.duration_value; g.duration_unit = g.duration_unit || it.duration_unit;});return [...map.values()];
  }
  function scheduleForm(r){const detailKey='sd_'+r.id;const slot=scheduleSlotText(r);const courseName=assignmentGroupName(r);const courseLabel=r.name||'Курс';return `<div class="scheduleMiniCard"><div class="scheduleMiniSlot">${escapeHtml(slot)}</div><div class="scheduleCourseToggle" onclick="toggleScheduleDetails('${detailKey}')"><div class="scheduleMiniName">${escapeHtml(r.name)}</div><div class="scheduleMiniMeta">Назначение: ${escapeHtml(courseName)} · курс: ${escapeHtml(courseLabel)}</div></div><div id="${detailKey}" class="scheduleMiniDetails hidden"><div class="muted" style="font-size:12px">${escapeHtml(r.dose||'')}${r.label?' · '+escapeHtml(r.label):''}<br>Период: ${r.start_date||'—'} — ${r.end_date||'—'}</div><div class="adminActions"><button class="gray" onclick="editSchedules.add(${r.id});legacyScheduleEdit(${r.id})">Изменить привязку</button><button class="danger" onclick="deleteSchedule(${r.id})">Удалить</button></div></div></div>`;}
  async function legacyScheduleEdit(id){const rows=await api('/api/schedules');const r=rows.find(x=>Number(x.id)===Number(id));if(!r)return;const body=`<label>Назначение</label>${courseSelectHtml(id,r.course_id)}<div class="muted" style="font-size:12px;margin-top:8px">Для полного изменения курса перейдите во вкладку “Назначения” и откройте курс лекарства.</div>`;const ok=await openModal('Привязать к назначению',body,()=>closeModal({course_id:document.getElementById('s_course_'+id)?.value?Number(document.getElementById('s_course_'+id).value):null}),'Сохранить');if(!ok)return;await api('/api/schedules/'+id,{method:'PUT',body:JSON.stringify({...r,course_id:ok.course_id,entries:[{time_local:r.time_local,label:r.label||r.time_local,timing_template:r.timing_template||'fixed',meal_name:r.meal_name||'',meal_offset_minutes:r.meal_offset_minutes||0}]})});toast('Привязка обновлена');editSchedules.delete(id);await loadSchedules();await loadCourses();}
  async function loadSchedules(){
    if(!me?.can_manage_current_profile)return;
    if(!coursesCache.length){try{coursesCache=await api('/api/courses')}catch(e){coursesCache=[]}}
    let rows=await api('/api/schedules');
    const selected=populateMedicineFilter('scheduleSearch', rows, r=>r.name||'');
    if(selected)rows=rows.filter(r=>(r.name||'')===selected);
    const root=document.getElementById('schedules');
    const toolbar=document.querySelector('.scheduleToolbar');
    if(toolbar){toolbar.innerHTML=`<div class="scheduleToolbarCompact"><div class="miniSwitch"><button class="${scheduleGroupByAssignmentUi?'active':''}" onclick="scheduleGroupByAssignmentUi=true;loadSchedules()">Группировать</button><button class="${!scheduleGroupByAssignmentUi?'active':''}" onclick="scheduleGroupByAssignmentUi=false;loadSchedules()">Списком</button></div><div class="miniSwitch"><button onclick="setScheduleGroupsOpen(true)">Раскрыть</button><button onclick="setScheduleGroupsOpen(false)">Скрыть</button></div></div>`}
    if(!rows.length){root.innerHTML='<div class="empty">Активное расписание пустое. Запустите курс в назначении.</div>';return}
    if(!scheduleGroupByAssignmentUi){root.innerHTML=`<div class="scheduleListLikeToday">${rows.map(scheduleForm).join('')}</div>`;return}
    const map=new Map();rows.forEach(r=>{const k=assignmentGroupName(r);if(!map.has(k))map.set(k,[]);map.get(k).push(r)});
    root.innerHTML=[...map.entries()].map(([name,list])=>{const key='sg_'+name.replace(/[^a-zA-Zа-яА-Я0-9]/g,'_');if(!expandedScheduleGroups.has(key)) expandedScheduleGroups.add(key);const open=expandedScheduleGroups.has(key);return `<div class="scheduleTreeGroup"><div class="scheduleTreeHead" data-key="${key}" onclick="toggleScheduleTreeGroup('${key}')"><span>${escapeHtml(name)}</span><span id="scheduleTreeChev_${key}">${open?'−':'＋'}</span></div><div id="scheduleTreeBody_${key}" class="scheduleTreeBody ${open?'':'hidden'}">${list.map(scheduleForm).join('')}</div></div>`}).join('');
  }


  function historyDefaultDates(g){
    const today=new Date().toISOString().slice(0,10);
    return {start:g.start_date||today,end:g.end_date&&g.end_date<today?g.end_date:today};
  }
  function historyStatusLabel(st){return st==='taken'?'✅ принято':st==='skipped'?'⏭️ пропущено':st==='pending'?'⏳ не принято':st==='none'?'нет события':'—'}
  function historyImportHtml(g){
    const d=historyDefaultDates(g);
    return `<div class="muted" style="font-size:12px;margin-bottom:8px">Курс: <b>${escapeHtml(g.name||'')}</b> · ${escapeHtml(g.dose||'')}</div>
      <div class="historyToolbar"><div><label>Дата с</label><input id="histStart" type="date" value="${escapeAttr(d.start)}" onchange="loadHistoryGridPreview('${g.medicine_course_id}')"></div><div><label>Дата по</label><input id="histEnd" type="date" value="${escapeAttr(d.end)}" onchange="loadHistoryGridPreview('${g.medicine_course_id}')"></div></div>
      <div class="historyToolbar3" style="margin-top:8px"><div><label>Заполнить</label><select id="histDefault"><option value="taken">Принято</option><option value="skipped">Пропущено</option><option value="pending">Не принято</option><option value="none">Не менять</option></select></div><div><label>Если уже есть</label><select id="histOverwrite"><option value="skip_existing">Не перезаписывать</option><option value="pending_only">Перезаписать только неотмеченные</option><option value="overwrite_all">Перезаписать все</option></select></div><div><label>Аптечка</label><select id="histInventory"><option value="false">Не списывать</option><option value="true">Списывать/возвращать</option></select></div></div>
      <div class="historySafety">Безопасность: по умолчанию существующие приемы не перезаписываются, а остатки аптечки не меняются. Для старой истории обычно оставьте “Не списывать”.</div>
      <div style="margin-top:10px"><button class="gray" onclick="fillHistoryGridDefault()">Заполнить таблицу выбранным статусом</button></div>
      <div id="historyGridPreview" class="muted" style="margin-top:10px">Загрузка...</div>`;
  }
  async function openHistoryImport(groupKey){
    const g=window.courseGroups?.[groupKey];
    if(!g||!g.medicine_course_id){toast('Для курса нет связанной записи');return;}
    const prom=openModal('Внести историю приема', historyImportHtml(g), async()=>{
      const payload=readHistoryGridPayload();
      if(!payload || !Object.keys(payload.cells||{}).length){toast('Заполните хотя бы одну ячейку');return;}
      try{
        const res=await api(`/api/medicine-courses/${g.medicine_course_id}/history-grid`,{method:'POST',body:JSON.stringify(payload)});
        closeModal(true);
        toast(`История внесена: изменено ${res.changed||0}, создано ${res.created||0}`);
        await loadCourses(); await loadToday(); await loadStats(); await loadAudit();
      }catch(e){toast('Ошибка: '+(e.message||e));}
    }, 'Применить');
    setTimeout(()=>loadHistoryGridPreview(g.medicine_course_id),50);
    await prom;
  }
  async function loadHistoryGridPreview(medicineCourseId){
    const root=document.getElementById('historyGridPreview'); if(!root)return;
    const s=document.getElementById('histStart')?.value; const e=document.getElementById('histEnd')?.value;
    if(!s||!e){root.innerHTML='<div class="empty compactEmpty">Укажите период</div>';return;}
    root.innerHTML='Загрузка...';
    try{
      const data=await api(`/api/medicine-courses/${medicineCourseId}/history-grid?start_date=${encodeURIComponent(s)}&end_date=${encodeURIComponent(e)}`);
      window.currentHistoryGrid=data;
      const rows=(data.days||[]).filter(d=>(d.items||[]).length);
      if(!rows.length){root.innerHTML='<div class="empty compactEmpty">В выбранном периоде нет приемов по схеме курса</div>';return;}
      const maxItems=Math.max(...rows.map(d=>(d.items||[]).length));
      let html='<div class="historyGridWrap"><table class="historyGridTable"><tr><th>Дата</th>';
      for(let i=0;i<maxItems;i++) html+=`<th>Прием ${i+1}</th>`;
      html+='</tr>';
      rows.forEach(day=>{html+=`<tr><td><b>${day.date}</b></td>`;for(let i=0;i<maxItems;i++){const it=(day.items||[])[i];if(!it){html+='<td></td>';continue;}const key=`${day.date}|${it.schedule_id}`;html+=`<td><div style="font-weight:800">${escapeHtml(it.time)} · ${escapeHtml(it.label||'')}</div><div class="muted" style="font-size:11px">сейчас: ${historyStatusLabel(it.status)}${it.taken_at?' '+escapeHtml(it.taken_at):''}</div><select class="histCell" data-key="${escapeAttr(key)}"><option value="none">Не менять</option><option value="taken">Принято</option><option value="skipped">Пропущено</option><option value="pending">Не принято</option></select></td>`;}html+='</tr>';});
      html+='</table></div>';
      root.innerHTML=html;
    }catch(err){root.innerHTML=`<div class="empty compactEmpty">Не удалось загрузить таблицу: ${escapeHtml(err.message||String(err))}</div>`;}
  }
  function fillHistoryGridDefault(){const v=document.getElementById('histDefault')?.value||'taken';document.querySelectorAll('.histCell').forEach(s=>s.value=v);}
  function readHistoryGridPayload(){const start=document.getElementById('histStart')?.value;const end=document.getElementById('histEnd')?.value;const cells={};document.querySelectorAll('.histCell').forEach(sel=>{if(sel.value&&sel.value!=='none')cells[sel.dataset.key]=sel.value;});return {start_date:start,end_date:end,cells,overwrite_mode:document.getElementById('histOverwrite')?.value||'skip_existing',apply_inventory:document.getElementById('histInventory')?.value==='true',actual_time_mode:'planned'};}


  // ===== v42 final UI/API overrides =====
  let selectedTodayDate = window.selectedTodayDate || new Date().toISOString().slice(0,10);
  let calendarWeekStart = null;
  function parseYmdLocal(ymd){const [y,m,d]=String(ymd).split('-').map(Number);return new Date(y, (m||1)-1, d||1);}
  function ymdLocal(d){const y=d.getFullYear();const m=String(d.getMonth()+1).padStart(2,'0');const day=String(d.getDate()).padStart(2,'0');return `${y}-${m}-${day}`;}
  function addDaysLocal(ymd, n){const d=parseYmdLocal(ymd);d.setDate(d.getDate()+n);return ymdLocal(d);}
  function weekStartMonday(d){const x=new Date(d); const day=x.getDay(); const diff=(day===0?-6:1-day); x.setDate(x.getDate()+diff); x.setHours(0,0,0,0); return x;}
  function ensureCalendarWeek(){if(!calendarWeekStart) calendarWeekStart=weekStartMonday(parseYmdLocal(selectedTodayDate));}
  function renderWeekCalendar(){
    const root=document.getElementById('weekCalendar'); if(!root)return;
    ensureCalendarWeek();
    const today=ymdLocal(new Date());
    const dows=['Вс','Пн','Вт','Ср','Чт','Пт','Сб'];
    const monthNames=['янв','фев','мар','апр','мая','июн','июл','авг','сен','окт','ноя','дек'];
    const end=new Date(calendarWeekStart); end.setDate(calendarWeekStart.getDate()+6);
    const title = `${calendarWeekStart.getDate()} ${monthNames[calendarWeekStart.getMonth()]} — ${end.getDate()} ${monthNames[end.getMonth()]}`;
    const days=Array.from({length:7},(_,i)=>{const d=new Date(calendarWeekStart); d.setDate(calendarWeekStart.getDate()+i); const ymd=ymdLocal(d); const classes=['dayChip']; if(ymd===selectedTodayDate)classes.push('active'); if(ymd===today)classes.push('todayMark'); return `<button class="${classes.join(' ')}" onclick="selectTodayDate('${ymd}')" aria-label="${ymd}"><span class="dow">${dows[d.getDay()]}</span><span class="num">${d.getDate()}</span></button>`;}).join('');
    root.innerHTML=`<div class="weekTop"><div class="weekMonthTitle">${title}</div><button class="todayMiniBtn ${selectedTodayDate===today?'active':''}" onclick="goTodayDate()">Сегодня</button></div><div class="weekNav"><button class="weekArrow" onclick="shiftWeek(-1)">‹</button><div class="weekDays">${days}</div><button class="weekArrow" onclick="shiftWeek(1)">›</button></div>`;
  }
  async function shiftWeek(delta){ensureCalendarWeek(); calendarWeekStart.setDate(calendarWeekStart.getDate()+delta*7); renderWeekCalendar();}
  async function goTodayDate(){selectedTodayDate=ymdLocal(new Date()); calendarWeekStart=weekStartMonday(parseYmdLocal(selectedTodayDate)); await loadToday();}
  async function selectTodayDate(ymd){selectedTodayDate=ymd; calendarWeekStart=weekStartMonday(parseYmdLocal(ymd)); await loadToday();}
  async function loadToday(){
    renderWeekCalendar();
    todayRows=await api('/api/today?day='+encodeURIComponent(selectedTodayDate));
    renderToday();
    renderWeekCalendar();
  }

  function toggleProfileMenu(e){if(e)e.stopPropagation(); const el=document.getElementById('profileMenuList'); if(el) el.classList.toggle('hidden');}
  document.addEventListener('click', (e)=>{const box=document.getElementById('profileBar'); if(box && !box.contains(e.target)) document.getElementById('profileMenuList')?.classList.add('hidden');});
  function renderProfileChips(){
    const btn=document.getElementById('profileMenuBtn'); const list=document.getElementById('profileMenuList');
    if(!btn||!list)return;
    const current=profiles.find(p=>p.id===currentProfileId)||profiles[0];
    btn.innerHTML=`${escapeHtml(profileLabel(current||{name:'Профиль',kind:'child'}))} <span class="caret">▾</span>`;
    list.innerHTML=profiles.map(p=>`<button class="profileMenuItem ${p.id===currentProfileId?'active':''}" onclick="changeProfile(${p.id});document.getElementById('profileMenuList')?.classList.add('hidden')">${escapeHtml(profileLabel(p))}</button>`).join('');
    document.getElementById('profileBar').classList.toggle('hidden', profiles.length<1);
  }

  async function aiAddMedicineToCourse(courseId){
    if(!aiEnabled){toast('ИИ выключен в настройках Railway');return;}
    const text = await openModal('Распознать курс из текста', `<label>Описание курса</label><textarea id="aiCourseText" placeholder="Например: Гимекромон 200 мг по 1 таблетке 3 раза в день за 30 минут до еды 14 дней"></textarea><div class="muted" style="font-size:12px;margin-top:8px">ИИ заполнит черновик курса, перед сохранением проверьте данные.</div>`, ()=>closeModal(document.getElementById('aiCourseText').value.trim()), 'Распознать');
    if(!text)return;
    try{
      const res=await api('/api/ai/parse-medicine',{method:'POST',body:JSON.stringify({text})});
      const meds=res.medicines||[];
      if(!meds.length){toast('Не удалось распознать курс');return;}
      const idx = meds.length===1 ? 0 : await openModal('Выберите курс', `<div class="aiDraftList">${meds.map((m,i)=>`<button class="statusOption" onclick="closeModal(${i})"><span>${aiMedicineSummary(m)}</span></button>`).join('')}</div>`, ()=>closeModal(null), 'Отмена');
      if(idx===null || idx===undefined)return;
      await openCourseMedicineForm(courseId,null,meds[Number(idx)]||meds[0]);
    }catch(e){toast('Ошибка ИИ: '+(e.message||e));}
  }

  async function loadStats(){
    if(!coursesCache.length){try{coursesCache=await api('/api/courses')}catch(e){coursesCache=[]}}
    const filterRoot=document.getElementById('statsFilters');
    const currentVal=document.getElementById('statsCourseFilter')?.value || '';
    if(filterRoot){
      filterRoot.innerHTML=`<label>Фильтр по назначению</label><select id="statsCourseFilter" onchange="loadStats()"><option value="">Все назначения</option>${(coursesCache||[]).map(c=>`<option value="${c.id}" ${String(c.id)===String(currentVal)?'selected':''}>${escapeHtml(c.name||'Назначение')}</option>`).join('')}</select>`;
      if(currentVal) document.getElementById('statsCourseFilter').value=currentVal;
    }
    const courseId=document.getElementById('statsCourseFilter')?.value || '';
    const rows=await api('/api/stats'+(courseId?`?course_id=${encodeURIComponent(courseId)}`:''));
    const root=document.getElementById('stats');
    if(!rows.length){root.innerHTML='<div class="empty">Статистики пока нет</div>';return}
    root.innerHTML=`<div class="tableWrap"><table class="miniTable statsTable"><tr><th>Препарат</th><th>✅</th><th>⏭️</th><th>⏳</th><th>%</th>${me?.can_manage_current_profile?'<th></th>':''}</tr>${rows.map(r=>`<tr><td>${escapeHtml(r.medicine)}</td><td>${r.taken}</td><td>${r.skipped}</td><td>${r.pending}</td><td>${r.taken_percent}%</td>${me?.can_manage_current_profile?`<td><button class="tinyDanger" onclick="clearMedicineStats(${r.medicine_id}, '${escapeAttr(r.medicine)}')">Очистить</button></td>`:''}</tr>`).join('')}</table></div><div class="muted" style="font-size:12px;margin-top:8px">Если тестовое лекарство видно только в статистике, нажмите “Очистить”: будут удалены факты приемов по нему в текущем профиле, само расписание не меняется.</div>`;
  }

  function courseNeedPanel(g){
    const x=courseNeedsStatus(g); const unit=x.unit;
    const shortage=Number(g.shortage_units||0);
    const state=g.inventory_quantity==null?'аптечка не заполнена':(shortage>0?`не хватает ${fmtNum(shortage)} ${unit}`:'хватает');
    const cls=(g.inventory_quantity!=null && shortage<=0)?'ok':'shortage';
    return `<div class="compactCourseNeed"><b>Всего:</b> ${fmtNum(g.planned_units_total||0)} ${unit} · <b>Принято:</b> ${fmtNum(g.taken_units||0)} ${unit} · <b>В аптечке:</b> ${x.stock} ${unit} · <span class="${cls}">${escapeHtml(state)}</span></div>`;
  }
  function courseGroupBody(g,key){
    const dp=doseParts(g.dose); const period=`${g.start_date||'—'} — ${g.end_date||'—'}`; const analogs=(g.analogs||'').trim(); const route=(g.administration_route||'').trim(); const form=(g.dosage_form||dp.unit||'').trim();
    const inactiveActions = g.active_all ? '' : `<button onclick="startScheduleGroup('${g.ids.join(',')}')">Начать курс</button>`;
    const rows=[['Формат', form||'—'],['Дозировка', g.dose||'—'],['Периодичность', courseFreqText(g)],['Прием', courseApplyText(g)],['Способ применения', route||'—'],['Длительность', durationText(g.start_date,g.end_date,g.duration_value,g.duration_unit)],['Период', period]];
    const details=rows.map(([k,v])=>`<div class="detailRow"><span>${escapeHtml(k)}</span><b>${escapeHtml(v)}</b></div>`).join('');
    return `<div id="courseBody_${key}" class="courseGroupBody hidden"><div class="courseDetailList">${details}${analogs?`<div class="detailRow analogsRow"><span>Аналоги</span><b>${escapeHtml(analogs)}</b></div>`:''}</div>${courseNeedPanel(g)}<div class="courseActionsRow">${inactiveActions||'<button class="gray" onclick="openCourseMedicineForm('+g.course_id+', null, null, \'${key}\')">Изменить</button>'.replace('${key}',key)}${inactiveActions?`<button class="gray" onclick="openCourseMedicineForm(${g.course_id}, null, null, '${key}')">Изменить</button>`:''}<button class="gray" onclick="openHistoryImport('${key}')">Внести историю</button><button class="danger" onclick="deleteScheduleGroup('${g.ids.join(',')}')">Удалить</button></div></div>`;
  }

  function scheduleCourseDurationText(r){return durationText(r.start_date,r.end_date,r.duration_value,r.duration_unit)||'—';}
  function scheduleForm(r){
    const detailKey='sd_'+r.id; const slot=scheduleSlotText(r); const courseName=assignmentGroupName(r);
    return `<div class="scheduleMiniCard"><div class="scheduleMiniSlot">${escapeHtml(slot)}</div><div class="scheduleCourseToggle" onclick="toggleScheduleDetails('${detailKey}')"><div class="scheduleMiniName">${escapeHtml(r.name)}</div><div class="scheduleMiniMeta">Назначение: ${escapeHtml(courseName)} · Длительность курса: ${escapeHtml(scheduleCourseDurationText(r))}</div></div><div id="${detailKey}" class="scheduleMiniDetails hidden"><div class="muted" style="font-size:12px">${escapeHtml(r.dose||'')}${r.label?' · '+escapeHtml(r.label):''}<br>Период: ${r.start_date||'—'} — ${r.end_date||'—'}</div><div class="adminActions"><button class="gray" onclick="legacyScheduleEdit(${r.id})">Изменить привязку</button><button class="danger" onclick="deleteSchedule(${r.id})">Удалить</button></div></div></div>`;
  }
  async function loadSchedules(){
    if(!me?.can_manage_current_profile)return;
    if(!coursesCache.length){try{coursesCache=await api('/api/courses')}catch(e){coursesCache=[]}}
    let rows=await api('/api/schedules');
    const selected=populateMedicineFilter('scheduleSearch', rows, r=>r.name||'');
    if(selected)rows=rows.filter(r=>(r.name||'')===selected);
    const root=document.getElementById('schedules'); const toolbar=document.querySelector('.scheduleToolbar');
    if(toolbar){toolbar.innerHTML=`<div class="scheduleToolbarCompact ${scheduleGroupByAssignmentUi?'':'listMode'}"><div class="miniSwitch"><button class="${scheduleGroupByAssignmentUi?'active':''}" onclick="scheduleGroupByAssignmentUi=true;loadSchedules()">Группировать</button><button class="${!scheduleGroupByAssignmentUi?'active':''}" onclick="scheduleGroupByAssignmentUi=false;loadSchedules()">Списком</button></div>${scheduleGroupByAssignmentUi?`<div class="miniSwitch expandSwitch"><button onclick="setScheduleGroupsOpen(true)">Раскрыть</button><button onclick="setScheduleGroupsOpen(false)">Скрыть</button></div>`:''}</div>`;}
    if(!rows.length){root.innerHTML='<div class="empty">Активное расписание пустое. Запустите курс в назначении.</div>';return;}
    if(!scheduleGroupByAssignmentUi){root.innerHTML=`<div class="scheduleListLikeToday">${rows.map(scheduleForm).join('')}</div>`;return;}
    const map=new Map();rows.forEach(r=>{const k=assignmentGroupName(r);if(!map.has(k))map.set(k,[]);map.get(k).push(r)});
    root.innerHTML=[...map.entries()].map(([name,list])=>{const key='sg_'+name.replace(/[^a-zA-Zа-яА-Я0-9]/g,'_');if(!expandedScheduleGroups.has(key)) expandedScheduleGroups.add(key);const open=expandedScheduleGroups.has(key);return `<div class="scheduleTreeGroup"><div class="scheduleTreeHead" data-key="${key}" onclick="toggleScheduleTreeGroup('${key}')"><span>${escapeHtml(name)}</span><span id="scheduleTreeChev_${key}">${open?'−':'＋'}</span></div><div id="scheduleTreeBody_${key}" class="scheduleTreeBody ${open?'':'hidden'}">${list.map(scheduleForm).join('')}</div></div>`}).join('');
  }

  async function clearMedicineStats(medicineId, name){
    const ok=await confirmAction('Очистить статистику?', `Удалить факты приемов по препарату “${name}” в текущем профиле?`, 'Это уберет тестовый препарат из статистики. Активное расписание и аптечка не будут удалены.');
    if(!ok)return;
    await api('/api/stats/medicine/'+medicineId,{method:'DELETE'});
    toast('Статистика очищена');
    await loadStats();
  }




  // ===== v46: family UI and invites =====
  function roleRu(r){return {owner:'Владелец',parent:'Родитель',child:'Ребенок',viewer:'Просмотр'}[r]||r}
  function familyRoleOptions(current){return ['owner','parent','child','viewer'].map(r=>`<option value="${r}" ${current===r?'selected':''}>${roleRu(r)}</option>`).join('')}
  function childProfileOptions(current){return `<option value="">—</option>` + profiles.filter(p=>p.kind==='child').map(p=>`<option value="${p.id}" ${Number(current)===p.id?'selected':''}>${escapeHtml(p.name)}</option>`).join('')}
  async function loadFamilyBox(){
    const root=document.getElementById('familyBox');
    if(!root)return;
    try{
      const fams=await api('/api/families');
      if(!fams.length){root.innerHTML=`<div class="empty">Семей пока нет</div><button class="wide" onclick="createFamilyUi()">Создать семью</button>`;return}
      const html=[];
      for(const f of fams){
        let members=[], invites=[];
        try{members=await api('/api/family/'+f.id+'/members')}catch(e){}
        try{if(f.can_manage)invites=await api('/api/family/'+f.id+'/invites')}catch(e){}
        html.push(`<div class="formrow familyCard"><div class="rowTop"><div><div class="med">${escapeHtml(f.name)}</div><div class="meta">Ваша роль: ${escapeHtml(roleRu(f.role))}</div></div>${f.can_manage?`<button class="smallBtn gray" onclick="renameFamily(${f.id}, '${escapeAttr(f.name)}')">Переименовать</button>`:''}</div>
          <div class="sectionTitle">Участники</div>
          <div class="profileList">${members.map(m=>`<div class="profileItem"><div><div class="profileName">${escapeHtml(m.full_name||String(m.tg_id))}</div><div class="meta">ID: ${m.tg_id} · ${escapeHtml(roleRu(m.role))}${m.linked_profile_id?' · профиль #'+m.linked_profile_id:''}</div></div>${f.can_manage?`<button class="smallBtn gray" onclick="editFamilyMember(${m.id}, '${m.role}', ${m.linked_profile_id||0})">Роль</button>${m.role!=='owner'?`<button class="smallBtn danger" onclick="removeFamilyMember(${m.id})">Удалить</button>`:'<span></span>'}`:''}</div>`).join('')}</div>
          ${f.can_manage?`<div class="sectionTitle">Приглашения</div><div class="sectionTools"><button class="smallBtn" onclick="createInviteUi(${f.id}, 'parent')">Пригласить родителя</button><button class="smallBtn" onclick="createInviteUi(${f.id}, 'child')">Пригласить ребенка</button><button class="smallBtn gray" onclick="createInviteUi(${f.id}, 'viewer')">Только просмотр</button></div>
          <div class="auditList">${invites.length?invites.map(i=>`<div class="auditItem"><div class="auditTop"><span>${escapeHtml(roleRu(i.role))}</span><span>${i.used_count}/${i.max_uses}</span></div><div class="auditText"><input readonly value="${escapeAttr(i.link)}" onclick="this.select()"></div><div class="sectionTools"><button class="smallBtn gray" onclick="copyText('${escapeAttr(i.link)}')">Копировать</button><button class="smallBtn danger" onclick="deleteInvite(${i.id})">Удалить</button></div></div>`).join(''):'<div class="empty">Активных приглашений нет</div>'}</div>`:''}
        </div>`);
      }
      html.push(`<button class="wide gray" onclick="createFamilyUi()">Создать еще одну семью</button>`);
      root.innerHTML=html.join('');
    }catch(e){root.innerHTML='<div class="empty">Не удалось загрузить семьи: '+escapeHtml(e.message||e)+'</div>'}
  }
  async function createFamilyUi(){const name=await openModal('Создать семью','<label>Название семьи</label><input id="famName" placeholder="Например, Семья Ивановых">',()=>closeModal(document.getElementById('famName').value.trim())); if(!name)return; await api('/api/families',{method:'POST',body:JSON.stringify({name})}); toast('Семья создана'); await loadProfiles(); await loadFamilyBox();}
  async function renameFamily(id,current){const name=await openModal('Переименовать семью',`<label>Название</label><input id="famName" value="${escapeAttr(current)}">`,()=>closeModal(document.getElementById('famName').value.trim())); if(!name)return; await api('/api/families/'+id,{method:'PUT',body:JSON.stringify({name})}); toast('Семья обновлена'); await loadFamilyBox();}
  async function createInviteUi(familyId,role){
    const needProfile=role==='child'||role==='viewer';
    const body=`<label>Роль</label><select id="invRole" onchange="document.getElementById('invProfileLine').classList.toggle('hidden', !(this.value==='child'||this.value==='viewer'))"><option value="parent" ${role==='parent'?'selected':''}>Родитель</option><option value="child" ${role==='child'?'selected':''}>Ребенок</option><option value="viewer" ${role==='viewer'?'selected':''}>Только просмотр</option></select><div id="invProfileLine" class="${needProfile?'':'hidden'}"><label>Профиль ребенка</label><select id="invProfile">${childProfileOptions('')}</select></div><label>Количество использований</label><input id="invMax" type="number" value="1" min="1" max="20">`;
    const data=await openModal('Создать приглашение',body,()=>closeModal({role:document.getElementById('invRole').value,target_profile_id:Number(document.getElementById('invProfile')?.value||0)||null,max_uses:Number(document.getElementById('invMax').value||1)}));
    if(!data)return;
    const res=await api('/api/family-invites',{method:'POST',body:JSON.stringify({family_id:familyId,...data})});
    await copyText(res.link); toast('Ссылка приглашения создана и скопирована'); await loadFamilyBox();
  }
  async function editFamilyMember(memberId,role,linked){const body=`<label>Роль</label><select id="memRole" onchange="document.getElementById('memProfileLine').classList.toggle('hidden', !(this.value==='child'||this.value==='viewer'))">${familyRoleOptions(role)}</select><div id="memProfileLine" class="${(role==='child'||role==='viewer')?'':'hidden'}"><label>Профиль ребенка</label><select id="memProfile">${childProfileOptions(linked)}</select></div>`; const data=await openModal('Изменить роль участника',body,()=>closeModal({role:document.getElementById('memRole').value,linked_profile_id:Number(document.getElementById('memProfile')?.value||0)||null})); if(!data)return; await api('/api/family-members/'+memberId,{method:'PUT',body:JSON.stringify(data)}); toast('Роль обновлена'); await loadFamilyBox();}
  async function removeFamilyMember(memberId){const ok=await confirmAction('Удалить участника?', 'Участник потеряет доступ к этой семье.'); if(!ok)return; await api('/api/family-members/'+memberId,{method:'DELETE'}); toast('Участник удален'); await loadFamilyBox();}
  async function deleteInvite(id){const ok=await confirmAction('Удалить приглашение?', 'Ссылка перестанет работать.'); if(!ok)return; await api('/api/family-invites/'+id,{method:'DELETE'}); toast('Приглашение удалено'); await loadFamilyBox();}
  async function copyText(text){try{await navigator.clipboard.writeText(text); toast('Скопировано')}catch(e){prompt('Скопируйте ссылку', text)}}

  // ===== v43: bottom navigation restructure + read-only mode =====
  let currentAnalyticsTab='stats';
  let currentMoreTab='family';
  function canManage(){ return !!me?.can_manage_current_profile; }
  function roleManageHtml(html){ return canManage()?html:''; }
  function showAdminTab(name){
    currentAdminTab=name;
    document.getElementById('adminTabAssignments')?.classList.toggle('active', name==='assignments');
    document.getElementById('adminTabMeds')?.classList.toggle('active', name==='meds');
    document.getElementById('adminPanelAssignments')?.classList.toggle('hidden', name!=='assignments');
    document.getElementById('adminPanelMeds')?.classList.toggle('hidden', name!=='meds');
    if(name==='assignments') loadCourses();
    if(name==='meds'){ loadCourses(); loadSchedules(); }
  }
  function showAnalyticsTab(name){
    currentAnalyticsTab=name;
    document.getElementById('analyticsTabStats')?.classList.toggle('active', name==='stats');
    document.getElementById('analyticsTabHistory')?.classList.toggle('active', name==='history');
    document.getElementById('analyticsPanelStats')?.classList.toggle('hidden', name!=='stats');
    document.getElementById('analyticsPanelHistory')?.classList.toggle('hidden', name!=='history');
    if(name==='stats') loadStats();
    if(name==='history') loadMedicines();
  }
  function showMoreTab(name){
    currentMoreTab=name;
    ['family','profiles','audit','notify','ui'].forEach(t=>{
      document.getElementById('moreTab'+t[0].toUpperCase()+t.slice(1))?.classList.toggle('active', name===t);
      document.getElementById('morePanel'+t[0].toUpperCase()+t.slice(1))?.classList.toggle('hidden', name!==t);
    });
    if(name==='family') loadFamilyBox();
    if(name==='profiles') loadProfileAdmin();
    if(name==='audit') loadAudit();
    if(name==='notify') loadNotificationSettings();
  }
  function showTab(name){
    ['today','assignments','inventory','analytics','more'].forEach(t=>{document.getElementById(pageId(t))?.classList.add('hidden');document.getElementById(tabId(t))?.classList.remove('active')});
    document.getElementById(pageId(name))?.classList.remove('hidden');
    document.getElementById(tabId(name))?.classList.add('active');
    location.hash=name==='today'?'':'#'+name;
    if(name==='today') loadToday();
    if(name==='assignments') showAdminTab(currentAdminTab||'assignments');
    if(name==='inventory') loadInventory();
    if(name==='analytics') showAnalyticsTab(currentAnalyticsTab||'stats');
    if(name==='more') showMoreTab(currentMoreTab||'family');
    window.scrollTo({top:0,behavior:'smooth'});
  }

  async function loadCourses(){
    const root=document.getElementById('coursesBox'); if(!root)return; root.innerHTML='<div class="empty">Загружаю назначения...</div>';
    try{
      const raw=await api('/api/courses'); const rows=Array.isArray(raw)?raw:(Array.isArray(raw?.items)?raw.items:[]); coursesCache=rows; window.courseGroups={};
      const rootActions=canManage()?`<div class="treeActions"><button onclick="recognizeAssignmentOpen=!recognizeAssignmentOpen;loadCourses()">✨ Распознать назначение</button><button class="gray" onclick="toggleAddAssignmentForm()">${addAssignmentOpen?'Скрыть форму':'Добавить назначение'}</button></div>`:`<div class="readonlyNotice">Просмотр назначений. Редактирование доступно родителю.</div>`;
      const recognize=canManage()?`<div class="recognizeBox ${recognizeAssignmentOpen?'':'collapsed'}" style="margin-top:10px"><div class="recognizeContent"><div class="recognizeTitle">📷 Распознать назначение по фото</div><div class="muted" style="font-size:12px;margin-top:4px">ИИ создаст черновик назначения и курсов. Перед сохранением проверьте данные.</div><div class="grid2" style="margin-top:10px"><input id="aiAssignmentFile" type="file" accept="image/*"><button onclick="aiRecognizeAssignment()" ${aiEnabled?'':'disabled'}>Распознать</button></div>${aiEnabled?'':'<div class="muted" style="font-size:12px;margin-top:6px">ИИ выключен в настройках Railway</div>'}</div></div><div id="assignmentAiPreview"></div>`:'';
      const addForm=canManage()?`<div id="courseAddForm" class="courseAddForm ${addAssignmentOpen?'':'hidden'}" style="margin-top:10px"><div class="fieldLine"><label>Название</label><input id="courseName" placeholder="Например, назначение гастроэнтеролога"></div><div class="fieldLine"><label>Дата назначения</label><input id="courseDate" type="date"></div><div class="fieldLine"><label>Врач</label><input id="courseDoctor" placeholder="опционально"></div><div class="fieldLine"><label>Комментарий</label><input id="courseComment" placeholder="опционально"></div><div class="fieldLine"><label>Фото / файл</label><input id="courseFile" type="file" accept="image/*,.pdf,.doc,.docx,.txt"></div><button class="wide" onclick="addCourse()" style="margin-top:8px">Сохранить назначение</button></div>`:'';
      const list=rows.length?rows.map(c=>{
        const open=expandedAssignments.has(c.id);
        const safeName=escapeAttr(c.name||''),safeDoctor=escapeAttr(c.doctor||''),safeComment=escapeAttr(c.comment||'');
        const actions=canManage()?`<div class="treeActions"><button onclick="openCourseMedicineForm(${c.id})">Добавить курс</button><button class="gray" onclick="aiAddMedicineToCourse(${c.id})" ${aiEnabled?'':'disabled'}>Распознать курс из текста</button><button class="gray" onclick="editCourse(${c.id}, '${safeName}', '${c.assignment_date||''}', '${safeDoctor}', '${safeComment}')">Изменить назначение</button><button class="danger" onclick="deleteCourse(${c.id})">Удалить</button></div><div class="fieldLine"><label>Добавить файл</label><input id="courseFile_${c.id}" type="file" accept="image/*,.pdf,.doc,.docx,.txt" onchange="uploadCourseFile(${c.id})"></div>`:'';
        return `<div class="assignmentTreeCard"><div class="assignmentTreeHead" onclick="toggleAssignmentTree(${c.id})"><div><div class="assignmentTreeTitle">${escapeHtml(c.name||'Без названия')}</div><div class="assignmentTreeMeta">${assignmentHeaderMeta(c)} · курсов: ${(c.items||[]).length?assignmentCourseGroups(c).length:0}</div></div><div class="chev" id="assignmentChev_${c.id}">${open?'−':'＋'}</div></div><div id="assignmentBody_${c.id}" class="assignmentTreeBody ${open?'':'hidden'}">${c.comment?`<div class="muted" style="font-size:12px;margin-bottom:6px">${escapeHtml(c.comment)}</div>`:''}<div class="attachments">${(c.attachments||[]).map(a=>`<button class="fileLinkBtn" onclick="openFilePreview('${attachmentUrl(a.id)}', '${escapeAttr(a.filename)}', ${isImageFilename(a.filename)})">📎 ${escapeHtml(a.filename)}</button>`).join('')}</div>${actions}<div class="courseItemsTitle">Курсы лекарств</div>${assignmentCoursesTree(c)}</div></div>`;
      }).join(''):'<div class="empty">Назначений пока нет</div>';
      root.innerHTML=rootActions+recognize+addForm+`<div class="adminList" style="margin-top:14px">${list}</div>`;
    }catch(e){console.error('loadCourses failed',e);root.innerHTML=`<div class="empty">Не удалось загрузить назначения.<br><br><small>${escapeHtml(e.message||String(e))}</small></div>`;toast('Ошибка загрузки назначений');}
  }

  function courseGroupBody(g,key){
    const dp=doseParts(g.dose); const period=`${g.start_date||'—'} — ${g.end_date||'—'}`; const analogs=(g.analogs||'').trim(); const route=(g.administration_route||'').trim(); const form=(g.dosage_form||dp.unit||'').trim();
    const inactiveActions = (canManage() && !g.active_all) ? `<button onclick="startScheduleGroup('${g.ids.join(',')}')">Начать курс</button>` : '';
    const rows=[['Формат', form||'—'],['Дозировка', g.dose||'—'],['Периодичность', courseFreqText(g)],['Прием', courseApplyText(g)],['Способ применения', route||'—'],['Длительность', durationText(g.start_date,g.end_date,g.duration_value,g.duration_unit)],['Период', period]];
    const details=rows.map(([k,v])=>`<div class="detailRow"><span>${escapeHtml(k)}</span><b>${escapeHtml(v)}</b></div>`).join('');
    const manage=canManage()?`<div class="courseActionsRow">${inactiveActions||`<button class="gray" onclick="openCourseMedicineForm(${g.course_id}, null, null, '${key}')">Изменить</button>`}${inactiveActions?`<button class="gray" onclick="openCourseMedicineForm(${g.course_id}, null, null, '${key}')">Изменить</button>`:''}<button class="gray" onclick="openHistoryImport('${key}')">Внести историю</button><button class="danger" onclick="deleteScheduleGroup('${g.ids.join(',')}')">Удалить</button></div>`:'';
    return `<div id="courseBody_${key}" class="courseGroupBody hidden"><div class="courseDetailList">${details}${analogs?`<div class="detailRow analogsRow"><span>Аналоги</span><b>${escapeHtml(analogs)}</b></div>`:''}</div>${courseNeedPanel(g)}${manage}</div>`;
  }

  function scheduleForm(r){
    const detailKey='sd_'+r.id; const slot=scheduleSlotText(r); const courseName=assignmentGroupName(r);
    const manage=canManage()?`<div class="adminActions"><button class="gray" onclick="legacyScheduleEdit(${r.id})">Изменить привязку</button><button class="danger" onclick="deleteSchedule(${r.id})">Удалить</button></div>`:'';
    return `<div class="scheduleMiniCard"><div class="scheduleMiniSlot">${escapeHtml(slot)}</div><div class="scheduleCourseToggle" onclick="toggleScheduleDetails('${detailKey}')"><div class="scheduleMiniName">${escapeHtml(r.name)}</div><div class="scheduleMiniMeta">Назначение: ${escapeHtml(courseName)} · Длительность курса: ${escapeHtml(scheduleCourseDurationText(r))}</div></div><div id="${detailKey}" class="scheduleMiniDetails hidden"><div class="muted" style="font-size:12px">${escapeHtml(r.dose||'')}${r.label?' · '+escapeHtml(r.label):''}<br>Период: ${r.start_date||'—'} — ${r.end_date||'—'}</div>${manage}</div></div>`;
  }
  async function loadSchedules(){
    if(!coursesCache.length){try{coursesCache=await api('/api/courses')}catch(e){coursesCache=[]}}
    let rows=await api('/api/schedules');
    const selected=populateMedicineFilter('scheduleSearch', rows, r=>r.name||'');
    if(selected)rows=rows.filter(r=>(r.name||'')===selected);
    const root=document.getElementById('schedules'); const toolbar=document.querySelector('.scheduleToolbar');
    if(toolbar){toolbar.innerHTML=`<div class="scheduleToolbarCompact ${scheduleGroupByAssignmentUi?'':'listMode'}"><div class="miniSwitch"><button class="${scheduleGroupByAssignmentUi?'active':''}" onclick="scheduleGroupByAssignmentUi=true;loadSchedules()">Группировать</button><button class="${!scheduleGroupByAssignmentUi?'active':''}" onclick="scheduleGroupByAssignmentUi=false;loadSchedules()">Списком</button></div>${scheduleGroupByAssignmentUi?`<div class="miniSwitch expandSwitch"><button onclick="setScheduleGroupsOpen(true)">Раскрыть</button><button onclick="setScheduleGroupsOpen(false)">Скрыть</button></div>`:''}</div>`;}
    if(!rows.length){root.innerHTML='<div class="empty">Активное расписание пустое. Запустите курс в назначении.</div>';return;}
    if(!scheduleGroupByAssignmentUi){root.innerHTML=`<div class="scheduleListLikeToday">${rows.map(scheduleForm).join('')}</div>`;return;}
    const map=new Map();rows.forEach(r=>{const k=assignmentGroupName(r);if(!map.has(k))map.set(k,[]);map.get(k).push(r)});
    root.innerHTML=[...map.entries()].map(([name,list])=>{const key='sg_'+name.replace(/[^a-zA-Zа-яА-Я0-9]/g,'_');if(!expandedScheduleGroups.has(key)) expandedScheduleGroups.add(key);const open=expandedScheduleGroups.has(key);return `<div class="scheduleTreeGroup"><div class="scheduleTreeHead" data-key="${key}" onclick="toggleScheduleTreeGroup('${key}')"><span>${escapeHtml(name)}</span><span id="scheduleTreeChev_${key}">${open?'−':'＋'}</span></div><div id="scheduleTreeBody_${key}" class="scheduleTreeBody ${open?'':'hidden'}">${list.map(scheduleForm).join('')}</div></div>`}).join('');
  }

  async function loadInventory(){
    if(canManage()) await populateInventoryMedicineSelect();
    let rows=await api('/api/inventory');
    const selected=populateMedicineFilter('inventorySearch', rows, i=>i.name||'');
    if(selected)rows=rows.filter(i=>(i.name||'')===selected);
    const manageBox=document.getElementById('inventoryManageBox'); if(manageBox) manageBox.classList.toggle('hidden', !canManage());
    const root=document.getElementById('inventoryBox');
    if(!rows.length){root.innerHTML='<div class="empty">Аптечка пустая</div>';return}
    root.innerHTML=`<div class="adminList">${rows.map(i=>{const manage=canManage()?`<div class="adminActions"><button class="gray" onclick="editInventory(${i.id}, '${escapeAttr(i.name)}', ${i.quantity}, '${escapeAttr(i.unit_name||'шт')}', ${i.low_threshold})">Изменить</button><button class="danger" onclick="deleteInventory(${i.id})">Удалить</button></div><div class="fieldLine"><label>Заменить фото</label><input type="file" accept="image/*" onchange="uploadInventoryPhoto(${i.id}, this.files[0])"></div>`:'';return `<div class="formrow"><div><div class="med">${escapeHtml(i.name)}</div><div class="meta">Осталось: ${i.quantity} ${escapeHtml(i.unit_name||'шт')} · напомнить при ${i.low_threshold}</div></div><div class="attachments">${i.photo_url?`<button class="fileLinkBtn" onclick="openFilePreview('${inventoryPhotoUrl(i)}', 'Фото: ${escapeAttr(i.name)}', true)">📷 Фото лекарства</button>`:''}</div>${manage}</div>`}).join('')}</div>`;
  }

  async function loadStats(){
    if(!coursesCache.length){try{coursesCache=await api('/api/courses')}catch(e){coursesCache=[]}}
    const filterRoot=document.getElementById('statsFilters');
    const currentVal=document.getElementById('statsCourseFilter')?.value || '';
    if(filterRoot){
      filterRoot.innerHTML=`<label>Фильтр по назначению</label><select id="statsCourseFilter" onchange="loadStats()"><option value="">Все назначения</option>${(coursesCache||[]).map(c=>`<option value="${c.id}" ${String(c.id)===String(currentVal)?'selected':''}>${escapeHtml(c.name||'Назначение')}</option>`).join('')}</select>`;
      if(currentVal) document.getElementById('statsCourseFilter').value=currentVal;
    }
    const courseId=document.getElementById('statsCourseFilter')?.value || '';
    const rows=await api('/api/stats'+(courseId?`?course_id=${encodeURIComponent(courseId)}`:''));
    const root=document.getElementById('stats');
    if(!rows.length){root.innerHTML='<div class="empty">Статистики пока нет</div>';return}
    root.innerHTML=`<div class="tableWrap"><table class="miniTable statsTable"><tr><th>Препарат</th><th>✅</th><th>⏭️</th><th>⏳</th><th>%</th></tr>${rows.map(r=>`<tr><td>${escapeHtml(r.medicine)}</td><td>${r.taken}</td><td>${r.skipped}</td><td>${r.pending}</td><td>${r.taken_percent}%</td></tr>`).join('')}</table></div>`;
  }



  const notifyLabels={
    reminders_enabled:'напоминания',
    taken_notifications:'принято',
    skipped_notifications:'пропуски',
    overdue_notifications:'просрочки',
    low_stock_notifications:'аптечка',
    daily_summary_enabled:'вечерний итог'
  };
  function roleLabel(r){return {owner:'Владелец',parent:'Родитель',child:'Ребенок',viewer:'Просмотр'}[r]||r;}
  function profileLabel(p){return (p.kind==='personal'?'👤 ':'👶 ') + (p.name||'Профиль');}
  function notifyToggle(member, profile, field, value, canManage){
    const disabled=canManage?'':'disabled';
    const cls=value?'on':'off';
    return `<label class="notifyChoice ${cls}"><input type="checkbox" ${value?'checked':''} ${disabled} onchange="const box=this.closest('.notifyChoice'); box?.classList.toggle('on', this.checked); box?.classList.toggle('off', !this.checked); saveNotificationSetting(${member.id},${profile.id})" data-member="${member.id}" data-profile="${profile.id}" data-field="${field}"><span class="notifyText">${notifyLabels[field]}</span><span class="notifyMark">✓</span></label>`;
  }
  async function loadNotificationSettings(){
    const root=document.getElementById('notifyBox'); if(!root)return; root.innerHTML='<div class="empty">Загрузка...</div>';
    try{
      const families=await api('/api/notification-settings');
      if(!families.length){root.innerHTML='<div class="empty">Семьи не найдены</div>';return}
      root.innerHTML=families.map(f=>{
        const can=!!f.can_manage;
        const profiles=f.profiles||[];
        const members=f.members||[];
        const body=profiles.map(p=>{
          const rows=members.map(m=>{
            const st=(m.settings||{})[String(p.id)];
            if(!st) return '';
            return `<div class="notifyMemberRow"><div class="notifyMemberHead"><b>${escapeHtml(m.full_name||String(m.tg_id))}</b><span>${roleLabel(m.role)}</span></div><div class="notifyToggles">${['reminders_enabled','taken_notifications','skipped_notifications','overdue_notifications','low_stock_notifications','daily_summary_enabled'].map(field=>notifyToggle(m,p,field,!!st[field],can)).join('')}</div></div>`;
          }).join('');
          return `<div class="notifyProfile"><div class="notifyProfileTitle">${profileLabel(p)}</div>${rows||'<div class="empty">Нет участников</div>'}</div>`;
        }).join('');
        return `<div class="card notifyFamily"><h2>${escapeHtml(f.name)}</h2>${can?'':'<div class="readonlyNotice">Настройки может менять родитель/владелец семьи.</div>'}${body}</div>`;
      }).join('') + `<div class="muted" style="font-size:12px;margin-top:10px">Настройки применяются к конкретному участнику и профилю. По умолчанию родители получают все важные уведомления, ребенок — напоминания по своему профилю.</div>`;
    }catch(e){root.innerHTML=`<div class="empty">Не удалось загрузить настройки уведомлений.<br><small>${escapeHtml(e.message||String(e))}</small></div>`;}
  }
  async function saveNotificationSetting(memberId, profileId){
    const inputs=[...document.querySelectorAll(`input[data-member="${memberId}"][data-profile="${profileId}"]`)];
    const payload={family_member_id:memberId,profile_id:profileId};
    inputs.forEach(i=>payload[i.dataset.field]=i.checked);
    try{await api('/api/notification-settings',{method:'PUT',body:JSON.stringify(payload)}); toast('Настройки сохранены');}
    catch(e){toast('Не удалось сохранить: '+(e.message||e)); await loadNotificationSettings();}
  }

  async function changeProfile(profileId){
    currentProfileId=Number(profileId); localStorage.setItem('activeProfileId',String(currentProfileId));
    renderProfileChips();
    try{ await api('/api/active-profile',{method:'POST',body:JSON.stringify({profile_id:currentProfileId})}); }catch(e){}
    editSchedules.clear(); coursesCache=[];
    await loadToday();
    if(!document.getElementById('pageAssignments')?.classList.contains('hidden')) showAdminTab(currentAdminTab||'assignments');
    if(!document.getElementById('pageInventory')?.classList.contains('hidden')) await loadInventory();
    if(!document.getElementById('pageAnalytics')?.classList.contains('hidden')) showAnalyticsTab(currentAnalyticsTab||'stats');
    if(!document.getElementById('pageMore')?.classList.contains('hidden')) showMoreTab(currentMoreTab||'family');
  }

  async function init(){
    try{
      renderFrequencySlots();
      try{const ai=await api('/api/ai/status');aiEnabled=!!ai.enabled;}catch(e){aiEnabled=false;}
      me=await api('/api/me');
      await loadProfiles();
      me=await api('/api/me');
      document.getElementById('main').classList.remove('hidden');
      document.getElementById('bottomTabs').classList.remove('hidden');
      document.body.classList.toggle('readonly', !canManage());
      await loadToday();
      const h=location.hash.replace('#','');
      if(['assignments','inventory','analytics','more'].includes(h)) showTab(h);
    }catch(e){
      const access=document.getElementById('access');
      access.className='card deny';
      access.textContent='Доступ пока не подтвержден. Напишите боту /start, чтобы отправить заявку администратору, и дождитесь подтверждения.';
    }
  }
  init();

