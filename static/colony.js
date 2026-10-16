(() => {
  'use strict';
  const csrf = document.querySelector('meta[name=csrf-token]')?.content;
  const tabs = [...document.querySelectorAll('[data-colony-tab]')];
  function selectTab(name) {
    if (!tabs.some(t => t.dataset.colonyTab === name)) return;
    tabs.forEach(t => t.classList.toggle('is-active', t.dataset.colonyTab === name));
    document.querySelectorAll('[data-colony-panel]').forEach(p => p.hidden = p.dataset.colonyPanel !== name);
  }
  tabs.forEach(tab => tab.addEventListener('click', () => { selectTab(tab.dataset.colonyTab); history.replaceState(null,'','#'+tab.dataset.colonyTab); }));
  window.addEventListener('hashchange', () => selectTab(location.hash.slice(1)));
  selectTab(location.hash.slice(1));
  window.OilColony = {selectTab};
  const fuel = document.getElementById('fuel-pct'), parts = document.getElementById('parts-pct');
  let allocationEdited = false;
  if (fuel && parts) {
    const allocation = changed => {
      allocationEdited = true;
      if (+fuel.value + +parts.value > 100) (changed === fuel ? parts : fuel).value = 100 - Number(changed.value);
      document.querySelector('[data-fuel-output]').textContent = fuel.value+'%';
      document.querySelector('[data-parts-output]').textContent = parts.value+'%';
      document.querySelector('[data-crude-output]').textContent = (100-fuel.value-parts.value)+'%';
    };
    fuel.addEventListener('input', () => allocation(fuel)); parts.addEventListener('input', () => allocation(parts));
  }
  const deck = document.getElementById('deck-form');
  if (deck) {
    const update = () => { const count = document.querySelectorAll('input[form=deck-form]:checked').length; document.querySelector('[data-deck-count]').textContent=count; document.querySelector('[data-deck-save]').disabled=count!==6; };
    document.querySelectorAll('input[form=deck-form]').forEach(input => input.addEventListener('change',update)); update();
  }
  const grid=document.querySelector('[data-colony-grid]');
  let latestWorld=grid?.dataset.world?JSON.parse(grid.dataset.world):null;
  function production(world){
    if(!world)return;
    latestWorld=world;
    const working=world.flow.remaining>0;
    const states={pump:working&&world.buffer.crude<world.storage.crude.capacity,
      pump_station:working&&world.buffer.crude<world.storage.crude.capacity,
      refinery:working&&world.network.fuel&&world.profile.fuel_pct>0&&world.buffer.fuel<world.storage.fuel.capacity,
      workshop:working&&world.network.parts&&world.profile.parts_pct>0&&world.buffer.parts<world.storage.parts.capacity,
      compressor:working&&world.network.booster,
      fuel_station:working&&world.network.catalyst&&world.profile.fuel_pct>0,
      battery:world.energy.current<world.energy.capacity};
    states.pump=states.pump||states.refinery||states.workshop;states.pump_station=states.pump;
    grid?.classList.toggle('production-on',states.pump);
    grid?.querySelectorAll('[data-kind]').forEach(node=>{
      const kind=node.dataset.kind,job=world.jobs.find(j=>j.kind===kind);
      node.dataset.process=job?'building':states[kind]?'working':'idle';
      node.dataset.processLabel=({pump:'◉ Добыча',pump_station:'◉ Добыча',refinery:'ϟ Топливо',workshop:'⚙ Детали',compressor:'↑ Поток',fuel_station:'ϟ Выход',battery:'✦ Заряд'})[kind]||'';
    });
    for(const [resource,item] of Object.entries(world.storage)){
      document.querySelectorAll(`[data-storage-meter="${resource}"]`).forEach(e=>e.style.setProperty('--storage',item.percent+'%'));
      document.querySelectorAll(`[data-storage-percent="${resource}"]`).forEach(e=>e.textContent=item.percent+'%');
      document.querySelectorAll(`[data-collection-preview="${resource}"]`).forEach(e=>e.textContent='Заберём '+item.take+' · останется готово '+item.remaining+' · резерв рынка '+item.reserved);
    }
    const take=Object.values(world.storage).reduce((sum,item)=>sum+item.take,0);
    document.querySelectorAll('[data-buffer-total]').forEach(e=>e.textContent=take);
    const button=document.querySelector('.collect-all');
    if(button){button.disabled=take===0;button.title=take?'Собрать до заполнения склада; излишек останется в буферах':'Свободного места или готовых ресурсов пока нет';}
  }
  document.addEventListener('colony:render',()=>production(latestWorld));production(latestWorld);
  document.querySelectorAll('.talent-path select').forEach(select=>{
    const effect=select.parentElement.querySelector('[data-leaf-effect]');
    const update=()=>{if(effect)effect.textContent=select.value===''?'Талант пока не выбран':select.selectedOptions[0].textContent;};
    select.addEventListener('change',update);update();
  });
  let offset=0, syncing=false;
  const initialJobs=document.querySelectorAll('[data-job-timer]').length;
  async function sync() {
    if (!document.querySelector('[data-worker-status]') || document.hidden || syncing) return;
    syncing=true;
    try {
      const response=await fetch('/api/colony/sync',{method:'POST',headers:{'X-CSRF-Token':csrf,'Accept':'application/json'}});
      if (!response.ok) throw new Error('Сеть недоступна');
      const {farm,world}=await response.json(); offset=world.now-Date.now()/1000;
      for (const [resource,amount] of Object.entries(farm.resources)) document.querySelectorAll(`[data-stat-resource="${resource}"]`).forEach(e => e.textContent=amount);
      document.querySelector('[data-stat-oil]').title=farm.oil+' OIL';
      document.querySelector('[data-stat-oil]').textContent=new Intl.NumberFormat('ru-RU',{maximumFractionDigits:8}).format(farm.oil);
      for (const [resource,amount] of Object.entries(world.buffer)) document.querySelectorAll(`[data-buffer="${resource}"]`).forEach(e => e.textContent=amount);
      production(world);
      const healthy=world.now-(world.worker.heartbeat||0)<10;
      document.querySelector('[data-worker-status]').textContent=healthy?'Автомайнинг включён · блок раз в 30 с':'Фоновый узел недоступен · проверьте worker на сервере';
      document.querySelectorAll('[data-job-timer]').forEach(e => {
        const job=world.jobs.find(j=>String(j.id)===e.dataset.jobId);
        if (job) e.dataset.jobTimer=job.ready_at||0;
      });
      if (world.jobs.length<initialJobs && !allocationEdited && !document.querySelector('.colony-grid.is-editing')) location.reload();
    } catch (_) { document.querySelector('[data-worker-status]').textContent='Соединение прервано. Прогресс сохранён; повторяем подключение.'; }
    finally { syncing=false; }
  }
  function timers() {
    const now=Date.now()/1000+offset;
    document.querySelectorAll('[data-job-timer]').forEach(e => {
      const end=Number(e.dataset.jobTimer);e.textContent=end?Math.max(0,Math.ceil(end-now))+' с · применится автоматически':'Ждём автоматического блока';
    });
  }
  let cardsEdited=false;
  if(deck)document.addEventListener('change',()=>cardsEdited=true);
  async function chestDelivery(){
    const pending=[...document.querySelectorAll('[data-order-status=pending]')];
    if(!pending.length||document.hidden)return;
    try{
      const response=await fetch('/api/cards/orders',{cache:'no-store'});if(!response.ok)return;
      const {orders}=await response.json();
      for(const element of pending){
        const order=orders.find(o=>String(o.id)===element.dataset.chestOrder);
        if(order?.status==='opened'){
          if(!cardsEdited){location.replace(location.pathname+'?delivered='+order.id+'#latest-draw');return;}
          element.dataset.orderStatus='opened';element.querySelector('span').textContent='Карты доставлены. Сохраните изменения колоды, затем обновите страницу.';
        }
      }
    }catch(_){}
  }
  setInterval(chestDelivery,5000);
  sync();setInterval(sync,10000);setInterval(timers,1000);
})();
