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
      elements.set(id,{id,value:'',innerHTML:'',textContent:'',disabled:false,
        addEventListener(){},classList:{contains:c=>classes.has(c),add:c=>classes.add(c),
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
