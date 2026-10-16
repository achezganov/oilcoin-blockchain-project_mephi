(() => {
  'use strict';
  const root=document.querySelector('[data-arena-root]');if(!root)return;
  const endpoint=root.dataset.endpoint||'arena',pve=endpoint==='campaign';
  const csrf=document.querySelector('meta[name=csrf-token]').content;
  let data=JSON.parse(root.dataset.initial),selected=null,busy=false,polling=false,dismissed=sessionStorage.getItem('oilcoin-dismissed-'+endpoint),lastHand='';
  let generation=0,lastEffects='',lastBoss='',lastDaily='',paintedMatch=null;
  const $=s=>root.querySelector(s),message=$('[data-arena-message]');
  const portrait=id=>{const e=document.createElement('span');e.className='card-portrait';e.style.setProperty('--portrait-x',((id%4)*100/3)+'%');e.style.setProperty('--portrait-y',Math.floor(id/4)*100+'%');return e;};
  async function command(action,fields={}){
    if(busy)return;busy=true;generation+=1;
    try{
      const response=await fetch('/'+endpoint+'/'+action,{method:'POST',headers:{'X-CSRF-Token':csrf,'Content-Type':'application/x-www-form-urlencoded'},body:new URLSearchParams(fields)});
      if(!response.headers.get('content-type')?.includes('application/json'))throw Error('Соединение прервано. Обновите страницу, если сессия истекла.');
      const result=await response.json();message.textContent=result.message||'';
      if(result.state)data=result.state;
      if(result.ok&&action==='play')selected=null;
      render();
    }catch(error){message.textContent=error.message;}finally{busy=false;}
  }
  function render(){
    const match=data.match,playing=match?.status==='active',result=match?.status==='finished'&&match.id!==dismissed&&!data.waiting;
    document.body.classList.toggle('arena-playing',!!playing);
    $('[data-lobby]').hidden=playing||result;$('[data-battle]').hidden=!playing;$('[data-result]').hidden=!result;
    $('[data-arena-energy]').textContent=data.world.energy.current;
    if(pve)renderCampaign();
    if($('[data-queue-status]'))$('[data-queue-status]').hidden=!data.waiting;
    function renderCampaign(){
    $('[data-campaign-progress]').textContent=data.completed;
    root.querySelectorAll('[data-stage]').forEach(button=>{
      const stage=data.stages.find(s=>s.id===Number(button.dataset.stage));
      button.disabled=!stage.unlocked||data.world.energy.current<3;
      button.classList.toggle('complete',stage.complete);
      button.querySelector('.stage-marker').textContent=stage.complete?'✓':stage.boss?'♛':stage.id;
      button.querySelector('[data-stage-status]').textContent=stage.complete?'Пройдено · тренировка':stage.unlocked?'Первое прохождение':'Пройдите этап '+(stage.id-1);
    });
    const daily=data.daily;
    $('[data-daily]').disabled=!daily.unlocked||data.world.energy.current<3;
    $('[data-daily-status]').textContent=daily.claimed?'Награда получена. Повторный бой — тренировка.':daily.unlocked?'Победите сегодня, чтобы забрать награду.':'Открывается после победы на этапе 4.';
    const left=Math.max(0,daily.reset-data.world.now);
    $('[data-daily-reset]').textContent='Новый босс через '+Math.floor(left/3600)+' ч '+Math.floor(left%3600/60)+' мин';
    if(lastDaily!==daily.day){
      lastDaily=daily.day;$('[data-daily-name]').textContent=daily.name;$('[data-daily-hint]').textContent=daily.hint;
      $('.daily-art').style.setProperty('--boss-x',daily.id*50+'%');$('.daily-boss').className='daily-boss theme-'+daily.theme;
    }
  }
  function effects(match){
    const field=$('[data-battle-field]'),layer=$('[data-effects]');
    field.dataset.theme=match.pve?.boss.theme||'reef';
    const boss=match.pve?.spec.boss?match.pve.boss:null,rule=$('[data-boss-rule]');
    rule.hidden=!boss;
    if(boss)rule.textContent=boss.power+' через '+(20-match.tick%20)+' с · '+boss.hint;
    const key=match.id+':'+match.tick;
    if(lastEffects!==key){
      lastEffects=key;
      for(const event of (match.events||[]).slice(0,60)){
        const spark=document.createElement('i');spark.className='combat-fx fx-'+event.kind;
        const y=x=>90-(match.side===0?x:100-x)*.8;
        spark.style.left=(event.lane===0?25:75)+'%';spark.style.top=y(event.x)+'%';
        spark.style.setProperty('--travel',((y(event.target)-y(event.x))/100*field.clientHeight)+'px');
        layer.append(spark);setTimeout(()=>spark.remove(),850);
      }
    }
    const power=match.boss_event,notice=$('[data-boss-power]');
    if(power&&match.tick-power.tick<=2&&lastBoss!==match.id+':'+power.tick){
      lastBoss=match.id+':'+power.tick;notice.textContent='⚡ '+power.name;notice.hidden=false;
      notice.className='boss-power power-'+power.theme;setTimeout(()=>notice.hidden=true,2200);
    }
  }
  root.querySelectorAll('[data-match-mode]:not([data-match-mode=cancel])').forEach(b=>b.disabled=!!data.waiting||data.world.energy.current<3);
    if(data.waiting)$('[data-queue-text]').textContent=data.waiting.room?'Код комнаты: '+data.waiting.room:'Ищем живого соперника…';
    if(result){
      $('[data-result-title]').textContent=match.winner===null?'Ничья':match.winner===match.side?'Победа!':'В этот раз соперник сильнее';
      $('[data-result-reward]').textContent=match.rewards[match.players[match.side].address]||'Бой завершён.';
    }
    if(!playing){lastHand='';return;}
    if(paintedMatch!==match.id){paintedMatch=match.id;selected=null;lastHand='';$('[data-units]').replaceChildren();$('[data-effects]').replaceChildren();$('[data-boss-power]').hidden=true;}
    const mine=match.players[match.side],enemy=match.players[1-match.side];
    $('[data-player-name="0"]').textContent=mine.label;$('[data-player-name="1"]').textContent=enemy.label;
    for(const [seat,player] of [[0,mine],[1,enemy]]){
      const element=$(`[data-player-avatar="${seat}"]`),[type,value]=(player.avatar||'adviser:0').split(':'),id=Number(value);
      if(element.dataset.avatar===player.avatar)continue;
      element.dataset.avatar=player.avatar;element.className='avatar '+(type==='boss'?'avatar-boss':type==='card'?'avatar-card':'avatar-adviser');
      element.style.setProperty('--portrait-x',(type==='boss'?id*50:(id%4)*100/3)+'%');
      element.style.setProperty('--portrait-y',Math.floor(id/4)*100+'%');
    }
    const remain=Math.max(0,120-match.tick);$('[data-battle-time]').textContent=Math.floor(remain/60)+':'+String(remain%60).padStart(2,'0');
    for(const [label,player] of [['own',mine],['enemy',enemy]])for(let lane=0;lane<2;lane++){
      const e=$(`[data-tower="${label}-${lane}"]`);e.querySelector('span').textContent=player.towers[lane]+' / '+(player.tower_max||300);e.classList.toggle('destroyed',player.towers[lane]<=0);e.style.setProperty('--tower-health',Math.max(0,100*player.towers[lane]/(player.tower_max||300))+'%');
    }
    $('[data-elixir-fill]').style.width=mine.elixir*10+'%';$('[data-elixir-label]').textContent=mine.elixir+' / 10 эликсира';
    const units=$('[data-units]'),ids=new Set(match.units.map(u=>String(u.id)));
    units.querySelectorAll('[data-unit]').forEach(e=>{if(!ids.has(e.dataset.unit)&&!e.classList.contains('unit-defeated')){e.classList.add('unit-defeated');setTimeout(()=>e.remove(),350);}});
    for(const unit of match.units){
      let e=units.querySelector(`[data-unit="${unit.id}"]`);
      if(!e){e=document.createElement('div');e.dataset.unit=unit.id;e.className='battle-unit'+(unit.side!==match.side?' unit-enemy':'');e.append(portrait(unit.card));const hp=document.createElement('div');hp.className='unit-hp';hp.append(document.createElement('i'));e.append(hp);units.append(e);}
      const x=match.side===0?unit.x:100-unit.x;
      e.style.top=(90-x*.8)+'%';e.style.left=((unit.lane===0?25:75)+(unit.id%3-1)*3)+'%';
      e.dataset.action=unit.action||'walk';e.classList.toggle('unit-healing',!!unit.healing);e.classList.toggle('unit-frozen',(unit.slow_until||0)>=match.tick);
      e.title=data.cards[unit.card].name+' · '+({walk:'движение',attack:'атака',siege:'атака башни',spawn:'высадка',idle:'ожидание'}[unit.action]||'бой')+' · '+unit.hp+' HP';
      e.querySelector('i').style.width=Math.max(0,unit.hp/unit.max_hp*100)+'%';
    }
    effects(match);
    const signature=match.id+':'+mine.hand_revision;
    if(signature!==lastHand){
      lastHand=signature;if(!mine.deck.slice(0,4).includes(selected))selected=null;
      $('[data-hand]').replaceChildren(...mine.deck.slice(0,4).map(id=>{
        const card=data.cards[id],button=document.createElement('button');button.className='hand-card';button.type='button';button.dataset.card=id;button.append(portrait(id));
        const cost=document.createElement('small');cost.textContent=card.cost;button.append(cost);
        const name=document.createElement('strong');name.textContent=card.name;button.append(name);const stats=mine.stats?.[String(id)]||card;button.title=card.role+' · '+card.cost+' эликсира · '+stats.hp+' HP · '+stats.attack+' урона · дальность '+stats.range;
        button.addEventListener('click',()=>{selected=selected===id?null:id;render();});return button;
      }));
    }
    root.querySelectorAll('[data-card]').forEach(e=>{e.disabled=data.cards[Number(e.dataset.card)].cost>mine.elixir;e.classList.toggle('selected',Number(e.dataset.card)===selected);});
    $('[data-battle-field]').classList.toggle('card-ready',selected!==null);
    if(match.server_now-(data.world.worker.heartbeat||0)>12)message.textContent='Фоновый узел не отвечает. Бой восстановится после подключения сервера.';
  }
  root.querySelectorAll('[data-stage]').forEach(b=>b.addEventListener('click',()=>command('start',{mode:'stage',stage:b.dataset.stage})));
  $('[data-daily]')?.addEventListener('click',()=>command('start',{mode:'daily'}));
  root.querySelectorAll('[data-match-mode]').forEach(b=>b.addEventListener('click',()=>command('queue',{mode:b.dataset.matchMode})));
  $('[data-room-form]')?.addEventListener('submit',e=>{e.preventDefault();command('queue',{mode:'join',room:$('#room-code').value});});
  root.querySelectorAll('[data-deploy]').forEach(b=>b.addEventListener('click',()=>{if(selected!==null&&data.match?.status==='active')command('play',{match_id:data.match.id,card_id:selected,lane:b.dataset.deploy,revision:data.match.players[data.match.side].hand_revision});else message.textContent='Сначала выберите карту в руке.';}));
  $('[data-surrender]').addEventListener('click',()=>{if(data.match)command('surrender',{match_id:data.match.id});});
  $('[data-next-match]').addEventListener('click',()=>{dismissed=data.match?.id;sessionStorage.setItem('oilcoin-dismissed-'+endpoint,dismissed||'');render();});
  async function poll(){
    if(polling||busy||document.hidden)return;polling=true;const startedGeneration=generation;
    try{const response=await fetch('/api/'+endpoint,{cache:'no-store'});if(!response.ok)throw Error();const next=await response.json();if(startedGeneration===generation){data=next;render();}}catch(_){message.textContent='Связь прервана. Пытаемся подключиться снова; результат хранится на сервере.';}finally{polling=false;}
  }
  async function heartbeat(){if(document.hidden)return;try{await fetch('/'+endpoint+'/heartbeat',{method:'POST',headers:{'X-CSRF-Token':csrf}});}catch(_){}}
  render();heartbeat();setInterval(poll,1000);setInterval(heartbeat,10000);
})();
