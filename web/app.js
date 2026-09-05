let CATEGORIES=[];
let TAGS=[];
let FOLDER_TEMPLATES=[];
let EXPLORER={rid:null,path:'',items:[],selected:new Set(),view:'grid',resource:null};
let GLOBAL_ASSETS=[];
let GLOBAL_SELECTED=new Map();
let COLLECTIONS=[];
let PENDING_COLLECTION_REFS=[];
let DOWNLOAD_TAG_IDS=new Set();
let DOWNLOAD_MODE='single';
let LIBRARY_MODE='resources';
const pages={
  dashboard:['Inicio','Organiza tus imágenes y recursos desde un solo lugar.'],
  downloads:['Descargas','Pega enlaces, clasifícalos y descárgalos.'],
  library:['Biblioteca','Busca, visualiza y administra todos los recursos e imágenes.'],
  collections:['Colecciones','Agrupa imágenes de distintos recursos sin modificar los originales.'],
  organization:['Organización','Administra categorías, etiquetas y plantillas de carpetas.'],
  settings:['Configuración','Almacenamiento y utilidades de la aplicación.']
};

async function api(path,opts={}){const r=await fetch(path,{headers:{'Content-Type':'application/json'},...opts});let data={};try{data=await r.json()}catch{}if(!r.ok)throw new Error(data.detail||'Error en la operación');return data}
function toast(msg){const t=document.getElementById('toast');t.textContent=msg;t.classList.remove('hidden');setTimeout(()=>t.classList.add('hidden'),3500)}
function fmtBytes(n){n=Number(n||0);if(!n)return '0 B';const u=['B','KB','MB','GB','TB'];let i=Math.min(u.length-1,Math.floor(Math.log(n)/Math.log(1024)));return (n/Math.pow(1024,i)).toFixed(i?1:0)+' '+u[i]}
function statusLabel(s){return {pendiente:'Pendiente',descargando:'Descargando',completado:'Completado',parcial:'Completado',error:'Con error'}[s]||s}
function catalogStatusLabel(s){return {seleccionado:'Seleccionado',preparacion:'En preparación',listo:'Listo para catálogo',publicado:'Publicado'}[s]||s}
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
  if(p==='organization'){loadCategories();loadUtilities();}
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
  const name=prompt('Nombre de la nueva colección:');if(!name||!name.trim())return;
  const catId=Number(document.getElementById('rCategory')?.value||0);
  const cat=CATEGORIES.find(c=>c.id===catId);
  const category=prompt('Categoría de la colección:',cat?.name||'General');
  if(category===null)return;
  try{
    const d=await api('/api/collections',{method:'POST',body:JSON.stringify({name:name.trim(),category:(category||'General').trim(),description:''})});
    await loadDownloadCollections();
    document.getElementById('downloadCollection').value=String(d.id);
    toast('Colección creada');
  }catch(e){toast(e.message)}
}
async function loadDownloadCollections(){
  try{
    const cols=await api('/api/collections');
    const sel=document.getElementById('downloadCollection');if(!sel)return;
    const keep=sel.value;
    sel.innerHTML='<option value="">No agregar a colección</option>'+cols.map(c=>`<option value="${c.id}">${esc(c.category)} · ${esc(c.name)}</option>`).join('');
    if(keep)sel.value=keep;
  }catch(e){console.warn(e)}
}
async function loadDownloadsSetup(){
  await Promise.all([loadCategories(),ensureTags(),loadDownloadCollections()]);
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
    avoid_duplicates:Boolean(document.getElementById('avoidDuplicates')?.checked)
  }
}
async function analyzeLink(){
  const p=resourcePayload();
  if(!p.url)return toast('Pega un enlace');
  if(!p.name)p.name='Sin nombre';
  try{
    const d=await api('/api/analyze',{method:'POST',body:JSON.stringify(p)});
    const b=document.getElementById('analyzeBox');b.classList.remove('hidden');
    b.innerHTML=d.existing
      ?`⚠ Este enlace ya está registrado como <strong>${esc(d.existing.name)}</strong> (${statusLabel(d.existing.status)}).`
      :`✓ Enlace válido. Tipo detectado: <strong>${d.info.kind==='folder'?'Carpeta':'Archivo/enlace'}</strong>${d.info.id?` · ID: ${esc(d.info.id)}`:''}.`;
  }catch(e){toast(e.message)}
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
function renderResources(rs,compact=false,filtered=false){
  if(!rs.length)return `<div class="empty">${filtered?'No hay recursos que coincidan con los filtros seleccionados. Usa “Limpiar filtros” para ver toda la biblioteca.':'Todavía no hay recursos registrados.'}</div>`;
  return rs.map(r=>{
    const dlTitle=r.status==='completado'?'Actualizar / volver a descargar':(r.status==='parcial'?'Actualizar descarga':'Descargar');
    return `<div class="resource"><div><div class="resource-title">${esc(r.name)}</div><div class="resource-sub">${esc(r.category_name||'Sin categoría')} · ${esc(r.subcategory_name||'General')} · ${r.file_count||0} archivos · ${fmtBytes(r.total_bytes)}</div>${r.live?`<div class="progress"><i style="width:${r.live.progress||0}%"></i></div><div class="resource-sub">${esc(r.live.message||'')}</div>`:''}${r.error&&!r.live&&r.status==='error'?`<div class="resource-sub error-summary">Requiere revisión. Abre Detalles para ver qué ocurrió.</div>`:''}${r.tags?.length?`<div class="chips resource-tags">${r.tags.map(t=>`<span class="chip">#${esc(t.name)}</span>`).join('')}</div>`:''}</div><div class="resource-sub">${esc(r.url)}</div><div><span class="status s-${r.status}">${statusLabel(r.status)}</span></div><div class="res-actions">${r.local_path?`<button class="icon-btn" title="Abrir en Biblioteca" onclick="openExplorer(${r.id})">Abrir</button>`:''}<button class="icon-btn" title="Etiquetas" onclick="openResourceTagEditorFor(${r.id})">🏷</button>${r.status!=='descargando'?`<button class="${compact?'icon-btn':'secondary resource-download-btn'}" title="${dlTitle}" onclick="download(${r.id})">${compact?'↓':'↓ Descargar'}</button>`:''}${r.local_path?`<button class="icon-btn" title="Abrir carpeta" onclick="openFolder(${r.id})">⌂</button><button class="icon-btn" title="Comprimir ZIP" onclick="zipResource(${r.id})">ZIP</button>`:''}<button class="icon-btn" title="Ver detalles" onclick="detail(${r.id})">⋯</button></div></div>`
  }).join('')
}
async function download(id){try{await api(`/api/resources/${id}/download`,{method:'POST'});toast('Descarga iniciada');setTimeout(loadLibrary,500)}catch(e){toast(e.message)}}
async function openFolder(id){try{await api(`/api/resources/${id}/open`,{method:'POST'})}catch(e){toast(e.message)}}
async function zipResource(id){try{const d=await api(`/api/resources/${id}/zip`,{method:'POST'});toast('ZIP creado en Exportaciones: '+d.path)}catch(e){toast(e.message)}}
async function rescan(id){try{const d=await api(`/api/resources/${id}/rescan`,{method:'POST'});toast(`Indexado: ${d.file_count} archivos`);detail(id)}catch(e){toast(e.message)}}
async function deleteResource(id){const delFiles=confirm('¿También deseas borrar los archivos descargados?\nAceptar = borrar registro y archivos.\nCancelar = solo borrar registro.');const sure=confirm(delFiles?'Se eliminará el registro y la carpeta local. ¿Continuar?':'Se eliminará solo el registro de la app. ¿Continuar?');if(!sure)return;try{await api(`/api/resources/${id}?delete_files=${delFiles?'true':'false'}`,{method:'DELETE'});closeModal();loadLibrary();toast('Recurso eliminado')}catch(e){toast(e.message)}}
async function detail(id){
  try{
    const r=await api(`/api/resources/${id}`);
    const types=(r.types||[]).map(t=>`<span class="type-pill"><b>${esc((t.ext||'').toUpperCase())}</b> · ${t.count} · ${fmtBytes(t.bytes)}</span>`).join('');
    const files=(r.files||[]).slice(0,100).map(f=>`<tr><td>${esc(f.rel_path)}</td><td>${esc((f.ext||'').toUpperCase())}</td><td>${fmtBytes(f.size_bytes)}</td></tr>`).join('');
    const technical=r.error?`<h3>${r.status==='error'?'Detalle del error':'Nota técnica de descarga'}</h3><div class="technical-detail ${r.status==='error'?'':'technical-note'}">${esc(r.error)}</div>`:'';
    document.getElementById('modalContent').innerHTML=`<h2>${esc(r.name)}</h2><p class="muted">${esc(r.category_name||'')} / ${esc(r.subcategory_name||'General')}</p><div class="detail-grid"><div class="detail-box"><b>Estado</b><br><span class="status s-${r.status}">${statusLabel(r.status)}</span></div><div class="detail-box"><b>Contenido local</b><br>${r.file_count} archivos · ${fmtBytes(r.total_bytes)}</div></div><h3>Tipos de archivo</h3><div class="type-grid">${types||'<span class="muted">Sin indexar</span>'}</div>${technical}<div class="actions left-actions">${r.local_path?`<button class="primary" onclick="closeModal();openExplorer(${r.id})">Explorar contenido</button>`:''}<button class="secondary" onclick="openResourceTagEditorFor(${r.id})">Etiquetas</button><button class="secondary" onclick="download(${r.id})">↓ Descargar / actualizar</button><button class="secondary" onclick="openFolder(${r.id})">Abrir carpeta</button><button class="secondary" onclick="rescan(${r.id})">Reindexar</button><button class="secondary" onclick="zipResource(${r.id})">Crear ZIP</button><button class="danger" onclick="deleteResource(${r.id})">Eliminar</button></div><h3>Archivos <small class="muted">(máx. 100 visibles)</small></h3><table class="file-table"><thead><tr><th>Ruta</th><th>Tipo</th><th>Tamaño</th></tr></thead><tbody>${files||'<tr><td colspan="3">Sin archivos indexados.</td></tr>'}</tbody></table>`;
    document.getElementById('modal').classList.remove('hidden')
  }catch(e){toast(e.message)}
}
function closeModal(){document.getElementById('modal').classList.add('hidden')}

// EXPLORADOR
async function loadExplorerResources(preselect=null){try{const rs=(await api('/api/resources')).filter(r=>r.local_path);const sel=document.getElementById('explorerResource');const keep=preselect||sel.value;sel.innerHTML='<option value="">Selecciona un recurso...</option>'+rs.map(r=>`<option value="${r.id}">${esc(r.name)} · ${r.file_count} archivos</option>`).join('');if(keep)sel.value=String(keep);if(keep&&sel.value)await selectExplorerResource()}catch(e){toast(e.message)}}
async function openExplorer(id){go('library');document.getElementById('libraryHome')?.classList.add('hidden');document.getElementById('libraryExplorerView')?.classList.remove('hidden');setTimeout(()=>loadExplorerResources(id),60)}
async function selectExplorerResource(){const rid=Number(document.getElementById('explorerResource').value);EXPLORER.rid=rid||null;EXPLORER.path='';EXPLORER.selected.clear();await ensureTags();await ensureFolderTemplates();if(!rid){document.getElementById('explorerEmpty').classList.remove('hidden');document.getElementById('explorerWorkspace').classList.add('hidden');return}try{const d=await api(`/api/resources/${rid}/tree`);EXPLORER.resource=d.resource;document.getElementById('explorerEmpty').classList.add('hidden');document.getElementById('explorerWorkspace').classList.remove('hidden');renderFolderTree(d.tree);await browseExplorer('')}catch(e){toast(e.message)}}
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
async function explorerOpenCurrent(){if(!EXPLORER.rid)return;try{await api(`/api/resources/${EXPLORER.rid}/folder/open`,{method:'POST',body:JSON.stringify({path:EXPLORER.path})})}catch(e){toast(e.message)}}
async function openExplorerFile(path){try{await api(`/api/resources/${EXPLORER.rid}/file/open`,{method:'POST',body:JSON.stringify({path})})}catch(e){toast(e.message)}}
async function explorerRescan(){if(!EXPLORER.rid)return;const current=EXPLORER.path;try{const d=await api(`/api/resources/${EXPLORER.rid}/rescan`,{method:'POST'});toast(`Sincronizado: ${d.file_count} archivos`);await selectExplorerResource();await browseExplorer(current)}catch(e){toast(e.message)}}
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
  if(!EXPLORER.rid)return;const destination=await chooseDestinationFolder();if(!destination)return;
  try{const d=await api(`/api/resources/${EXPLORER.rid}/files/export`,{method:'POST',body:JSON.stringify({paths:[path],destination_dir:destination,folder_name:''})});toast(`Archivo descargado en ${d.destination}`)}catch(e){toast(e.message)}
}
async function exportSingleGlobalAsset(rid,path){const destination=await chooseDestinationFolder();if(!destination)return;try{const d=await api(`/api/resources/${rid}/files/export`,{method:'POST',body:JSON.stringify({paths:[path],destination_dir:destination,folder_name:''})});toast(`Archivo descargado en ${d.destination}`)}catch(e){toast(e.message)}}

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

