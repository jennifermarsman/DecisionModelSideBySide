const state = {
  config: null,
  scenario: null,
  labels: [],
  running: false,
  controller: null,
};

const $ = (sel, root = document) => root.querySelector(sel);
const panels = {
  openai: $('.panel[data-model="openai"]'),
  decision: $('.panel[data-model="decision"]'),
};
const field = (model, name) => $(`[data-field="${name}"]`, panels[model]);

const fmt = (ms) => `${(ms / 1000).toFixed(3)} s`;
const fmtMs = (ms) => `${ms.toFixed(3)} ms`;
const money = (usd) => `$${usd.toFixed(8)}`;

async function init() {
  state.config = await (await fetch('/api/config')).json();
  $('#mock-badge').hidden = !state.config.mock;
  field('openai', 'name').textContent = state.config.openai_model;
  field('decision', 'name').textContent = state.config.decision_model;

  const select = $('#scenario');
  for (const [key, s] of Object.entries(state.config.scenarios)) {
    select.add(new Option(s.name, key));
  }
  select.value = 'expenses';
  select.addEventListener('change', () => selectScenario(select.value));
  selectScenario(select.value);

  $('#add-label').addEventListener('submit', (e) => {
    e.preventDefault();
    const input = $('#new-label');
    const name = input.value.trim();
    if (name && !state.labels.some((l) => l.name.toLowerCase() === name.toLowerCase())) {
      state.labels.push({ name, description: '' });
      renderLabels();
    }
    input.value = '';
  });
  $('#reset-labels').addEventListener('click', () => selectScenario(state.scenario));
  $('#run').addEventListener('click', () => (state.running ? stop() : run()));
}

function selectScenario(key) {
  state.scenario = key;
  const s = state.config.scenarios[key];
  $('#scenario-desc').textContent = s.description;
  state.labels = s.labels.map((l) => ({ ...l }));
  renderLabels();
  resetPanels();
}

function renderLabels() {
  const list = $('#labels');
  list.replaceChildren();
  state.labels.forEach((label, i) => {
    const li = document.createElement('li');
    li.title = label.description || label.name;
    li.append(label.name);
    const remove = document.createElement('button');
    remove.textContent = '×';
    remove.title = `Remove ${label.name}`;
    remove.disabled = state.running;
    remove.addEventListener('click', () => {
      state.labels.splice(i, 1);
      renderLabels();
    });
    li.append(remove);
    list.append(li);
  });
}

function resetPanels() {
  for (const model of Object.keys(panels)) {
    field(model, 'wall').textContent = fmt(0);
    field(model, 'calls').textContent = '0';
    field(model, 'model').textContent = fmt(0);
    field(model, 'avg').textContent = fmtMs(0);
    field(model, 'cost').textContent = money(0);
    field(model, 'tokens').textContent = '0';
    field(model, 'pricing').textContent = 'USD estimates; rates shown after the first response.';
    field(model, 'relative-cost').textContent = '';
    field(model, 'bar').style.width = '0';
    field(model, 'completion').textContent = '';
    field(model, 'relative').textContent = '';
    field(model, 'prompt').textContent = 'Press Run to start.';
    field(model, 'results').replaceChildren();
  }
}

function setRunning(running) {
  state.running = running;
  $('#run').textContent = running ? 'Stop' : 'Run';
  for (const el of document.querySelectorAll('#scenario, #new-label, #add-label button, #reset-labels')) {
    el.disabled = running;
  }
  renderLabels();
}

async function run() {
  if (!state.labels.length) {
    alert('Add at least one label first.');
    return;
  }
  resetPanels();
  setRunning(true);
  state.controller = new AbortController();
  const { signal } = state.controller;
  const labels = state.labels.map((l) => ({ ...l }));

  try {
    // Both models run at the same time; each side classifies its items one call at a time.
    const results = await Promise.all(
      Object.keys(panels).map((model) => runSide(model, state.scenario, labels, signal)),
    );
    showRelative(results);
  } catch (err) {
    if (err.name !== 'AbortError') console.error(err);
  } finally {
    setRunning(false);
  }
}

function stop() {
  state.controller?.abort();
}

async function* readEvents(response) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let pending = '';
  let ended = false;
  try {
    while (true) {
      const { value, done } = await reader.read();
      pending += decoder.decode(value, { stream: !done });
      let newline;
      while ((newline = pending.indexOf('\n')) !== -1) {
        const line = pending.slice(0, newline);
        pending = pending.slice(newline + 1);
        if (line.trim()) yield JSON.parse(line);
      }
      if (done) {
        ended = true;
        if (pending.trim()) throw new Error('Incomplete response from server.');
        return;
      }
    }
  } finally {
    try {
      if (!ended) await reader.cancel();
    } finally {
      reader.releaseLock();
    }
  }
}

