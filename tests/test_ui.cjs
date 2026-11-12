const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { test } = require('node:test');
const vm = require('node:vm');

const source = readFileSync(`${__dirname}/../static/app.js`, 'utf8')
  .replace(/^init\(\);$/m, '');

function setup(fetch) {
  const fields = {};
  function element() {
    return {
      textContent: '', style: {}, children: [],
      classList: { remove() {} },
      append(...children) { this.children.push(...children); },
      replaceChildren(...children) { this.children = children; },
    };
  }
  const panels = Object.fromEntries(['openai', 'decision'].map((model) => {
    fields[model] = {};
    return [model, {
      querySelector(selector) {
        const name = selector.match(/data-field="([^"]+)"/)[1];
        return fields[model][name] ??= element();
      },
    }];
  }));
  const context = vm.createContext({
    document: {
      querySelector: (selector) => panels[selector.match(/data-model="([^"]+)"/)[1]],
      createElement: element,
    },
    TextDecoder, fetch,
  });
  vm.runInContext(source, context);
  vm.runInContext('resetPanels()', context);
  return { context, fields };
}

function eventStream() {
  const encoder = new TextEncoder();
  let controller;
  const body = new ReadableStream({ start(value) { controller = value; } });
  return {
    response: { ok: true, body },
    send(event) { controller.enqueue(encoder.encode(`${JSON.stringify(event)}\n`)); },
    close() { controller.close(); },
  };
}

const resultEvent = (overrides = {}) => ({
  type: 'result', text: 'sample', label: 'Support', model_ms: 10,
  wall_ms: 50, total: 2, cost_usd: 0.00001,
  usage: { input_tokens: 10, output_tokens: 1 },
  pricing: { input: 2, output: 10, basis: 'Standard' },
  ...overrides,
});
const flush = () => new Promise(setImmediate);

for (const model of ['openai', 'decision']) {
  test(`${model} streams prompts and totals, completing only on the server's done event`, async () => {
    const stream = eventStream();
    let requests = 0;
    const { context, fields } = setup(async (url, options) => {
      requests += 1;
      assert.equal(url, `/api/run/${model}/expenses`);
      assert.deepEqual(JSON.parse(options.body), { labels: [] });
      return stream.response;
    });
    assert.equal(fields[model].avg.textContent, '0.000 ms');
    const result = vm.runInContext(
      `runSide('${model}', 'expenses', [], { aborted: false })`, context,
    );
    stream.send({ type: 'calling', prompt: 'First request' });
    await flush();
    assert.equal(fields[model].prompt.textContent, 'First request');
    assert.equal(fields[model].calls.textContent, '0');
    assert.equal(fields[model].results.children.length, 0);
    stream.send(resultEvent());
    stream.send({ type: 'calling', prompt: 'Second request' });
    await flush();
    assert.equal(fields[model].calls.textContent, '1');
    assert.equal(fields[model].avg.textContent, '10.000 ms');
    assert.equal(fields[model].model.textContent, '0.010 s');
    assert.equal(fields[model].bar.style.width, '50%');
    assert.equal(fields[model].cost.textContent, '$0.00001000');
    assert.equal(fields[model].tokens.textContent, '11');
    assert.equal(fields[model].completion.textContent, '');
    stream.send(resultEvent({ model_ms: 25, wall_ms: 80 }));
    await flush();
    assert.equal(fields[model].avg.textContent, '17.500 ms');
    assert.equal(fields[model].model.textContent, '0.035 s');
    assert.equal(fields[model].completion.textContent, '');
    stream.send({ type: 'done', wall_ms: 85 });
    const completed = await result;
    assert.equal(completed.complete, true);
    assert.equal(completed.wallMs, 85);
    assert.equal(completed.costUsd, 0.00002);
    assert.equal(requests, 1);
    assert.equal(fields[model].wall.textContent, '0.085 s');
    assert.equal(fields[model].avg.textContent, '17.500 ms');
    assert.equal(fields[model].cost.textContent, '$0.00002000');
    assert.equal(fields[model].tokens.textContent, '22');
    assert.equal(fields[model].completion.textContent, 'Done');
    vm.runInContext('resetPanels()', context);
    assert.equal(fields[model].completion.textContent, '');
    assert.equal(fields[model].cost.textContent, '$0.00000000');
    assert.equal(fields[model].avg.textContent, '0.000 ms');
  });
}

