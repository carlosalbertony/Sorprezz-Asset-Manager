function isLocalHost(){const h=location.hostname;return h==='127.0.0.1'||h==='localhost'||h==='::1'}
let CATEGORIES=[];
let TAGS=[];
let EXPLORER={rid:null,path:'',items:[],selected:new Set(),view:'grid',resource:null};
let GLOBAL_ASSETS=[];
let GLOBAL_SELECTED=new Map();
let COLLECTIONS=[];
let PENDING_COLLECTION_REFS=[];
let DOWNLOAD_TAG_IDS=new Set();
let DOWNLOAD_MODE='single';
let SOURCE_MODE='drive';
let LIBRARY_MODE='resources';
let DRIVE_STATUS={client_configured:false,accounts:[]};
const pages={
  dashboard:['Inicio','Organiza tus imágenes y recursos desde un solo lugar.'],
  downloads:['Agregar material','Incorpora material desde Google Drive o crea una carpeta manual.'],
  library:['Biblioteca','Busca, visualiza y administra todos los recursos e imágenes.'],
  collections:['Colecciones','Agrupa imágenes de distintos recursos sin modificar los originales.'],
  organization:['Organización','Administra categorías, subcategorías y etiquetas.'],
  settings:['Configuración','Almacenamiento y utilidades de la aplicación.']
};

async function api(path,opts={}){const r=await fetch(path,{headers:{'Content-Type':'application/json'},...opts});let data={};try{data=await r.json()}catch{}if(!r.ok)throw new Error(data.detail||'Error en la operación');return data}
async function apiForm(path,form,opts={}){const r=await fetch(path,{method:'POST',body:form,...opts});let data={};try{data=await r.json()}catch{}if(!r.ok)throw new Error(data.detail||'Error en la operación');return data}
function toast(msg){const t=document.getElementById('toast');t.textContent=msg;t.classList.remove('hidden');setTimeout(()=>t.classList.add('hidden'),3500)}
function fmtBytes(n){n=Number(n||0);if(!n)return '0 B';const u=['B','KB','MB','GB','TB'];let i=Math.min(u.length-1,Math.floor(Math.log(n)/Math.log(1024)));return (n/Math.pow(1024,i)).toFixed(i?1:0)+' '+u[i]}
function statusLabel(s){return {pendiente:'Pendiente',descargando:'Descargando',completado:'Completado',parcial:'Completado',incompleto:'Incompleto',error:'Con error'}[s]||s}
function go(p){
  document.querySelectorAll('.page').forEach(x=>x.classList.remove('active'));
  document.querySelectorAll('.nav').forEach(x=>x.classList.toggle('active',x.dataset.page===p));
  const page=document.getElementById('page-'+p);if(!page)return;
  page.classList.add('active');
  document.getElementById('pageTitle').textContent=pages[p][0];
  document.getElementById('pageSubtitle').textContent=pages[p][1];
  if(p==='dashboard')loadDashboard();
  if(p==='downloads')loadDownloadsSetup();
  if(p==='library'){closeLibraryExplorer(true);setLibraryMode(LIBRARY_MODE||'resources');}
  if(p==='collections')loadCollectionsPage();
  if(p==='organization'){loadCategories();ensureTags().then(renderTagsManager);}
  if(p==='settings')loadSettings();
}
document.querySelectorAll('.nav').forEach(b=>b.onclick=()=>go(b.dataset.page));

async function loadCategories(){
  CATEGORIES=await api('/api/categories');
  const sels=['rCategory','filterCategory','subCatParent','globalAssetCategory'];
  for(const id of sels){
    const s=document.getElementById(id);if(!s)continue;
    const preserve=s.value;
    const first=(id==='filterCategory'||id==='globalAssetCategory')?'<option value="">Todas las categorías</option>':'';
    s.innerHTML=first+CATEGORIES.map(c=>`<option value="${c.id}">${esc(c.name)}</option>`).join('');
    if(preserve)s.value=preserve;
  }
  updateSubcategorySelect();
  const tree=document.getElementById('categoryTree');
  if(tree)tree.innerHTML=CATEGORIES.map(c=>`<div class="cat-card"><strong>${esc(c.name)}</strong><div class="chips">${c.subcategories.length?c.subcategories.map(s=>`<span class="chip">${esc(s.name)}</span>`).join(''):'<span class="muted">Sin subcategorías</span>'}</div></div>`).join('');
}
function updateSubcategorySelect(){const el=document.getElementById('rCategory');if(!el)return;const catId=Number(el.value);const cat=CATEGORIES.find(c=>c.id===catId);const s=document.getElementById('rSubcategory');if(s)s.innerHTML='<option value="">General</option>'+((cat?.subcategories||[]).map(x=>`<option value="${x.id}">${esc(x.name)}</option>`).join(''))}
document.getElementById('rCategory')?.addEventListener('change',updateSubcategorySelect);
async function createCategory(){const i=document.getElementById('newCategoryName');if(!i.value.trim())return;try{await api('/api/categories',{method:'POST',body:JSON.stringify({name:i.value.trim()})});i.value='';await loadCategories();toast('Categoría creada')}catch(e){toast(e.message)}}
async function createSubcategory(){const i=document.getElementById('newSubcategoryName'),cat=Number(document.getElementById('subCatParent').value);if(!i.value.trim()||!cat)return;try{await api('/api/subcategories',{method:'POST',body:JSON.stringify({category_id:cat,name:i.value.trim()})});i.value='';await loadCategories();toast('Subcategoría creada')}catch(e){toast(e.message)}}
async function quickSubcategory(){const cat=Number(document.getElementById('rCategory').value);if(!cat)return toast('Selecciona primero una categoría');const name=prompt('Nombre de la nueva subcategoría:');if(!name)return;try{await api('/api/subcategories',{method:'POST',body:JSON.stringify({category_id:cat,name:name.trim()})});await loadCategories();const c=CATEGORIES.find(c=>c.id===cat),s=c?.subcategories.find(s=>s.name.toLowerCase()===name.trim().toLowerCase());document.getElementById('rCategory').value=cat;updateSubcategorySelect();if(s)document.getElementById('rSubcategory').value=s.id;toast('Subcategoría creada')}catch(e){toast(e.message)}}

async function quickCategory(){
  const name=prompt('Nombre de la nueva categoría:');
  if(!name||!name.trim())return;
  try{
    const d=await api('/api/categories',{method:'POST',body:JSON.stringify({name:name.trim()})});
    await loadCategories();
    const found=CATEGORIES.find(c=>c.name.toLowerCase()===name.trim().toLowerCase());
    if(found){document.getElementById('rCategory').value=String(found.id);updateSubcategorySelect();}
    toast('Categoría creada');
  }catch(e){toast(e.message)}
}

