'use strict';
(() => {
  const clock = document.querySelector('[data-server-now]');
  const offset = clock ? Number(clock.dataset.serverNow) * 1000 - Date.now() : 0;
  const formatTime = seconds => {
    if (seconds <= 0) return 'Готово — заберите результат';
    const hours = Math.floor(seconds / 3600);
    return (hours ? hours + ' ч ' : '') + Math.floor(seconds % 3600 / 60) + ' мин ' + seconds % 60 + ' с';
  };
  function tick() {
    const now = Math.floor((Date.now() + offset) / 1000);
    if (clock && document.querySelector('[data-collect-button]')) {
      const capacity = Number(clock.dataset.crudeCap);
      const ready = Math.min(capacity, Number(clock.dataset.crudeReady) + Math.floor(Math.max(0, now - Number(clock.dataset.crudeTick)) / 10) * Number(clock.dataset.crudeRate));
      const budget = now >= Number(clock.dataset.dayEnd) ? Number(clock.dataset.crudeBudgetLimit) : Number(clock.dataset.crudeBudget);
      const collectable = Math.min(ready, Math.max(0, capacity - Number(clock.dataset.crudeStore)), budget);
      document.querySelector('[data-collect-amount]').textContent = '+' + collectable;
      document.querySelector('[data-crude-amount]').textContent = ready;
      const button = document.querySelector('[data-collect-button]');
      if (!button.closest('form').hasAttribute('aria-busy')) button.disabled = collectable <= 0;
    }
    document.querySelectorAll('[data-countdown]').forEach(element => {
      const end = Number(element.dataset.countdown);
      if (end) element.textContent = formatTime(Math.max(0, end - now));
    });
    document.querySelectorAll('button[data-ready-at]').forEach(button => {
      if (Number(button.dataset.readyAt) <= now && !button.closest('form').hasAttribute('aria-busy')) button.disabled = false;
    });
  }
  tick();
  if (clock) window.setInterval(tick, 1000);
  document.querySelectorAll('[data-building]').forEach(link => link.addEventListener('click', () => {
    const target = document.getElementById('building-' + link.dataset.building);
    document.querySelectorAll('.building-panel').forEach(panel => { panel.open = panel === target; });
    document.querySelectorAll('[data-building]').forEach(other => other.classList.toggle('is-selected', other === link));
  }));
  document.querySelectorAll('.expedition-form').forEach(form => {
    const select = form.querySelector('select');
    const button = form.querySelector('button[type=submit]');
    const originalLabel = button.textContent;
    const update = () => {
      const option = select.selectedOptions[0];
      const power = Number(form.dataset.power) + Number(option.dataset.power);
      const loot = form.dataset.repeat === 'true' && form.dataset.dailyClaimed === 'true' ? 0 : Math.floor(Number(form.dataset.parts) * Number(option.dataset.loot) / (form.dataset.repeat === 'true' ? 400 : 100));
      const enough = power >= Number(form.dataset.required);
      const preview = form.querySelector('.tactic-preview');
      preview.textContent = `${power} силы · ${loot} деталей добычи − ${form.dataset.repair} обслуживания · ${enough ? 'победа обеспечена' : 'нужно усиление'}`;
      preview.classList.toggle('tactic-weak', !enough);
      const hasParts = Number(form.dataset.partsAvailable) >= Number(form.dataset.repair);
      const hasFuel = Number(form.dataset.fuelAvailable) >= Number(option.dataset.fuel);
      if (!form.hasAttribute('aria-busy')) {
        button.disabled = form.dataset.busyExpedition === 'true' || !enough || !hasFuel || !hasParts;
        button.textContent = !enough ? 'Нужно усиление' : !hasFuel ? 'Нужно топливо' : !hasParts ? 'Нужны детали' : originalLabel;
      }
    };
    select.addEventListener('change', update); update();
  });
})();