function collectionModalFields(title='Nueva colección',defaultName='',defaultCategory=''){return `<h2>${esc(title)}</h2><p class="muted">Se creará una carpeta física dentro de <b>Colecciones</b>. Los originales no se modifican.</p><div class="catalog-form"><label>Nombre de la colección<input id="collectionName" value="${esc(defaultName)}" placeholder="Ej. Nuevos diseños 8M"></label><label>Categoría<input id="collectionCategory" value="${esc(defaultCategory)}" placeholder="Ej. Camisetas / Día de la Mujer"></label><label class="wide">Descripción<input id="collectionDescription" placeholder="Opcional"></label></div>`}
async function openCollectionCreateFromExplorer(){
  if(!EXPLORER.rid||!EXPLORER.selected.size)return toast('Selecciona una o varias imágenes');
  PENDING_COLLECTION_REFS=[...EXPLORER.selected].map(path=>({resource_id:EXPLORER.rid,path}));
  const defaultCategory=EXPLORER.resource?.category_name||'';
  document.getElementById('modalContent').innerHTML=collectionModalFields('Crear colección desde la selección','',defaultCategory)+`<div class="actions"><button class="secondary" onclick="closeModal()">Cancelar</button><button class="primary" onclick="createCollectionWithRefs()">Crear carpeta y copiar</button></div>`;document.getElementById('modal').classList.remove('hidden')
}
async function createCollectionWithRefs(){
  const refs=[...PENDING_COLLECTION_REFS];const name=document.getElementById('collectionName').value.trim(),category=document.getElementById('collectionCategory').value.trim()||'General',description=document.getElementById('collectionDescription').value.trim();if(!name)return toast('Escribe el nombre de la colección');
  try{const c=await api('/api/collections',{method:'POST',body:JSON.stringify({name,category,description})});const d=await api(`/api/collections/${c.id}/items`,{method:'POST',body:JSON.stringify({items:refs})});closeModal();toast(`Colección creada: ${d.added} archivos copiados`);clearExplorerSelection();if(document.querySelector('.page.active')?.id==='page-collections')await loadCollectionsPage()}catch(e){toast(e.message)}
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
  try{COLLECTIONS=await api('/api/collections');if(!COLLECTIONS.length)return toast('Primero crea una colección');document.getElementById('modalContent').innerHTML=`<h2>Agregar a una colección existente</h2><p class="muted">Se copiarán ${PENDING_COLLECTION_REFS.length} archivos. Los originales permanecerán intactos.</p><label>Colección<select id="existingCollectionSelect">${COLLECTIONS.map(c=>`<option value="${c.id}">${esc(c.category)} / ${esc(c.name)} · ${c.item_count} archivos</option>`).join('')}</select></label><div class="actions"><button class="secondary" onclick="closeModal()">Cancelar</button><button class="primary" onclick="addPendingToExistingCollection()">Copiar a colección</button></div>`;document.getElementById('modal').classList.remove('hidden')}catch(e){toast(e.message)}
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
  if(!GLOBAL_ASSETS.length){grid.innerHTML='<div class="empty">No hay imágenes que coincidan con los filtros.</div>';return}
  grid.innerHTML=GLOBAL_ASSETS.map(a=>{const key=globalKey(a.resource_id,a.rel_path),checked=GLOBAL_SELECTED.has(key)?'checked':'',ep=encodeURIComponent(a.rel_path);return `<div class="asset-card file-asset global-asset"><label class="select-box"><input type="checkbox" ${checked} onchange="toggleGlobalSelection(${a.resource_id},decodeURIComponent('${ep}'),this.checked)"></label><div class="asset-preview"><img loading="lazy" src="/api/resources/${a.resource_id}/preview?path=${ep}" alt="${esc(a.name)}"></div><div class="asset-name">${esc(a.name)}</div><div class="asset-meta">${esc(a.resource_name)} · ${esc(a.category_name||'Sin categoría')}</div>${a.tags?.length?`<div class="chips file-tags">${a.tags.map(t=>`<span class="chip">#${esc(t.name)}</span>`).join('')}</div>`:''}<div class="asset-mini-actions"><button class="mini-link" onclick="openGlobalAsset(${a.resource_id},decodeURIComponent('${ep}'))">Abrir origen</button><button class="mini-link" onclick="exportSingleGlobalAsset(${a.resource_id},decodeURIComponent('${ep}'))">Descargar</button></div></div>`}).join('')
}
function toggleGlobalSelection(rid,path,checked){const key=globalKey(rid,path);if(checked)GLOBAL_SELECTED.set(key,{resource_id:rid,path});else GLOBAL_SELECTED.delete(key);updateGlobalSelectionBar()}
function clearGlobalSelection(){GLOBAL_SELECTED.clear();renderGlobalAssets();updateGlobalSelectionBar()}
function updateGlobalSelectionBar(){const n=GLOBAL_SELECTED.size;const bar=document.getElementById('globalSelectionBar');if(!bar)return;document.getElementById('globalSelectionCount').textContent=`${n} seleccionado${n===1?'':'s'}`;bar.classList.toggle('hidden',!n)}
function clearGlobalAssetFilters(){['globalAssetSearch','globalAssetTag','globalAssetCategory','globalAssetResource'].forEach(id=>{const e=document.getElementById(id);if(e)e.value=''});loadGlobalAssets()}
async function openGlobalAsset(rid,path){openExplorer(rid);setTimeout(async()=>{await browseExplorer(path.split('/').slice(0,-1).join('/'))},160)}
async function openCollectionCreateFromGlobal(){if(!GLOBAL_SELECTED.size)return toast('Selecciona imágenes de cualquier recurso');PENDING_COLLECTION_REFS=[...GLOBAL_SELECTED.values()];document.getElementById('modalContent').innerHTML=collectionModalFields('Crear colección')+`<div class="actions"><button class="secondary" onclick="closeModal()">Cancelar</button><button class="primary" onclick="createGlobalCollection()">Crear carpeta y copiar</button></div>`;document.getElementById('modal').classList.remove('hidden')}
async function createGlobalCollection(){const refs=[...PENDING_COLLECTION_REFS];const name=document.getElementById('collectionName').value.trim(),category=document.getElementById('collectionCategory').value.trim()||'General',description=document.getElementById('collectionDescription').value.trim();if(!name)return toast('Escribe el nombre de la colección');try{const c=await api('/api/collections',{method:'POST',body:JSON.stringify({name,category,description})});const d=await api(`/api/collections/${c.id}/items`,{method:'POST',body:JSON.stringify({items:refs})});closeModal();GLOBAL_SELECTED.clear();PENDING_COLLECTION_REFS=[];toast(`${d.added} imágenes copiadas a ${category} / ${name}`);await loadCollectionsPage()}catch(e){toast(e.message)}}
async function exportGlobalSelected(){if(!GLOBAL_SELECTED.size)return;const destination=await chooseDestinationFolder();if(!destination)return;const folderName=prompt('Nombre de la carpeta que recibirá esta selección:','Seleccion');if(folderName===null)return;const groups={};for(const ref of GLOBAL_SELECTED.values()){(groups[ref.resource_id]??=[]).push(ref.path)}let total=0,last='';try{for(const [rid,paths] of Object.entries(groups)){const d=await api(`/api/resources/${rid}/files/export`,{method:'POST',body:JSON.stringify({paths,destination_dir:destination,folder_name:folderName})});total+=d.files;last=d.destination}toast(`${total} archivos copiados a ${last}`)}catch(e){toast(e.message)}}