test('both model streams start concurrently and comparisons use server times', async () => {
  const streams = { openai: eventStream(), decision: eventStream() };
  const requested = [];
  const { context, fields } = setup(async (url) => {
    const model = url.split('/')[3];
    requested.push(model);
    return streams[model].response;
  });
  const results = vm.runInContext(
    `Promise.all(['openai', 'decision'].map((model) =>
      runSide(model, 'expenses', [], { aborted: false })))`, context,
  );
  assert.deepEqual(requested, ['openai', 'decision']);
  for (const model of ['openai', 'decision']) {
    assert.equal(fields[model].calls.textContent, '0');
    assert.equal(fields[model].bar.style.width, '0');
    assert.equal(fields[model].results.children.length, 0);
    assert.equal(fields[model].completion.textContent, '');
  }
  for (const [model, wallMs, cost] of [['openai', 100, 0.002], ['decision', 200, 0.00002]]) {
    streams[model].send(resultEvent({ total: 1, cost_usd: cost }));
    streams[model].send({ type: 'done', wall_ms: wallMs });
  }
  context.results = await results;
  vm.runInContext('showRelative(results)', context);
  assert.match(fields.openai.relative.textContent, /2\.00× faster/);
  assert.match(fields.openai['relative-cost'].textContent, /100\.00× higher/);
  assert.match(fields.decision['relative-cost'].textContent, /100\.00× lower/);
});

test('missing usage prevents partial cost totals even if later calls report cost', async () => {
  const stream = eventStream();
  const { context, fields } = setup(async () => stream.response);
  const result = vm.runInContext(
    "runSide('openai', 'expenses', [], { aborted: false })", context,
  );
  stream.send(resultEvent({ cost_usd: null, usage: null, cost_unavailable_reason: 'No usage' }));
  stream.send(resultEvent());
  stream.send({ type: 'done', wall_ms: 100 });
  const completed = await result;
  assert.equal(completed.costUsd, null);
  assert.equal(fields.openai.cost.textContent, 'Unavailable');
  assert.equal(fields.openai.tokens.textContent, 'Unavailable');
  assert.match(fields.openai.pricing.textContent, /Total unavailable: No usage/);
  context.results = [completed, { model: 'decision', wallMs: 200, complete: true, costUsd: 0.01 }];
  vm.runInContext('showRelative(results)', context);
  assert.match(fields.openai['relative-cost'].textContent, /comparison unavailable/);
});

test('NDJSON parser handles split UTF-8 characters and multiple events per chunk', async () => {
  const bytes = new TextEncoder().encode('{"type":"calling","prompt":"café"}\n{"type":"heartbeat"}\n');
  const split = bytes.indexOf(0xc3) + 1;
  const body = new ReadableStream({
    start(controller) {
      controller.enqueue(bytes.slice(0, split));
      controller.enqueue(bytes.slice(split));
      controller.close();
    },
  });
  const { context } = setup();
  context.response = { body };
  const events = await vm.runInContext(
    '(async () => { const events = []; for await (const event of readEvents(response)) events.push(event); return events; })()',
    context,
  );
  assert.equal(events[0].prompt, 'café');
  assert.equal(events[1].type, 'heartbeat');
});

for (const outcome of ['http-error', 'network-error', 'abort', 'server-error', 'truncated']) {
  test(`${outcome} does not show Done`, async () => {
    const stream = eventStream();
    const { context, fields } = setup(async () => {
      if (outcome === 'http-error') {
        return { ok: false, json: async () => ({ error: 'Model failed' }) };
      }
      if (outcome === 'network-error' || outcome === 'abort') {
        const error = new Error('Request failed');
        if (outcome === 'abort') error.name = 'AbortError';
        throw error;
      }
      return stream.response;
    });
    const result = vm.runInContext(
      "runSide('openai', 'expenses', [], { aborted: false })", context,
    );
    if (outcome === 'server-error') stream.send({ type: 'error', error: 'Model failed' });
    if (outcome === 'truncated') stream.send(resultEvent({ total: 1 }));
    if (['server-error', 'truncated'].includes(outcome)) stream.close();
    assert.equal((await result).complete, false);
    assert.equal(fields.openai.completion.textContent, '');
  });
}

for (const [a, b, expected] of [
  [0, 0, 'Same estimated cost.'],
  [0.01, 0.01, 'Same estimated cost.'],
  [0, 0.01, 'Lower estimated cost'],
]) {
  test(`cost comparison handles ${a} and ${b} without an infinite ratio`, () => {
    const { context, fields } = setup();
    context.results = [
      { model: 'openai', wallMs: 100, complete: true, costUsd: a },
      { model: 'decision', wallMs: 200, complete: true, costUsd: b },
    ];
    vm.runInContext('showRelative(results)', context);
    assert.ok(fields.openai['relative-cost'].textContent.startsWith(expected));
    assert.doesNotMatch(fields.openai['relative-cost'].textContent, /Infinity|NaN/);
  });
}

test('incomplete runs do not show comparisons', () => {
  const { context, fields } = setup();
  context.results = [
    { model: 'openai', wallMs: 100, complete: false, costUsd: 0.01 },
    { model: 'decision', wallMs: 200, complete: true, costUsd: 0.001 },
  ];
  vm.runInContext('showRelative(results)', context);
  for (const model of ['openai', 'decision']) {
    assert.equal(fields[model].relative.textContent, '');
    assert.equal(fields[model]['relative-cost'].textContent, '');
  }
});