function selectedDownloadTagIds(){return [...DOWNLOAD_TAG_IDS]}
function renderDownloadTagChips(){
  const box=document.getElementById('downloadTagChips');if(!box)return;
  box.innerHTML=[...DOWNLOAD_TAG_IDS].map(id=>{
    const t=TAGS.find(x=>x.id===id);if(!t)return '';
    return `<span class="chip strong">#${esc(t.name)} <button class="chip-x" onclick="removeDownloadTag(${id})">×</button></span>`;
  }).join('');
}
function removeDownloadTag(id){DOWNLOAD_TAG_IDS.delete(Number(id));renderDownloadTagChips()}
function addDownloadTagFromSelect(){
  const s=document.getElementById('downloadTagSelect');const id=Number(s?.value||0);
  if(id){DOWNLOAD_TAG_IDS.add(id);renderDownloadTagChips();}
  if(s)s.value='';
}
async function quickDownloadTag(){
  const name=prompt('Nombre de la nueva etiqueta:');if(!name||!name.trim())return;
  try{
    const d=await api('/api/tags',{method:'POST',body:JSON.stringify({name:name.trim()})});
    await ensureTags();
    const t=TAGS.find(x=>x.name.toLowerCase()===name.trim().toLowerCase());
    if(t){DOWNLOAD_TAG_IDS.add(t.id);renderDownloadTagChips()}
    toast('Etiqueta creada');
  }catch(e){toast(e.message)}
}
async function quickDownloadCollection(){
  await loadCategories();
  const catId=Number(document.getElementById('rCategory')?.value||CATEGORIES[0]?.id||0);
  const subId=Number(document.getElementById('rSubcategory')?.value||0);
  document.getElementById('modalContent').innerHTML=collectionModalFields('Nueva colección','',catId,subId)+`<div class="actions"><button class="secondary" onclick="closeModal()">Cancelar</button><button class="primary" onclick="createDownloadCollectionFromModal()">Crear y seleccionar</button></div>`;
  document.getElementById('modal').classList.remove('hidden');
}
async function createDownloadCollectionFromModal(){const name=document.getElementById('collectionName').value.trim(),category_id=Number(document.getElementById('collectionCategory').value||0),subcategory_id=Number(document.getElementById('collectionSubcategory').value||0)||null,description=document.getElementById('collectionDescription').value.trim();if(!name)return toast('Escribe un nombre');if(!category_id)return toast('Selecciona una categoría global');try{const d=await api('/api/collections',{method:'POST',body:JSON.stringify({name,category_id,subcategory_id,description})});closeModal();await loadDownloadCollections();document.getElementById('downloadCollection').value=String(d.id);toast('Colección creada y seleccionada')}catch(e){toast(e.message)}}
async function loadDownloadCollections(){
  try{
    const cols=await api('/api/collections');
    const sel=document.getElementById('downloadCollection');if(!sel)return;
    const keep=sel.value;
    sel.innerHTML='<option value="">No agregar a colección</option>'+cols.map(c=>`<option value="${c.id}">${esc(c.category)}${c.subcategory?' / '+esc(c.subcategory):''} · ${esc(c.name)}</option>`).join('');
    if(keep)sel.value=keep;
  }catch(e){console.warn(e)}
}
async function loadDriveStatus(renderSettings=false){
  try{
    DRIVE_STATUS=await api('/api/google/status');
  }catch(e){DRIVE_STATUS={client_configured:false,accounts:[],error:e.message}}
  renderDownloadDriveAccounts();
  if(renderSettings)renderGoogleDriveSettings();
  return DRIVE_STATUS;
}
function renderDownloadDriveAccounts(){
  const sel=document.getElementById('downloadDriveAccount');
  if(sel){
    const keep=sel.value;
    sel.innerHTML='<option value="">Automático (cuenta activa)</option>'+(DRIVE_STATUS.accounts||[]).map(a=>`<option value="${a.id}">${a.is_active?'★ ':''}${esc(a.email)}</option>`).join('');
    if(keep&&[...sel.options].some(o=>o.value===keep))sel.value=keep;
  }
  const st=document.getElementById('downloadDriveStatus');
  if(st){
    if(!DRIVE_STATUS.client_configured)st.innerHTML='<span class="warn-dot">●</span> Falta seleccionar el JSON OAuth en Configuración';
    else if(!(DRIVE_STATUS.accounts||[]).length)st.innerHTML='<span class="warn-dot">●</span> Credenciales listas · conecta una cuenta Google';
    else st.innerHTML=`<span class="ok-dot">●</span> API oficial activa · ${(DRIVE_STATUS.accounts||[]).length} cuenta${DRIVE_STATUS.accounts.length===1?'':'s'}`;
  }
}
function onDownloadDriveAccountChange(){}
function renderGoogleDriveSettings(){
  const badge=document.getElementById('googleDriveBadge'),client=document.getElementById('googleClientStatus'),list=document.getElementById('googleAccounts'),connect=document.getElementById('connectGoogleBtn'),remove=document.getElementById('removeGoogleClientBtn');
  if(!badge)return;
  const accounts=DRIVE_STATUS.accounts||[];
  if(DRIVE_STATUS.client_configured&&accounts.length){badge.textContent='Conectado';badge.className='connection-badge connected'}
  else if(DRIVE_STATUS.client_configured){badge.textContent='Falta conectar cuenta';badge.className='connection-badge waiting'}
  else{badge.textContent='No configurado';badge.className='connection-badge disconnected'}
  if(client)client.innerHTML=DRIVE_STATUS.client_configured?'<b>✓ JSON OAuth configurado localmente</b>':'Selecciona el JSON descargado desde Google Cloud.';
  if(remove)remove.classList.toggle('hidden',!DRIVE_STATUS.client_configured);
  if(connect)connect.disabled=!DRIVE_STATUS.client_configured;
  if(list){
    list.innerHTML=accounts.length?accounts.map(a=>`<div class="google-account ${a.is_active?'active-account':''}"><div><b>${esc(a.display_name||a.email)}</b><span>${esc(a.email)}</span></div><div class="google-account-actions">${a.is_active?'<span class="active-pill">Cuenta activa</span>':`<button class="secondary compact-btn" onclick="activateGoogleAccount(${a.id})">Usar esta cuenta</button>`}<button class="danger ghost-danger compact-btn" onclick="disconnectGoogleAccount(${a.id})">Desconectar</button></div></div>`).join(''):'<div class="empty-inline">Todavía no hay cuentas de Google conectadas.</div>';
  }
}
async function uploadGoogleClientJson(input){
  const file=input?.files?.[0];if(!file)return;
  const form=new FormData();form.append('file',file);
  try{await apiForm('/api/google/client',form);toast('Credenciales OAuth configuradas');input.value='';await loadDriveStatus(true)}catch(e){toast(e.message);input.value=''}
}
async function removeGoogleClient(){
  if(!confirm('¿Quitar el JSON OAuth guardado localmente? Las cuentas ya autorizadas seguirán registradas, pero no podrás conectar nuevas hasta volver a cargarlo.'))return;
  try{await api('/api/google/client',{method:'DELETE'});toast('Credenciales OAuth quitadas');await loadDriveStatus(true)}catch(e){toast(e.message)}
}
async function connectGoogleAccount(){
  const btn=document.getElementById('connectGoogleBtn');if(btn){btn.disabled=true;btn.textContent='Esperando autorización en Google…'}
  toast('Se abrirá Google en tu navegador. Autoriza la cuenta y vuelve a Sorprezz.');
  try{const d=await api('/api/google/accounts/connect',{method:'POST'});toast(`Google Drive conectado: ${d.account.email}`);await loadDriveStatus(true);await loadDownloadsSetup()}catch(e){toast(e.message)}finally{if(btn){btn.disabled=false;btn.textContent='+ Conectar cuenta Google'}}
}
async function activateGoogleAccount(id){try{await api(`/api/google/accounts/${id}/activate`,{method:'POST'});toast('Cuenta activa actualizada');await loadDriveStatus(true)}catch(e){toast(e.message)}}
async function disconnectGoogleAccount(id){if(!confirm('¿Desconectar esta cuenta de Google Drive de Sorprezz?'))return;try{await api(`/api/google/accounts/${id}`,{method:'DELETE'});toast('Cuenta desconectada');await loadDriveStatus(true);await loadDownloadsSetup()}catch(e){toast(e.message)}}

async function loadDownloadsSetup(){
  await Promise.all([loadCategories(),ensureTags(),loadDownloadCollections(),loadDriveStatus(false)]);
  const tagSel=document.getElementById('downloadTagSelect');
  if(tagSel){
    tagSel.innerHTML='<option value="">Agregar etiqueta…</option>'+TAGS.map(t=>`<option value="${t.id}">${esc(t.name)}</option>`).join('');
    tagSel.onchange=addDownloadTagFromSelect;
  }
  try{
    const rs=await api('/api/resources');
    const dl=document.getElementById('resourceNameSuggestions');
    if(dl)dl.innerHTML=[...new Set(rs.map(r=>r.name).filter(Boolean))].slice(0,500).map(n=>`<option value="${esc(n)}"></option>`).join('');
  }catch(e){console.warn(e)}
  renderDownloadTagChips();
}
function setSourceMode(mode){
  SOURCE_MODE=mode==='manual'?'manual':'drive';
  document.getElementById('sourceDriveBtn')?.classList.toggle('active',SOURCE_MODE==='drive');
  document.getElementById('sourceManualBtn')?.classList.toggle('active',SOURCE_MODE==='manual');
  document.getElementById('driveSourcePanel')?.classList.toggle('hidden',SOURCE_MODE!=='drive');
  document.getElementById('manualSourcePanel')?.classList.toggle('hidden',SOURCE_MODE!=='manual');
  document.getElementById('driveOptions')?.classList.toggle('hidden',SOURCE_MODE!=='drive');
  document.getElementById('driveAutomaticNote')?.classList.toggle('hidden',SOURCE_MODE!=='drive');
  document.getElementById('driveActions')?.classList.toggle('hidden',SOURCE_MODE!=='drive');
  document.getElementById('manualActions')?.classList.toggle('hidden',SOURCE_MODE!=='manual');
}

function setDownloadMode(mode){
  DOWNLOAD_MODE=mode==='batch'?'batch':'single';
  document.getElementById('downloadSingleSource')?.classList.toggle('hidden',DOWNLOAD_MODE!=='single');
  document.getElementById('downloadBatchSource')?.classList.toggle('hidden',DOWNLOAD_MODE!=='batch');
  document.getElementById('downloadModeSingleBtn')?.classList.toggle('active',DOWNLOAD_MODE==='single');
  document.getElementById('downloadModeBatchBtn')?.classList.toggle('active',DOWNLOAD_MODE==='batch');
}
function resourcePayload(nameOverride=null,urlOverride=null){
  return {
    name:(nameOverride??document.getElementById('rName')?.value??'').trim(),
    url:(urlOverride??document.getElementById('rUrl')?.value??'').trim(),
    category_id:Number(document.getElementById('rCategory')?.value||0),
    subcategory_id:document.getElementById('rSubcategory')?.value?Number(document.getElementById('rSubcategory').value):null,
    tag_ids:selectedDownloadTagIds(),
    collection_id:document.getElementById('downloadCollection')?.value?Number(document.getElementById('downloadCollection').value):null,
    avoid_duplicates:Boolean(document.getElementById('avoidDuplicates')?.checked),
    drive_account_id:document.getElementById('downloadDriveAccount')?.value?Number(document.getElementById('downloadDriveAccount').value):null
  }
}
async function analyzeLink(){
  const p=resourcePayload();
  if(!p.url)return toast('Pega un enlace');
  if(!p.name)p.name='Sin nombre';
  const b=document.getElementById('analyzeBox');if(b){b.classList.remove('hidden');b.innerHTML='Analizando acceso y contenido de Google Drive…'}
  try{
    const d=await api('/api/analyze',{method:'POST',body:JSON.stringify(p)});
    if(d.remote){
      const r=d.remote;
      if((!document.getElementById('rName').value.trim()||document.getElementById('rName').value.trim()==='Sin nombre')&&r.item?.name)document.getElementById('rName').value=r.item.name;
      if(document.getElementById('downloadDriveAccount')&&r.account?.id)document.getElementById('downloadDriveAccount').value=String(r.account.id);
      b.innerHTML=`<strong>✓ Acceso confirmado con la API oficial</strong><div class="analyze-grid"><span><b>${esc(r.item?.name||'Google Drive')}</b><small>${r.item?.kind==='folder'?'Carpeta':'Archivo'}</small></span><span><b>${r.file_count}</b><small>archivos</small></span><span><b>${r.folder_count}</b><small>subcarpetas</small></span><span><b>${fmtBytes(r.total_bytes)}</b><small>tamaño conocido</small></span></div><div class="muted">Cuenta con acceso: <b>${esc(r.account?.email||'')}</b>${r.skipped_count?` · ${r.skipped_count} elementos especiales se revisarán al descargar`:''}</div>${d.existing?`<div class="warning-inline">⚠ Este enlace ya está registrado como <b>${esc(d.existing.name)}</b>.</div>`:''}`;
    }else{
      b.innerHTML=d.existing?`⚠ Este enlace ya está registrado como <strong>${esc(d.existing.name)}</strong> (${statusLabel(d.existing.status)}).`:`✓ Enlace reconocido. ${d.drive_api_error?`La API oficial no pudo analizarlo: ${esc(d.drive_api_error)}. Se podrá intentar con enlace público.`:'Conecta Google Drive en Configuración para contar y verificar el contenido antes de descargar.'}`;
    }
  }catch(e){if(b)b.innerHTML=`<span class="error-text">${esc(e.message)}</span>`;toast(e.message)}
}
function parseBatchLines(){
  const raw=(document.getElementById('batchLinks')?.value||'').split(/\r?\n/).map(x=>x.trim()).filter(Boolean);
  return raw.map((line,i)=>{
    const pipe=line.indexOf('|');
    if(pipe>0){
      return {name:line.slice(0,pipe).trim(),url:line.slice(pipe+1).trim()};
    }
    return {name:`Recurso ${String(i+1).padStart(3,'0')}`,url:line};
  }).filter(x=>x.url);
}
async function createAndStartResource(payload){
  const r=await api('/api/resources',{method:'POST',body:JSON.stringify(payload)});
  await api(`/api/resources/${r.id}/download`,{method:'POST'});
  return r;
}
function resetDownloadForm(preserveClassification=true){
  document.getElementById('rUrl').value='';
  document.getElementById('rName').value='';
  document.getElementById('batchLinks').value='';
  document.getElementById('analyzeBox')?.classList.add('hidden');
  const keep=preserveClassification&&Boolean(document.getElementById('keepClassification')?.checked);
  if(!keep){
    DOWNLOAD_TAG_IDS.clear();
    const c=document.getElementById('rCategory');if(c&&c.options.length)c.selectedIndex=0;
    updateSubcategorySelect();
    const col=document.getElementById('downloadCollection');if(col)col.value='';
  }
  renderDownloadTagChips();
}
async function saveAndDownload(addAnother=false){
  try{
    if(DOWNLOAD_MODE==='batch'){
      const rows=parseBatchLines();if(!rows.length)return toast('Pega al menos un enlace');
      const base=resourcePayload();
      if(!base.category_id)return toast('Selecciona una categoría');
      let ok=0,failed=0;
      for(const row of rows){
        try{await createAndStartResource({...base,name:row.name,url:row.url});ok++}catch(e){failed++}
      }
      toast(`Lote iniciado: ${ok} descargas${failed?` · ${failed} no se pudieron registrar`:''}`);
      resetDownloadForm(addAnother);
      if(!addAnother)go('library');
      return;
    }
    const p=resourcePayload();
    if(!p.name||!p.url||!p.category_id)return toast('Completa nombre, enlace y categoría');
    await createAndStartResource(p);
    toast('Recurso agregado. La descarga comenzó.');
    resetDownloadForm(addAnother);
    if(!addAnother)go('library');
  }catch(e){toast(e.message)}
}