async function loadCollections(){
  try{
    const q=(document.getElementById('collectionSearch')?.value||'').trim();
    COLLECTIONS=await api('/api/collections'+(q?`?q=${encodeURIComponent(q)}`:''));
    const el=document.getElementById('collectionsGrid');if(!el)return;
    el.innerHTML=COLLECTIONS.length
      ?COLLECTIONS.map(c=>`<div class="collection-card" onclick="collectionDetail(${c.id})"><div class="collection-folder">📁</div><div><h3>${esc(c.name)}</h3><p>${esc(c.category)}</p><span>${c.item_count} archivos · ${fmtBytes(c.total_bytes)}</span></div><div class="collection-actions"><button class="mini-link" onclick="event.stopPropagation();openCollectionFolder(${c.id})">Abrir carpeta</button><button class="mini-link" onclick="event.stopPropagation();zipCollection(${c.id})">Descargar ZIP</button></div></div>`).join('')
      :'<div class="empty">No hay colecciones que coincidan con la búsqueda.</div>';
  }catch(e){toast(e.message)}
}
function clearCollectionSearch(){const i=document.getElementById('collectionSearch');if(i)i.value='';loadCollections()}
async function openCollectionCreateEmpty(){document.getElementById('modalContent').innerHTML=collectionModalFields('Nueva colección vacía')+`<div class="actions"><button class="secondary" onclick="closeModal()">Cancelar</button><button class="primary" onclick="createEmptyCollection()">Crear carpeta</button></div>`;document.getElementById('modal').classList.remove('hidden')}
async function createEmptyCollection(){const name=document.getElementById('collectionName').value.trim(),category=document.getElementById('collectionCategory').value.trim()||'General',description=document.getElementById('collectionDescription').value.trim();if(!name)return toast('Escribe un nombre');try{await api('/api/collections',{method:'POST',body:JSON.stringify({name,category,description})});closeModal();toast('Colección creada');loadCollections()}catch(e){toast(e.message)}}
async function openCollectionFromTag(){await ensureTags();if(!TAGS.length)return toast('Primero crea una etiqueta');document.getElementById('modalContent').innerHTML=`<h2>Crear colección desde una etiqueta</h2><p class="muted">Se copiarán todos los archivos que tengan la etiqueta elegida, aunque estén en recursos y carpetas diferentes.</p><div class="catalog-form"><label>Etiqueta<select id="collectionSourceTag">${TAGS.map(t=>`<option value="${t.id}">${esc(t.name)} · ${t.files||0} archivos</option>`).join('')}</select></label><label>Nombre de la colección<input id="collectionName" placeholder="Ej. Selección Día de la Madre"></label><label>Categoría<input id="collectionCategory" placeholder="Ej. Camisetas"></label><label class="wide">Descripción<input id="collectionDescription" placeholder="Opcional"></label></div><div class="actions"><button class="secondary" onclick="closeModal()">Cancelar</button><button class="primary" onclick="createCollectionFromTag()">Crear carpeta desde etiqueta</button></div>`;document.getElementById('modal').classList.remove('hidden')}
async function createCollectionFromTag(){const tag_id=Number(document.getElementById('collectionSourceTag').value),name=document.getElementById('collectionName').value.trim(),category=document.getElementById('collectionCategory').value.trim()||'General',description=document.getElementById('collectionDescription').value.trim();if(!name)return toast('Escribe un nombre');try{const d=await api('/api/collections/from-tag',{method:'POST',body:JSON.stringify({tag_id,name,category,description})});closeModal();toast(`Colección creada con ${d.added} archivos`);await loadCollectionsPage()}catch(e){toast(e.message)}}
async function collectionDetail(id){try{const c=await api(`/api/collections/${id}`);const items=c.items.map(i=>`<div class="asset-card file-asset"><div class="asset-preview">${i.previewable?`<img src="/api/collections/${id}/items/${i.id}/preview" alt="${esc(i.name)}">`:`<div class="file-icon">${fileIcon(i.ext)}</div>`}</div><div class="asset-name">${esc(i.name)}</div><div class="asset-meta">Origen: ${esc(i.resource_name||'')} · ${fmtBytes(i.size_bytes)}</div><div class="asset-mini-actions"><button class="mini-link danger-text" onclick="removeCollectionItem(${id},${i.id})">Quitar copia</button></div></div>`).join('');document.getElementById('modalContent').innerHTML=`<div class="collection-detail-head"><div><h2>${esc(c.name)}</h2><p class="muted">${esc(c.category)} · ${c.items.length} archivos</p><p class="muted">${esc(c.description||'')}</p></div><div class="quick-actions"><button class="secondary" onclick="openCollectionFolder(${id})">Abrir carpeta</button><button class="secondary" onclick="zipCollection(${id})">↓ Descargar ZIP</button><button class="danger" onclick="deleteCollection(${id})">Eliminar colección</button></div></div><div class="asset-grid collection-items-grid">${items||'<div class="empty">Colección vacía.</div>'}</div>`;document.getElementById('modal').classList.remove('hidden')}catch(e){toast(e.message)}}
async function openCollectionFolder(id){try{await api(`/api/collections/${id}/open`,{method:'POST'})}catch(e){toast(e.message)}}
async function zipCollection(id){try{const d=await api(`/api/collections/${id}/zip`,{method:'POST'});toast('ZIP creado en Exportaciones: '+d.path)}catch(e){toast(e.message)}}
async function removeCollectionItem(cid,itemId){if(!confirm('¿Quitar esta copia de la colección? El original no se borrará.'))return;try{await api(`/api/collections/${cid}/items/${itemId}`,{method:'DELETE'});toast('Copia retirada');collectionDetail(cid);loadCollections()}catch(e){toast(e.message)}}
async function deleteCollection(id){if(!confirm('¿Eliminar esta colección y su carpeta física? Los archivos originales de Biblioteca no se borrarán.'))return;try{await api(`/api/collections/${id}`,{method:'DELETE'});closeModal();toast('Colección eliminada');loadCollections()}catch(e){toast(e.message)}}

