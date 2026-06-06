
  const tg = window.Telegram?.WebApp; if (tg) { tg.ready(); tg.expand(); }
  const initData = tg?.initData || ""; let me = null; let profiles = []; let currentProfileId = null; let todayRows = []; let todayFilter = 'pending'; let addMedicineOpen=false; let addAssignmentOpen=false; let addInventoryOpen=false; localStorage.setItem('todayFilter','pending');
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
    currentProfileId = Number(localStorage.getItem('activeProfileId')) || (profiles.find(p=>p.active)?.id) || profiles[0].id;
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
    if(name==='profiles'){loadProfileAdmin();}
  }
  function showTab(name){['today','stats','history','admin'].forEach(t=>{document.getElementById(pageId(t))?.classList.add('hidden');document.getElementById(tabId(t))?.classList.remove('active')});document.getElementById(pageId(name)).classList.remove('hidden');document.getElementById(tabId(name)).classList.add('active');location.hash=name==='today'?'':'#'+name;if(name==='today')loadToday();if(name==='stats')loadStats();if(name==='history')loadMedicines();if(name==='admin'){showAdminTab(currentAdminTab||'assignments');}window.scrollTo({top:0,behavior:'smooth'});}
  function setTodayFilter(f){todayFilter=f;localStorage.setItem('todayFilter',f);renderToday()}
  function applyFilter(rows){if(todayFilter==='all')return rows;if(todayFilter==='skipped')return rows.filter(r=>r.status==='skipped');if(todayFilter==='taken')return rows.filter(r=>r.status==='taken');if(todayFilter==='snoozed')return rows.filter(r=>r.status==='pending'&&r.postponed_until);return rows.filter(r=>r.status==='pending')}
  function statusText(r){if(r.status==='taken')return '✅ принято в '+(r.taken_at||'—');if(r.status==='skipped')return '⏭️ пропущено '+(r.skipped_at||'');if(r.postponed_until)return '😴 отложено до '+r.postponed_until;return '⏳ ждет отметки'}
  function escapeAttr(v){return String(v??'').replace(/&/g,'&amp;').replace(/'/g,'&#39;').replace(/"/g,'&quot;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}
  function escapeHtml(s){return String(s??'').replace(/[&<>\"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
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
    try{ rows=await api('/api/schedules'); }catch(e){ rows=[]; }
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
  async function loadCourses(){
    if(!me?.can_manage_current_profile)return;
    const rows=await api('/api/courses');
    const sel=document.getElementById('courseSelect');
    if(sel) sel.innerHTML='<option value="">Без назначения</option>'+rows.map(c=>`<option value="${c.id}">${escapeHtml(c.name)}</option>`).join('');
    const root=document.getElementById('coursesBox');
    root.innerHTML=`<button id="courseAddToggle" class="collapseToggle" onclick="toggleAddAssignmentForm()"><span>${addAssignmentOpen?'Скрыть форму добавления':'Добавить назначение'}</span><span class="chev">${addAssignmentOpen?'−':'＋'}</span></button><div id="courseAddForm" class="courseAddForm ${addAssignmentOpen?'':'hidden'}"><div class="fieldLine"><label>Название</label><input id="courseName" placeholder="Например, назначение гастроэнтеролога"></div><div class="fieldLine"><label>Дата назначения</label><input id="courseDate" type="date"></div><div class="fieldLine"><label>Врач</label><input id="courseDoctor" placeholder="опционально"></div><div class="fieldLine"><label>Комментарий</label><input id="courseComment" placeholder="опционально"></div><div class="fieldLine"><label>Фото / файл</label><input id="courseFile" type="file" accept="image/*,.pdf,.doc,.docx,.txt"></div><button class="wide" onclick="addCourse()" style="margin-top:8px">Сохранить назначение</button></div><div class="adminList" style="margin-top:14px">${rows.length?rows.map(c=>`<div class="formrow"><b>${escapeHtml(c.name)}</b><div class="muted" style="font-size:12px;margin-top:3px">Дата назначения: ${escapeHtml(c.assignment_date||'—')}${c.doctor?' · '+escapeHtml(c.doctor):''}</div>${c.comment?`<div class="muted" style="font-size:12px;margin-top:3px">${escapeHtml(c.comment)}</div>`:''}<div class="attachments">${(c.attachments||[]).map(a=>`<button class="fileLinkBtn" onclick="openFilePreview('${attachmentUrl(a.id)}', '${escapeAttr(a.filename)}', ${isImageFilename(a.filename)})">📎 ${escapeHtml(a.filename)}</button>`).join('')}</div><div class="adminActions"><button class="gray" onclick="editCourse(${c.id}, '${escapeAttr(c.name)}', '${c.assignment_date||''}', '${escapeAttr(c.doctor||'')}', '${escapeAttr(c.comment||'')}')">Изменить</button><button class="danger" onclick="deleteCourse(${c.id})">Удалить назначение</button></div><div class="fieldLine"><label>Добавить файл</label><input id="courseFile_${c.id}" type="file" accept="image/*,.pdf,.doc,.docx,.txt" onchange="uploadCourseFile(${c.id})"></div></div>`).join(''):'<div class="empty">Назначений пока нет</div>'}</div>`;
  }
  async function addCourse(){const p={name:document.getElementById('courseName').value.trim(),assignment_date:document.getElementById('courseDate').value||null,doctor:document.getElementById('courseDoctor').value.trim(),comment:document.getElementById('courseComment').value.trim()}; if(!p.name){toast('Укажите название назначения');return} const created=await api('/api/courses',{method:'POST',body:JSON.stringify(p)}); const file=document.getElementById('courseFile').files[0]; if(file){await uploadFileToCourse(created.id,file)} toast('Назначение добавлено'); addAssignmentOpen=false; await loadCourses(); await loadAudit()}
  async function editCourse(id,name,assignmentDate,doctor,comment){const body=`<label>Название</label><input id="cName" value="${name}"><label>Дата назначения</label><input id="cDate" type="date" value="${assignmentDate}"><label>Врач</label><input id="cDoctor" value="${doctor}"><label>Комментарий</label><input id="cComment" value="${comment}">`; const ok=await openModal('Изменить назначение', body, ()=>closeModal({name:document.getElementById('cName').value.trim(),assignment_date:document.getElementById('cDate').value||null,doctor:document.getElementById('cDoctor').value.trim(),comment:document.getElementById('cComment').value.trim()})); if(!ok)return; await api('/api/courses/'+id,{method:'PUT',body:JSON.stringify(ok)}); toast('Назначение обновлено'); await loadCourses(); await loadAudit()}
  async function deleteCourse(id){const ok=await confirmAction('Удалить назначение?', 'Назначение будет отключено вместе с лекарствами внутри него.', 'Действие попадет в журнал.'); if(!ok)return; await api('/api/courses/'+id,{method:'DELETE'}); toast('Назначение удалено'); await loadCourses(); await loadSchedules(); await loadToday(); await loadAudit()}
  async function uploadFileToCourse(id,file){const fd=new FormData();fd.append('file',file);await api('/api/courses/'+id+'/attachments',{method:'POST',body:fd,headers:{}})}
  async function uploadCourseFile(id){const el=document.getElementById('courseFile_'+id);if(!el?.files?.[0])return;await uploadFileToCourse(id,el.files[0]);toast('Файл добавлен');await loadCourses();await loadAudit()}
  const editSchedules = new Set();
  const scheduleDrafts = new Map();
  function fieldHtml(id, value, editable, type='text'){
    const safe = escapeHtml(value || '');
    if(editable) return `<input id="${id}" ${type!=='text'?`type="${type}"`:''} value="${safe}">`;
    return `<div id="${id}" class="valueBox ${safe?'':'isEmpty'}" data-value="${safe}">${safe || '—'}</div>`;
  }
  function scheduleForm(r){
    const edit=editSchedules.has(r.id);
    return `<div class="formrow" id="sched_${r.id}">
      <div class="schedTop">
        <div><label>Лекарство</label>${fieldHtml('s_name_'+r.id, r.name, edit)}</div>
        <div><label>Доза</label>${fieldHtml('s_dose_'+r.id, r.dose, edit)}</div>
        <div><label>Время</label>${fieldHtml('s_time_'+r.id, edit ? r.time_local : (r.display_time || r.time_local), edit, 'time')}</div>
      </div>
      <div class="schedComment"><label>Комментарий</label>${fieldHtml('s_label_'+r.id, r.label, edit)}</div>
      <div class="schedDates"><div><label>Дата начала</label>${fieldHtml('s_start_'+r.id, r.start_date || '', edit, 'date')}</div><div><label>Дата окончания</label>${fieldHtml('s_end_'+r.id, r.end_date || '', edit, 'date')}</div></div>
      <div class="adminActions">${edit?`<button onclick="saveSchedule(${r.id})">Сохранить</button><button class="gray" onclick="cancelScheduleEdit(${r.id})">Отменить</button>`:`<button class="blue" onclick="enableScheduleEdit(${r.id})">Изменить</button><button class="danger" onclick="deleteSchedule(${r.id})">Удалить</button>`}</div>
    </div>`
  }
  function enableScheduleEdit(id){editSchedules.add(id);loadSchedules()}
  function cancelScheduleEdit(id){editSchedules.delete(id);scheduleDrafts.delete(id);loadSchedules()}
  async function loadSchedules(){if(!me?.can_manage_current_profile)return;let rows=await api('/api/schedules');const selected=populateMedicineFilter('scheduleSearch', rows, r=>r.name||'');if(selected)rows=rows.filter(r=>(r.name||'')===selected);const root=document.getElementById('schedules');root.innerHTML=rows.length?`<div class="adminList">${rows.map(scheduleForm).join('')}</div>`:'<div class="empty">Расписание пустое</div>'}
  function readSchedule(id){return {name:document.getElementById('s_name_'+id).value.trim(),dose:document.getElementById('s_dose_'+id).value.trim(),time_local:document.getElementById('s_time_'+id).value,label:document.getElementById('s_label_'+id).value.trim(),start_date:document.getElementById('s_start_'+id).value||null,end_date:document.getElementById('s_end_'+id).value||null}}
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

  async function loadStats(){const rows=await api('/api/stats');const root=document.getElementById('stats');if(!rows.length){root.innerHTML='<div class="empty">Статистики пока нет</div>';return}root.innerHTML=`<div class="tableWrap"><table class="miniTable statsTable"><tr><th>Препарат</th><th>✅</th><th>⏭️</th><th>⏳</th><th>%</th></tr>${rows.map(r=>`<tr><td>${escapeHtml(r.medicine)}</td><td>${r.taken}</td><td>${r.skipped}</td><td>${r.pending}</td><td>${r.taken_percent}%</td></tr>`).join('')}</table></div>`}
  async function loadMedicines(){const select=document.getElementById('medicineSelect');const meds=await api('/api/medicines');select.innerHTML=meds.map(m=>`<option value="${m.id}">${escapeHtml(m.name)}</option>`).join('');if(meds.length)loadHistory();else document.getElementById('history').innerHTML='<div class="empty">Препаратов пока нет</div>'}
  async function loadHistory(){const id=document.getElementById('medicineSelect').value;const root=document.getElementById('history');if(!id){root.textContent='Выберите препарат';return}const rows=await api(`/api/medicines/${id}/history`);if(!rows.length){root.innerHTML='<div class="empty">Истории пока нет</div>';return}root.innerHTML=`<div class="tableWrap"><table class="miniTable historyTable"><tr><th>Дата</th><th>План</th><th>Статус</th></tr>${rows.map(r=>`<tr><td>${r.date}</td><td>${r.due_time}<br>${escapeHtml(r.dose)}</td><td>${r.status==='taken'?'✅ '+(r.taken_at||''):r.status==='skipped'?'⏭️ '+(r.skipped_at||''):'⏳'}</td></tr>`).join('')}</table></div>`}
  async function init(){try{renderFrequencySlots();me=await api('/api/me');await loadProfiles();me=await api('/api/me');document.getElementById('main').classList.remove('hidden');document.getElementById('bottomTabs').classList.remove('hidden');if(me.can_manage_current_profile)document.getElementById('tabAdmin').classList.remove('hidden');await loadToday();const h=location.hash.replace('#','');if(['stats','history','admin'].includes(h)){if(h==='admin'&&!me.can_manage_current_profile)showTab('today');else showTab(h)}}catch(e){const access=document.getElementById('access');access.className='card deny';access.textContent='Доступ закрыт. Откройте мини-приложение из Telegram и убедитесь, что ваш Telegram ID добавлен в CHILD_CHAT_ID или PARENT_CHAT_IDS.'}}
  init();