function resetManualForm(){
  const n=document.getElementById('manualName');if(n)n.value='';
  DOWNLOAD_TAG_IDS.clear();renderDownloadTagChips();
  const col=document.getElementById('downloadCollection');if(col)col.value='';
}
async function createManualMaterial(){
  const name=(document.getElementById('manualName')?.value||'').trim();
  const category_id=Number(document.getElementById('rCategory')?.value||0);
  if(!name||!category_id)return toast('Completa nombre y categoría');
  const payload={name,category_id,subcategory_id:document.getElementById('rSubcategory')?.value?Number(document.getElementById('rSubcategory').value):null,tag_ids:selectedDownloadTagIds(),collection_id:document.getElementById('downloadCollection')?.value?Number(document.getElementById('downloadCollection').value):null};
  try{
    const r=await api('/api/resources/manual',{method:'POST',body:JSON.stringify(payload)});
    toast('Material manual creado en Biblioteca');
    resetManualForm();
    go('library');
    setTimeout(()=>openExplorer(r.id),80);
  }catch(e){toast(e.message)}
}

async function loadDashboard(){
  const s=await api('/api/stats');
  document.getElementById('stats').innerHTML=[
    ['Recursos',s.resources],
    ['Colecciones',s.collections||0],
    ['Completados',s.completed],
    ['En cola',s.pending],
    ['Archivos',s.files],
    ['Espacio',fmtBytes(s.bytes)]
  ].map(([a,b])=>`<div class="stat"><b>${b}</b><span>${a}</span></div>`).join('');
  const rs=await api('/api/resources');
  document.getElementById('recentResources').innerHTML=renderResources(rs.slice(0,8),true);
}
function setLibraryMode(mode){
  LIBRARY_MODE=mode==='images'?'images':'resources';
  document.getElementById('libraryResourcesBtn')?.classList.toggle('active',LIBRARY_MODE==='resources');
  document.getElementById('libraryImagesBtn')?.classList.toggle('active',LIBRARY_MODE==='images');
  document.getElementById('libraryResourcesView')?.classList.toggle('hidden',LIBRARY_MODE!=='resources');
  document.getElementById('libraryImagesView')?.classList.toggle('hidden',LIBRARY_MODE!=='images');
  if(LIBRARY_MODE==='resources')loadLibrary();else loadLibraryAssets();
}
async function loadLibraryAssets(){
  await Promise.all([ensureTags(),loadCategories()]);
  try{
    const resources=(await api('/api/resources')).filter(r=>r.local_path);
    const tag=document.getElementById('globalAssetTag'),cat=document.getElementById('globalAssetCategory'),res=document.getElementById('globalAssetResource');
    if(tag){const keep=tag.value;tag.innerHTML='<option value="">Todas las etiquetas</option>'+TAGS.map(t=>`<option value="${t.id}">${esc(t.name)} · ${t.files||0} archivos</option>`).join('');tag.value=keep}
    if(cat){const keep=cat.value;cat.innerHTML='<option value="">Todas las categorías</option>'+CATEGORIES.map(c=>`<option value="${c.id}">${esc(c.name)}</option>`).join('');cat.value=keep}
    if(res){const keep=res.value;res.innerHTML='<option value="">Todos los recursos</option>'+resources.map(r=>`<option value="${r.id}">${esc(r.name)}</option>`).join('');res.value=keep}
    await loadGlobalAssets();
  }catch(e){toast(e.message)}
}
function closeLibraryExplorer(silent=false){
  document.getElementById('libraryExplorerView')?.classList.add('hidden');
  document.getElementById('libraryHome')?.classList.remove('hidden');
  if(!silent)setLibraryMode(LIBRARY_MODE||'resources');
}

async function loadLibrary(){
  const box=document.getElementById('libraryList');
  try{
    await ensureTags();
    const q=(document.getElementById('searchQ').value||'').trim();
    const c=document.getElementById('filterCategory').value||'';
    const s=document.getElementById('filterStatus').value||'';
    const t=document.getElementById('filterTag')?.value||'';
    const params=new URLSearchParams();
    if(q)params.set('q',q);
    if(c)params.set('category_id',c);
    if(s)params.set('status',s);
    if(t)params.set('tag_id',t);
    const query=params.toString();
    const rs=await api('/api/resources'+(query?'?'+query:''));
    const filtered=Boolean(q||c||s||t);
    box.innerHTML=renderResources(rs,false,filtered);
  }catch(e){
    box.innerHTML='<div class="empty"><b>No se pudo cargar la biblioteca.</b><br><span class="muted">'+esc(e.message)+'</span></div>';
    toast(e.message);
  }
}
function sourceLabel(r){if(r.source_type==='manual')return 'Manual';return r.download_engine==='drive_api'?'Google Drive · API oficial':'Google Drive'}
function renderResources(rs,compact=false,filtered=false){
  if(!rs.length)return `<div class="empty">${filtered?'No hay recursos que coincidan con los filtros seleccionados. Usa “Limpiar filtros” para ver toda la biblioteca.':'Todavía no hay recursos registrados.'}</div>`;
  return rs.map(r=>{
    const isManual=r.source_type==='manual';
    const dlTitle=r.status==='completado'?'Actualizar / volver a descargar':(r.status==='parcial'?'Actualizar descarga':'Descargar');
    const sourceDetail=isManual?`<span class="source-badge source-manual">Manual</span>`:`<span class="source-badge source-drive">${esc(sourceLabel(r))}</span>`;
    return `<div class="resource"><div><div class="resource-title">${esc(r.name)}</div><div class="resource-sub">${esc(r.category_name||'Sin categoría')} · ${esc(r.subcategory_name||'General')} · ${r.file_count||0} archivos · ${fmtBytes(r.total_bytes)} · ${sourceDetail}</div>${r.live?`<div class="progress"><i style="width:${r.live.progress||0}%"></i></div><div class="resource-sub">${esc(r.live.message||'')}</div>`:''}${r.error&&!r.live&&(r.status==='error'||r.status==='incompleto')?`<div class="resource-sub error-summary">${r.status==='incompleto'?'Descarga incompleta. Abre Detalles para revisar.':'Requiere revisión. Abre Detalles para ver qué ocurrió.'}</div>`:''}${r.tags?.length?`<div class="chips resource-tags">${r.tags.map(t=>`<span class="chip">#${esc(t.name)}</span>`).join('')}</div>`:''}</div><div class="resource-sub">${isManual?'Creado dentro de Biblioteca':esc(r.url)}</div><div><span class="status s-${r.status}">${statusLabel(r.status)}</span></div><div class="res-actions">${r.local_path?`<button class="icon-btn" title="Abrir en Biblioteca" onclick="openExplorer(${r.id})">Abrir</button>`:''}<button class="icon-btn" title="Editar clasificación" onclick="openResourceMetadataEditorFor(${r.id})">Editar</button><button class="icon-btn" title="Etiquetas" onclick="openResourceTagEditorFor(${r.id})">🏷</button>${!isManual&&r.status!=='descargando'?`<button class="${compact?'icon-btn':'secondary resource-download-btn'}" title="${dlTitle}" onclick="download(${r.id})">${compact?'↓':'↓ Descargar'}</button>`:''}${r.local_path?`<button class="icon-btn" title="Abrir carpeta" onclick="openFolder(${r.id})">⌂</button><button class="icon-btn" title="Comprimir ZIP" onclick="zipResource(${r.id})">ZIP</button>`:''}<button class="icon-btn" title="Ver detalles" onclick="detail(${r.id})">⋯</button></div></div>`
  }).join('')
}
async function download(id){try{await api(`/api/resources/${id}/download`,{method:'POST'});toast('Descarga iniciada');setTimeout(loadLibrary,500)}catch(e){toast(e.message)}}
async function openFolder(id){if(!isLocalHost())return openExplorer(id);try{await api(`/api/resources/${id}/open`,{method:'POST'})}catch(e){toast(e.message)}}
async function zipResource(id){try{const d=await api(`/api/resources/${id}/zip`,{method:'POST'});toast('ZIP creado en Exportaciones: '+d.path)}catch(e){toast(e.message)}}
async function rescan(id){try{const d=await api(`/api/resources/${id}/rescan`,{method:'POST'});toast(`Indexado: ${d.file_count} archivos`);detail(id)}catch(e){toast(e.message)}}
async function deleteResource(id){const delFiles=confirm('¿También deseas borrar los archivos descargados?\nAceptar = borrar registro y archivos.\nCancelar = solo borrar registro.');const sure=confirm(delFiles?'Se eliminará el registro y la carpeta local. ¿Continuar?':'Se eliminará solo el registro de la app. ¿Continuar?');if(!sure)return;try{await api(`/api/resources/${id}?delete_files=${delFiles?'true':'false'}`,{method:'DELETE'});closeModal();loadLibrary();toast('Recurso eliminado')}catch(e){toast(e.message)}}
async function detail(id){
  try{
    const r=await api(`/api/resources/${id}`);
    const types=(r.types||[]).map(t=>`<span class="type-pill"><b>${esc((t.ext||'').toUpperCase())}</b> · ${t.count} · ${fmtBytes(t.bytes)}</span>`).join('');
    const files=(r.files||[]).slice(0,100).map(f=>`<tr><td>${esc(f.rel_path)}</td><td>${esc((f.ext||'').toUpperCase())}</td><td>${fmtBytes(f.size_bytes)}</td></tr>`).join('');
    const technical=r.error?`<h3>${r.status==='error'?'Detalle del error':'Nota técnica de descarga'}</h3><div class="technical-detail ${r.status==='error'?'':'technical-note'}">${esc(r.error)}</div>`:'';
    document.getElementById('modalContent').innerHTML=`<h2>${esc(r.name)}</h2><p class="muted">${esc(r.category_name||'')} / ${esc(r.subcategory_name||'General')} · Fuente: ${sourceLabel(r)}</p><div class="detail-grid"><div class="detail-box"><b>Estado</b><br><span class="status s-${r.status}">${statusLabel(r.status)}</span></div><div class="detail-box"><b>Contenido local</b><br>${r.file_count} archivos · ${fmtBytes(r.total_bytes)}</div>${r.download_engine==='drive_api'?`<div class="detail-box"><b>Verificación Drive</b><br>${r.downloaded_file_count||0} de ${r.remote_file_count||0} archivos${r.skipped_file_count?` · ${r.skipped_file_count} pendientes/omitidos`:''}</div><div class="detail-box"><b>Motor</b><br>Google Drive API oficial</div>`:''}</div><h3>Tipos de archivo</h3><div class="type-grid">${types||'<span class="muted">Sin indexar</span>'}</div>${technical}<div class="actions left-actions">${r.local_path?`<button class="primary" onclick="closeModal();openExplorer(${r.id})">Explorar contenido</button>`:''}<button class="secondary" onclick="openResourceTagEditorFor(${r.id})">Etiquetas</button>${r.source_type==='manual'?'':`<button class="secondary" onclick="download(${r.id})">↓ Descargar / actualizar</button>`}<button class="secondary" onclick="openFolder(${r.id})">Abrir carpeta</button><button class="secondary" onclick="rescan(${r.id})">Reindexar</button><button class="secondary" onclick="zipResource(${r.id})">Crear ZIP</button><button class="danger" onclick="deleteResource(${r.id})">Eliminar</button></div><h3>Archivos <small class="muted">(máx. 100 visibles)</small></h3><table class="file-table"><thead><tr><th>Ruta</th><th>Tipo</th><th>Tamaño</th></tr></thead><tbody>${files||'<tr><td colspan="3">Sin archivos indexados.</td></tr>'}</tbody></table>`;
    document.getElementById('modal').classList.remove('hidden')
  }catch(e){toast(e.message)}
}
function closeModal(){document.getElementById('modal').classList.add('hidden')}