async function addGlobalSelectionToCatalog(){if(!GLOBAL_SELECTED.size)return toast('Selecciona imágenes');PENDING_GLOBAL_CATALOG_REFS=[...GLOBAL_SELECTED.values()];document.getElementById('modalContent').innerHTML=`<h2>Agregar selección global al catálogo</h2><p class="muted">${PENDING_GLOBAL_CATALOG_REFS.length} imágenes de distintos recursos.</p><div class="catalog-form"><label>Producto<select id="globalCatProduct">${catalogProductOptions('Otro')}</select></label><label>Técnica<select id="globalCatTechnique"><option>Sublimación</option><option>DTF</option><option>Vinil textil</option><option>Otro</option></select></label><label>Estado<select id="globalCatStatus"><option value="seleccionado">Seleccionado</option><option value="preparacion">En preparación</option><option value="listo">Listo para catálogo</option><option value="publicado">Publicado</option></select></label><label>Etiquetas<input id="globalCatTags" placeholder="Ej. verano, mujer"></label></div><div class="actions"><button class="secondary" onclick="closeModal()">Cancelar</button><button class="primary" onclick="createGlobalCatalogItems()">Agregar al catálogo</button></div>`;document.getElementById('modal').classList.remove('hidden')}
async function createGlobalCatalogItems(){const refs=[...PENDING_GLOBAL_CATALOG_REFS],product_type=document.getElementById('globalCatProduct').value,technique=document.getElementById('globalCatTechnique').value,status=document.getElementById('globalCatStatus').value,tags=document.getElementById('globalCatTags').value.trim();let ok=0,skip=0;try{for(const ref of refs){const asset=GLOBAL_ASSETS.find(a=>a.resource_id===ref.resource_id&&a.rel_path===ref.path);try{await api('/api/catalog',{method:'POST',body:JSON.stringify({resource_id:ref.resource_id,source_rel_path:ref.path,title:(asset?.name||ref.path.split('/').pop()).replace(/\.[^.]+$/,''),product_type,technique,print_size:'',variants:'',status,sku:'',price:'',tags,notes:''})});ok++}catch(e){if(String(e.message).includes('ya está agregado'))skip++;else throw e}}closeModal();PENDING_GLOBAL_CATALOG_REFS=[];toast(`${ok} diseños agregados al catálogo${skip?` · ${skip} ya existían`:''}`)}catch(e){toast(e.message)}}