async function runSide(model, scenario, labels, signal) {
  let calls = 0;
  let wallMs = 0;
  let complete = false;
  let modelMs = 0;
  let costUsd = 0;
  let tokens = 0;
  let costAvailable = true;
  let costUnavailableReason = '';
  let usageAvailable = true;
  try {
    const resp = await fetch(`/api/run/${model}/${scenario}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ labels }),
      signal,
    });
    if (!resp.ok) {
      const data = await resp.json();
      throw new Error(data.error || `HTTP ${resp.status}`);
    }
    let total = null;
    for await (const data of readEvents(resp)) {
      if (signal.aborted) break;
      if (data.type === 'heartbeat') continue;
      if (data.type === 'calling') {
        field(model, 'prompt').classList.remove('muted');
        field(model, 'prompt').textContent = data.prompt;
        continue;
      }
      if (data.type === 'error') throw new Error(data.error);
      if (data.type === 'done') {
        wallMs = data.wall_ms;
        complete = total !== null && calls === total;
        break;
      }
      if (data.type !== 'result') throw new Error('Unknown server event.');
      total = data.total;
      wallMs = data.wall_ms;
      field(model, 'wall').textContent = fmt(wallMs);
      calls += 1;
      modelMs += data.model_ms;
      field(model, 'calls').textContent = String(calls);
      field(model, 'model').textContent = fmt(modelMs);
      field(model, 'avg').textContent = fmtMs(modelMs / calls);
      if (Number.isFinite(data.cost_usd) && data.cost_usd >= 0) {
        costUsd += data.cost_usd;
      } else {
        costAvailable = false;
        costUnavailableReason ||= data.cost_unavailable_reason || 'Token usage or pricing unavailable.';
      }
      if (data.usage && [data.usage.input_tokens, data.usage.output_tokens]
        .every((count) => Number.isInteger(count) && count >= 0)) {
        tokens += data.usage.input_tokens + data.usage.output_tokens;
      } else {
        usageAvailable = false;
      }
      field(model, 'cost').textContent = costAvailable ? money(costUsd) : 'Unavailable';
      field(model, 'tokens').textContent = usageAvailable ? tokens.toLocaleString() : 'Unavailable';
      const pricing = data.pricing;
      field(model, 'pricing').textContent = pricing
        ? `${pricing.basis}: $${pricing.input}/1M input, $${pricing.output}/1M output`
          + (pricing.cached_input !== undefined ? `, $${pricing.cached_input}/1M cached input` : '')
          + (pricing.cache_write !== undefined ? `, $${pricing.cache_write}/1M cache writes` : '')
          + (pricing.checked_on ? `. Verified ${pricing.checked_on}.` : '.')
        : data.cost_unavailable_reason || 'Token usage or pricing unavailable.';
      if (!costAvailable) {
        field(model, 'pricing').textContent += ` Total unavailable: ${costUnavailableReason}`;
      }
      field(model, 'bar').style.width = `${(100 * calls) / total}%`;
      addResult(model, data.text, data.label, data.model_ms, false);
    }
    if (!complete && !signal.aborted) throw new Error('Run ended before completion.');
  } catch (err) {
    if (err.name !== 'AbortError') {
      addResult(model, 'Request failed', 'Error', null, true);
      field(model, 'prompt').textContent = String(err);
    }
  }
  field(model, 'wall').textContent = fmt(wallMs);
  if (complete) field(model, 'completion').textContent = 'Done';
  return { model, wallMs, complete, costUsd: costAvailable && calls > 0 ? costUsd : null };
}

function addResult(model, text, label, ms, isError) {
  const li = document.createElement('li');
  if (isError) li.className = 'error';
  const span = document.createElement('span');
  span.className = 'text';
  span.textContent = text;
  const tag = document.createElement('span');
  tag.className = 'tag';
  tag.textContent = label;
  li.append(span, tag);
  if (ms !== null) {
    const time = document.createElement('span');
    time.className = 'time';
    time.textContent = fmt(ms);
    li.append(time);
  }
  const list = field(model, 'results');
  list.append(li);
  list.scrollTop = list.scrollHeight;
}

function showRelative(results) {
  const [a, b] = results;
  if (!a.complete || !b.complete) return;
  const [fast, slow] = a.wallMs <= b.wallMs ? [a, b] : [b, a];
  const ratio = (slow.wallMs / fast.wallMs).toFixed(2);
  const diff = fmt(slow.wallMs - fast.wallMs);
  field(fast.model, 'relative').textContent = `Finished first: ${ratio}× faster (${diff} less wall clock time)`;
  field(slow.model, 'relative').textContent = `Finished second: ${ratio}× slower (${diff} more wall clock time)`;
  if (![a.costUsd, b.costUsd].every((cost) => Number.isFinite(cost) && cost >= 0)) {
    for (const result of results) {
      field(result.model, 'relative-cost').textContent = 'Cost comparison unavailable: usage or pricing is missing.';
    }
    return;
  }
  if (a.costUsd === b.costUsd) {
    for (const result of results) {
      field(result.model, 'relative-cost').textContent = 'Same estimated cost.';
    }
    return;
  }
  const [cheaper, dearer] = a.costUsd < b.costUsd ? [a, b] : [b, a];
  const savings = money(dearer.costUsd - cheaper.costUsd);
  const costRatio = cheaper.costUsd > 0 ? (dearer.costUsd / cheaper.costUsd).toFixed(2) : null;
  field(cheaper.model, 'relative-cost').textContent = costRatio
    ? `Estimated cost: ${costRatio}× lower (${savings} less)`
    : `Lower estimated cost (${savings} less)`;
  field(dearer.model, 'relative-cost').textContent = costRatio
    ? `Estimated cost: ${costRatio}× higher (${savings} more)`
    : `Higher estimated cost (${savings} more)`;
}

init();