// EXPLORADOR
async function loadExplorerResources(preselect=null){try{const rs=(await api('/api/resources')).filter(r=>r.local_path);const sel=document.getElementById('explorerResource');const keep=preselect||sel.value;sel.innerHTML='<option value="">Selecciona un recurso...</option>'+rs.map(r=>`<option value="${r.id}">${esc(r.name)} · ${r.file_count} archivos</option>`).join('');if(keep)sel.value=String(keep);if(keep&&sel.value)await selectExplorerResource()}catch(e){toast(e.message)}}
async function openExplorer(id){go('library');document.getElementById('libraryHome')?.classList.add('hidden');document.getElementById('libraryExplorerView')?.classList.remove('hidden');setTimeout(()=>loadExplorerResources(id),60)}
async function selectExplorerResource(){const rid=Number(document.getElementById('explorerResource').value);EXPLORER.rid=rid||null;EXPLORER.path='';EXPLORER.selected.clear();await ensureTags();if(!rid){document.getElementById('explorerEmpty').classList.remove('hidden');document.getElementById('explorerWorkspace').classList.add('hidden');return}try{const d=await api(`/api/resources/${rid}/tree`);EXPLORER.resource=d.resource;document.getElementById('explorerEmpty').classList.add('hidden');document.getElementById('explorerWorkspace').classList.remove('hidden');renderFolderTree(d.tree);await browseExplorer('')}catch(e){toast(e.message)}}
function renderFolderTree(nodes){const render=(arr)=>arr.map(n=>{const ep=encodeURIComponent(n.path);return `<div class="tree-node"><button onclick="browseExplorer(decodeURIComponent('${ep}'))">📁 ${esc(n.name)}</button>${n.children?.length?`<div class="tree-children">${render(n.children)}</div>`:''}</div>`}).join('');document.getElementById('folderTree').innerHTML=render(nodes)}
async function browseExplorer(path=''){if(!EXPLORER.rid)return;try{const d=await api(`/api/resources/${EXPLORER.rid}/browse?path=${encodeURIComponent(path)}`);EXPLORER.path=d.current_path;EXPLORER.items=d.items;EXPLORER.selected.clear();renderExplorerBreadcrumb();renderExplorerItems();updateSelectionBar()}catch(e){toast(e.message)}}
function renderExplorerBreadcrumb(){const parts=EXPLORER.path?EXPLORER.path.split('/'):[];let html=`<button onclick="browseExplorer('')">${esc(EXPLORER.resource?.name||'Raíz')}</button>`;let acc=[];for(const p of parts){acc.push(p);const ep=encodeURIComponent(acc.join('/'));html+=`<span>›</span><button onclick="browseExplorer(decodeURIComponent('${ep}'))">${esc(p)}</button>`}document.getElementById('explorerBreadcrumb').innerHTML=html;const files=EXPLORER.items.filter(x=>x.kind==='file').length,folders=EXPLORER.items.filter(x=>x.kind==='folder').length;document.getElementById('explorerSummary').textContent=`${folders} carpetas · ${files} archivos`}
function setExplorerView(view){EXPLORER.view=view;renderExplorerItems()}
function renderExplorerItems(){
  const el=document.getElementById('explorerItems');
  el.className=EXPLORER.view==='list'?'asset-list':'asset-grid';
  if(!EXPLORER.items.length){el.innerHTML='<div class="empty">Esta carpeta está vacía.</div>';return}
  el.innerHTML=EXPLORER.items.map(item=>{
    const ep=encodeURIComponent(item.path);
    if(item.kind==='folder')return `<div class="asset-card folder-asset" ondblclick="browseExplorer(decodeURIComponent('${ep}'))"><div class="folder-icon">📁</div><div class="asset-name">${esc(item.name)}</div><div class="asset-meta">${item.child_count} elementos</div>${item.tags?.length?`<div class="chips folder-tags">${item.tags.map(t=>`<span class="chip">#${esc(t.name)}</span>`).join('')}</div>`:''}<div class="asset-mini-actions"><button class="mini-link" onclick="event.stopPropagation();browseExplorer(decodeURIComponent('${ep}'))">Abrir</button><button class="mini-link" onclick="event.stopPropagation();openFolderTagEditor(decodeURIComponent('${ep}'))">Etiquetar</button><button class="mini-link" onclick="event.stopPropagation();renameExplorerPath(decodeURIComponent('${ep}'),false)">Renombrar</button></div></div>`;
    const checked=EXPLORER.selected.has(item.path)?'checked':'';
    const preview=item.previewable?`<img loading="lazy" src="/api/resources/${EXPLORER.rid}/preview?path=${ep}" alt="${esc(item.name)}">`:`<div class="file-icon">${fileIcon(item.ext)}</div>`;
    return `<div class="asset-card file-asset" ondblclick="openExplorerFile(decodeURIComponent('${ep}'))"><label class="select-box" onclick="event.stopPropagation()"><input type="checkbox" ${checked} onchange="toggleExplorerSelection(decodeURIComponent('${ep}'),this.checked)"></label><div class="asset-preview">${preview}</div><div class="asset-name" title="${esc(item.name)}">${esc(item.name)}</div><div class="asset-meta">${esc((item.ext||'').toUpperCase())} · ${fmtBytes(item.size_bytes)}</div>${item.tags?.length?`<div class="chips file-tags">${item.tags.map(t=>`<span class="chip">#${esc(t.name)}</span>`).join('')}</div>`:''}<div class="asset-mini-actions"><button class="mini-link" onclick="event.stopPropagation();openExplorerFile(decodeURIComponent('${ep}'))">Abrir archivo</button><button class="mini-link" onclick="event.stopPropagation();exportSingleExplorerFile(decodeURIComponent('${ep}'))">Descargar</button><button class="mini-link" onclick="event.stopPropagation();openFileTagEditor(decodeURIComponent('${ep}'))">Etiquetar</button><button class="mini-link" onclick="event.stopPropagation();renameExplorerPath(decodeURIComponent('${ep}'),true)">Renombrar</button></div></div>`
  }).join('')
}
function fileIcon(ext){const m={psd:'PSD',ai:'AI',eps:'EPS',svg:'SVG',pdf:'PDF',zip:'ZIP',ttf:'FONT',otf:'FONT'};return m[String(ext).toLowerCase()]||'FILE'}
function toggleExplorerSelection(path,checked){if(checked)EXPLORER.selected.add(path);else EXPLORER.selected.delete(path);updateSelectionBar()}
function clearExplorerSelection(){EXPLORER.selected.clear();renderExplorerItems();updateSelectionBar()}
function updateSelectionBar(){const n=EXPLORER.selected.size;document.getElementById('selectionCount').textContent=`${n} seleccionado${n===1?'':'s'}`;document.getElementById('selectionBar').classList.toggle('hidden',!n)}
async function explorerNewFolder(){if(!EXPLORER.rid)return toast('Selecciona un recurso');const current=EXPLORER.path;const name=prompt('Nombre de la nueva carpeta:');if(!name)return;try{await api(`/api/resources/${EXPLORER.rid}/folders`,{method:'POST',body:JSON.stringify({parent_path:current,name})});toast('Carpeta creada');await selectExplorerResource();await browseExplorer(current)}catch(e){toast(e.message)}}
async function explorerOpenCurrent(){if(!EXPLORER.rid)return;if(!isLocalHost())return toast('Ya estás viendo esta carpeta aquí mismo.');try{await api(`/api/resources/${EXPLORER.rid}/folder/open`,{method:'POST',body:JSON.stringify({path:EXPLORER.path})})}catch(e){toast(e.message)}}
async function openExplorerFile(path){if(!isLocalHost()){const ext=(path.split('.').pop()||'').toLowerCase();const previewable=['jpg','jpeg','png','webp','gif','bmp','svg'].includes(ext);if(previewable)window.open(`/api/resources/${EXPLORER.rid}/preview?path=${encodeURIComponent(path)}`,'_blank');else toast('Este tipo de archivo solo se puede abrir directamente en la compu donde está instalado el programa.');return}try{await api(`/api/resources/${EXPLORER.rid}/file/open`,{method:'POST',body:JSON.stringify({path})})}catch(e){toast(e.message)}}
async function explorerRescan(){if(!EXPLORER.rid)return;const current=EXPLORER.path;try{const d=await api(`/api/resources/${EXPLORER.rid}/rescan`,{method:'POST'});toast(`Sincronizado: ${d.file_count} archivos`);await selectExplorerResource();await browseExplorer(current)}catch(e){toast(e.message)}}
function explorerImportFiles(){
  if(!EXPLORER.rid)return toast('Selecciona un recurso');
  const input=document.getElementById('localFilePicker');
  if(!input)return toast('No se encontró el selector de archivos');
  input.value='';
  input.click();
}
function explorerImportFolder(){
  if(!EXPLORER.rid)return toast('Selecciona un recurso');
  const input=document.getElementById('localFolderPicker');
  if(!input)return toast('No se encontró el selector de carpeta');
  input.value='';
  input.click();
}
async function handleLocalImport(input,isFolder=false){
  if(!EXPLORER.rid||!input?.files?.length)return;
  const files=[...input.files];
  const current=EXPLORER.path;
  const fd=new FormData();
  fd.append('target_path',current||'');
  const rels=files.map(f=>isFolder?(f.webkitRelativePath||f.name):f.name);
  fd.append('rel_paths_json',JSON.stringify(rels));
  files.forEach(f=>fd.append('files',f,f.name));
  toast(`Importando ${files.length} archivo${files.length===1?'':'s'}…`);
  try{
    const r=await fetch(`/api/resources/${EXPLORER.rid}/files/upload`,{method:'POST',body:fd});
    let d={};try{d=await r.json()}catch{}
    if(!r.ok)throw new Error(d.detail||'No se pudieron importar los archivos');
    toast(`${d.copied} archivos importados${d.skipped?` · ${d.skipped} omitidos`:''}`);
    await loadExplorerResources(EXPLORER.rid);
    await browseExplorer(current);
    await ensureTags();
  }catch(e){toast(e.message||'No se pudieron importar los archivos')}
  finally{input.value=''}
}
async function deleteExplorerSelected(){
  if(!EXPLORER.rid||!EXPLORER.selected.size)return;
  if(!confirm(`Se eliminarán ${EXPLORER.selected.size} archivos de la carpeta física. Esta acción no se puede deshacer. ¿Continuar?`))return;
  const current=EXPLORER.path;
  try{const d=await api(`/api/resources/${EXPLORER.rid}/items/delete`,{method:'POST',body:JSON.stringify({paths:[...EXPLORER.selected]})});toast(`${d.deleted} elementos eliminados`);EXPLORER.selected.clear();await selectExplorerResource();await browseExplorer(current)}catch(e){toast(e.message)}
}
async function openResourceMetadataEditor(){if(!EXPLORER.rid)return toast('Selecciona un recurso');return openResourceMetadataEditorFor(EXPLORER.rid)}
async function openResourceMetadataEditorFor(rid){
  try{await Promise.all([loadCategories(),ensureTags()]);const r=await api(`/api/resources/${rid}`);const catOpts=CATEGORIES.map(c=>`<option value="${c.id}" ${Number(r.category_id)===c.id?'selected':''}>${esc(c.name)}</option>`).join('');const cat=CATEGORIES.find(c=>c.id===Number(r.category_id));const subOpts='<option value="">General</option>'+((cat?.subcategories||[]).map(x=>`<option value="${x.id}" ${Number(r.subcategory_id)===x.id?'selected':''}>${esc(x.name)}</option>`).join(''));document.getElementById('modalContent').innerHTML=`<h2>Editar clasificación</h2><p class="muted">Fuente: <b>${sourceLabel(r)}</b></p><label>Nombre<input id="metaName" value="${esc(r.name)}"></label><label>Categoría<select id="metaCategory" onchange="refreshMetadataSubcategories(${rid})">${catOpts}</select></label><label>Subcategoría<select id="metaSubcategory">${subOpts}</select></label><h3>Etiquetas</h3>${tagChecklist((r.tags||[]).map(x=>x.id))}<div class="actions"><button class="secondary" onclick="closeModal()">Cancelar</button><button class="primary" onclick="saveResourceMetadata(${rid})">Guardar cambios</button></div>`;document.getElementById('modal').classList.remove('hidden')}catch(e){toast(e.message)}
}
function refreshMetadataSubcategories(){const catId=Number(document.getElementById('metaCategory')?.value||0);const cat=CATEGORIES.find(c=>c.id===catId);const s=document.getElementById('metaSubcategory');if(s)s.innerHTML='<option value="">General</option>'+((cat?.subcategories||[]).map(x=>`<option value="${x.id}">${esc(x.name)}</option>`).join(''))}
async function saveResourceMetadata(rid){const name=document.getElementById('metaName').value.trim(),category_id=Number(document.getElementById('metaCategory').value),subcategory_id=document.getElementById('metaSubcategory').value?Number(document.getElementById('metaSubcategory').value):null,tag_ids=[...document.querySelectorAll('#modalContent .tag-checklist input:checked')].map(x=>Number(x.value));try{await api(`/api/resources/${rid}/metadata`,{method:'PUT',body:JSON.stringify({name,category_id,subcategory_id,tag_ids})});closeModal();toast('Clasificación actualizada');if(EXPLORER.rid===rid)await loadExplorerResources(rid);loadLibrary()}catch(e){toast(e.message)}}

