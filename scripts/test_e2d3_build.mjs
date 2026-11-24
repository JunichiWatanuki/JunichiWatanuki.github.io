import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import { spawn, spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { parse } from 'csv-parse/sync';
import { stringify } from 'csv-stringify/sync';

const repo = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const cli = path.join(repo, 'scripts/e2d3_build.mjs');
const contrib = process.env.E2D3_CONTRIB_DIR;
const runtime = process.env.E2D3_RUNTIME_DIR;
const ready = Boolean(contrib && runtime);
const root = fs.mkdtempSync(path.join(os.tmpdir(), 'greenlog-e2d3-test-'));
test.after(() => fs.rmSync(root, { recursive: true, force: true }));
const sample = '"湘南OWS2.5km大会(男子)","湘南OWS2.5km大会(女子)","　","　"\n"年代階級","完泳記録(HH:MM:SS)","男子","女子"\n"02_高校生","02:00:00～02:14:59","2","1"\n"02_高校生","02:15:00～02:29:59","3","1"\n';
function createSite() {
  const dir = fs.mkdtempSync(path.join(root, 'site-'));
  fs.mkdirSync(path.join(dir, 'data/contents/blog'), { recursive: true });
  return dir;
}
function graph(template, data) {
  const raw = Buffer.from(JSON.stringify({ template, data }), 'utf8');
  return { id: createHash('sha256').update(raw).digest('hex'), encoded: raw.toString('base64') };
}
function writeArticle(site, name, graphs) {
  const article = path.join(site, 'data/contents/blog', name);
  fs.mkdirSync(path.dirname(article), { recursive: true });
  const base = path.join(site, 'data/contents/blog/_e2d3');
  const html = graphs.map(g => `<section class="main-e2d3" data-e2d3-spec="${g.encoded}"><iframe class="main-e2d3-frame" src="${path.relative(path.dirname(article), path.join(base, g.id + '.html')).split(path.sep).join('/')}"></iframe></section>`).join('\n');
  fs.writeFileSync(article, `<!doctype html><html><body>${html}</body></html>`);
}
function run(site, extra = []) {
  // Test fixtures are synthetic pages; the real Python builder writes this
  // manifest from recognized Markdown fences, never from raw HTML.
  const values = [];
  const walk = dir => {
    for (const item of fs.readdirSync(dir, { withFileTypes: true })) {
      const p = path.join(dir, item.name);
      if (item.name === '_e2d3') continue;
      if (item.isDirectory()) walk(p);
      else if (item.isFile() && item.name.endsWith('.html')) {
        for (const match of fs.readFileSync(p, 'utf8').matchAll(/data-e2d3-spec="([^"]*)"/g)) values.push(match[1]);
      }
    }
  };
  walk(path.join(site, 'data/contents/blog'));
  fs.writeFileSync(path.join(site, '.greenlog-e2d3-input.json'), JSON.stringify({format: 'greenlog-e2d3-input-v1', specs: values}));
  return spawnSync(process.execPath, [cli, '--site-dir', site, ...(contrib ? ['--contrib-dir', contrib] : []), ...(runtime ? ['--runtime-dir', runtime] : []), ...extra], { encoding: 'utf8', maxBuffer: 10 * 1024 * 1024 });
}
function success(p) { assert.equal(p.status, 0, p.stderr); }
function out(site, filename) { return path.join(site, 'data/contents/blog/_e2d3', filename); }

test('empty site needs no upstream checkout and existing unowned directory is never deleted', () => {
  const site = createSite(); success(run(site));
  fs.mkdirSync(out(site, '')); fs.writeFileSync(out(site, 'asset.txt'), 'leave me');
  const r = run(site); assert.notEqual(r.status, 0); assert.match(r.stderr, /already contains blog files/);
  assert.equal(fs.readFileSync(out(site, 'asset.txt'), 'utf8'), 'leave me');
});

test('raw HTML that resembles an embed is ignored when it is not in the fence manifest', { skip: !ready }, () => {
  const site = createSite(), g = graph('barchart-javascript', 'name,age\nA,10\n');
  writeArticle(site, 'x.html', [g]);
  fs.appendFileSync(path.join(site, 'data/contents/blog/x.html'),
    '<section class="main-e2d3" data-e2d3-spec="not!base64">raw HTML</section>');
  const input = path.join(site, '.greenlog-e2d3-input.json');
  fs.writeFileSync(input, JSON.stringify({format: 'greenlog-e2d3-input-v1', specs: [g.encoded]}));
  const r = spawnSync(process.execPath, [cli, '--site-dir', site, '--contrib-dir', contrib,
    '--runtime-dir', runtime], { encoding: 'utf8' });
  success(r);
  assert.ok(fs.existsSync(out(site, `${g.id}.html`)));
  assert.ok(!fs.existsSync(input));
});

test('sample, separate and duplicate graphs, fallbacks and TSV are prepared reproducibly', { skip: !ready }, () => {
  const site = createSite();
  const a = graph('bipartite-graph-original', sample);
  const b = graph('barchart-javascript', 'name,age\nA,10\nB,20\n');
  const invalid = graph('bipartite-graph-original', 'x,y,z,w\nx,y,z,w\na,b,broken,1\n');
  const tsv = graph('parallel-coordinates', 'wrong,shape\na,b\n');
  const pie = graph('piechart-javascript', 'A,B\n12,8\n');
  const line = graph('multi-series-line-chart', 'wrong,A,B,C\n20260101,1,2,3\n20260102,4,5,6\n');
  const c3 = graph('c3js-line-chart', 'data1,30,200,100,400,150,250\ndata2,50,20,10,40,15,25\n');
  writeArticle(site, 'root.html', [a, b, a, invalid, tsv, pie, line, c3]);
  writeArticle(site, 'level/深い/nested.html', [a]);
  success(run(site));
  const rows = parse(fs.readFileSync(out(site, `${a.id}/data.csv`)), { bom: true });
  assert.deepEqual(rows[2], ['', '', '0', '0']);
  assert.deepEqual(rows[3], ['02_高校生', '02:00:00～02:14:59', '2', '1']);
  assert.equal(rows.length, parse(sample, { bom: true, record_delimiter: ['\r\n', '\n', '\r'] }).length + 1);
  assert.doesNotMatch(fs.readFileSync(out(site, `${a.id}.html`), 'utf8'), /公式の既定データ/);
  const def = parse(spawnSync('git', ['-C', contrib, 'show', 'd48bd8256e95cfcc393d37e3e7769000dd738fc9:bipartite-graph-original/data.csv']).stdout, { bom: true });
  const actual = parse(fs.readFileSync(out(site, `${invalid.id}/data.csv`)), { bom: true });
  assert.deepEqual(actual, [...def.slice(0, 2), ['', '', '0', '0'], ...def.slice(2)]);
  assert.match(fs.readFileSync(out(site, `${invalid.id}.html`), 'utf8'), /公式の既定データ/);
  assert.match(fs.readFileSync(out(site, `${tsv.id}.html`), 'utf8'), /公式の既定データ/);
  assert.equal(parse(fs.readFileSync(out(site, `${tsv.id}/data.csv`)))[0].length, 5);
  assert.deepEqual(parse(fs.readFileSync(out(site, `${pie.id}/data.csv`))), [['A', 'B'], ['12', '8']]);
  assert.doesNotMatch(fs.readFileSync(out(site, `${pie.id}.html`), 'utf8'), /公式の既定データ/);
  assert.match(fs.readFileSync(out(site, `${line.id}.html`), 'utf8'), /公式の既定データ/);
  assert.equal(parse(fs.readFileSync(out(site, `${line.id}/data.csv`)))[0][0], 'date');
  assert.ok(fs.statSync(out(site, `${c3.id}/c3.js`)).size > 0);
  assert.equal(fs.readFileSync(out(site, `${b.id}/data.csv`), 'utf8'), 'name,age\nA,10\nB,20\n');
  assert.ok(fs.statSync(out(site, `${b.id}/main.js`)).size > 0);
  assert.ok(fs.statSync(out(site, 'runtime/e2d3.js')).size > 0);
  for (const pkg of ['e2d3','contrib','d3','jquery','requirejs','domReady','requirejs-text','js-yaml']) assert.ok(fs.statSync(out(site, `licenses/${pkg}-LICENSE`)).size > 0);
  assert.ok(!fs.existsSync(out(site, 'licenses/topojson-client-LICENSE')));
  const marker = fs.readFileSync(out(site, '.greenlog-e2d3-manifest.json'), 'utf8');
  success(run(site)); assert.equal(fs.readFileSync(out(site, '.greenlog-e2d3-manifest.json'), 'utf8'), marker);
  fs.unlinkSync(path.join(site, 'data/contents/blog/level/深い/nested.html'));
  writeArticle(site, 'root.html', [b]); success(run(site));
  assert.ok(!fs.existsSync(out(site, `${a.id}.html`))); assert.ok(fs.existsSync(out(site, `${b.id}.html`)));
  fs.writeFileSync(out(site, 'unexpected.txt'), 'do not delete');
  const r = run(site); assert.notEqual(r.status, 0); assert.match(r.stderr, /Unexpected/); assert.ok(fs.existsSync(out(site, 'unexpected.txt')));
});

test('invalid CSV variants fall back, including duplicate keys and negative numbers', { skip: !ready }, () => {
  const site = createSite();
  const cases = ['', 'a,b\n"unclosed', 'a,b,c,d\na,b,c,d\na,b,-1,2\n', 'a,b,c,d\na,b,c,d\na,b,1,2\na,b,3,4\n', 'a,b,c,d\na,b,c,d\na,,1,2\n', 'a,b,c\na,b,c\na,b,c\n'];
  const gs = cases.map(x => graph('bipartite-graph-original', x)); writeArticle(site, 'x.html', gs); success(run(site));
  for (const g of gs) assert.match(fs.readFileSync(out(site, `${g.id}.html`), 'utf8'), /公式の既定データ/);
});

test('rejects traversal, unknown templates/dependencies, malformed base64 and symlinks', { skip: !ready }, () => {
  for (const template of ['../bipartite-graph-original', 'bipartite-graph-original/../../bad', 'nonexistent', 'japan-map']) {
    const site = createSite(); writeArticle(site, 'x.html', [graph(template, 'a,b\nx,1\n')]);
    const r = run(site); assert.notEqual(r.status, 0, template); assert.match(r.stderr, /unsafe|Missing official|dependency/); assert.ok(!fs.existsSync(out(site, '')));
  }
  {
    const site = createSite(), a = graph('barchart-javascript', 'a,b\nx,1\n');
    writeArticle(site, 'x.html', [a]); const p = path.join(site, 'data/contents/blog/x.html');
    fs.writeFileSync(p, '<section class="main-e2d3" data-e2d3-spec="not!base64"></section>');
    assert.match(run(site).stderr, /base64/);
  }
  {
    const site = createSite(); fs.symlinkSync(os.tmpdir(), out(site, ''));
    assert.notEqual(run(site).status, 0); assert.ok(fs.lstatSync(out(site, '')).isSymbolicLink());
  }
});

const chromium = process.env.CHROMIUM || ['/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser'].find(p => fs.existsSync(p));
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

// Use the Chrome DevTools protocol instead of --dump-dom with virtual time.
// A fresh profile prevents the command from handing the page to an unrelated
// running browser, which can return successfully without producing a DOM.
async function openBrowser() {
  const profile = fs.mkdtempSync(path.join(root, 'chrome-profile-'));
  const child = spawn(chromium, ['--headless', '--no-sandbox', '--disable-gpu',
    '--disable-dev-shm-usage', '--disable-background-networking', '--no-first-run',
    '--no-default-browser-check', '--no-proxy-server', '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1',
    '--remote-debugging-port=0', '--remote-allow-origins=*', `--user-data-dir=${profile}`,
    '--window-size=400,600', 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
  let stderr = '', exit;
  child.stderr.on('data', chunk => { stderr = (stderr + chunk.toString()).slice(-8000); });
  child.on('error', error => { exit = error.message; });
  child.on('exit', (code, signal) => { exit = `code=${code}, signal=${signal}`; });
  const stop = async () => {
    if (child.exitCode === null && child.signalCode === null) {
      child.kill('SIGTERM');
      for (let i = 0; i < 50 && child.exitCode === null && child.signalCode === null; i++) await sleep(100);
      if (child.exitCode === null && child.signalCode === null) child.kill('SIGKILL');
    }
  };
  try {
    const portFile = path.join(profile, 'DevToolsActivePort');
    const deadline = Date.now() + 30000;
    while (!fs.existsSync(portFile) && !exit && Date.now() < deadline) await sleep(100);
    if (!fs.existsSync(portFile)) throw new Error(`Chromium did not start (${exit || 'timeout'}).\n${stderr}`);
    const port = Number(fs.readFileSync(portFile, 'utf8').split('\n')[0]);
    const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
    const target = targets.find(item => item.type === 'page');
    if (!target?.webSocketDebuggerUrl) throw new Error('Chromium did not provide a page target');
    const socket = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('Chromium DevTools connection timed out')), 10000);
      socket.addEventListener('open', () => { clearTimeout(timer); resolve(); }, { once: true });
      socket.addEventListener('error', () => { clearTimeout(timer); reject(new Error('Chromium DevTools connection failed')); }, { once: true });
    });
    let seq = 0;
    const pending = new Map();
    const events = [];
    socket.addEventListener('message', event => {
      const message = JSON.parse(event.data), item = pending.get(message.id);
      if (!item) { if (message.method?.startsWith('Network.')) events.push(message); return; }
      pending.delete(message.id); clearTimeout(item.timer);
      if (message.error) item.reject(new Error(message.error.message)); else item.resolve(message.result);
    });
    function command(method, params = {}) {
      return new Promise((resolve, reject) => {
        const id = ++seq;
        const timer = setTimeout(() => { pending.delete(id); reject(new Error(`Chromium ${method} timed out`)); }, 10000);
        pending.set(id, { resolve, reject, timer });
        socket.send(JSON.stringify({ id, method, params }));
      });
    }
    return { command, events: () => events, close: async () => { socket.close(); await stop(); }, diagnostics: () => stderr };
  } catch (error) { await stop(); throw error; }
}