// ETIQUETAS Y LISTAS DE CARPETAS
async function ensureTags(){
  try{
    TAGS=await api('/api/tags');
    const sel=document.getElementById('filterTag');
    if(sel){const keep=sel.value;sel.innerHTML='<option value="">Todas las etiquetas</option>'+TAGS.map(t=>`<option value="${t.id}">${esc(t.name)}</option>`).join('');sel.value=keep}
  }catch(e){console.warn(e)}
}
async function ensureFolderTemplates(){
  try{
    FOLDER_TEMPLATES=await api('/api/folder-templates');
    const sel=document.getElementById('explorerTemplate');
    if(sel){const keep=sel.value;sel.innerHTML='<option value="">Plantilla de carpetas…</option>'+FOLDER_TEMPLATES.map(t=>`<option value="${t.id}">${esc(t.name)}</option>`).join('');sel.value=keep}
  }catch(e){console.warn(e)}
}
function setOrganizationTab(tab){
  const all=['categories','tags','templates'];
  for(const name of all){
    document.getElementById('org'+name[0].toUpperCase()+name.slice(1))?.classList.toggle('hidden',name!==tab);
    document.getElementById('org'+name[0].toUpperCase()+name.slice(1)+'Btn')?.classList.toggle('active',name===tab);
  }
  if(tab==='categories')loadCategories();
  if(tab==='tags'){ensureTags().then(renderTagsManager);}
  if(tab==='templates')loadUtilities();
}
async function loadUtilities(){await ensureTags();await ensureFolderTemplates();renderTagsManager();renderFolderTemplates();try{const rs=(await api('/api/resources')).filter(r=>r.local_path);const rsel=document.getElementById('utilityResource');if(rsel){const keep=rsel.value;rsel.innerHTML='<option value="">Selecciona un recurso...</option>'+rs.map(r=>`<option value="${r.id}">${esc(r.name)}</option>`).join('');rsel.value=keep}const tsel=document.getElementById('utilityTemplate');if(tsel){const keep=tsel.value;tsel.innerHTML='<option value="">Selecciona una lista...</option>'+FOLDER_TEMPLATES.map(t=>`<option value="${t.id}">${esc(t.name)}</option>`).join('');tsel.value=keep}}catch(e){toast(e.message)}}
function renderTagsManager(){const el=document.getElementById('tagsManager');if(!el)return;el.innerHTML=TAGS.length?TAGS.map(t=>`<div class="tag-row"><span class="chip strong">#${esc(t.name)}</span><span class="muted">${t.resources||0} recursos · ${t.folders||0} carpetas · ${t.files||0} archivos</span><button class="mini-link danger-text" onclick="deleteTag(${t.id})">Eliminar</button></div>`).join(''):'<div class="empty small">Todavía no hay etiquetas.</div>'}
async function createTag(){const i=document.getElementById('newTagName');const name=i.value.trim();if(!name)return;try{await api('/api/tags',{method:'POST',body:JSON.stringify({name})});i.value='';await loadUtilities();await loadDownloadsSetup();toast('Etiqueta creada')}catch(e){toast(e.message)}}
async function deleteTag(id){if(!confirm('¿Eliminar esta etiqueta? Se quitará de los recursos y carpetas donde esté asignada.'))return;try{await api(`/api/tags/${id}`,{method:'DELETE'});await loadUtilities();toast('Etiqueta eliminada')}catch(e){toast(e.message)}}
function tagChecklist(selected=[]){const set=new Set(selected.map(Number));return TAGS.length?`<div class="tag-checklist">${TAGS.map(t=>`<label><input type="checkbox" value="${t.id}" ${set.has(t.id)?'checked':''}> <span>#${esc(t.name)}</span></label>`).join('')}</div>`:'<p class="muted">Primero crea etiquetas en “Organización”.</p>'}
async function openResourceTagEditor(){if(!EXPLORER.rid)return toast('Selecciona un recurso');return openResourceTagEditorFor(EXPLORER.rid)}
async function openResourceTagEditorFor(rid){await ensureTags();try{const current=await api(`/api/resources/${rid}/tags`);document.getElementById('modalContent').innerHTML=`<h2>Etiquetas del recurso</h2><p class="muted">Marca una o varias etiquetas para encontrar esta carpeta más rápido.</p>${tagChecklist(current.map(x=>x.id))}<div class="actions"><button class="secondary" onclick="closeModal()">Cancelar</button><button class="primary" onclick="saveResourceTagsFor(${rid})">Guardar etiquetas</button></div>`;document.getElementById('modal').classList.remove('hidden')}catch(e){toast(e.message)}}
async function saveResourceTags(){if(!EXPLORER.rid)return;return saveResourceTagsFor(EXPLORER.rid)}
async function saveResourceTagsFor(rid){const ids=[...document.querySelectorAll('#modalContent .tag-checklist input:checked')].map(x=>Number(x.value));try{await api(`/api/resources/${rid}/tags`,{method:'PUT',body:JSON.stringify({tag_ids:ids})});closeModal();toast('Etiquetas guardadas');if(EXPLORER.rid===rid)await loadExplorerResources(rid);const active=document.querySelector('.page.active')?.id;if(active==='page-library')loadLibrary();if(active==='page-dashboard')loadDashboard()}catch(e){toast(e.message)}}
async function openCurrentFolderTagEditor(){if(!EXPLORER.rid)return toast('Selecciona un recurso');return openFolderTagEditor(EXPLORER.path)}
async function openFolderTagEditor(path=''){if(!EXPLORER.rid)return toast('Selecciona un recurso');await ensureTags();try{const current=await api(`/api/resources/${EXPLORER.rid}/folder-tags?path=${encodeURIComponent(path)}`);const label=path||'Raíz del recurso';document.getElementById('modalContent').innerHTML=`<h2>Etiquetas de carpeta</h2><p class="muted">Carpeta: <b>${esc(label)}</b></p>${tagChecklist(current.map(x=>x.id))}<div class="actions"><button class="secondary" onclick="closeModal()">Cancelar</button><button class="primary" onclick="saveFolderTagsFor(decodeURIComponent('${encodeURIComponent(path)}'))">Guardar etiquetas</button></div>`;document.getElementById('modal').classList.remove('hidden')}catch(e){toast(e.message)}}
async function saveCurrentFolderTags(){return saveFolderTagsFor(EXPLORER.path)}
async function saveFolderTagsFor(path=''){const ids=[...document.querySelectorAll('#modalContent .tag-checklist input:checked')].map(x=>Number(x.value));try{await api(`/api/resources/${EXPLORER.rid}/folder-tags`,{method:'PUT',body:JSON.stringify({path,tag_ids:ids})});closeModal();toast('Etiquetas de carpeta guardadas');await browseExplorer(EXPLORER.path)}catch(e){toast(e.message)}}
async function createFolderTemplate(){const name=document.getElementById('templateName').value.trim(),description=document.getElementById('templateDescription').value.trim(),folders=document.getElementById('templateFolders').value.split(/\r?\n/).map(x=>x.trim()).filter(Boolean);if(!name||!folders.length)return toast('Escribe un nombre y al menos una carpeta');try{await api('/api/folder-templates',{method:'POST',body:JSON.stringify({name,description,folders})});document.getElementById('templateName').value='';document.getElementById('templateDescription').value='';document.getElementById('templateFolders').value='';await loadUtilities();toast('Lista de carpetas guardada')}catch(e){toast(e.message)}}
function renderFolderTemplates(){const el=document.getElementById('folderTemplates');if(!el)return;el.innerHTML=FOLDER_TEMPLATES.length?FOLDER_TEMPLATES.map(t=>`<div class="template-card"><div class="template-head"><div><h3>${esc(t.name)}</h3><p class="muted">${esc(t.description||'Sin descripción')}</p></div><button class="mini-link danger-text" onclick="deleteFolderTemplate(${t.id})">Eliminar</button></div><div class="template-paths">${t.folders.map(f=>`<span>📁 ${esc(f)}</span>`).join('')}</div><div class="template-foot"><b>${t.folders.length} rutas</b><button class="secondary" onclick="editFolderTemplate(${t.id})">Editar</button></div></div>`).join(''):'<div class="empty">Todavía no hay listas de carpetas guardadas.</div>'}
async function deleteFolderTemplate(id){if(!confirm('¿Eliminar esta lista de carpetas? No se borrarán las carpetas físicas ya creadas.'))return;try{await api(`/api/folder-templates/${id}`,{method:'DELETE'});await loadUtilities();toast('Lista eliminada')}catch(e){toast(e.message)}}
async function editFolderTemplate(id){const t=FOLDER_TEMPLATES.find(x=>x.id===id);if(!t)return;document.getElementById('modalContent').innerHTML=`<h2>Editar lista de carpetas</h2><label>Nombre<input id="editTplName" value="${esc(t.name)}"></label><label>Descripción<input id="editTplDescription" value="${esc(t.description||'')}"></label><label>Carpetas<textarea id="editTplFolders" rows="12">${esc(t.folders.join('\n'))}</textarea></label><div class="actions"><button class="secondary" onclick="closeModal()">Cancelar</button><button class="primary" onclick="saveFolderTemplate(${id})">Guardar cambios</button></div>`;document.getElementById('modal').classList.remove('hidden')}
async function saveFolderTemplate(id){const name=document.getElementById('editTplName').value.trim(),description=document.getElementById('editTplDescription').value.trim(),folders=document.getElementById('editTplFolders').value.split(/\r?\n/).map(x=>x.trim()).filter(Boolean);try{await api(`/api/folder-templates/${id}`,{method:'PUT',body:JSON.stringify({name,description,folders})});closeModal();await loadUtilities();toast('Lista actualizada')}catch(e){toast(e.message)}}
async function applyFolderTemplate(){if(!EXPLORER.rid)return toast('Selecciona un recurso');const current=EXPLORER.path;const id=Number(document.getElementById('explorerTemplate').value);if(!id)return toast('Selecciona una plantilla de carpetas');const t=FOLDER_TEMPLATES.find(x=>x.id===id);const where=current||'raíz del recurso';if(!confirm(`Se creará la estructura “${t?.name||''}” dentro de ${where}.\n\nLas carpetas que ya existan se conservarán.`))return;try{const d=await api(`/api/resources/${EXPLORER.rid}/folder-templates/${id}/apply`,{method:'POST',body:JSON.stringify({parent_path:current})});toast(`Estructura lista: ${d.created_count} carpetas nuevas`);await selectExplorerResource();await browseExplorer(current)}catch(e){toast(e.message)}}
// CATÁLOGO
function catalogProductOptions(selected='Otro'){return PRODUCT_TYPES.map(x=>`<option ${x===selected?'selected':''}>${esc(x)}</option>`).join('')}
async function openCatalogCreate(){
  if(!EXPLORER.selected.size)return toast('Selecciona al menos una imagen o archivo');
  const paths=[...EXPLORER.selected];
  const first=EXPLORER.items.find(x=>x.path===paths[0]);
  const suggested=CATEGORY_PRODUCT_MAP[EXPLORER.resource?.category_name]||'Otro';
  const many=paths.length>1;
  document.getElementById('modalContent').innerHTML=`<h2>${many?`Agregar ${paths.length} diseños al catálogo`:'Agregar al catálogo'}</h2><p class="muted">Se crea una copia en Catalogo_Sorprezz; los originales permanecen intactos.</p><div class="catalog-form">${many?'':`<label>Nombre comercial<input id="catTitle" value="${esc((first?.name||'Diseño').replace(/\.[^.]+$/,''))}"></label>`}<label>Producto<select id="catProduct">${catalogProductOptions(PRODUCT_TYPES.includes(suggested)?suggested:'Otro')}</select></label><label>Técnica<select id="catTechnique"><option>Sublimación</option><option>DTF</option><option>Vinil textil</option><option>Otro</option></select></label><label>Estado<select id="catStatus"><option value="seleccionado">Seleccionado</option><option value="preparacion">En preparación</option><option value="listo">Listo para catálogo</option><option value="publicado">Publicado</option></select></label><label>Medida / área de impresión<input id="catPrintSize" placeholder="Ej. 20 x 25 cm"></label><label>Variantes<input id="catVariants" placeholder="Tallas, colores o modelos"></label>${many?'':`<label>SKU<input id="catSku" placeholder="Opcional"></label><label>Precio referencial<input id="catPrice" placeholder="Ej. 12.00"></label>`}<label class="wide">Etiquetas<input id="catTags" placeholder="Disney, Stitch, Día de la Madre..."></label><label class="wide">Notas<textarea id="catNotes" rows="3" placeholder="Notas de producción..."></textarea></label></div><div class="actions"><button class="secondary" onclick="closeModal()">Cancelar</button><button class="primary" onclick="createCatalogItems()">${many?'Agregar seleccionados':'Guardar en catálogo'}</button></div>`;
  document.getElementById('modal').classList.remove('hidden')
}
async function createCatalogItem(path){EXPLORER.selected=new Set([path]);return createCatalogItems()}
async function createCatalogItems(){
  const paths=[...EXPLORER.selected];if(!paths.length)return;
  const many=paths.length>1;let ok=0,skipped=0;
  for(const path of paths){
    const item=EXPLORER.items.find(x=>x.path===path);
    if(!item||item.kind!=='file'){skipped++;continue}
    const payload={resource_id:EXPLORER.rid,source_rel_path:path,title:many?(item.name||'Diseño').replace(/\.[^.]+$/,''):document.getElementById('catTitle').value.trim(),product_type:document.getElementById('catProduct').value,technique:document.getElementById('catTechnique').value,print_size:document.getElementById('catPrintSize').value.trim(),variants:document.getElementById('catVariants').value.trim(),status:document.getElementById('catStatus').value,sku:many?'':document.getElementById('catSku').value.trim(),price:many?'':document.getElementById('catPrice').value.trim(),tags:document.getElementById('catTags').value.trim(),notes:document.getElementById('catNotes').value.trim()};
    try{await api('/api/catalog',{method:'POST',body:JSON.stringify(payload)});ok++}catch(e){if(String(e.message).includes('ya está agregado'))skipped++;else throw e}
  }
  closeModal();clearExplorerSelection();toast(`${ok} diseño${ok===1?'':'s'} agregado${ok===1?'':'s'} al catálogo${skipped?` · ${skipped} omitidos`:''}`);await browseExplorer(EXPLORER.path)
}
async function loadCatalog(){try{const qRaw=document.getElementById('catalogSearch')?.value||'',sRaw=document.getElementById('catalogStatus')?.value||'',tRaw=document.getElementById('catalogType')?.value||'';const items=await api(`/api/catalog?q=${encodeURIComponent(qRaw)}&status=${encodeURIComponent(sRaw)}&product_type=${encodeURIComponent(tRaw)}`);const filtered=Boolean(qRaw||sRaw||tRaw);document.getElementById('catalogGrid').innerHTML=items.length?items.map(c=>`<div class="catalog-card" onclick="catalogDetail(${c.id})"><div class="catalog-image"><img src="/api/catalog/${c.id}/preview" onerror="this.style.display='none';this.nextElementSibling.style.display='grid'" alt=""><div class="catalog-placeholder">${fileIcon((c.catalog_path||'').split('.').pop())}</div></div><div class="catalog-body"><div class="catalog-title">${esc(c.title)}</div><div class="catalog-product">${esc(c.product_type)} · ${esc(c.technique||'Sublimación')}</div><div class="catalog-tags">${esc(c.tags||'Sin etiquetas')}</div><div class="catalog-footer"><span class="catalog-status cs-${c.status}">${catalogStatusLabel(c.status)}</span>${c.price?`<b>$${esc(c.price)}</b>`:''}</div></div></div>`).join(''):(filtered?'<div class="panel empty">No hay diseños que coincidan con los filtros actuales.<br><button class="secondary inline-empty-btn" onclick="clearCatalogFilters()">Limpiar filtros</button></div>':'<div class="panel empty">Todavía no hay diseños en el catálogo.<br>Ve a <b>Explorador</b>, selecciona una o varias imágenes y pulsa “Agregar al catálogo”.<br><button class="primary inline-empty-btn" onclick="go(\'explorer\')">Ir al Explorador</button></div>')}catch(e){toast(e.message)}}
async function catalogDetail(id){try{const c=await api(`/api/catalog/${id}`);document.getElementById('modalContent').innerHTML=`<div class="catalog-detail-head"><img src="/api/catalog/${id}/preview" onerror="this.style.display='none'"><div><h2>${esc(c.title)}</h2><p class="muted">Origen: ${esc(c.resource_name||'')} / ${esc(c.source_rel_path||'')}</p></div></div><div class="catalog-form"><label>Nombre comercial<input id="editCatTitle" value="${esc(c.title)}"></label><label>Producto<select id="editCatProduct">${catalogProductOptions(c.product_type)}</select></label><label>Técnica<select id="editCatTechnique"><option ${c.technique==='Sublimación'?'selected':''}>Sublimación</option><option ${c.technique==='DTF'?'selected':''}>DTF</option><option ${c.technique==='Vinil textil'?'selected':''}>Vinil textil</option><option ${c.technique==='Otro'?'selected':''}>Otro</option></select></label><label>Medida / área de impresión<input id="editCatPrintSize" value="${esc(c.print_size||'')}"></label><label>Variantes<input id="editCatVariants" value="${esc(c.variants||'')}"></label><label>Estado<select id="editCatStatus"><option value="seleccionado" ${c.status==='seleccionado'?'selected':''}>Seleccionado</option><option value="preparacion" ${c.status==='preparacion'?'selected':''}>En preparación</option><option value="listo" ${c.status==='listo'?'selected':''}>Listo para catálogo</option><option value="publicado" ${c.status==='publicado'?'selected':''}>Publicado</option></select></label><label>SKU<input id="editCatSku" value="${esc(c.sku||'')}"></label><label>Precio referencial<input id="editCatPrice" value="${esc(c.price||'')}"></label><label>Etiquetas<input id="editCatTags" value="${esc(c.tags||'')}"></label><label class="wide">Notas<textarea id="editCatNotes" rows="4">${esc(c.notes||'')}</textarea></label></div><div class="actions left-actions"><button class="secondary" onclick="openCatalogFolder(${id})">Abrir carpeta de catálogo</button><button class="danger" onclick="deleteCatalogItem(${id})">Quitar del catálogo</button><button class="primary" onclick="saveCatalogItem(${id})">Guardar cambios</button></div>`;document.getElementById('modal').classList.remove('hidden')}catch(e){toast(e.message)}}
async function saveCatalogItem(id){try{const p={title:document.getElementById('editCatTitle').value.trim(),product_type:document.getElementById('editCatProduct').value,technique:document.getElementById('editCatTechnique').value,print_size:document.getElementById('editCatPrintSize').value.trim(),variants:document.getElementById('editCatVariants').value.trim(),status:document.getElementById('editCatStatus').value,sku:document.getElementById('editCatSku').value.trim(),price:document.getElementById('editCatPrice').value.trim(),tags:document.getElementById('editCatTags').value.trim(),notes:document.getElementById('editCatNotes').value.trim()};await api(`/api/catalog/${id}`,{method:'PUT',body:JSON.stringify(p)});closeModal();loadCatalog();toast('Catálogo actualizado')}catch(e){toast(e.message)}}
async function openCatalogFolder(id){try{await api(`/api/catalog/${id}/open`,{method:'POST'})}catch(e){toast(e.message)}}
async function deleteCatalogItem(id){const del=confirm('¿También deseas borrar la copia creada dentro de Catalogo_Sorprezz?\nAceptar = quitar y borrar copia.\nCancelar = quitar solo del catálogo.');if(!confirm('¿Confirmas quitar este elemento del catálogo?'))return;try{await api(`/api/catalog/${id}?delete_copy=${del?'true':'false'}`,{method:'DELETE'});closeModal();loadCatalog();toast('Elemento retirado del catálogo')}catch(e){toast(e.message)}}
async function exportCatalog(){try{const d=await api('/api/catalog/export',{method:'POST'});toast(`CSV creado: ${d.items} elementos`)}catch(e){toast(e.message)}}


function clearCatalogFilters(){document.getElementById('catalogSearch').value='';document.getElementById('catalogStatus').value='';document.getElementById('catalogType').value='';loadCatalog()}
function clearLibraryFilters(){document.getElementById('searchQ').value='';document.getElementById('filterCategory').value='';document.getElementById('filterStatus').value='';if(document.getElementById('filterTag'))document.getElementById('filterTag').value='';loadLibrary()}
async function syncAllResources(){
  try{
    const d=await api('/api/resources/rescan-all',{method:'POST'});
    toast(`Biblioteca sincronizada: ${d.resources} recursos · ${d.files} archivos`);
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
async function applyTemplateFromUtilities(){const rid=Number(document.getElementById('utilityResource')?.value||0),tid=Number(document.getElementById('utilityTemplate')?.value||0),parent=(document.getElementById('utilityParentPath')?.value||'').trim();if(!rid||!tid)return toast('Selecciona un recurso y una plantilla');const t=FOLDER_TEMPLATES.find(x=>x.id===tid);if(!confirm(`Crear la estructura “${t?.name||''}” ${parent?`dentro de ${parent}`:'en la raíz del recurso'}?`))return;try{const d=await api(`/api/resources/${rid}/folder-templates/${tid}/apply`,{method:'POST',body:JSON.stringify({parent_path:parent})});toast(`Estructura creada: ${d.created_count} carpetas nuevas`)}catch(e){toast(e.message)}}
async function loadSettings(){const c=await api('/api/config');document.getElementById('libraryPath').value=c.library_path;document.getElementById('configMeta').innerHTML=`Versión ${c.version}<br>Base de datos: ${esc(c.db_path)}`}
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