async function organizeSelected(operation){if(!EXPLORER.selected.size)return;const current=EXPLORER.path;const target=prompt(`Ruta de carpeta destino dentro del recurso.\nEjemplo: Seleccionados/Camisetas\nSe creará si no existe.`,current||'Seleccionados');if(target===null)return;try{const d=await api(`/api/resources/${EXPLORER.rid}/files/organize`,{method:'POST',body:JSON.stringify({paths:[...EXPLORER.selected],target_path:target,operation})});toast(`${d.processed} archivos ${operation==='copy'?'copiados':'movidos'}`);await selectExplorerResource();await browseExplorer(current)}catch(e){toast(e.message)}}
async function renameExplorerPath(path,isFile){const currentPath=EXPLORER.path;const current=path.split('/').pop();const name=prompt(isFile?'Nuevo nombre del archivo:':'Nuevo nombre de la carpeta:',current);if(!name||name===current)return;try{await api(`/api/resources/${EXPLORER.rid}/rename`,{method:'POST',body:JSON.stringify({path,new_name:name})});toast('Nombre actualizado');await selectExplorerResource();await browseExplorer(currentPath)}catch(e){toast(e.message)}}
async function zipSelected(){if(!EXPLORER.selected.size)return;const name=prompt('Nombre del archivo ZIP:','Seleccion_Sorprezz');if(!name)return;try{const d=await api(`/api/resources/${EXPLORER.rid}/files/zip`,{method:'POST',body:JSON.stringify({paths:[...EXPLORER.selected],name})});toast(`ZIP creado con ${d.files} archivos`)}catch(e){toast(e.message)}}

async function chooseDestinationFolder(){
  try{
    if(window.pywebview&&window.pywebview.api&&window.pywebview.api.choose_folder){return await window.pywebview.api.choose_folder()}
  }catch(e){console.warn(e)}
  toast('Esta función necesita la aplicación instalada de Windows.');
  return null
}
async function exportSelected(){
  if(!EXPLORER.rid||!EXPLORER.selected.size)return;
  const destination=await chooseDestinationFolder();if(!destination)return;
  const folderName=prompt('Opcional: nombre de una carpeta nueva dentro del destino.\nDeja vacío para copiar directamente.','Seleccion_Sorprezz');if(folderName===null)return;
  try{const d=await api(`/api/resources/${EXPLORER.rid}/files/export`,{method:'POST',body:JSON.stringify({paths:[...EXPLORER.selected],destination_dir:destination,folder_name:folderName})});toast(`${d.files} archivos copiados a ${d.destination}`)}catch(e){toast(e.message)}
}

async function exportSingleExplorerFile(path){
  if(!EXPLORER.rid)return;
  if(!isLocalHost())return window.open(`/api/resources/${EXPLORER.rid}/files/download?path=${encodeURIComponent(path)}`,'_blank');
  const destination=await chooseDestinationFolder();if(!destination)return;
  try{const d=await api(`/api/resources/${EXPLORER.rid}/files/export`,{method:'POST',body:JSON.stringify({paths:[path],destination_dir:destination,folder_name:''})});toast(`Archivo descargado en ${d.destination}`)}catch(e){toast(e.message)}
}
async function exportSingleGlobalAsset(rid,path){
  if(!isLocalHost())return window.open(`/api/resources/${rid}/files/download?path=${encodeURIComponent(path)}`,'_blank');
  const destination=await chooseDestinationFolder();if(!destination)return;try{const d=await api(`/api/resources/${rid}/files/export`,{method:'POST',body:JSON.stringify({paths:[path],destination_dir:destination,folder_name:''})});toast(`Archivo descargado en ${d.destination}`)}catch(e){toast(e.message)}}

async function openFileTagEditor(path){
  if(!EXPLORER.rid)return;await ensureTags();
  try{const current=await api(`/api/resources/${EXPLORER.rid}/file-tags?path=${encodeURIComponent(path)}`);document.getElementById('modalContent').innerHTML=`<h2>Etiquetas del archivo</h2><p class="muted">${esc(path)}</p>${tagChecklist(current.map(x=>x.id))}<div class="actions"><button class="secondary" onclick="closeModal()">Cancelar</button><button class="primary" onclick="saveFileTags(decodeURIComponent('${encodeURIComponent(path)}'))">Guardar etiquetas</button></div>`;document.getElementById('modal').classList.remove('hidden')}catch(e){toast(e.message)}
}
async function saveFileTags(path){const ids=[...document.querySelectorAll('#modalContent .tag-checklist input:checked')].map(x=>Number(x.value));try{await api(`/api/resources/${EXPLORER.rid}/file-tags`,{method:'PUT',body:JSON.stringify({path,tag_ids:ids})});closeModal();toast('Etiquetas del archivo guardadas');await browseExplorer(EXPLORER.path)}catch(e){toast(e.message)}}
async function openBulkFileTagEditor(){
  if(!EXPLORER.rid||!EXPLORER.selected.size)return;await ensureTags();
  document.getElementById('modalContent').innerHTML=`<h2>Etiquetar selección</h2><p class="muted">${EXPLORER.selected.size} archivos seleccionados. Marca las etiquetas y elige si deseas agregarlas o quitarlas.</p>${tagChecklist([])}<div class="actions left-actions"><button class="secondary" onclick="closeModal()">Cancelar</button><button class="secondary" onclick="saveBulkFileTags('remove')">Quitar marcadas</button><button class="primary" onclick="saveBulkFileTags('add')">Agregar marcadas</button></div>`;document.getElementById('modal').classList.remove('hidden')
}
async function saveBulkFileTags(mode){const ids=[...document.querySelectorAll('#modalContent .tag-checklist input:checked')].map(x=>Number(x.value));if(!ids.length)return toast('Marca al menos una etiqueta');try{const d=await api(`/api/resources/${EXPLORER.rid}/file-tags/bulk`,{method:'PUT',body:JSON.stringify({paths:[...EXPLORER.selected],tag_ids:ids,mode})});closeModal();toast(`${d.files} archivos actualizados`);await browseExplorer(EXPLORER.path)}catch(e){toast(e.message)}}

