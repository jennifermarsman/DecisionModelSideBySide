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
    assert.equal(fields[model].bar.style.width, '0');
    assert.equal(fields[model].results.children.length, 0);
    assert.equal(fields[model].completion.textContent, '');
    pending[model]({
      ok: true,
      json: async () => ({ label: 'Support', model_ms: 10 }),
    });
  }
  await results;
  for (const model of ['openai', 'decision']) {
    assert.equal(fields[model].calls.textContent, '1');
    assert.equal(fields[model].bar.style.width, '100%');
    assert.equal(fields[model].results.children.length, 1);
    assert.equal(fields[model].completion.textContent, 'Done');
  }
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
