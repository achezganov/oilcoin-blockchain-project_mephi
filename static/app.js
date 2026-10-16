'use strict';
(() => {
  const menu = document.querySelector('.menu-toggle');
  menu?.addEventListener('click', () => {
    const expanded = menu.getAttribute('aria-expanded') !== 'true';
    menu.setAttribute('aria-expanded', String(expanded));
    document.getElementById('main-nav')?.classList.toggle('is-open', expanded);
  });
  document.querySelectorAll('[data-dismiss]').forEach(button => button.addEventListener('click', () => button.closest('.flash')?.remove()));
  let toastTimer;
  function toast(message) {
    const element = document.getElementById('toast');
    if (!element) return;
    element.textContent = message;
    element.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { element.hidden = true; }, 4000);
  }
  document.querySelectorAll('[data-copy]').forEach(button => button.addEventListener('click', async () => {
    const value = button.dataset.copy || '';
    try {
      if (navigator.clipboard && window.isSecureContext) await navigator.clipboard.writeText(value);
      else {
        const field = document.createElement('textarea');
        field.value = value;
        field.style.position = 'fixed';
        field.style.opacity = '0';
        document.body.appendChild(field);
        field.select();
        const success = document.execCommand('copy');
        field.remove();
        button.focus();
        if (!success) throw new Error('Clipboard unavailable');
      }
      toast('Скопировано в буфер обмена');
    } catch (_) { toast('Не удалось скопировать. Выделите адрес и скопируйте его вручную.'); }
  }));
  document.querySelectorAll('form[data-busy]').forEach(form => form.addEventListener('submit', () => {
    const button = form.querySelector('button[type="submit"]');
    if (!button) return;
    button.disabled = true;
    button.dataset.originalText = button.textContent;
    button.textContent = form.dataset.busy;
    form.setAttribute('aria-busy', 'true');
  }));
  window.addEventListener('pageshow', () => {
    document.querySelectorAll('button[data-original-text]').forEach(button => {
      button.disabled = false;
      button.textContent = button.dataset.originalText;
      button.closest('form')?.removeAttribute('aria-busy');
    });
  });
  const form = document.getElementById('transfer-form');
  if (form) {
    const format = value => `${value.toLocaleString('ru-RU', { maximumFractionDigits: 8 })} OIL`;
    const update = () => {
      const amount = Number(form.elements.amount.value);
      const fee = Number(form.elements.fee.value);
      const validAmount = Number.isFinite(amount) && amount >= 0 ? amount : 0;
      const validFee = Number.isFinite(fee) && fee >= 0 ? fee : 0;
      document.getElementById('preview-amount').textContent = format(validAmount);
      document.getElementById('preview-fee').textContent = format(validFee);
      document.getElementById('preview-total').textContent = format(validAmount + validFee);
    };
    form.addEventListener('input', update);
    update();
  }
  const chartData = document.getElementById('chart-data');
  if (!chartData) return;
  let charts;
  try { charts = JSON.parse(chartData.textContent); } catch (_) { return; }
  const labels = charts.block_indexes || [];
  const compact = value => new Intl.NumberFormat('ru-RU', { notation: value >= 10000 ? 'compact' : 'standard', maximumFractionDigits: value > 0 && value < 1 ? 4 : 1 }).format(value);
  function drawChart(canvas) {
    const values = (charts[canvas.dataset.chart] || []).map(value => Number.isFinite(Number(value)) ? Number(value) : 0);
    const width = canvas.clientWidth;
    const height = canvas.clientHeight;
    if (!width || !height) return;
    const ratio = window.devicePixelRatio || 1;
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.scale(ratio, ratio);
    const padding = { left: 46, right: 15, top: 16, bottom: 28 };
    const innerWidth = width - padding.left - padding.right;
    const innerHeight = height - padding.top - padding.bottom;
    const highest = values.reduce((maximum, value) => Math.max(maximum, value), 1);
    const maximum = Number.isFinite(highest * 1.12) ? highest * 1.12 : highest;
    ctx.font = '10px -apple-system, BlinkMacSystemFont, sans-serif';
    ctx.textBaseline = 'middle';
    for (let step = 0; step <= 4; step += 1) {
      const y = padding.top + innerHeight * step / 4;
      ctx.strokeStyle = '#263548';
      ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(padding.left, y); ctx.lineTo(width - padding.right, y); ctx.stroke();
      ctx.fillStyle = '#7e93ad'; ctx.textAlign = 'right';
      ctx.fillText(compact(maximum * (1 - step / 4)), padding.left - 8, y);
    }
    if (!values.length) {
      ctx.fillStyle = '#98a9bd'; ctx.textAlign = 'center';
      ctx.fillText('Данные появятся после первого блока', width / 2, height / 2);
      return;
    }
    const pointX = i => padding.left + (values.length > 1 ? i / (values.length - 1) : 0.5) * innerWidth;
    const pointY = value => padding.top + innerHeight - value / maximum * innerHeight;
    const color = canvas.dataset.color || '#d6ef78';
    ctx.beginPath();
    values.forEach((value, i) => { if (i === 0) ctx.moveTo(pointX(i), pointY(value)); else ctx.lineTo(pointX(i), pointY(value)); });
    ctx.lineTo(pointX(values.length - 1), padding.top + innerHeight);
    ctx.lineTo(pointX(0), padding.top + innerHeight); ctx.closePath();
    const gradient = ctx.createLinearGradient(0, padding.top, 0, padding.top + innerHeight);
    gradient.addColorStop(0, color + '30'); gradient.addColorStop(1, color + '00');
    ctx.fillStyle = gradient; ctx.fill();
    ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.lineJoin = 'round'; ctx.beginPath();
    values.forEach((value, i) => { if (i === 0) ctx.moveTo(pointX(i), pointY(value)); else ctx.lineTo(pointX(i), pointY(value)); }); ctx.stroke();
    const step = Math.max(1, Math.ceil(values.length / 5));
    values.forEach((value, i) => {
      if (values.length < 40) { ctx.fillStyle = color; ctx.beginPath(); ctx.arc(pointX(i), pointY(value), 3, 0, Math.PI * 2); ctx.fill(); }
      if (i % step === 0 || i === values.length - 1) {
        ctx.fillStyle = '#7e93ad'; ctx.textAlign = 'center';
        ctx.fillText('#' + (labels[i] ?? i), pointX(i), height - 10);
      }
    });
  }
  const canvases = document.querySelectorAll('canvas[data-chart]');
  const drawAll = () => canvases.forEach(drawChart);
  drawAll();
  if ('ResizeObserver' in window) {
    const observer = new ResizeObserver(drawAll);
    canvases.forEach(canvas => observer.observe(canvas));
  } else window.addEventListener('resize', drawAll);
})();