function collectionCategoryOptions(selectedId=0){return CATEGORIES.map(c=>`<option value="${c.id}" ${Number(selectedId)===Number(c.id)?'selected':''}>${esc(c.name)}</option>`).join('')}
function collectionSubcategoryOptions(categoryId,selectedId=0){const c=CATEGORIES.find(x=>Number(x.id)===Number(categoryId));return '<option value="">General / Sin subcategoría</option>'+((c?.subcategories||[]).map(s=>`<option value="${s.id}" ${Number(selectedId)===Number(s.id)?'selected':''}>${esc(s.name)}</option>`).join(''))}
function updateCollectionSubcategorySelect(){const catId=Number(document.getElementById('collectionCategory')?.value||0),sel=document.getElementById('collectionSubcategory');if(sel)sel.innerHTML=collectionSubcategoryOptions(catId,Number(sel.dataset.selected||0));if(sel)sel.dataset.selected='0'}
async function quickCollectionCategory(){const name=prompt('Nombre de la nueva categoría global:');if(!name||!name.trim())return;try{const d=await api('/api/categories',{method:'POST',body:JSON.stringify({name:name.trim()})});await loadCategories();const sel=document.getElementById('collectionCategory');if(sel){sel.innerHTML=collectionCategoryOptions(d.id);sel.value=String(d.id);updateCollectionSubcategorySelect()}toast('Categoría global creada y seleccionada')}catch(e){toast(e.message)}}
async function quickCollectionSubcategory(){const catId=Number(document.getElementById('collectionCategory')?.value||0);if(!catId)return toast('Selecciona primero una categoría');const name=prompt('Nombre de la nueva subcategoría:');if(!name||!name.trim())return;try{const d=await api('/api/subcategories',{method:'POST',body:JSON.stringify({category_id:catId,name:name.trim()})});await loadCategories();const cat=document.getElementById('collectionCategory');if(cat)cat.value=String(catId);const sub=document.getElementById('collectionSubcategory');if(sub){sub.innerHTML=collectionSubcategoryOptions(catId,d.id);sub.value=String(d.id)}toast('Subcategoría creada y seleccionada')}catch(e){toast(e.message)}}
function collectionModalFields(title='Nueva colección',defaultName='',defaultCategoryId=0,defaultSubcategoryId=0){const categoryId=Number(defaultCategoryId)||Number(CATEGORIES[0]?.id||0);return `<h2>${esc(title)}</h2><p class="muted">La colección usa la misma clasificación global de Biblioteca. Se creará una carpeta física dentro de <b>Colecciones</b> y los originales no se modifican.</p><div class="catalog-form"><label>Nombre de la colección<input id="collectionName" value="${esc(defaultName)}" placeholder="Ej. Nuevos diseños 8M"></label><label>Categoría global<div class="field-with-action"><select id="collectionCategory" onchange="updateCollectionSubcategorySelect()">${collectionCategoryOptions(categoryId)}</select><button type="button" class="secondary compact-btn" onclick="quickCollectionCategory()">+ Nueva</button></div></label><label>Subcategoría<div class="field-with-action"><select id="collectionSubcategory" data-selected="${Number(defaultSubcategoryId)||0}">${collectionSubcategoryOptions(categoryId,defaultSubcategoryId)}</select><button type="button" class="secondary compact-btn" onclick="quickCollectionSubcategory()">+ Nueva</button></div></label><label>Descripción<input id="collectionDescription" placeholder="Opcional"></label></div>`}
async function openCollectionCreateFromExplorer(){
  if(!EXPLORER.rid||!EXPLORER.selected.size)return toast('Selecciona una o varias imágenes');
  PENDING_COLLECTION_REFS=[...EXPLORER.selected].map(path=>({resource_id:EXPLORER.rid,path}));
  const defaultCategoryId=Number(EXPLORER.resource?.category_id||0),defaultSubcategoryId=Number(EXPLORER.resource?.subcategory_id||0);
  document.getElementById('modalContent').innerHTML=collectionModalFields('Crear colección desde la selección','',defaultCategoryId,defaultSubcategoryId)+`<div class="actions"><button class="secondary" onclick="closeModal()">Cancelar</button><button class="primary" onclick="createCollectionWithRefs()">Crear carpeta y copiar</button></div>`;document.getElementById('modal').classList.remove('hidden')
}
async function createCollectionWithRefs(){
  const refs=[...PENDING_COLLECTION_REFS];const name=document.getElementById('collectionName').value.trim(),category_id=Number(document.getElementById('collectionCategory').value||0),subcategory_id=Number(document.getElementById('collectionSubcategory').value||0)||null,description=document.getElementById('collectionDescription').value.trim();if(!name)return toast('Escribe el nombre de la colección');if(!category_id)return toast('Selecciona una categoría global');
  try{const c=await api('/api/collections',{method:'POST',body:JSON.stringify({name,category_id,subcategory_id,description})});const d=await api(`/api/collections/${c.id}/items`,{method:'POST',body:JSON.stringify({items:refs})});closeModal();toast(`Colección creada: ${d.added} archivos copiados`);clearExplorerSelection();if(document.querySelector('.page.active')?.id==='page-collections')await loadCollectionsPage()}catch(e){toast(e.message)}
}

// COLECCIONES WEB Y BANCO GLOBAL
async function openAddExplorerToExistingCollection(){
  if(!EXPLORER.rid||!EXPLORER.selected.size)return toast('Selecciona archivos');
  PENDING_COLLECTION_REFS=[...EXPLORER.selected].map(path=>({resource_id:EXPLORER.rid,path}));
  return openAddToExistingCollectionModal();
}
async function openAddGlobalToExistingCollection(){
  if(!GLOBAL_SELECTED.size)return toast('Selecciona imágenes');
  PENDING_COLLECTION_REFS=[...GLOBAL_SELECTED.values()];
  return openAddToExistingCollectionModal();
}
async function openAddToExistingCollectionModal(){
  try{COLLECTIONS=await api('/api/collections');if(!COLLECTIONS.length)return toast('Primero crea una colección');document.getElementById('modalContent').innerHTML=`<h2>Agregar a una colección existente</h2><p class="muted">Se copiarán ${PENDING_COLLECTION_REFS.length} archivos. Los originales permanecerán intactos.</p><label>Colección<select id="existingCollectionSelect">${COLLECTIONS.map(c=>`<option value="${c.id}">${esc(c.category)}${c.subcategory?' / '+esc(c.subcategory):''} / ${esc(c.name)} · ${c.item_count} archivos</option>`).join('')}</select></label><div class="actions"><button class="secondary" onclick="closeModal()">Cancelar</button><button class="primary" onclick="addPendingToExistingCollection()">Copiar a colección</button></div>`;document.getElementById('modal').classList.remove('hidden')}catch(e){toast(e.message)}
}
async function addPendingToExistingCollection(){const cid=Number(document.getElementById('existingCollectionSelect').value);if(!cid)return;try{const d=await api(`/api/collections/${cid}/items`,{method:'POST',body:JSON.stringify({items:PENDING_COLLECTION_REFS})});closeModal();toast(`${d.added} archivos agregados${d.skipped?` · ${d.skipped} omitidos`:''}`);PENDING_COLLECTION_REFS=[];clearExplorerSelection();clearGlobalSelection();if(document.querySelector('.page.active')?.id==='page-collections')loadCollections()}catch(e){toast(e.message)}}