test('browser draws the first 2/1 sample row and confines the SVG width', { skip: !ready || !chromium, timeout: 150000 }, async () => {
  const site = createSite(), g = graph('bipartite-graph-original', sample);
  const bar = graph('barchart-javascript', 'name,age\nA,10\nB,20\n');
  const parallel = graph('parallel-coordinates', 'wrong,shape\na,b\n');
  const fixed = graph('simple-barchart', '1,95\n2,80\n3,82\n');
  const c3 = graph('c3js-line-chart', 'data1,30,200,100,400,150,250\ndata2,50,20,10,40,15,25\n');
  writeArticle(site, 'nested/article.html', [g, bar, parallel, fixed, c3]); success(run(site));
  const data = parse(sample, { bom: true, record_delimiter: ['\r\n', '\n', '\r'] });
  const male = data.slice(2).filter(r => r[0] === '02_高校生').reduce((sum,r) => sum + Number(r[2]), 0);
  const female = data.slice(2).filter(r => r[0] === '02_高校生').reduce((sum,r) => sum + Number(r[3]), 0);
  const snapshot = `(() => { var area=document.getElementById('e2d3-chart-area');
    var error=document.getElementById('e2d3-error'); if(!area || !error) return null;
    var v={url:location.href}; ['SalesAttempts','Sales'].forEach(function(id){
      var xs=[].slice.call(document.querySelectorAll('#'+id+' .part0 .mainbar'));
      var row=xs.find(function(x){return x.querySelector('.barlabel')?.textContent==='02_高校生'});
      v[id]=row?.querySelector('.barvalue')?.textContent;
    });
    var s=area.querySelector('svg'); v.svg=!!s; v.viewBox=s?.getAttribute('viewBox');
    v.width=s?.getBoundingClientRect().width; v.viewport=document.documentElement.clientWidth;
    var bars=[].slice.call(document.querySelectorAll('rect.bar'));
    v.bars=bars.length; v.barRight=bars.map(x=>x.getBoundingClientRect().right);
    v.paths=area.querySelectorAll('path').length; v.error=!error.hidden; return v; })()`;
  const server = http.createServer((req, res) => {
    let filename;
    try { filename = path.join(site, decodeURIComponent(new URL(req.url, 'http://localhost').pathname).replace(/^\/project-name\//, '/')); } catch { res.writeHead(400).end(); return; }
    if (!filename.startsWith(site + path.sep) || !fs.existsSync(filename) || !fs.statSync(filename).isFile()) { res.writeHead(404).end(); return; }
    if (filename.endsWith('.html')) { res.setHeader('Content-Type', 'text/html; charset=utf-8'); res.end(fs.readFileSync(filename)); }
    else { res.setHeader('Content-Type', filename.endsWith('.js') ? 'application/javascript' : filename.endsWith('.css') ? 'text/css' : 'text/plain'); res.end(fs.readFileSync(filename)); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    browser = await openBrowser();
    async function render(id, isReady) {
      const url = `http://127.0.0.1:${server.address().port}/project-name/data/contents/blog/_e2d3/${id}.html`;
      await browser.command('Page.navigate', { url });
      const deadline = Date.now() + 20000;
      let value = null, lastError = '';
      while (Date.now() < deadline) {
        try {
          const result = await browser.command('Runtime.evaluate', { expression: snapshot, returnByValue: true });
          value = result.result?.value;
        } catch (error) { lastError = error.message; } // Navigation can temporarily replace the JS context.
        if (value?.url === url && value.error)
          throw new Error(`Chromium reported a chart error for ${id}: ${JSON.stringify(value)}\n${browser.diagnostics()}`);
        if (value?.url === url && isReady(value)) {
          // Let the templates' transitions finish before checking SVG bounds.
          await sleep(1000);
          const result = await browser.command('Runtime.evaluate', { expression: snapshot, returnByValue: true });
          return result.result?.value;
        }
        await sleep(100);
      }
      throw new Error(`Chromium did not finish drawing ${id}: ${JSON.stringify(value)} ${lastError}\n${browser.diagnostics()}`);
    }
    const value = await render(g.id, v => v.svg && v.viewBox && v.SalesAttempts && v.Sales);
    assert.equal(Number(value.SalesAttempts), male); assert.equal(Number(value.Sales), female);
    const [x, y, w, h] = value.viewBox.split(' ').map(Number);
    assert.ok(value.svg && x <= 0 && y <= 0 && x+w >= 1200 && y+h >= 571);
    assert.ok(value.width <= value.viewport, JSON.stringify(value)); assert.equal(value.error, false);
    const barValue = await render(bar.id, v => v.svg && v.viewBox && v.bars === 2); assert.equal(barValue.bars, 2); assert.ok(barValue.width <= barValue.viewport); assert.equal(barValue.error, false);
    const parallelValue = await render(parallel.id, v => v.svg && v.viewBox && v.paths > 0); assert.ok(parallelValue.svg && parallelValue.paths > 0); assert.ok(parallelValue.width <= parallelValue.viewport); assert.equal(parallelValue.error, false);
    const fixedValue = await render(fixed.id, v => v.svg && v.viewBox && v.bars === 3); assert.equal(fixedValue.bars, 3);
    assert.ok(fixedValue.barRight.every(right => right <= fixedValue.viewport + 1), JSON.stringify(fixedValue));
    const c3Value = await render(c3.id, v => v.svg && v.paths > 0);
    assert.ok(c3Value.svg && c3Value.paths > 0 && !c3Value.error, JSON.stringify(c3Value));
  } finally { if (browser) await browser.close(); await new Promise(resolve => server.close(resolve)); }
});

// Read the immutable official blob used by the builder, not a mutable checkout
// file. These fixtures intentionally differ from the article and the defaults.
function official(template, name) {
  const result = spawnSync('git', ['-C', contrib, 'show', `d48bd8256e95cfcc393d37e3e7769000dd738fc9:${template}/${name}`], { encoding: 'utf8', maxBuffer: 10 * 1024 * 1024 });
  success(result);
  return result.stdout;
}
function csv(rows, delimiter = ',') {
  return stringify(rows, { delimiter, record_delimiter: '\n' });
}
function fourTemplateFixtures() {
  const infiniteName = 'InfiniteElementMethod_E2D3-LIG_03';
  const raw = official(infiniteName, 'data.csv');
  assert.ok(raw.endsWith('"1') && !raw.endsWith('"1"'), 'official malformed final quote changed');
  assert.throws(() => parse(raw));
  const infiniteDefaults = parse(raw + '"', { bom: true });
  const infiniteRows = infiniteDefaults.map(row => [...row]);
  const oldLeft = infiniteRows[0][0], oldValue = Number(infiniteRows[0][2]);
  const infiniteMarker = 'ARTICLE_INFINITE_FIRST_MARKER';
  for (const row of infiniteRows) if (row[0] === oldLeft) row[0] = infiniteMarker;
  infiniteRows[0][2] = '987654';
  const infiniteSum = infiniteRows.filter(row => row[0] === infiniteMarker).reduce((n, row) => n + Number(row[2]), 0);
  assert.ok(infiniteSum > 987654 && Number(infiniteRows[0][2]) !== oldValue);
  const infinite = graph(infiniteName, csv(infiniteRows));
  const brokenInfinite = graph(infiniteName, raw); // the official unclosed quote is invalid article input

  const forceName = 'force-directed-graph';
  const forceDefaults = parse(official(forceName, 'data.tsv'), { delimiter: '\t', bom: true });
  const forceRows = forceDefaults.map(row => [...row]);
  const forceMarker = 'ARTICLE_FORCE_LABEL_MARKER';
  assert.notEqual(forceRows[1][1], forceMarker);
  forceRows[1][1] = forceMarker;
  const forceTsv = graph(forceName, csv(forceRows, '\t'));
  const forceCsv = graph(forceName, csv(forceRows));
  const badForceTsvRows = forceRows.map(row => [...row]);
  badForceTsvRows[1][3] = '9999';
  const badForceTsv = graph(forceName, csv(badForceTsvRows, '\t'));
  const badForceCsv = graph(forceName, 'id,label,source,target,value,color,size,width,distance\n0,broken,0,9999,1,red,1,1,1\n');

  const mapName = 'jp013-tokyo';
  const mapDefaults = parse(official(mapName, 'data.csv'), { bom: true });
  const mapRows = mapDefaults.map(row => [...row]);
  const mapRow = mapRows.find(row => row[0] === '千代田区');
  assert.ok(mapRow); assert.notEqual(mapRow[1], '9137'); mapRow[1] = '9137';
  const map = graph(mapName, csv(mapRows));
  const badMap = graph(mapName, '市区町村,ランナー\n千代田区,invalid\n');

  const frostName = 'virtual-frostbite-halfmarathon';
  const frostRows = parse(official(frostName, 'data.csv'), { bom: true });
  assert.equal(frostRows.length - 1, 15);
  assert.ok(!frostRows.some(row => row[0] === 'Tajitsu'));
  const frost = graph(frostName, csv(frostRows));
  const badFrost = graph(frostName, 'athlete,HH,MM,SS,acolor\nArticle,0,99,00,ffffff\n');
  return { infinite, infiniteRows, infiniteDefaults, infiniteMarker, infiniteSum, brokenInfinite,
    forceTsv, forceCsv, forceRows, forceDefaults, forceMarker, badForceTsv, badForceCsv,
    map, mapRows, mapDefaults, badMap, frost, frostRows, badFrost };
}
function assertFixtureOutput(site, fixture) {
  const accepted = [[fixture.infinite, fixture.infiniteRows], [fixture.forceTsv, fixture.forceRows],
    [fixture.forceCsv, fixture.forceRows], [fixture.map, fixture.mapRows], [fixture.frost, fixture.frostRows]];
  const rejected = [[fixture.brokenInfinite, fixture.infiniteDefaults], [fixture.badForceTsv, fixture.forceDefaults],
    [fixture.badForceCsv, fixture.forceDefaults], [fixture.badMap, fixture.mapDefaults], [fixture.badFrost, fixture.frostRows]];
  for (const [g, rows] of accepted) {
    assert.deepEqual(parse(fs.readFileSync(out(site, `${g.id}/data.csv`), 'utf8'), { bom: true }), rows, g.id);
    assert.doesNotMatch(fs.readFileSync(out(site, `${g.id}.html`), 'utf8'), /公式の既定データ/, g.id);
  }
  for (const [g, rows] of rejected) {
    assert.deepEqual(parse(fs.readFileSync(out(site, `${g.id}/data.csv`), 'utf8'), { bom: true }), rows, g.id);
    assert.match(fs.readFileSync(out(site, `${g.id}.html`), 'utf8'), /公式の既定データ/, g.id);
  }
}

test('four official-template fixtures preserve article data and report invalid-input fallback', { skip: !ready }, () => {
  const site = createSite(), f = fourTemplateFixtures();
  const single = graph('InfiniteElementMethod_E2D3-LIG_03', '左,右,3,1\n');
  writeArticle(site, 'nested/article.html', [f.infinite, f.brokenInfinite, f.forceTsv, f.forceCsv,
    f.badForceTsv, f.badForceCsv, f.map, f.badMap, f.frost, f.badFrost, single]);
  success(run(site));
  assertFixtureOutput(site, f);
  assert.ok(fs.statSync(out(site, 'licenses/topojson-client-LICENSE')).size > 0);
  assert.deepEqual(parse(fs.readFileSync(out(site, `${single.id}/data.csv`))), [['左', '右', '3', '1']]);
  assert.doesNotMatch(fs.readFileSync(out(site, `${single.id}.html`), 'utf8'), /公式の既定データ/);
});

test('four official templates render article data and local resources with DNS blocked', { skip: !ready || !chromium, timeout: 150000 }, async () => {
  const site = createSite(), f = fourTemplateFixtures();
  writeArticle(site, 'nested/article.html', [f.infinite, f.forceTsv, f.forceCsv, f.map, f.frost]);
  success(run(site));
  const requests = [];
  const denied = new Set();
  const server = http.createServer((req, res) => {
    let filename, pathname;
    try {
      pathname = decodeURIComponent(new URL(req.url, 'http://localhost').pathname);
      if (!pathname.startsWith('/project-name/')) throw new Error('outside subpath');
      filename = path.resolve(site, '.' + pathname.slice('/project-name'.length));
    } catch { requests.push({ path: req.url, status: 400 }); res.writeHead(400).end(); return; }
    if (denied.has(pathname) || !filename.startsWith(site + path.sep) || !fs.existsSync(filename) || !fs.statSync(filename).isFile()) {
      requests.push({ path: pathname, status: 404 }); res.writeHead(404).end(); return;
    }
    requests.push({ path: pathname, status: 200 });
    const ext = path.extname(filename);
    res.setHeader('Content-Type', ({ '.html': 'text/html; charset=utf-8', '.js': 'application/javascript',
      '.css': 'text/css', '.jpg': 'image/jpeg', '.topojson': 'application/json' })[ext] || 'text/plain; charset=utf-8');
    res.end(fs.readFileSync(filename));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    browser = await openBrowser();
    async function render(g, expression, isReady) {
      const url = `http://127.0.0.1:${server.address().port}/project-name/data/contents/blog/_e2d3/${g.id}.html`;
      await browser.command('Page.navigate', { url });
      const deadline = Date.now() + 20000;
      let value, lastError = '';
      while (Date.now() < deadline) {
        try {
          const result = await browser.command('Runtime.evaluate', { expression, returnByValue: true });
          value = result.result?.value;
        } catch (error) { lastError = error.message; }
        if (value?.url === url && value.error) throw new Error(`Chart error: ${g.id} ${JSON.stringify(value)}\n${browser.diagnostics()}`);
        if (value?.url === url && isReady(value)) return value;
        await sleep(100);
      }
      throw new Error(`Chart timed out: ${g.id} ${JSON.stringify(value)} ${lastError}\n${browser.diagnostics()}`);
    }
    const common = `url: location.href, error: !document.getElementById('e2d3-error')?.hidden`;
    const infinite = await render(f.infinite, `(() => { const area=document.getElementById('e2d3-chart-area'); if(!area)return null;
      const bars=[...area.querySelectorAll('#SalesAttempts .part0 .mainbar')];
      const marker=bars.find(x=>x.querySelector('.barlabel')?.textContent===${JSON.stringify(f.infiniteMarker)});
      return {${common}, left:bars.length, right:area.querySelectorAll('#SalesAttempts .part1 .mainbar').length,
        links:area.querySelectorAll('#SalesAttempts .edges .edge').length, marker:marker?.querySelector('.barlabel')?.textContent,
        sum:marker?.__data__?.value}; })()`, v => v.left === 17 && v.right === 9 && v.links === 153 && v.marker);
    assert.equal(infinite.marker, f.infiniteMarker);
    assert.ok(Math.abs(infinite.sum - f.infiniteSum) < 1e-6, JSON.stringify(infinite));
    for (const g of [f.forceTsv, f.forceCsv]) {
      const force = await render(g, `(() => { const area=document.getElementById('e2d3-chart-area'); if(!area)return null;
        return {${common}, circles:area.querySelectorAll('svg circle').length, lines:area.querySelectorAll('svg line').length,
          labels:[...area.querySelectorAll('svg text')].map(x=>x.textContent)}; })()`,
        v => v.circles === 48 && v.lines === 59 && v.labels.length === 48 && v.labels.includes(f.forceMarker));
      assert.equal(force.labels.filter(x => x === f.forceMarker).length, 1);
    }
    const map = await render(f.map, `(() => { const area=document.getElementById('e2d3-chart-area'); if(!area)return null;
      const paths=[...area.querySelectorAll('path.states')]; const places=paths.filter(x=>x.__data__?.properties?.N03_004==='千代田区');
      return {${common}, paths:paths.length, places:places.map(x=>x.__data__.properties.data)}; })()`,
      v => v.paths === 150 && v.places?.length === 1 && Number(v.places[0]?.['ランナー']) === 9137);
    assert.equal(Number(map.places[0]['ランナー']), 9137);
    const base = `/project-name/data/contents/blog/_e2d3/${f.map.id}/`;
    for (const name of ['greenlog-topojson.js', 'jp013-tokyo.topojson']) {
      assert.ok(requests.some(r => r.path === base + name && r.status === 200), JSON.stringify(requests));
    }
    // SVG image elements do not expose HTMLImageElement.complete. Track the
    // actual Image request's response and completion via DevTools instead.
    await browser.command('Network.enable');
    const imageRequest = href => browser.events().find(e => e.method === 'Network.requestWillBeSent' &&
      e.params.type === 'Image' && e.params.request.url === href);
    const imageFinished = href => {
      const request = imageRequest(href);
      return request && browser.events().some(e => e.method === 'Network.loadingFinished' && e.params.requestId === request.params.requestId);
    };
    const frost = await render(f.frost, `(() => { const area=document.getElementById('e2d3-chart-area'); if(!area)return null;
      const image=area.querySelector('svg image'); return {${common}, svgs:area.querySelectorAll('svg').length,
        labels:[...area.querySelectorAll('svg g.runner text')].map(x=>x.textContent),
        href:image?.href?.baseVal || image?.getAttribute('href') || image?.getAttribute('xlink:href')}; })()`,
      v => v.svgs === 1 && v.labels.length === 15 && v.href && imageFinished(v.href));
    assert.deepEqual(frost.labels, f.frostRows.slice(1).map(row => row[0]));
    assert.ok(!frost.labels.includes('Tajitsu'));
    assert.ok(frost.href.endsWith(`/${f.frost.id}/map.jpg`), frost.href);
    const imageId = imageRequest(frost.href).params.requestId;
    assert.ok(browser.events().some(e => e.method === 'Network.responseReceived' && e.params.requestId === imageId && e.params.response.status === 200));
    assert.ok(!browser.events().some(e => e.method === 'Network.loadingFailed' && e.params.requestId === imageId));
    // SVGImageElement has no HTMLImageElement.complete/naturalWidth. Decode the
    // exact SVG href in an Image, and require the SVG request itself to succeed.
    const image = await browser.command('Runtime.evaluate', { expression: `(async () => {
      const svgImage=document.querySelector('#e2d3-chart-area svg image');
      const href=svgImage.href.baseVal; const img=new Image(); img.src=href;
      try { await img.decode(); } catch(e) { return {error:String(e), href}; }
      return {href, complete:img.complete, width:img.naturalWidth, height:img.naturalHeight,
        chartError:!document.getElementById('e2d3-error').hidden}; })()`, awaitPromise: true, returnByValue: true });
    assert.ok(image.result.value?.complete && image.result.value.width > 0 && image.result.value.height > 0, JSON.stringify(image));
    assert.equal(image.result.value.chartError, false);
    assert.ok(requests.some(r => r.path === `/project-name/data/contents/blog/_e2d3/${f.frost.id}/map.jpg` && r.status === 200), JSON.stringify(requests));

    await browser.command('Network.setCacheDisabled', { cacheDisabled: true });
    async function expectResourceError(g, resource) {
      const base = `/project-name/data/contents/blog/_e2d3/${g.id}/`;
      denied.add(base + resource);
      const url = `http://127.0.0.1:${server.address().port}/project-name/data/contents/blog/_e2d3/${g.id}.html`;
      await browser.command('Page.navigate', { url });
      let value;
      for (let i = 0; i < 150; i++) {
        try {
          const result = await browser.command('Runtime.evaluate', { expression: `({url:location.href,error:!document.getElementById('e2d3-error')?.hidden})`, returnByValue: true });
          value = result.result?.value;
        } catch (_) { /* Navigation can replace the execution context. */ }
        if (value?.url === url && value.error) break;
        await sleep(100);
      }
      assert.ok(value?.url === url && value.error, JSON.stringify(value));
      assert.ok(requests.some(r => r.path === base + resource && r.status === 404));
    }
    await expectResourceError(f.map, 'jp013-tokyo.topojson');
    await expectResourceError(f.frost, 'map.jpg');
  } finally { if (browser) await browser.close(); await new Promise(resolve => server.close(resolve)); }
});
