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
      textContent: '',
      style: {},
      children: [],
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
    performance: { now: () => 100 },
    requestAnimationFrame() {},
    fetch,
    prompts: ['first', 'second'].map((text) => ({
      openai: `LLM request: ${text}`,
      decision: `Decision request: ${text}`,
    })),
  });
  vm.runInContext(source, context);
  vm.runInContext('resetPanels()', context);
  return { context, fields };
}

for (const model of ['openai', 'decision']) {
  test(`${model} shows Done only after its last sample and clears on reset`, async () => {
    let release;
    let calls = 0;
    const response = {
      ok: true,
      json: async () => ({ label: 'Support', model_ms: 10, prompt: 'Classify' }),
    };
    const { context, fields } = setup(async () => {
      calls += 1;
      if (calls === 2) await new Promise((resolve) => { release = resolve; });
      return response;
    });
    const result = vm.runInContext(
      `runSide('${model}', ['first', 'second'], [], { aborted: false }, prompts)`, context,
    );
    while (!release) await new Promise(setImmediate);
    assert.equal(fields[model].calls.textContent, '1');
    assert.equal(fields[model].completion.textContent, '');
    assert.equal(fields[model].cost.textContent, 'Unavailable');
    assert.equal(fields[model].prompt.textContent, context.prompts[1][model]);
    release();
    assert.equal((await result).complete, true);
    assert.equal(fields[model].completion.textContent, 'Done');
    const other = model === 'openai' ? 'decision' : 'openai';
    assert.equal(fields[other].completion.textContent, '');
    vm.runInContext('resetPanels()', context);
    assert.equal(fields[model].completion.textContent, '');
  });
}

test('both requests appear before either response, without advancing results or progress', async () => {
  const pending = {};
  const { context, fields } = setup((url) => new Promise((resolve) => {
    pending[url.split('/').pop()] = resolve;
  }));
  const results = vm.runInContext(
    `Promise.all(['openai', 'decision'].map((model) =>
      runSide(model, ['first'], [], { aborted: false }, prompts)))`, context,
  );
  for (const model of ['openai', 'decision']) {
    assert.equal(typeof pending[model], 'function');
    assert.equal(fields[model].prompt.textContent, context.prompts[0][model]);
    assert.equal(fields[model].calls.textContent, '0');
    assert.equal(fields[model].cost.textContent, '$0.00000000');
    assert.equal(fields[model].bar.style.width, '0');
    assert.equal(fields[model].results.children.length, 0);
    assert.equal(fields[model].completion.textContent, '');
    pending[model]({
      ok: true,
      json: async () => ({
        label: 'Support',
        model_ms: 10,
        cost_usd: model === 'openai' ? 0.002 : 0.00002,
        usage: { input_tokens: 100, output_tokens: 10 },
        pricing: { input: 2, output: 10, basis: 'Standard', checked_on: '2026-11-06' },
      }),
    });
  }
  await results;
  for (const model of ['openai', 'decision']) {
    assert.equal(fields[model].calls.textContent, '1');
    assert.equal(fields[model].bar.style.width, '100%');
    assert.equal(fields[model].results.children.length, 1);
    assert.equal(fields[model].completion.textContent, 'Done');
    assert.equal(fields[model].tokens.textContent, '110');
  }
  context.results = await results;
  vm.runInContext('showRelative(results)', context);
  assert.match(fields.openai['relative-cost'].textContent, /100\.00× higher/);
  assert.match(fields.decision['relative-cost'].textContent, /100\.00× lower/);
  assert.equal(fields.openai.cost.textContent, '$0.00200000');
  vm.runInContext('resetPanels()', context);
  assert.equal(fields.openai.cost.textContent, '$0.00000000');
  assert.equal(fields.openai['relative-cost'].textContent, '');
});

test('cost totals aggregate completed responses, not pending calls', async () => {
  let release;
  let calls = 0;
  const { context, fields } = setup(async () => {
    calls += 1;
    if (calls === 2) await new Promise((resolve) => { release = resolve; });
    return {
      ok: true,
      json: async () => ({
        label: 'Support', model_ms: 10, cost_usd: 0.00001,
        usage: { input_tokens: 10, output_tokens: 1 },
      }),
    };
  });
  const result = vm.runInContext(
    "runSide('openai', ['first', 'second'], [], { aborted: false }, prompts)", context,
  );
  while (!release) await new Promise(setImmediate);
  assert.equal(fields.openai.cost.textContent, '$0.00001000');
  assert.equal(fields.openai.tokens.textContent, '11');
  assert.equal(fields.openai.model.textContent, '0.010 s');
  assert.equal(fields.openai.avg.textContent, '0.010 s');
  release();
  assert.equal((await result).costUsd, 0.00002);
  assert.equal(fields.openai.cost.textContent, '$0.00002000');
  assert.equal(fields.openai.tokens.textContent, '22');
  assert.equal(fields.openai.model.textContent, '0.020 s');
  assert.equal(fields.openai.avg.textContent, '0.010 s');
});

test('missing usage prevents a partial total and ratio even if later calls report cost', async () => {
  let calls = 0;
  const { context, fields } = setup(async () => {
    calls += 1;
    return {
      ok: true,
      json: async () => calls === 1
        ? { label: 'Support', model_ms: 10, cost_usd: null, cost_unavailable_reason: 'No usage' }
        : {
          label: 'Support', model_ms: 10, cost_usd: 0.01,
          usage: { input_tokens: 10, output_tokens: 1 },
          pricing: { input: 2, output: 10, basis: 'Standard' },
        },
    };
  });
  const result = await vm.runInContext(
    "runSide('openai', ['first', 'second'], [], { aborted: false }, prompts)", context,
  );
  assert.equal(result.costUsd, null);
  assert.equal(fields.openai.cost.textContent, 'Unavailable');
  assert.equal(fields.openai.tokens.textContent, 'Unavailable');
  assert.match(fields.openai.pricing.textContent, /Total unavailable: No usage/);
  context.results = [result, { model: 'decision', wallMs: 100, complete: true, costUsd: 0.01 }];
  vm.runInContext('showRelative(results)', context);
  assert.match(fields.openai['relative-cost'].textContent, /comparison unavailable/);
});

for (const [a, b, expected] of [
  [0, 0, 'Same estimated cost.'],
  [0.01, 0.01, 'Same estimated cost.'],
  [0, 0.01, 'Lower estimated cost'],
]) {
  test(`cost comparison handles costs ${a} and ${b} without an infinite ratio`, () => {
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

test('incomplete runs do not show a cost comparison', () => {
  const { context, fields } = setup();
  context.results = [
    { model: 'openai', wallMs: 100, complete: false, costUsd: 0.01 },
    { model: 'decision', wallMs: 200, complete: true, costUsd: 0.001 },
  ];
  vm.runInContext('showRelative(results)', context);
  assert.equal(fields.openai['relative-cost'].textContent, '');
  assert.equal(fields.decision['relative-cost'].textContent, '');
});

for (const outcome of ['http-error', 'network-error', 'abort']) {
  test(`${outcome} does not show Done`, async () => {
    const { context, fields } = setup(async () => {
      if (outcome === 'http-error') {
        return { ok: false, json: async () => ({ error: 'Model failed' }) };
      }
      const error = new Error('Request failed');
      if (outcome === 'abort') error.name = 'AbortError';
      throw error;
    });
    const result = await vm.runInContext(
      "runSide('openai', ['sample'], [], { aborted: false }, prompts)", context,
    );
    assert.equal(result.complete, false);
    assert.equal(fields.openai.completion.textContent, '');
  });
}