async function loadCollectionsPage(){
  try{
    await Promise.all([ensureTags(),loadCategories()]);
    await loadCollections();
  }catch(e){toast(e.message)}
}
async function loadGlobalAssets(){
  const grid=document.getElementById('globalAssetsGrid');if(!grid)return;
  try{
    const params=new URLSearchParams();const q=(document.getElementById('globalAssetSearch')?.value||'').trim(),tag=document.getElementById('globalAssetTag')?.value||'',cat=document.getElementById('globalAssetCategory')?.value||'',res=document.getElementById('globalAssetResource')?.value||'';
    if(q)params.set('q',q);if(tag)params.set('tag_id',tag);if(cat)params.set('category_id',cat);if(res)params.set('resource_id',res);params.set('images_only','true');params.set('limit','800');
    GLOBAL_ASSETS=await api('/api/assets?'+params.toString());
    // Conserva solo selecciones que siguen existiendo en los resultados conocidos.
    renderGlobalAssets();updateGlobalSelectionBar();
  }catch(e){grid.innerHTML=`<div class="empty">No se pudo cargar el banco de imágenes.<br>${esc(e.message)}</div>`;toast(e.message)}
}
function globalKey(rid,path){return `${rid}::${path}`}
function renderGlobalAssets(){
  const grid=document.getElementById('globalAssetsGrid');if(!grid)return;
  if(!GLOBAL_ASSETS.length){const tagId=document.getElementById('globalAssetTag')?.value||'',tagObj=TAGS.find(t=>String(t.id)===String(tagId));grid.innerHTML=`<div class="empty">${tagObj?`No hay imágenes visibles con <b>#${esc(tagObj.name)}</b>.<br><span class="muted">Si la etiqueta está aplicada solo al recurso o a una carpeta, edítala y activa “Aplicar también a las imágenes”.</span>`:'No hay imágenes que coincidan con los filtros.'}</div>`;return}
  grid.innerHTML=GLOBAL_ASSETS.map(a=>{const key=globalKey(a.resource_id,a.rel_path),checked=GLOBAL_SELECTED.has(key)?'checked':'',ep=encodeURIComponent(a.rel_path);return `<div class="asset-card file-asset global-asset"><label class="select-box"><input type="checkbox" ${checked} onchange="toggleGlobalSelection(${a.resource_id},decodeURIComponent('${ep}'),this.checked)"></label><div class="asset-preview"><img loading="lazy" src="/api/resources/${a.resource_id}/preview?path=${ep}" alt="${esc(a.name)}"></div><div class="asset-name">${esc(a.name)}</div><div class="asset-meta">${esc(a.resource_name)} · ${esc(a.category_name||'Sin categoría')}</div>${a.tags?.length?`<div class="chips file-tags">${a.tags.map(t=>`<span class="chip">#${esc(t.name)}</span>`).join('')}</div>`:''}<div class="asset-mini-actions"><button class="mini-link" onclick="openGlobalAsset(${a.resource_id},decodeURIComponent('${ep}'))">Abrir origen</button><button class="mini-link" onclick="exportSingleGlobalAsset(${a.resource_id},decodeURIComponent('${ep}'))">Descargar</button></div></div>`}).join('')
}
function toggleGlobalSelection(rid,path,checked){const key=globalKey(rid,path);if(checked)GLOBAL_SELECTED.set(key,{resource_id:rid,path});else GLOBAL_SELECTED.delete(key);updateGlobalSelectionBar()}
function clearGlobalSelection(){GLOBAL_SELECTED.clear();renderGlobalAssets();updateGlobalSelectionBar()}
function updateGlobalSelectionBar(){const n=GLOBAL_SELECTED.size;const bar=document.getElementById('globalSelectionBar');if(!bar)return;document.getElementById('globalSelectionCount').textContent=`${n} seleccionado${n===1?'':'s'}`;bar.classList.toggle('hidden',!n)}
function clearGlobalAssetFilters(){['globalAssetSearch','globalAssetTag','globalAssetCategory','globalAssetResource'].forEach(id=>{const e=document.getElementById(id);if(e)e.value=''});loadGlobalAssets()}
async function openGlobalAsset(rid,path){openExplorer(rid);setTimeout(async()=>{await browseExplorer(path.split('/').slice(0,-1).join('/'))},160)}
async function openCollectionCreateFromGlobal(){if(!GLOBAL_SELECTED.size)return toast('Selecciona imágenes de cualquier recurso');await loadCategories();PENDING_COLLECTION_REFS=[...GLOBAL_SELECTED.values()];document.getElementById('modalContent').innerHTML=collectionModalFields('Crear colección')+`<div class="actions"><button class="secondary" onclick="closeModal()">Cancelar</button><button class="primary" onclick="createGlobalCollection()">Crear carpeta y copiar</button></div>`;document.getElementById('modal').classList.remove('hidden')}
async function createGlobalCollection(){const refs=[...PENDING_COLLECTION_REFS];const name=document.getElementById('collectionName').value.trim(),category_id=Number(document.getElementById('collectionCategory').value||0),subcategory_id=Number(document.getElementById('collectionSubcategory').value||0)||null,description=document.getElementById('collectionDescription').value.trim();if(!name)return toast('Escribe el nombre de la colección');if(!category_id)return toast('Selecciona una categoría global');try{const c=await api('/api/collections',{method:'POST',body:JSON.stringify({name,category_id,subcategory_id,description})});const d=await api(`/api/collections/${c.id}/items`,{method:'POST',body:JSON.stringify({items:refs})});closeModal();GLOBAL_SELECTED.clear();PENDING_COLLECTION_REFS=[];toast(`${d.added} imágenes copiadas a ${c.category}${c.subcategory?' / '+c.subcategory:''} / ${name}`);await loadCollectionsPage()}catch(e){toast(e.message)}}
async function exportGlobalSelected(){if(!GLOBAL_SELECTED.size)return;const destination=await chooseDestinationFolder();if(!destination)return;const folderName=prompt('Nombre de la carpeta que recibirá esta selección:','Seleccion');if(folderName===null)return;const groups={};for(const ref of GLOBAL_SELECTED.values()){(groups[ref.resource_id]??=[]).push(ref.path)}let total=0,last='';try{for(const [rid,paths] of Object.entries(groups)){const d=await api(`/api/resources/${rid}/files/export`,{method:'POST',body:JSON.stringify({paths,destination_dir:destination,folder_name:folderName})});total+=d.files;last=d.destination}toast(`${total} archivos copiados a ${last}`)}catch(e){toast(e.message)}}

async function loadCollections(){
  try{
    const q=(document.getElementById('collectionSearch')?.value||'').trim();
    COLLECTIONS=await api('/api/collections'+(q?`?q=${encodeURIComponent(q)}`:''));
    const el=document.getElementById('collectionsGrid');if(!el)return;
    el.innerHTML=COLLECTIONS.length
      ?COLLECTIONS.map(c=>`<div class="collection-card" onclick="collectionDetail(${c.id})"><div class="collection-folder">📁</div><div><h3>${esc(c.name)}</h3><p>${esc(c.category)}${c.subcategory?' / '+esc(c.subcategory):''}</p><span>${c.item_count} archivos · ${fmtBytes(c.total_bytes)}</span></div><div class="collection-actions"><button class="mini-link" onclick="event.stopPropagation();openCollectionFolder(${c.id})">Abrir carpeta</button><button class="mini-link" onclick="event.stopPropagation();zipCollection(${c.id})">Descargar ZIP</button></div></div>`).join('')
      :'<div class="empty">No hay colecciones que coincidan con la búsqueda.</div>';
  }catch(e){toast(e.message)}
}
function clearCollectionSearch(){const i=document.getElementById('collectionSearch');if(i)i.value='';loadCollections()}
async function openCollectionCreateEmpty(){await loadCategories();document.getElementById('modalContent').innerHTML=collectionModalFields('Nueva colección vacía')+`<div class="actions"><button class="secondary" onclick="closeModal()">Cancelar</button><button class="primary" onclick="createEmptyCollection()">Crear carpeta</button></div>`;document.getElementById('modal').classList.remove('hidden')}
async function createEmptyCollection(){const name=document.getElementById('collectionName').value.trim(),category_id=Number(document.getElementById('collectionCategory').value||0),subcategory_id=Number(document.getElementById('collectionSubcategory').value||0)||null,description=document.getElementById('collectionDescription').value.trim();if(!name)return toast('Escribe un nombre');if(!category_id)return toast('Selecciona una categoría global');try{await api('/api/collections',{method:'POST',body:JSON.stringify({name,category_id,subcategory_id,description})});closeModal();toast('Colección creada');loadCollections()}catch(e){toast(e.message)}}
async function openCollectionFromTag(){await Promise.all([ensureTags(),loadCategories()]);if(!TAGS.length)return toast('Primero crea una etiqueta');document.getElementById('modalContent').innerHTML=`<h2>Crear colección desde una etiqueta</h2><p class="muted">Se copiarán todos los archivos con la etiqueta elegida. La colección usará las categorías globales de Biblioteca.</p><div class="catalog-form"><label>Etiqueta<select id="collectionSourceTag">${TAGS.map(t=>`<option value="${t.id}">${esc(t.name)} · ${t.files||0} archivos</option>`).join('')}</select></label><label>Nombre de la colección<input id="collectionName" placeholder="Ej. Selección Día de la Madre"></label><label>Categoría global<div class="field-with-action"><select id="collectionCategory" onchange="updateCollectionSubcategorySelect()">${collectionCategoryOptions(CATEGORIES[0]?.id||0)}</select><button type="button" class="secondary compact-btn" onclick="quickCollectionCategory()">+ Nueva</button></div></label><label>Subcategoría<div class="field-with-action"><select id="collectionSubcategory">${collectionSubcategoryOptions(CATEGORIES[0]?.id||0)}</select><button type="button" class="secondary compact-btn" onclick="quickCollectionSubcategory()">+ Nueva</button></div></label><label class="wide">Descripción<input id="collectionDescription" placeholder="Opcional"></label></div><div class="actions"><button class="secondary" onclick="closeModal()">Cancelar</button><button class="primary" onclick="createCollectionFromTag()">Crear carpeta desde etiqueta</button></div>`;document.getElementById('modal').classList.remove('hidden')}
async function createCollectionFromTag(){const tag_id=Number(document.getElementById('collectionSourceTag').value),name=document.getElementById('collectionName').value.trim(),category_id=Number(document.getElementById('collectionCategory').value||0),subcategory_id=Number(document.getElementById('collectionSubcategory').value||0)||null,description=document.getElementById('collectionDescription').value.trim();if(!name)return toast('Escribe un nombre');if(!category_id)return toast('Selecciona una categoría global');try{const d=await api('/api/collections/from-tag',{method:'POST',body:JSON.stringify({tag_id,name,category_id,subcategory_id,description})});closeModal();toast(`Colección creada con ${d.added} archivos`);await loadCollectionsPage()}catch(e){toast(e.message)}}
async function collectionDetail(id){try{const c=await api(`/api/collections/${id}`);const items=c.items.map(i=>`<div class="asset-card file-asset"><div class="asset-preview">${i.previewable?`<img src="/api/collections/${id}/items/${i.id}/preview" alt="${esc(i.name)}">`:`<div class="file-icon">${fileIcon(i.ext)}</div>`}</div><div class="asset-name">${esc(i.name)}</div><div class="asset-meta">Origen: ${esc(i.resource_name||'')} · ${fmtBytes(i.size_bytes)}</div><div class="asset-mini-actions"><button class="mini-link danger-text" onclick="removeCollectionItem(${id},${i.id})">Quitar copia</button></div></div>`).join('');document.getElementById('modalContent').innerHTML=`<div class="collection-detail-head"><div><h2>${esc(c.name)}</h2><p class="muted">${esc(c.category)}${c.subcategory?' / '+esc(c.subcategory):''} · ${c.items.length} archivos</p><p class="muted">${esc(c.description||'')}</p></div><div class="quick-actions"><button class="secondary" onclick="openCollectionFolder(${id})">Abrir carpeta</button><button class="secondary" onclick="zipCollection(${id})">↓ Descargar ZIP</button><button class="danger" onclick="deleteCollection(${id})">Eliminar colección</button></div></div><div class="asset-grid collection-items-grid">${items||'<div class="empty">Colección vacía.</div>'}</div>`;document.getElementById('modal').classList.remove('hidden')}catch(e){toast(e.message)}}
async function openCollectionFolder(id){if(!isLocalHost())return collectionDetail(id);try{await api(`/api/collections/${id}/open`,{method:'POST'})}catch(e){toast(e.message)}}
async function zipCollection(id){try{const d=await api(`/api/collections/${id}/zip`,{method:'POST'});toast('ZIP creado en Exportaciones: '+d.path)}catch(e){toast(e.message)}}
async function removeCollectionItem(cid,itemId){if(!confirm('¿Quitar esta copia de la colección? El original no se borrará.'))return;try{await api(`/api/collections/${cid}/items/${itemId}`,{method:'DELETE'});toast('Copia retirada');collectionDetail(cid);loadCollections()}catch(e){toast(e.message)}}
async function deleteCollection(id){if(!confirm('¿Eliminar esta colección y su carpeta física? Los archivos originales de Biblioteca no se borrarán.'))return;try{await api(`/api/collections/${id}`,{method:'DELETE'});closeModal();toast('Colección eliminada');loadCollections()}catch(e){toast(e.message)}}


// ETIQUETAS Y ORGANIZACIÓN
async function ensureTags(){
  try{
    TAGS=await api('/api/tags');
    const sel=document.getElementById('filterTag');
    if(sel){const keep=sel.value;sel.innerHTML='<option value="">Todas las etiquetas</option>'+TAGS.map(t=>`<option value="${t.id}">${esc(t.name)}</option>`).join('');sel.value=keep}
  }catch(e){console.warn(e)}
}
function setOrganizationTab(tab){
  const current=tab==='tags'?'tags':'categories';
  document.getElementById('orgCategories')?.classList.toggle('hidden',current!=='categories');
  document.getElementById('orgTags')?.classList.toggle('hidden',current!=='tags');
  document.getElementById('orgCategoriesBtn')?.classList.toggle('active',current==='categories');
  document.getElementById('orgTagsBtn')?.classList.toggle('active',current==='tags');
  if(current==='categories')loadCategories();else ensureTags().then(renderTagsManager);
}
async function loadUtilities(){await ensureTags();renderTagsManager()}
function renderTagsManager(){
  const el=document.getElementById('tagsManager');if(!el)return;
  if(!TAGS.length){el.innerHTML='<div class="empty small">Todavía no hay etiquetas.</div>';return}
  el.innerHTML=TAGS.map(t=>{
    const samples=(t.samples||[]).map(a=>{const ep=encodeURIComponent(a.rel_path);return `<button class="tag-preview" title="${esc(a.resource_name)} · ${esc(a.rel_path)}" onclick="showTagImages(${t.id})"><img loading="lazy" src="/api/resources/${a.resource_id}/preview?path=${ep}" alt=""></button>`}).join('');
    return `<div class="tag-card"><div class="tag-card-head"><span class="chip strong">#${esc(t.name)}</span><span class="muted">${t.resources||0} recursos · ${t.folders||0} carpetas · <b>${t.files||0} imágenes/archivos</b></span></div><div class="tag-card-body">${samples?`<div class="tag-previews">${samples}</div>`:`<div class="tag-no-preview">Sin imágenes etiquetadas visibles</div>`}<div class="tag-row-actions"><button class="secondary compact-btn" onclick="showTagImages(${t.id})">Ver imágenes</button><button class="mini-link danger-text" onclick="deleteTag(${t.id})">Eliminar</button></div></div></div>`
  }).join('');
}
async function showTagImages(tagId){go('library');setLibraryMode('images');await loadLibraryAssets();const sel=document.getElementById('globalAssetTag');if(sel){sel.value=String(tagId);await loadGlobalAssets()}}
async function createTag(){const i=document.getElementById('newTagName');const name=i.value.trim();if(!name)return;try{await api('/api/tags',{method:'POST',body:JSON.stringify({name})});i.value='';await loadUtilities();await loadDownloadsSetup();toast('Etiqueta creada')}catch(e){toast(e.message)}}
async function deleteTag(id){if(!confirm('¿Eliminar esta etiqueta? Se quitará de los recursos y carpetas donde esté asignada.'))return;try{await api(`/api/tags/${id}`,{method:'DELETE'});await loadUtilities();toast('Etiqueta eliminada')}catch(e){toast(e.message)}}
function tagChecklist(selected=[]){const set=new Set(selected.map(Number));return TAGS.length?`<div class="tag-checklist">${TAGS.map(t=>`<label><input type="checkbox" value="${t.id}" ${set.has(t.id)?'checked':''}> <span>#${esc(t.name)}</span></label>`).join('')}</div>`:'<p class="muted">Primero crea etiquetas en “Organización”.</p>'}
async function openResourceTagEditor(){if(!EXPLORER.rid)return toast('Selecciona un recurso');return openResourceTagEditorFor(EXPLORER.rid)}
async function openResourceTagEditorFor(rid){await ensureTags();try{const current=await api(`/api/resources/${rid}/tags`);document.getElementById('modalContent').innerHTML=`<h2>Etiquetas del recurso</h2><p class="muted">La etiqueta del recurso sirve para clasificar la carpeta completa. Si también quieres encontrar sus imágenes por esa etiqueta, activa la opción inferior.</p>${tagChecklist(current.map(x=>x.id))}<label class="apply-tags-option"><input id="applyResourceTagsToImages" type="checkbox"> <span><b>Aplicar también a todas las imágenes del recurso</b><small>Las miniaturas aparecerán al buscar esta etiqueta en Biblioteca.</small></span></label><div class="actions"><button class="secondary" onclick="closeModal()">Cancelar</button><button class="primary" onclick="saveResourceTagsFor(${rid})">Guardar etiquetas</button></div>`;document.getElementById('modal').classList.remove('hidden')}catch(e){toast(e.message)}}
async function saveResourceTags(){if(!EXPLORER.rid)return;return saveResourceTagsFor(EXPLORER.rid)}
async function saveResourceTagsFor(rid){const ids=[...document.querySelectorAll('#modalContent .tag-checklist input:checked')].map(x=>Number(x.value)),apply=!!document.getElementById('applyResourceTagsToImages')?.checked;try{await api(`/api/resources/${rid}/tags`,{method:'PUT',body:JSON.stringify({tag_ids:ids})});let applied=0;if(apply&&ids.length){const d=await api(`/api/resources/${rid}/tags/apply-to-files`,{method:'POST',body:JSON.stringify({path:'',tag_ids:ids,mode:'add',images_only:true})});applied=d.files||0}closeModal();await ensureTags(true);toast(apply?`Etiquetas guardadas · ${applied} imágenes etiquetadas`:'Etiquetas guardadas');if(EXPLORER.rid===rid)await loadExplorerResources(rid);const active=document.querySelector('.page.active')?.id;if(active==='page-library'){if(LIBRARY_MODE==='images')await loadLibraryAssets();else loadLibrary()}if(active==='page-dashboard')loadDashboard()}catch(e){toast(e.message)}}
async function openCurrentFolderTagEditor(){if(!EXPLORER.rid)return toast('Selecciona un recurso');return openFolderTagEditor(EXPLORER.path)}
async function openFolderTagEditor(path=''){if(!EXPLORER.rid)return toast('Selecciona un recurso');await ensureTags();try{const current=await api(`/api/resources/${EXPLORER.rid}/folder-tags?path=${encodeURIComponent(path)}`);const label=path||'Raíz del recurso';document.getElementById('modalContent').innerHTML=`<h2>Etiquetas de carpeta</h2><p class="muted">Carpeta: <b>${esc(label)}</b>. Puedes etiquetar solo la carpeta o también todas las imágenes que contiene.</p>${tagChecklist(current.map(x=>x.id))}<label class="apply-tags-option"><input id="applyFolderTagsToImages" type="checkbox"> <span><b>Aplicar también a las imágenes de esta carpeta y subcarpetas</b><small>Así aparecerán al filtrar por esta etiqueta en Biblioteca.</small></span></label><div class="actions"><button class="secondary" onclick="closeModal()">Cancelar</button><button class="primary" onclick="saveFolderTagsFor(decodeURIComponent('${encodeURIComponent(path)}'))">Guardar etiquetas</button></div>`;document.getElementById('modal').classList.remove('hidden')}catch(e){toast(e.message)}}
async function saveCurrentFolderTags(){return saveFolderTagsFor(EXPLORER.path)}
async function saveFolderTagsFor(path=''){const ids=[...document.querySelectorAll('#modalContent .tag-checklist input:checked')].map(x=>Number(x.value)),apply=!!document.getElementById('applyFolderTagsToImages')?.checked;try{await api(`/api/resources/${EXPLORER.rid}/folder-tags`,{method:'PUT',body:JSON.stringify({path,tag_ids:ids})});let applied=0;if(apply&&ids.length){const d=await api(`/api/resources/${EXPLORER.rid}/tags/apply-to-files`,{method:'POST',body:JSON.stringify({path,tag_ids:ids,mode:'add',images_only:true})});applied=d.files||0}closeModal();await ensureTags(true);toast(apply?`Etiquetas guardadas · ${applied} imágenes etiquetadas`:'Etiquetas de carpeta guardadas');await browseExplorer(EXPLORER.path)}catch(e){toast(e.message)}}
function clearLibraryFilters(){document.getElementById('searchQ').value='';document.getElementById('filterCategory').value='';document.getElementById('filterStatus').value='';if(document.getElementById('filterTag'))document.getElementById('filterTag').value='';loadLibrary()}
async function syncAllResources(){
  try{
    const d=await api('/api/resources/rescan-all',{method:'POST'});
    const tagMsg=d.tags_preserved===false?' · ⚠ revisa las etiquetas':' · etiquetas conservadas';
    toast(`Biblioteca sincronizada: ${d.resources} recursos · ${d.files} archivos${tagMsg}`);
    const active=document.querySelector('.page.active')?.id;
    if(active==='page-dashboard')loadDashboard();
    if(active==='page-library'){
      if(!document.getElementById('libraryExplorerView')?.classList.contains('hidden') && EXPLORER.rid){
        loadExplorerResources(EXPLORER.rid);
      }else if(LIBRARY_MODE==='images')loadLibraryAssets();
      else loadLibrary();
    }
  }catch(e){toast(e.message)}
}
async function loadSettings(){const [c]=await Promise.all([api('/api/config'),loadDriveStatus(true)]);document.getElementById('libraryPath').value=c.library_path;document.getElementById('configMeta').innerHTML=`Versión ${c.version}<br>Base de datos: ${esc(c.db_path)}`}
async function chooseLibraryFolder(){try{if(window.pywebview&&window.pywebview.api&&window.pywebview.api.choose_folder){const p=await window.pywebview.api.choose_folder();if(p)document.getElementById('libraryPath').value=p;return}toast('El selector de carpetas está disponible en la aplicación instalada de Windows.')}catch(e){toast(e.message||'No se pudo abrir el selector de carpetas')}}
async function saveSettings(){const p=document.getElementById('libraryPath').value.trim();if(!p)return;try{const d=await api('/api/config',{method:'PUT',body:JSON.stringify({library_path:p})});toast('Ubicación guardada: '+d.library_path);loadSettings()}catch(e){toast(e.message)}}
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}

(async function init(){
  await loadCategories();
  await ensureTags();
  await loadDownloadsSetup();
  await loadDashboard();
  setInterval(()=>{
    const active=document.querySelector('.page.active')?.id;
    if(active==='page-library' && !document.getElementById('libraryHome')?.classList.contains('hidden') && LIBRARY_MODE==='resources')loadLibrary();
    if(active==='page-dashboard')loadDashboard();
  },3000);
})().catch(e=>toast(e.message));
