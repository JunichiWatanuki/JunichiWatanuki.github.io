import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const commonSource = await readFile(new URL('../js/common.js', import.meta.url), 'utf8');
const homeSource = await readFile(new URL('../index.js', import.meta.url), 'utf8');
const oldPath = 'data/contents/blog/e2d3-recollection.html';
const newPath = 'data/contents/blog/2026-11-24.html';
const indexUrl = 'https://example.test/index.json';

function indexData(path, title, date) {
  return {
    menu: [{ label: 'ブログ', href: 'index.html#blog', children: [{ label: title, href: path }] }],
    posts: [{ title, date, type: 'blog', path }],
  };
}

// A minimal DOM for the methods used by common.js and index.js; no browser
// globals or prototypes are modified permanently, even if a test fails.
class Element {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.attributes = {};
    this.classList = { add() {} };
  }
  append(...children) {
    for (const child of children) {
      child.parentElement = this;
      this.children.push(child);
    }
  }
  replaceChildren(...children) {
    this.children = [];
    this.append(...children);
  }
  setAttribute(name, value) { this.attributes[name] = value; }
  getAttribute(name) { return this.attributes[name] ?? null; }
}

function links(element) {
  return [element, ...element.children.flatMap(links)].filter(node => node.tagName === 'A');
}

function browser(location, dataset, fetch) {
  const selectors = [
    '[data-top-nav]', '[data-mobile-nav]', '[data-category-nav]',
    '[data-latest-posts]', '[data-popular-posts]', '[data-archive-blog]',
    '[data-archive-training]', '[data-archive-sample]',
  ];
  const elements = Object.fromEntries(selectors.map(selector => [selector, new Element('div')]));
  return {
    elements,
    globals: {
      location: { href: location },
      window: { addEventListener() {} },
      document: {
        body: { dataset },
        createElement: tag => new Element(tag),
        querySelector: selector => elements[selector] ?? null,
      },
      fetch,
    },
  };
}

async function withBrowser(page, run) {
  const previous = Object.fromEntries(Object.keys(page.globals).map(key =>
    [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  try {
    for (const [key, value] of Object.entries(page.globals)) {
      Object.defineProperty(globalThis, key, { value, writable: true, configurable: true });
    }
    return await run();
  } finally {
    for (const [key, descriptor] of Object.entries(previous)) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor);
      else delete globalThis[key];
    }
    for (const [key, descriptor] of Object.entries(previous)) {
      assert.deepEqual(Object.getOwnPropertyDescriptor(globalThis, key), descriptor);
    }
  }
}

let moduleId = 0;
function moduleUrl(source) {
  return `data:text/javascript;base64,${Buffer.from(source).toString('base64')}#${++moduleId}`;
}

async function loadHome() {
  // Only the import specifier changes; both modules' executable code is real.
  const specifier = "'./js/common.js'";
  assert.equal(homeSource.split(specifier).length, 2, 'expected one common.js import');
  await import(moduleUrl(homeSource.replace(specifier, JSON.stringify(moduleUrl(commonSource)))));
}

function fakeCache(initialData) {
  let origin = initialData;
  let cached;
  const calls = [];
  return {
    calls,
    update(data) { origin = data; },
    fetch: async (url, options) => {
      const address = String(url);
      calls.push({ address, options });
      assert.equal(address, indexUrl);
      // Model a still-fresh cached response. no-cache requests revalidate and
      // receive the changed origin data; this is not a real HTTP cache test.
      if (!cached || options?.cache === 'no-cache') cached = structuredClone(origin);
      return { ok: true, json: async () => structuredClone(cached) };
    },
  };
}

function assertRendered(page, path, title) {
  for (const selector of ['[data-category-nav]', '[data-latest-posts]',
    '[data-popular-posts]', '[data-archive-blog]']) {
    const renderedLinks = links(page.elements[selector]);
    assert.equal(renderedLinks.length, 1, selector);
    assert.equal(renderedLinks[0].href, new URL(path, indexUrl).href, selector);
    assert.notEqual(renderedLinks[0].href, new URL(path === oldPath ? newPath : oldPath, indexUrl).href, selector);
    const text = node => [node.textContent ?? '', ...node.children.flatMap(text)];
    assert.ok(text(renderedLinks[0]).includes(title), selector);
  }
  assert.equal(links(page.elements['[data-category-nav]'])[0].textContent, title);
  for (const selector of ['[data-top-nav]', '[data-mobile-nav]']) {
    assert.equal(links(page.elements[selector])[0].textContent, 'ブログ');
  }
}

test('home revalidates index.json and replaces stale menu and post links', async () => {
  const cache = fakeCache(indexData(oldPath, 'e2d3-recollection', '日付不明'));
  const oldPage = browser('https://example.test/index.html', { root: './', index: './index.json' }, cache.fetch);
  await withBrowser(oldPage, loadHome);
  assertRendered(oldPage, oldPath, 'e2d3-recollection');

  cache.update(indexData(newPath, 'なつかしいE2D3', '2026-11-24'));
  const newPage = browser('https://example.test/index.html', { root: './', index: './index.json' }, cache.fetch);
  await withBrowser(newPage, loadHome);
  assert.equal(cache.calls.length, 2);
  assertRendered(newPage, newPath, 'なつかしいE2D3');
  for (const call of cache.calls) {
    assert.equal(call.address, indexUrl);
    assert.equal(call.options?.cache, 'no-cache', 'index.json must be revalidated');
  }
});

test('nested article resolves and revalidates the root index', async () => {
  const cache = fakeCache(indexData(newPath, 'なつかしいE2D3', '2026-11-24'));
  const page = browser(`https://example.test/${newPath}`,
    { root: '../../../', index: '../../../index.json' }, cache.fetch);
  const data = await withBrowser(page, async () => {
    const { initCommon } = await import(moduleUrl(commonSource));
    return initCommon();
  });
  assert.equal(cache.calls.length, 1);
  assert.equal(cache.calls[0].address, indexUrl);
  assert.equal(cache.calls[0].options?.cache, 'no-cache');
  assert.equal(data.posts[0].path, newPath);
  const categoryLink = links(page.elements['[data-category-nav]'])[0];
  assert.equal(categoryLink.href, `https://example.test/${newPath}`);
  assert.equal(categoryLink.getAttribute('aria-current'), 'page');
});

test('browser mocks are restored when the operation throws', async () => {
  const page = browser('https://example.test/index.html', {}, async () => {});
  const descriptors = Object.fromEntries(Object.keys(page.globals).map(key =>
    [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  await assert.rejects(withBrowser(page, () => { throw new Error('intentional failure'); }), /intentional failure/);
  for (const [key, descriptor] of Object.entries(descriptors)) {
    assert.deepEqual(Object.getOwnPropertyDescriptor(globalThis, key), descriptor);
  }
});
