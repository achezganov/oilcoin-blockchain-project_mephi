'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const rootDirectory = process.env.OILCOIN_ROOT || path.resolve(__dirname, '..');

function element() {
  return {
    dataset: {}, style: {setProperty() {}}, classList: {toggle() {}, add() {}, remove() {}},
    listeners: new Map(),
    addEventListener(kind, handler) {
      const handlers = this.listeners.get(kind) || [];
      handlers.push(handler); this.listeners.set(kind, handlers);
    },
    querySelector() { return element(); },
    querySelectorAll() { return []; },
  };
}

async function campaignListenersRemainBoundOnce() {
  const stage = element(); stage.dataset.stage = '1';
  const nodes = new Map();
  const data = {
    match: null, waiting: null, completed: 0,
    stages: [{id: 1, unlocked: true, complete: false, boss: false}],
    world: {energy: {current: 10}, now: 1000},
    daily: {day: '2026-10-06', reset: 2000, id: 0, name: 'Boss', hint: 'Hint', theme: 'reef'},
  };
  const root = element();
  root.dataset = {endpoint: 'campaign', initial: JSON.stringify(data)};
  root.querySelector = selector => {
    if (!nodes.has(selector)) nodes.set(selector, element());
    return nodes.get(selector);
  };
  root.querySelectorAll = selector => selector === '[data-stage]' ? [stage] : [];
  const intervals = new Map();
  const context = {
    document: {
      hidden: false, body: element(),
      querySelector: selector => selector === '[data-arena-root]' ? root : {content: 'test-csrf'},
    },
    sessionStorage: {getItem() { return null; }, setItem() {}},
    URLSearchParams,
    fetch: async () => ({ok: true, json: async () => structuredClone(data)}),
    setInterval(fn, delay) { intervals.set(delay, fn); }, setTimeout() {},
  };
  vm.runInNewContext(fs.readFileSync(path.join(rootDirectory, 'static/arena.js'), 'utf8'), context);
  for (let tick = 0; tick < 100; tick++) await intervals.get(1000)();
  assert.equal(stage.listeners.get('click').length, 1, 'stage listeners multiplied while polling');
  assert.equal(nodes.get('[data-daily]').listeners.get('click').length, 1,
               'daily boss listeners multiplied while polling');
}

function longLedgerChartDoesNotExceedArgumentLimit() {
  const values = Array.from({length: 200000}, (_, index) => index);
  values[values.length - 1] = Number.MAX_VALUE;
  let points = 0;
  let minimumY = Infinity;
  const graphics = new Proxy({}, {
    get(_, name) {
      if (name === 'createLinearGradient') return () => ({addColorStop() {}});
      if (name === 'lineTo') return (x, y) => {
        assert.ok(Number.isFinite(x) && Number.isFinite(y));
        minimumY = Math.min(minimumY, y); points++;
      };
      return () => {};
    },
    set() { return true; },
  });
  const canvas = {dataset: {chart: 'supply', color: '#d6ef78'},
                  clientWidth: 600, clientHeight: 250, getContext: () => graphics};
  const context = {
    document: {
      querySelector: () => null,
      querySelectorAll: selector => selector === 'canvas[data-chart]' ? [canvas] : [],
      getElementById: id => id === 'chart-data'
        ? {textContent: JSON.stringify({block_indexes: values, supply: values})} : null,
    },
    window: {devicePixelRatio: 1, addEventListener() {}}, Intl,
  };
  vm.runInNewContext(fs.readFileSync(path.join(rootDirectory, 'static/app.js'), 'utf8'), context);
  assert.ok(points >= values.length, 'chart did not draw the ledger');
  assert.ok(minimumY < canvas.clientHeight/2, 'an overflow collapsed the graph to its baseline');
}

async function constructionRefreshPreservesUnsavedAllocation() {
  const fuel = element(); fuel.value = '0';
  const parts = element(); parts.value = '0';
  const oil = element(), status = element();
  let reloads = 0;
  const world = {now: 1000, jobs: [], storage: {}, buffer: {}, profile: {},
                 flow: {remaining: 0}, network: {placed: {}}, energy: {current: 10, capacity: 10},
                 worker: {heartbeat: 1000}};
  const context = {
    document: {
      hidden: false, addEventListener() {},
      getElementById(id) { return id === 'fuel-pct' ? fuel : id === 'parts-pct' ? parts : null; },
      querySelector(selector) {
        if (selector === 'meta[name=csrf-token]') return {content: 'test-csrf'};
        if (selector === '[data-worker-status]') return status;
        if (selector === '[data-stat-oil]') return oil;
        if (['[data-fuel-output]', '[data-parts-output]', '[data-crude-output]'].includes(selector)) return element();
        return null;
      },
      querySelectorAll(selector) { return selector === '[data-job-timer]' ? [element()] : []; },
    },
    window: {addEventListener() {}},
    location: {hash: '', reload() { reloads++; }},
    fetch: async () => ({ok: true, json: async () => ({farm: {resources: {}, oil: 50}, world})}),
    setInterval() {}, Intl,
  };
  vm.runInNewContext(fs.readFileSync(path.join(rootDirectory, 'static/colony.js'), 'utf8'), context);
  fuel.value = '50';
  fuel.listeners.get('input').forEach(listener => listener());
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(reloads, 0, 'completed construction discarded an unsaved allocation');
  assert.equal(fuel.value, '50');
}

(async () => {
  await campaignListenersRemainBoundOnce();
  longLedgerChartDoesNotExceedArgumentLimit();
  await constructionRefreshPreservesUnsavedAllocation();
  console.log('3 client regression checks passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
