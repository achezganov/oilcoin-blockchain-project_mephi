(() => {
  'use strict';
  const grid=document.querySelector('[data-colony-grid]'); if(!grid)return;
  const viewport=grid.parentElement, size=Number(grid.dataset.size);
  const initial=JSON.parse(grid.dataset.objects), inventory=JSON.parse(grid.dataset.inventory||'{}');
  const catalog=JSON.parse(grid.dataset.catalog||'{}'), decor=JSON.parse(grid.dataset.decor||'{}');
  const main=['hq','pump','depot','refinery','garage'];
  let objects=structuredClone(initial), editing=false, tool='move', selected=null, gesture=null, undo=[];
  const form=document.querySelector('[data-editor-form]'), tools=document.querySelector('.editor-tools');
  const hint=document.querySelector('[data-editor-hint]'), save=document.querySelector('[data-editor-save]');
  const cells=[...grid.querySelectorAll('.island-cell')];
  const width=o=>main.includes(o.kind)||catalog[o.kind]?.size===2||(!form && !['pipe','road','tree','monument'].includes(o.kind))?2:1;
  const count=(list,kind)=>list.filter(o=>o.kind===kind).length;
  const hit=(o,x,y)=>x>=o.x&&x<o.x+width(o)&&y>=o.y&&y<o.y+width(o);
  function valid(item,ignore=null){return item.x>=0&&item.y>=0&&item.x+width(item)<=size&&item.y+width(item)<=size&&!objects.some((o,i)=>i!==ignore&&item.x<o.x+width(o)&&item.x+width(item)>o.x&&item.y<o.y+width(o)&&item.y+width(item)>o.y);}
  const dirty=()=>JSON.stringify(objects)!==JSON.stringify(initial);
  function remember(){undo.push(structuredClone(objects));if(undo.length>40)undo.shift();}
  function livePipes(){
    const pipes=new Set(objects.filter(o=>o.kind==='pipe').map(o=>`${o.x},${o.y}`)); const live=new Set(),todo=[];
    for(const o of objects.filter(o=>['pump','pump_station'].includes(o.kind))){
      for(let i=0;i<2;i++)for(const [x,y] of [[o.x-1,o.y+i],[o.x+2,o.y+i],[o.x+i,o.y-1],[o.x+i,o.y+2]])if(pipes.has(`${x},${y}`)&&!live.has(`${x},${y}`)){live.add(`${x},${y}`);todo.push([x,y]);}
    }
    while(todo.length){const [x,y]=todo.pop();for(const [a,b] of [[x-1,y],[x+1,y],[x,y-1],[x,y+1]])if(pipes.has(`${a},${b}`)&&!live.has(`${a},${b}`)){live.add(`${a},${b}`);todo.push([a,b]);}}
    return live;
  }
  function render(){
    grid.querySelectorAll('.island-object').forEach(e=>e.remove());const live=livePipes();
    objects.forEach((o,index)=>{
      const node=document.createElement('button');node.type='button';node.className='island-object '+(width(o)===2?'object-building':'object-'+o.kind);
      node.dataset.objectIndex=index;node.dataset.kind=o.kind;node.style.gridColumn=`${o.x+1} / span ${width(o)}`;node.style.gridRow=`${o.y+1} / span ${width(o)}`;
      const template=document.querySelector(`[data-object-art="${o.kind}"]`);
      if(template)node.append(template.content.cloneNode(true));else node.textContent=o.kind==='pipe'?'╋':decor[o.kind]?.symbol||({'road':'▪','tree':'♠','monument':'◆'}[o.kind]);
      if(o.kind==='pipe'){
        const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 100 100');svg.setAttribute('class','pipe-art');svg.setAttribute('aria-hidden','true');
        const ports=['pipe','pump','pump_station','refinery','workshop','fuel_station','compressor'];
        let linked=0;
        for(const [dx,dy] of [[-1,0],[1,0],[0,-1],[0,1]]){
          if(!objects.some(other=>other!==o&&ports.includes(other.kind)&&hit(other,o.x+dx,o.y+dy)))continue;
          const line=document.createElementNS(svg.namespaceURI,'line');line.setAttribute('x1','50');line.setAttribute('y1','50');line.setAttribute('x2',String(50+dx*50));line.setAttribute('y2',String(50+dy*50));svg.append(line);linked++;
        }
        const joint=document.createElementNS(svg.namespaceURI,'circle');joint.setAttribute('cx','50');joint.setAttribute('cy','50');joint.setAttribute('r',linked?'9':'17');svg.append(joint);node.replaceChildren(svg);
      }
      node.setAttribute('aria-label',node.textContent.trim()||o.kind);node.classList.toggle('object-selected',editing&&selected===index);
      if(o.kind==='pipe')node.classList.toggle('pipe-live',live.has(`${o.x},${o.y}`));grid.append(node);
    });
    document.dispatchEvent(new Event('colony:render'));
    if(form){
      form.querySelector('[data-layout-value]').value=JSON.stringify(objects);save.disabled=!dirty();
      const cost=Object.entries(decor).reduce((sum,[kind,spec])=>sum+Math.max(0,count(objects,kind)-count(initial,kind))*spec.cost,0);
      document.querySelector('[data-editor-cost]').textContent=`Новый декор: ${cost} деталей · трубы: ${count(objects,'pipe')}/${inventory.pipe||0}`;
    }
  }
  function setTool(value){tool=value;selected=null;document.querySelectorAll('[data-tool]').forEach(e=>e.classList.toggle('selected',e.dataset.tool===value));if(hint)hint.textContent=value==='pipe'?'Проведите пальцем или мышью по свободным клеткам. Золотые трубы подключены к вышке.':value==='erase'?'Нажмите объект, чтобы убрать. Купленные станции и трубы вернутся в инвентарь; декор удаляется без возврата.':'Перетащите объект. Можно также выбрать здание и нажать клетку назначения.';render();}
  function edit(value){if(!tools)return;editing=value;tools.hidden=!value;grid.classList.toggle('is-editing',value);cells.forEach(c=>c.tabIndex=value?0:-1);document.querySelector('[data-editor-toggle]').textContent=value?'Закрыть редактор':'Редактировать';if(!value){selected=null;clearPreview();}render();}
  function position(event){const rect=grid.getBoundingClientRect(), border=parseFloat(getComputedStyle(grid).borderLeftWidth);const w=grid.clientWidth,h=grid.clientHeight;return{x:Math.floor((event.clientX-rect.left-border)/w*size),y:Math.floor((event.clientY-rect.top-border)/h*size)};}
  function clearPreview(){cells.forEach(c=>c.classList.remove('cell-good','cell-bad'));}
  function preview(x,y){clearPreview();const item=tool==='move'&&selected!==null?{...objects[selected],x,y}:catalog[tool]||decor[tool]?{kind:tool,x,y}:null;if(!item)return;const good=valid(item,tool==='move'?selected:null);cells.filter(c=>hit(item,+c.dataset.x,+c.dataset.y)).forEach(c=>c.classList.add(good?'cell-good':'cell-bad'));}
  function place(x,y,record=true){
    if(x<0||y<0||x>=size||y>=size)return;
    const index=objects.findIndex(o=>hit(o,x,y));
    if(tool==='erase'){
      if(index<0)return;if(main.includes(objects[index].kind)){hint.textContent='Основные пять зданий можно переносить, но нельзя убрать.';return;}
      if(record)remember();objects.splice(index,1);selected=null;render();return;
    }
    if(tool==='move'){
      if(selected===null){if(index>=0){selected=index;render();}return;}
      const item={...objects[selected],x,y};if(!valid(item,selected)){hint.textContent='Здесь мало места. Контур объекта должен быть зелёным.';return;}
      if(record)remember();objects[selected]=item;render();return;
    }
    if(!(catalog[tool]||decor[tool]))return;
    const item={kind:tool,x,y};if(!valid(item))return;
    if(catalog[tool]&&count(objects,tool)>=(inventory[tool]||0)){hint.textContent='Объекты этого типа закончились. Купите ещё в магазине.';return;}
    if(record)remember();objects.push(item);render();
  }
  grid.addEventListener('pointerdown',event=>{
    if(!editing||event.button!==0)return;event.preventDefault();const cell=position(event);grid.setPointerCapture(event.pointerId);
    const target=event.target.closest('[data-object-index]');
    gesture={startX:event.clientX,startY:event.clientY,moved:false,fromObject:!!target,paint:tool==='pipe'||tool==='erase'};
    if(gesture.paint){remember();place(cell.x,cell.y,false);}
    else if(target&&tool==='move'){selected=Number(target.dataset.objectIndex);render();}
  });
  grid.addEventListener('pointermove',event=>{
    if(!editing)return;const cell=position(event);if(gesture){gesture.moved=gesture.moved||Math.hypot(event.clientX-gesture.startX,event.clientY-gesture.startY)>6;if(gesture.paint)place(cell.x,cell.y,false);}preview(cell.x,cell.y);
  });
  grid.addEventListener('pointerup',event=>{
    if(!gesture)return;const cell=position(event);if(!gesture.paint&&(!gesture.fromObject||gesture.moved))place(cell.x,cell.y);gesture=null;clearPreview();if(grid.hasPointerCapture(event.pointerId))grid.releasePointerCapture(event.pointerId);
  });
  grid.addEventListener('pointercancel',()=>{gesture=null;clearPreview();});
  grid.addEventListener('pointerleave',()=>{if(!gesture)clearPreview();});
  grid.addEventListener('click',event=>{
    const node=event.target.closest('[data-object-index]');
    if(!editing&&node){
      const kind=node.dataset.kind,target=document.getElementById('building-'+kind);
      if(target){document.querySelectorAll('.building-panel').forEach(p=>p.open=p===target);target.scrollIntoView({block:'nearest',behavior:'smooth'});}
      else{const info=document.querySelector('[data-object-info]');if(info){info.hidden=false;info.textContent=catalog[kind]?.description||'Объект на территории острова.';}}
    }else if(editing&&event.detail===0){const cell=event.target.closest('[data-x]');if(cell)place(+cell.dataset.x,+cell.dataset.y);}
  });
  cells.forEach(cell=>cell.addEventListener('keydown',event=>{const shift={ArrowLeft:-1,ArrowRight:1,ArrowUp:-size,ArrowDown:size}[event.key];if(shift){event.preventDefault();cells[Math.max(0,Math.min(cells.length-1,+cell.dataset.y*size+ +cell.dataset.x+shift))].focus();}}));
  document.querySelectorAll('[data-zoom]').forEach(button=>button.addEventListener('click',()=>{
    const current=grid.getBoundingClientRect().width,fit=viewport.clientWidth-36;
    grid.style.setProperty('--grid-pixels',(button.dataset.zoom==='fit'?fit:Math.max(fit*.75,Math.min(size*110,current*(button.dataset.zoom==='in'?1.2:1/1.2))))+'px');
  }));
  if(form){
    document.querySelector('[data-editor-toggle]').addEventListener('click',()=>edit(!editing));
    document.querySelectorAll('[data-tool]').forEach(b=>b.addEventListener('click',()=>setTool(b.dataset.tool)));
    document.querySelectorAll('[data-place-from-shop]').forEach(b=>b.addEventListener('click',()=>{window.OilColony?.selectTab('island');edit(true);setTool(b.dataset.placeFromShop);grid.scrollIntoView({behavior:'smooth',block:'center'});}));
    document.querySelector('[data-undo]').addEventListener('click',()=>{if(undo.length){objects=undo.pop();selected=null;render();}});
    document.querySelector('[data-editor-cancel]').addEventListener('click',()=>{objects=structuredClone(initial);undo=[];selected=null;render();});
    const warn=event=>{if(dirty()){event.preventDefault();event.returnValue='';}};window.addEventListener('beforeunload',warn);form.addEventListener('submit',()=>window.removeEventListener('beforeunload',warn));
  }
  render();
})();
