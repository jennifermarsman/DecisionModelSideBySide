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

// All times are displayed in seconds so they are directly comparable.
const fmt = (ms) => `${(ms / 1000).toFixed(3)} s`;

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
    field(model, 'avg').textContent = fmt(0);
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
    const resp = await fetch(`/api/data/${state.scenario}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ labels }),
      signal,
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || `HTTP ${resp.status}`);
    const { items, prompts } = data;
    // Both models run at the same time; each side classifies its items one call at a time.
    const results = await Promise.all(
      Object.keys(panels).map((model) => runSide(model, items, labels, signal, prompts)),
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

async function runSide(model, items, labels, signal, prompts) {
  const start = performance.now();
  let calls = 0;
  let modelMs = 0;
  let done = false;
  const tick = () => {
    if (done) return;
    field(model, 'wall').textContent = fmt(performance.now() - start);
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);

  try {
    for (const [index, text] of items.entries()) {
      if (signal.aborted) break;
      field(model, 'prompt').classList.remove('muted');
      field(model, 'prompt').textContent = prompts[index][model];
      const resp = await fetch(`/api/classify/${model}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, labels }),
        signal,
      });
      const data = await resp.json();
      if (!resp.ok) {
        addResult(model, text, 'Error', null, true);
        field(model, 'prompt').textContent = data.error || `HTTP ${resp.status}`;
        break;
      }
      calls += 1;
      modelMs += data.model_ms;
      field(model, 'calls').textContent = String(calls);
      field(model, 'model').textContent = fmt(modelMs);
      field(model, 'avg').textContent = fmt(modelMs / calls);
      field(model, 'bar').style.width = `${(100 * calls) / items.length}%`;
      addResult(model, text, data.label, data.model_ms, false);
    }
  } catch (err) {
    if (err.name !== 'AbortError') {
      addResult(model, 'Request failed', 'Error', null, true);
      field(model, 'prompt').textContent = String(err);
    }
  } finally {
    done = true;
  }
  const wallMs = performance.now() - start;
  field(model, 'wall').textContent = fmt(wallMs);
  const complete = calls === items.length;
  if (complete) field(model, 'completion').textContent = 'Done';
  return { model, wallMs, complete };
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
}

init();
