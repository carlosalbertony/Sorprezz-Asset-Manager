// Run with: node --test tests/sync_ui.test.js
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../web/app.js'),'utf8').split('(async function init(){')[0];

function setup(){
  const elements=new Map();
  const element=id=>{
    if(!elements.has(id)){
      const classes=new Set();
      elements.set(id,{id,value:'',innerHTML:'',textContent:'',disabled:false,dataset:{},scrollTop:0,
        addEventListener(){},focus(){this.focused=true},showModal(){this.open=true},
        close(value){this.open=false;this.returnValue=value;this.onclose?.()},
        classList:{contains:c=>classes.has(c),add:c=>classes.add(c),
          remove:c=>classes.delete(c),toggle(c,on){if(on)classes.add(c);else classes.delete(c)}}});
    }
    return elements.get(id);
  };
  const button=element('syncButton');button.innerHTML='Sincronizar';
  element('modal').classList.add('hidden');
  let active='page-library';
  const calls=[];
  let handler=async path=>{
    if(path.endsWith('/tree'))return {resource:{id:1,name:'Compartido'},tree:[]};
    if(path.includes('/browse'))return {current_path:'Familia',items:[photo('Familia/primera.png'),photo('Familia/nueva.png')]};
    if(path.includes('rescan'))return {ok:true,resources:1,files:2,tags_preserved:true};
    throw new Error(`Unexpected request: ${path}`);
  };
  const context=vm.createContext({console,URLSearchParams,setTimeout(){},clearTimeout(){},
    location:{hostname:'192.168.1.5'},window:{},
    document:{hidden:false,getElementById:element,addEventListener(){},
      querySelector:()=>({id:active}),querySelectorAll:s=>s.includes('syncAllResources')?[button]:[]},
    fetch:async(path,opts)=>{
      calls.push({path,opts});
      const result=await handler(path,opts);
      return {ok:!result.httpError,status:result.httpError||200,json:async()=>result};
    }});
  vm.runInContext(source,context);
  const run=code=>vm.runInContext(code,context);
  run(`EXPLORER.rid=1;EXPLORER.path='Familia';EXPLORER.items=[];EXPLORER.selected=new Set(['Familia/primera.png'])`);
  return {run,element,calls,button,setHandler:fn=>{handler=fn},setActive:id=>{active=id}};
}
function photo(path,revision='1'){return {kind:'file',path,name:path.split('/').pop(),ext:'png',previewable:true,revision,size_bytes:5,tags:[]}}

test('apostrophes and accented paths generate executable folder and image controls',()=>{
  const ui=setup();
  const path="Diseños de papá/John's foto.png";
  ui.run(`EXPLORER.items=[${JSON.stringify(photo(path))}];renderExplorerItems()`);
  const html=ui.element('explorerItems').innerHTML;
  const handlers=[...html.matchAll(/(?:onclick|ondblclick|onchange)="([^"]+)"/g)].map(m=>m[1]);
  assert.ok(handlers.length>4);
  for(const handler of handlers)assert.doesNotThrow(()=>new Function('event',handler));
  const select=handlers.find(h=>h.startsWith('toggleExplorerSelection'));
  ui.run(select.replace('this.checked','true'));
  assert.equal(ui.run(`EXPLORER.selected.has(${JSON.stringify(path)})`),true);
});

test('opening a library folder from details closes the overlay and loads immediately',async()=>{
  const ui=setup();ui.element('modal').classList.remove('hidden');
  ui.run('go=()=>{}');
  ui.setHandler(async path=>{
    if(path.endsWith('/open'))return {mode:'browser'};
    if(path==='/api/resources')return [{id:2,name:'Recurso',local_path:'server'}];
    if(path==='/api/tags')return [];
    if(path.endsWith('/tree'))return {resource:{id:2},tree:[]};
    if(path.includes('/browse'))return {current_path:'',items:[]};
    throw new Error(path);
  });
  await ui.run('openFolder(2)');
  assert.equal(ui.element('modal').classList.contains('hidden'),true);
  assert.equal(ui.run('EXPLORER.rid'),2);
  assert.ok(ui.calls.some(c=>c.path.startsWith('/api/resources/2/browse')));
});

test('polling is installed even when the first startup request fails',async()=>{
  const ui=setup();
  ui.run('setInterval=()=>{window.pollingInstalled=true};loadCategories=async()=>{throw new Error("Sin conexión")}');
  const init=fs.readFileSync(require('node:path').join(__dirname,'../web/app.js'),'utf8').split('(async function init(){')[1];
  await ui.run('(async function init(){'+init);
  assert.equal(ui.run('window.pollingInstalled'),true);
});

test('automatic refresh shows new uploads in open subfolder, preserving selection',async()=>{
  const ui=setup();
  await ui.run('refreshSharedView()');
  assert.equal(ui.run('EXPLORER.path'),'Familia');
  assert.equal(ui.run('EXPLORER.selected.has("Familia/primera.png")'),true);
  assert.match(ui.element('explorerItems').innerHTML,/nueva.png/);
  assert.match(ui.element('explorerItems').innerHTML,/&v=1/);
  assert.ok(ui.calls.every(c=>c.opts.cache==='no-store'));
});

test('both manual sync actions preserve the open subfolder and report completion',async()=>{
  const ui=setup();
  await ui.run('syncAllResources()');
  await ui.run('explorerRescan()');
  assert.equal(ui.run('EXPLORER.path'),'Familia');
  assert.equal(ui.run('EXPLORER.selected.size'),1);
  assert.ok(ui.calls.some(c=>c.path==='/api/resources/rescan-all'));
  assert.ok(ui.calls.some(c=>c.path==='/api/resources/1/rescan'));
  assert.ok(ui.calls.filter(c=>c.path.includes('/browse')).every(c=>c.path.endsWith('path=Familia')));
  assert.match(ui.element('syncStatus').textContent,/Sincronizado/);
  assert.equal(ui.button.disabled,false);
});

test('sync buttons prevent repeated requests and recover after server failure',async()=>{
  const ui=setup();let reject;
  ui.setHandler(()=>new Promise((_,r)=>{reject=r}));
  const pending=ui.run('syncAllResources()');
  assert.equal(ui.button.disabled,true);
  await ui.run('syncAllResources()');
  assert.equal(ui.calls.length,1);
  reject(new Error('Servidor desconectado'));
  await pending;
  assert.equal(ui.button.disabled,false);
  assert.equal(ui.button.innerHTML,'Sincronizar');
  assert.match(ui.element('syncStatus').textContent,/Servidor desconectado/);
});

test('partial failures remain visible instead of showing success',async()=>{
  const ui=setup();ui.setActive('page-settings');
  ui.setHandler(async()=>({ok:false,resources:0,errors:[{name:'Familia',detail:'Disco desconectado'}]}));
  await ui.run('syncAllResources()');
  assert.match(ui.element('syncStatus').textContent,/Sincronización incompleta.*Familia: Disco desconectado/);
});

test('a delayed refresh cannot overwrite navigation to another folder',async()=>{
  const ui=setup();let resolveTree;
  ui.setHandler(async path=>{
    if(path.endsWith('/tree'))return new Promise(r=>{resolveTree=r});
    return {current_path:path.includes('Otra')?'Otra':'Familia',items:[]};
  });
  const pending=ui.run('refreshSharedView()');
  await ui.run('browseExplorer("Otra")');
  resolveTree({resource:{id:1,name:'Compartido'},tree:[]});
  await pending;
  assert.equal(ui.run('EXPLORER.path'),'Otra');
});

test('image view refreshes other client uploads without losing filters or selection',async()=>{
  const ui=setup();
  ui.element('libraryExplorerView').classList.add('hidden');
  ui.element('globalAssetTag').value='3';
  ui.run(`LIBRARY_MODE='images';GLOBAL_SELECTED.set('1::primera.png',{resource_id:1,path:'primera.png'})`);
  ui.setHandler(async()=>[{resource_id:1,rel_path:'nueva.png',name:'nueva.png',resource_name:'Compartido',tags:[],revision:'2'}]);
  await ui.run('refreshSharedView()');
  assert.match(ui.element('globalAssetsGrid').innerHTML,/nueva.png/);
  assert.equal(ui.run('GLOBAL_SELECTED.size'),1);
  assert.equal(ui.element('globalAssetTag').value,'3');
  assert.match(ui.calls[0].path,/tag_id=3/);
});

test('polling does not cancel navigation that is still loading',async()=>{
  const ui=setup();let resolveBrowse;
  ui.setHandler(()=>new Promise(resolve=>{resolveBrowse=resolve}));
  const navigation=ui.run('browseExplorer("Otra")');
  await ui.run('refreshSharedView()');
  assert.equal(ui.calls.length,1);
  resolveBrowse({current_path:'Otra',items:[]});
  await navigation;
  assert.equal(ui.run('EXPLORER.path'),'Otra');
});

test('removed open folder falls back to root and clears unavailable selections',async()=>{
  const ui=setup();
  ui.setHandler(async path=>{
    if(path.endsWith('/tree'))return {resource:{id:1,name:'Compartido'},tree:[]};
    if(path.endsWith('path=Familia'))return {httpError:404,detail:'La carpeta no existe'};
    return {current_path:'',items:[]};
  });
  await ui.run('refreshSharedView()');
  assert.equal(ui.run('EXPLORER.path'),'');
  assert.equal(ui.run('EXPLORER.selected.size'),0);
});

test('canceling collection deletion leaves it untouched and focuses Cancel',async()=>{
  const ui=setup();
  ui.setHandler(async()=>({id:4,name:'AÑO NUEVO',items:[]}));
  const pending=ui.run('deleteCollection(4)');
  await new Promise(setImmediate);
  assert.equal(ui.element('deleteConfirm').open,true);
  assert.equal(ui.element('deleteConfirmCancel').focused,true);
  assert.ok(ui.calls.every(c=>c.opts.method!=='DELETE'));
  ui.element('deleteConfirm').close('cancel');
  await pending;
  assert.ok(ui.calls.every(c=>c.opts.method!=='DELETE'));
  assert.equal(ui.run('DELETE_CONFIRM_PENDING'),false);
});

test('collection deletion only runs after explicitly accepting the dialog',async()=>{
  const ui=setup();
  ui.setHandler(async path=>path==='/api/collections/4'?{id:4,name:'AÑO NUEVO',items:[]}:[]);
  const pending=ui.run('deleteCollection(4)');
  await new Promise(setImmediate);
  assert.match(ui.element('deleteConfirmMessage').textContent,/AÑO NUEVO/);
  ui.element('deleteConfirm').close('confirm');
  await pending;
  assert.equal(ui.calls.filter(c=>c.opts.method==='DELETE').length,1);
  assert.equal(ui.calls.find(c=>c.opts.method==='DELETE').path,'/api/collections/4');
});

test('canceling copy removal sends no deletion request',async()=>{
  const ui=setup();
  const pending=ui.run('removeCollectionItem(4,7)');
  assert.equal(ui.element('deleteConfirm').open,true);
  ui.element('deleteConfirm').close('cancel');
  await pending;
  assert.equal(ui.calls.length,0);
});

test('permanent deletion has a separate warning and explicit API confirmation',async()=>{
  const ui=setup();
  ui.run(`TRASH_ITEMS=[{id:'test-entry',name:'Copia',kind:'collection_item',state:'ready',deleted_at:'2026-09-18T12:00:00'}]`);
  ui.setHandler(async path=>path==='/api/trash'?[]:{ok:true});
  const pending=ui.run('purgeTrash("test-entry")');
  assert.equal(ui.calls.length,0);
  assert.equal(ui.element('deleteConfirmTitle').textContent,'¿Eliminar definitivamente?');
  assert.match(ui.element('deleteConfirmMessage').textContent,/no se puede deshacer/);
  ui.element('deleteConfirm').close('confirm');
  await pending;
  const deletion=ui.calls.find(c=>c.opts.method==='DELETE');
  assert.equal(deletion.path,'/api/trash/test-entry');
  assert.deepEqual(JSON.parse(deletion.opts.body),{confirmed:true});
});

test('collection deletion control is placed after the grid in its own footer',async()=>{
  const ui=setup();
  ui.setHandler(async()=>({id:4,name:'AÑO NUEVO',category:'Camisetas',items:[]}));
  await ui.run('collectionDetail(4)');
  const html=ui.element('modalContent').innerHTML;
  assert.ok(html.indexOf('modal-danger-zone')>html.indexOf('collection-items-grid'));
  assert.ok(html.indexOf('deleteCollection(4)')>html.indexOf('modal-danger-zone'));
});

function collection(id=4,items=[]){return {id,name:'ANIME & MANGA',category:'Camisetas',items,physical_path:'C:/Documentos/Colecciones/Anime',open_mode:'browser'}}
function collectionImage(id,name){return {id,name,rel_path:name,previewable:true,ext:'png',revision:String(id),size_bytes:10}}

test('an open collection modal refreshes Windows copies and preserves scroll',async()=>{
  const ui=setup();
  let items=[collectionImage(1,'primera.png')];
  ui.setHandler(async()=>collection(4,items));
  await ui.run('collectionDetail(4)');
  ui.element('collectionModalItems').scrollTop=180;
  items=[...items,collectionImage(2,'copiada-desde-Windows.png')];
  await ui.run('refreshSharedView()');
  assert.match(ui.element('collectionModalItems').innerHTML,/copiada-desde-Windows.png/);
  assert.equal(ui.element('collectionModalItems').scrollTop,180);
  assert.match(ui.element('collectionModalMeta').textContent,/2 archivos/);
  assert.equal(ui.element('modal').classList.contains('hidden'),false);
});

test('collection polling does not replace an unchanged image grid',async()=>{
  const ui=setup();
  ui.setHandler(async()=>collection(4,[collectionImage(1,'foto.png')]));
  await ui.run('collectionDetail(4)');
  ui.element('collectionModalItems').innerHTML='unchanged grid';
  await ui.run('refreshSharedView()');
  assert.equal(ui.element('collectionModalItems').innerHTML,'unchanged grid');
});

test('closing a collection during a delayed refresh never reopens it',async()=>{
  const ui=setup();let finish;
  ui.setHandler(()=>new Promise(resolve=>{finish=resolve}));
  const pending=ui.run('collectionDetail(4)');
  ui.run('closeModal()');
  finish(collection());await pending;
  assert.equal(ui.element('modal').classList.contains('hidden'),true);
  assert.equal(ui.run('COLLECTION_DETAIL_ID'),null);
});

test('remote Open folder navigates to the collection explorer instead of reopening the same modal',async()=>{
  const ui=setup();
  ui.setHandler(async path=>path.endsWith('/open')?{mode:'browser'}:
    {collection:collection(),current_path:'',folders:[{name:'Diseños',path:'Diseños'}],items:[]});
  await ui.run('openCollectionFolder(4)');
  assert.equal(ui.element('modal').classList.contains('hidden'),true);
  assert.equal(ui.element('page-collection-browser').classList.contains('active'),true);
  assert.match(ui.element('collectionBrowserItems').innerHTML,/Diseños/);
  assert.ok(ui.calls.some(c=>c.path==='/api/collections/4/browse?path='));
});

test('local Open folder only asks the server to open Windows',async()=>{
  const ui=setup();ui.setHandler(async()=>({mode:'windows'}));
  await ui.run('openCollectionFolder(4)');
  assert.equal(ui.calls.length,1);
  assert.equal(ui.calls[0].path,'/api/collections/4/open');
  assert.match(ui.element('toast').textContent,/Explorador de Windows/);
});

test('collection explorer polling preserves the subfolder and sees added files',async()=>{
  const ui=setup();ui.setActive('page-collection-browser');
  ui.run(`COLLECTION_BROWSER={id:4,path:'Diseños',signature:''}`);
  ui.setHandler(async()=>({collection:collection(),current_path:'Diseños',folders:[],items:[collectionImage(2,'nueva.png')]}));
  await ui.run('refreshSharedView()');
  assert.equal(ui.run('COLLECTION_BROWSER.path'),'Diseños');
  assert.match(ui.element('collectionBrowserItems').innerHTML,/nueva.png/);
  assert.ok(ui.calls[0].path.endsWith('path=Dise%C3%B1os'));
});

test('a confirmation dialog pauses collection polling',async()=>{
  const ui=setup();ui.run('COLLECTION_DETAIL_ID=4;DELETE_CONFIRM_PENDING=true');
  ui.element('modal').classList.remove('hidden');
  await ui.run('refreshSharedView()');
  assert.equal(ui.calls.length,0);
});

test('manual sync in an open collection refreshes that modal',async()=>{
  const ui=setup();
  let items=[];
  ui.setHandler(async path=>{
    if(path.endsWith('/rescan')){items=[collectionImage(2,'sincronizada.png')];return {file_count:1}}
    return collection(4,items);
  });
  await ui.run('collectionDetail(4)');
  await ui.run('syncCollection(4)');
  assert.match(ui.element('collectionModalItems').innerHTML,/sincronizada.png/);
  assert.ok(ui.calls.some(c=>c.path==='/api/collections/4/rescan'));
});
