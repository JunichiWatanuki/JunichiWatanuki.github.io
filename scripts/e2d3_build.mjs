#!/usr/bin/env node
// Build isolated static E2D3 frames from the Markdown builder's temporary
// manifest, without executing contrib JavaScript in Node. Only the pinned
// upstream Git tree is accepted.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { TextDecoder } from 'node:util';
import { parse as csvParse } from 'csv-parse/sync';
import { stringify } from 'csv-stringify/sync';
import { validateTemplate } from './e2d3_validation.mjs';

const CONTRIB_COMMIT = 'd48bd8256e95cfcc393d37e3e7769000dd738fc9';
const RUNTIME_SHA256 = '6e953de0bd7613f987288690ee4c82a948a0eba4812d14f0a4edb9bb552b81c6';
const PACKAGE_SHA256 = '5282001264034b36955dcc8ff5811ea0e3b870608d965f462303c8158bcb375c';
const MARKER = '.greenlog-e2d3-manifest.json';
const LIMIT = 2 * 1024 * 1024;
const MAX_FILE = 16 * 1024 * 1024;
const MAX_TOTAL = 100 * 1024 * 1024;
const MAX_GRAPHS = 100;
const utf8 = new TextDecoder('utf-8', { fatal: true });
const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.dirname(here);
const digest = bytes => createHash('sha256').update(bytes).digest('hex');
const decode = (bytes, label) => { try { return utf8.decode(bytes); } catch { throw new Error(`${label}: invalid UTF-8`); } };
const escapeHtml = s => s.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#39;');

function assertNoSymlinks(p) {
  const absolute = path.resolve(p);
  let current = path.parse(absolute).root;
  for (const part of absolute.slice(current.length).split(path.sep).filter(Boolean)) {
    current = path.join(current, part);
    if (fs.existsSync(current) || fs.lstatSync(current, { throwIfNoEntry: false })) {
      if (fs.lstatSync(current).isSymbolicLink()) throw new Error(`Symlink is forbidden: ${current}`);
    }
  }
}
function regular(p, label, max = MAX_FILE) {
  assertNoSymlinks(p);
  const st = fs.lstatSync(p, { throwIfNoEntry: false });
  if (!st?.isFile()) throw new Error(`Missing regular ${label}: ${p}`);
  if (st.size > max) throw new Error(`${label} exceeds size limit: ${p}`);
  return fs.readFileSync(p);
}
function walk(dir, visitor) {
  for (const name of fs.readdirSync(dir).sort()) {
    const p = path.join(dir, name), st = fs.lstatSync(p);
    if (st.isSymbolicLink()) throw new Error(`Symlink is forbidden: ${p}`);
    if (st.isDirectory()) { visitor(p, true); walk(p, visitor); }
    else if (st.isFile()) visitor(p, false);
    else throw new Error(`Non-regular file: ${p}`);
  }
}
function checkedExisting(dir) {
  const st = fs.lstatSync(dir, { throwIfNoEntry: false });
  if (!st) return false;
  if (st.isSymbolicLink() || !st.isDirectory()) throw new Error(`Reserved _e2d3 path is not a directory: ${dir}`);
  const marker = path.join(dir, MARKER);
  if (!fs.existsSync(marker)) throw new Error(`Reserved _e2d3 path already contains blog files: ${dir}`);
  let manifest;
  try { manifest = JSON.parse(decode(regular(marker, 'ownership marker', LIMIT), marker)); }
  catch { throw new Error(`Invalid e2d3 ownership marker: ${marker}`); }
  if (manifest.format !== 'greenlog-e2d3-v1' || !manifest.files || Array.isArray(manifest.files) || typeof manifest.files !== 'object') throw new Error(`Invalid e2d3 ownership marker: ${marker}`);
  const expected = new Map(Object.entries(manifest.files));
  const dirs = new Set();
  for (const [rel, hash] of expected) {
    if (!safeRelative(rel) || !/^[a-f0-9]{64}$/.test(hash) || rel === MARKER) throw new Error('Invalid e2d3 ownership entry');
    let parent = path.posix.dirname(rel);
    while (parent !== '.') { dirs.add(parent); parent = path.posix.dirname(parent); }
  }
  walk(dir, (p, isDir) => {
    const rel = path.relative(dir, p).split(path.sep).join('/');
    if (isDir) { if (!dirs.has(rel)) throw new Error(`Unexpected directory in generated _e2d3: ${p}`); return; }
    if (rel === MARKER) return;
    const hash = expected.get(rel);
    if (!hash || digest(regular(p, 'generated file')) !== hash) throw new Error(`Unexpected or modified file in generated _e2d3: ${p}`);
    expected.delete(rel);
  });
  if (expected.size) throw new Error('Incomplete generated _e2d3 tree; refusing to delete it');
  return true;
}
function safeRelative(p) {
  return typeof p === 'string' && p.length > 0 && !p.startsWith('/') && !p.includes('\\') && p.split('/').every(x => x && x !== '.' && x !== '..' && !/[\x00-\x1f\x7f-\x9f]/.test(x));
}
function safeTemplate(name) {
  return typeof name === 'string' && Buffer.byteLength(name) <= 128 && name.trim() === name && name !== '.' && name !== '..' && safeRelative(name) && !name.includes('/');
}
function readSpec(b64, page) {
  if (typeof b64 !== 'string' || b64.length > Math.ceil(LIMIT * 4 / 3) + 4 || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(b64)) throw new Error(`Invalid or oversized e2d3 base64: ${page}`);
  const bytes = Buffer.from(b64, 'base64');
  if (bytes.length > LIMIT || bytes.toString('base64') !== b64) throw new Error(`Noncanonical or oversized e2d3 spec: ${page}`);
  let spec;
  try { spec = JSON.parse(decode(bytes, 'spec')); } catch { throw new Error(`Invalid UTF-8 JSON e2d3 spec: ${page}`); }
  if (!spec || Array.isArray(spec) || typeof spec !== 'object' || typeof spec.data !== 'string' || !safeTemplate(spec.template) || Object.keys(spec).sort().join(',') !== 'data,template') throw new Error(`Invalid or unsafe e2d3 template/spec: ${page}`);
  return { id: digest(bytes), ...spec };
}
function collect(input) {
  let manifest;
  try { manifest = JSON.parse(decode(regular(input, 'e2d3 input manifest', MAX_TOTAL), input)); }
  catch (e) { throw new Error(`Invalid or missing e2d3 input manifest: ${e.message}`); }
  if (!manifest || Array.isArray(manifest) || manifest.format !== 'greenlog-e2d3-input-v1' ||
      !Array.isArray(manifest.specs) || Object.keys(manifest).sort().join(',') !== 'format,specs')
    throw new Error('Invalid e2d3 input manifest');
  const graphs = new Map();
  for (const value of manifest.specs) {
    const spec = readSpec(value, input);
    graphs.set(spec.id, spec);
    if (graphs.size > MAX_GRAPHS) throw new Error(`More than ${MAX_GRAPHS} distinct e2d3 graphs`);
  }
  return graphs;
}
function git(contrib, args, maxBuffer = MAX_FILE + LIMIT) {
  const result = spawnSync('git', ['-C', contrib, ...args], { encoding: null, maxBuffer, env: { ...process.env, GIT_NO_REPLACE_OBJECTS: '1', GIT_CONFIG_NOSYSTEM: '1' } });
  if (result.error || result.status !== 0) throw new Error(`git ${args[0]} failed: ${result.error || result.stderr.toString('utf8').trim()}`);
  return result.stdout;
}
function command(program, args) {
  const result = spawnSync(program, args, { encoding: 'utf8', maxBuffer: 8 * 1024 * 1024 });
  if (result.error || result.status !== 0) throw new Error(`${program} failed: ${result.error || result.stderr.trim()}`);
}
function acquireSources(contribDir, runtimeDir) {
  if (contribDir && runtimeDir) return { contribDir, runtimeDir, temp: null };
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'greenlog-e2d3-sources-'));
  try {
    if (!runtimeDir) {
      const archive = path.join(temp, 'e2d3-0.6.4.tgz');
      console.log('E2D3: fetching official browser runtime 0.6.4');
      command('curl', ['--fail', '--silent', '--show-error', '--location', '--retry', '3',
        '--connect-timeout', '20', '--max-time', '120', '--output', archive,
        'https://registry.npmjs.org/e2d3/-/e2d3-0.6.4.tgz']);
      if (digest(regular(archive, 'E2D3 package archive', MAX_TOTAL)) !== PACKAGE_SHA256)
        throw new Error('Official E2D3 package SHA-256 mismatch');
      command('tar', ['-xzf', archive, '-C', temp, 'package/dist/lib/e2d3.js', 'package/LICENSE']);
      runtimeDir = path.join(temp, 'package');
    }
    if (!contribDir) {
      contribDir = path.join(temp, 'contrib');
      console.log(`E2D3: fetching official templates at ${CONTRIB_COMMIT}`);
      command('git', ['init', '-q', contribDir]);
      command('git', ['-C', contribDir, 'fetch', '--depth=1', 'https://github.com/e2d3/e2d3-contrib.git', CONTRIB_COMMIT]);
      command('git', ['-C', contribDir, 'checkout', '-q', '--detach', 'FETCH_HEAD']);
    }
    return { contribDir, runtimeDir, temp };
  } catch (e) {
    fs.rmSync(temp, { recursive: true, force: true });
    throw e;
  }
}
function verifyContrib(dir) {
  assertNoSymlinks(dir);
  if (!fs.statSync(dir).isDirectory()) throw new Error('contrib directory is not a directory');
  const head = git(dir, ['rev-parse', '--verify', 'HEAD^{commit}']).toString('ascii').trim();
  if (head !== CONTRIB_COMMIT) throw new Error(`Contrib checkout must be exactly ${CONTRIB_COMMIT}, got ${head}`);
}
function contribBlob(dir, oid, label) { const data = git(dir, ['cat-file', 'blob', oid]); if (data.length > MAX_FILE) throw new Error(`Template asset too large: ${label}`); return data; }
function tree(dir, template) {
  const raw = git(dir, ['ls-tree', '-rz', '--full-tree', CONTRIB_COMMIT, '--', `:(literal)${template}/`], MAX_TOTAL);
  const files = new Map();
  for (const record of raw.toString('utf8').split('\0').filter(Boolean)) {
    const m = /^(\d+) (\w+) ([0-9a-f]{40})\t([\s\S]+)$/.exec(record);
    if (!m || m[2] !== 'blob' || !['100644', '100755'].includes(m[1]) || !m[4].startsWith(template + '/')) throw new Error(`Unsupported file or symlink in template ${template}`);
    const rel = m[4].slice(template.length + 1);
    if (!safeRelative(rel) || Buffer.byteLength(rel) > 1024) throw new Error(`Unsafe template asset: ${rel}`);
    files.set(rel, contribBlob(dir, m[3], rel));
  }
  if (!files.has('main.js')) throw new Error(`Missing official main.js for template ${template}`);
  if (!files.has('main.css')) files.set('main.css', Buffer.alloc(0));
  return files;
}
function checkDependencies(main, template, files) {
  const source = decode(main, `${template}/main.js`);
  if (/^\/\*#\s*config\s*=/m.test(source)) throw new Error(`${template}: custom RequireJS configuration is unsupported`);
  // The official runtime already bundles the first group; the rest have
  // official CDN paths and may also have a local template fallback.
  const allowed = new Set(['d3', 'jquery', 'js-yaml', 'e2d3model', 'async', 'c3',
    'coffee-script', 'crossfilter', 'd3plus', 'dc', 'mathjs', 'numeric', 'react',
    'three', 'topojson', 'vue', 'jquery-with-global', 'JSXTransformer', 'font', 'goog', 'image']);
  for (const m of source.matchAll(/^\/\/#\s*require\s*=(.*)$/gm)) {
    for (const entry of m[1].split(',').map(x => x.trim()).filter(Boolean)) {
      const dep = entry.includes(':') ? entry.split(':')[1] : entry;
      if (!/^[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*$/.test(dep) ||
          (!allowed.has(dep) && !files.has(`${dep}.js`)))
        throw new Error(`${template}: dependency ${dep} is not available in the browser bundle, official paths or template`);
    }
  }
  // Direct hard-coded remote loads cannot be made self-contained by copying a template directory.
  if (/\b(?:fetch|d3\.(?:json|csv|tsv|text|xml|html)|\$\.get(?:JSON|Script)?)\s*\(\s*["'](?:https?:)?\/\//i.test(source)) throw new Error(`${template}: direct external data/script dependency is unsupported`);
}
function parseRows(csv, delimiter = ',', minColumns = 2) {
  if (Buffer.byteLength(csv, 'utf8') > LIMIT || /\0/.test(csv)) throw new Error('CSV is too large or contains NUL');
  const rows = csvParse(csv, { delimiter, bom: true, record_delimiter: ['\r\n', '\n', '\r'], skip_empty_lines: true, relax_column_count: false, max_record_size: LIMIT });
  if (rows.length < 2 || rows.length > 10000 || rows[0].length < minColumns || rows[0].length > 256 || rows.some(r => r.every(v => !v.trim()))) throw new Error('CSV is empty or has an invalid shape');
  return rows;
}
function number(s) { return s.trim() !== '' && /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(s.trim()) && Number.isFinite(Number(s)); }
function validateBipartite(rows) {
  if (rows.length < 3 || rows.some(r => r.length !== 4) || !rows[0][0].trim() || !rows[0][1].trim() || rows[1].some(v => !v.trim())) throw new Error('Expected two headers and at least one four-column row');
  const seen = new Set();
  const left = new Set(), right = new Set();
  let sum1 = 0, sum2 = 0;
  for (const r of rows.slice(2)) {
    if (!r[0].trim() || !r[1].trim() || /[\x00-\x1f\x7f-\x9f]/.test(r[0] + r[1])) throw new Error('Invalid category');
    if (!number(r[2]) || !number(r[3]) || Number(r[2]) < 0 || Number(r[3]) < 0) throw new Error('Expected non-negative numeric values');
    const pair = JSON.stringify([r[0], r[1]]);
    if (seen.has(pair)) throw new Error('Duplicate category pair');
    seen.add(pair);
    left.add(r[0]); right.add(r[1]); sum1 += Number(r[2]); sum2 += Number(r[3]);
  }
  if (!Number.isFinite(sum1) || !Number.isFinite(sum2) || left.size > 23 || left.size * right.size > 10000)
    throw new Error('Values or category matrix exceed the safe rendering limit');
}
function prepareData(spec, files) {
  const defaultName = files.has('data.csv') ? 'data.csv' : (files.has('data.tsv') ? 'data.tsv' : null);
  if (!defaultName) throw new Error(`${spec.template}: missing default data.csv/data.tsv`);
  let defaults;
  try { defaults = parseRows(decode(files.get(defaultName), `${spec.template}/${defaultName}`), defaultName.endsWith('.tsv') ? '\t' : ','); if (spec.template === 'bipartite-graph-original') validateBipartite(defaults); }
  catch (e) { throw new Error(`${spec.template}: invalid default data: ${e.message}`); }
  let rows, fallback = false, why = '';
  try {
    rows = parseRows(spec.data, ',', spec.template === 'piechart-javascript' ? 1 : 2);
    if (spec.template === 'bipartite-graph-original') validateBipartite(rows);
    else validateTemplate(spec.template, rows, defaults);
  } catch (e) { rows = defaults; fallback = true; why = e.message; }
  if (spec.template === 'bipartite-graph-original') rows = [...rows.slice(0, 2), ['', '', '0', '0'], ...rows.slice(2)];
  return { data: Buffer.from(stringify(rows, { record_delimiter: '\n' }), 'utf8'), fallback, why };
}
function frame(id, template, fallback, why) {
  const message = fallback ? `<p class="notice" role="status">入力 CSV がテンプレートに適合しないため、公式の既定データを表示しています。 (${escapeHtml(why)})</p>` : '';
  return `<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>${escapeHtml(template)}</title><base id="e2d3-base" href="./"><style>
html,body { margin:0; width:100%; height:100%; overflow-x:hidden }
body { display:flex; flex-direction:column }
#e2d3-chart-area { flex:1; min-height:0; width:100%; margin:0; overflow-x:hidden; overflow-y:auto }
#e2d3-chart-area svg { max-width:100%!important; box-sizing:border-box }
.notice { font:14px sans-serif; color:#683d00; background:#fff3cd; padding:8px; margin:0 }
</style></head><body>${message}
<p class="notice" id="e2d3-error" role="alert" hidden>グラフの描画に失敗しました。テンプレートの追加依存、データ、またはネットワークを確認してください。</p>
<figure id="e2d3-chart-area"></figure><script>
(function () {
  function fail() { document.getElementById('e2d3-error').hidden = false; }
  // RequireJS may try a CDN first and then successfully load a local fallback.
  // Only uncaught script errors are fatal here; the timeout covers no output.
  window.addEventListener('error', function (event) { if (event.target === window) fail(); }, true);
  window.addEventListener('unhandledrejection', fail);
  setTimeout(function () { if (!document.getElementById('e2d3-chart-area').children.length) fail(); }, 12000);
  function fit() {
    document.querySelectorAll('#e2d3-chart-area svg').forEach(function (s) {
      var wa = s.getAttribute('width') || '', ha = s.getAttribute('height') || '';
      var w = parseFloat(wa), h = parseFloat(ha);
      if (w > 0 && h > 0 && !/%/.test(wa) && !/%/.test(ha) &&
          (!s.hasAttribute('viewBox') || s.hasAttribute('data-e2d3-auto-viewbox'))) {
        var left = 0, top = 0, right = w, bottom = h;
        try {
          var box = s.getBBox();
          if ([box.x, box.y, box.width, box.height].every(Number.isFinite)) {
            left = Math.min(left, box.x); top = Math.min(top, box.y);
            right = Math.max(right, box.x + box.width); bottom = Math.max(bottom, box.y + box.height);
          }
        } catch (_) { /* Not all SVGs have measurable contents immediately. */ }
        s.setAttribute('data-e2d3-auto-viewbox', '');
        s.setAttribute('viewBox', [left, top, right - left, bottom - top].join(' '));
      }
      if (s.hasAttribute('viewBox')) s.style.height = 'auto';
    });
  }
  new MutationObserver(fit).observe(document.getElementById('e2d3-chart-area'),
    {childList:true, subtree:true, attributes:true, attributeFilter:['width','height']});
  window.addEventListener('load', fit);
})();
</script><script src="runtime/e2d3.js" data-base-url="./${id}" data-data-type="csv" data-data-url="data.csv"></script></body></html>
`;
}
const REQJS_LICENSE = "Copyright jQuery Foundation and other contributors, https://jquery.org/\n\nThis software consists of voluntary contributions made by many\nindividuals. For exact contribution history, see the revision history\navailable at https://github.com/requirejs/requirejs\n\nThe following license applies to all parts of this software except as\ndocumented below:\n\n====\n\nPermission is hereby granted, free of charge, to any person obtaining\na copy of this software and associated documentation files (the\n\"Software\"), to deal in the Software without restriction, including\nwithout limitation the rights to use, copy, modify, merge, publish,\ndistribute, sublicense, and/or sell copies of the Software, and to\npermit persons to whom the Software is furnished to do so, subject to\nthe following conditions:\n\nThe above copyright notice and this permission notice shall be\nincluded in all copies or substantial portions of the Software.\n\nTHE SOFTWARE IS PROVIDED \"AS IS\", WITHOUT WARRANTY OF ANY KIND,\nEXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF\nMERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND\nNONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE\nLIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION\nOF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION\nWITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.\n\n====\n\nCopyright and related rights for sample code are waived via CC0. Sample\ncode is defined as all source code displayed within the prose of the\ndocumentation.\n\nCC0: http://creativecommons.org/publicdomain/zero/1.0/\n\n====\n\nFiles located in the node_modules directory, and certain utilities used\nto build or test the software in the test and dist directories, are\nexternally maintained libraries used by this software which have their own\nlicenses; we recommend you read them, as their terms may differ from the\nterms above.\n";
const DOMREADY_LICENSE = "RequireJS is released under two licenses: new BSD, and MIT. You may pick the\nlicense that best suits your development needs. The text of both licenses are\nprovided below.\n\n\nThe \"New\" BSD License:\n----------------------\n\nCopyright (c) 2010-2011, The Dojo Foundation\nAll rights reserved.\n\nRedistribution and use in source and binary forms, with or without\nmodification, are permitted provided that the following conditions are met:\n\n  * Redistributions of source code must retain the above copyright notice, this\n    list of conditions and the following disclaimer.\n  * Redistributions in binary form must reproduce the above copyright notice,\n    this list of conditions and the following disclaimer in the documentation\n    and/or other materials provided with the distribution.\n  * Neither the name of the Dojo Foundation nor the names of its contributors\n    may be used to endorse or promote products derived from this software\n    without specific prior written permission.\n\nTHIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS \"AS IS\" AND\nANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED\nWARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE\nDISCLAIMED.  IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE\nFOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL\nDAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR\nSERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER\nCAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,\nOR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE\nOF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.\n\n\n\nMIT License\n-----------\n\nCopyright (c) 2010-2011, The Dojo Foundation\n\nPermission is hereby granted, free of charge, to any person obtaining a copy\nof this software and associated documentation files (the \"Software\"), to deal\nin the Software without restriction, including without limitation the rights\nto use, copy, modify, merge, publish, distribute, sublicense, and/or sell\ncopies of the Software, and to permit persons to whom the Software is\nfurnished to do so, subject to the following conditions:\n\nThe above copyright notice and this permission notice shall be included in\nall copies or substantial portions of the Software.\n\nTHE SOFTWARE IS PROVIDED \"AS IS\", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR\nIMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,\nFITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE\nAUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER\nLIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,\nOUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN\nTHE SOFTWARE.\n";
function build({ siteDir, contribDir, runtimeDir }) {
  if (!siteDir) throw new Error('--site-dir is required');
  const site = path.resolve(siteDir), blog = path.join(site, 'data/contents/blog'), out = path.join(blog, '_e2d3');
  const input = path.join(site, '.greenlog-e2d3-input.json');
  assertNoSymlinks(blog);
  if (!fs.statSync(blog, { throwIfNoEntry: false })?.isDirectory()) throw new Error(`Missing generated blog directory: ${blog}`);
  const existed = checkedExisting(out);
  const graphs = collect(input);
  if (!graphs.size) {
    if (existed) fs.rmSync(out, { recursive: true });
    fs.unlinkSync(input);
    console.log('E2D3: no graphs'); return;
  }
  const sources = acquireSources(contribDir, runtimeDir);
  contribDir = path.resolve(sources.contribDir); runtimeDir = path.resolve(sources.runtimeDir);
  try {
  verifyContrib(contribDir);
  const runtime = regular(path.join(runtimeDir, 'dist/lib/e2d3.js'), 'official E2D3 runtime');
  if (digest(runtime) !== RUNTIME_SHA256) throw new Error('Official E2D3 bundle SHA-256 mismatch');
  const outputs = new Map();
  let total = 0;
  function add(name, bytes) {
    if (!safeRelative(name) || outputs.has(name)) throw new Error(`Conflicting generated e2d3 output: ${name}`);
    const data = Buffer.isBuffer(bytes) ? bytes : Buffer.from(bytes, 'utf8');
    total += data.length;
    if (data.length > MAX_FILE || total > MAX_TOTAL) throw new Error('E2D3 generated output exceeds size limit');
    outputs.set(name, data);
  }
  add('runtime/e2d3.js', runtime);
  add('licenses/e2d3-LICENSE', regular(path.join(runtimeDir, 'LICENSE'), 'E2D3 license', LIMIT));
  add('licenses/contrib-LICENSE', git(contribDir, ['show', `${CONTRIB_COMMIT}:LICENSE`]));
  for (const [pkg, file] of [['d3','LICENSE'], ['jquery','LICENSE.txt'], ['requirejs-text','LICENSE'], ['js-yaml','LICENSE'], ['argparse','LICENSE'], ['esprima','LICENSE.BSD']]) {
    add(`licenses/${pkg}-LICENSE`, regular(path.join(repo, 'node_modules', pkg, file), `${pkg} license`, LIMIT));
  }
  add('licenses/requirejs-LICENSE', REQJS_LICENSE);
  add('licenses/domReady-LICENSE', DOMREADY_LICENSE);
  add('licenses/NOTICE.txt', `E2D3 browser bundle: e2d3 npm 0.6.4, SHA-256 ${RUNTIME_SHA256}.\nOfficial templates: e2d3/e2d3-contrib commit ${CONTRIB_COMMIT}.\nBundle includes RequireJS 2.2.0, domReady 2.0.1, text 2.0.14, D3 3.5.16, jQuery 2.2.3, js-yaml 3.5.5 and E2D3 code. Original license texts are in this directory. The text plugin license is reproduced from the compatible 2.0.15 release. Individual template files and any associated notices are preserved in their respective hash directories.\n`);
  const templates = new Map();
  for (const spec of [...graphs.values()].sort((a,b) => a.id.localeCompare(b.id))) {
    if (!templates.has(spec.template)) { const files = tree(contribDir, spec.template); checkDependencies(files.get('main.js'), spec.template, files); templates.set(spec.template, files); }
    const files = templates.get(spec.template), result = prepareData(spec, files);
    for (const [rel, bytes] of [...files.entries()].sort(([a],[b]) => a.localeCompare(b))) if (rel !== 'data.csv') add(`${spec.id}/${rel}`, bytes);
    add(`${spec.id}/data.csv`, result.data);
    add(`${spec.id}.html`, frame(spec.id, spec.template, result.fallback, result.why));
    if (result.fallback) console.warn(`E2D3 ${spec.id} (${spec.template}): default data used: ${result.why}`);
  }
  const manifest = { format: 'greenlog-e2d3-v1', files: Object.fromEntries([...outputs.entries()].sort(([a],[b]) => a.localeCompare(b)).map(([name, data]) => [name, digest(data)])) };
  if (existed) fs.rmSync(out, { recursive: true });
  fs.mkdirSync(out);
  for (const [name, bytes] of outputs) { const target = path.join(out, ...name.split('/')); fs.mkdirSync(path.dirname(target), { recursive: true }); fs.writeFileSync(target, bytes, { flag: 'wx' }); }
  fs.writeFileSync(path.join(out, MARKER), JSON.stringify(manifest, null, 2) + '\n', { flag: 'wx' });
  fs.unlinkSync(input);
  console.log(`E2D3: built ${graphs.size} distinct graph(s), ${templates.size} template(s), ${total} bytes`);
  } finally {
    if (sources.temp) fs.rmSync(sources.temp, { recursive: true, force: true });
  }
}
function main() {
  const options = { contribDir: process.env.E2D3_CONTRIB_DIR, runtimeDir: process.env.E2D3_RUNTIME_DIR };
  const names = { '--site-dir': 'siteDir', '--contrib-dir': 'contribDir', '--runtime-dir': 'runtimeDir' };
  for (let i = 2; i < process.argv.length; i++) { const key = names[process.argv[i]]; if (!key || !process.argv[i+1] || process.argv[i+1].startsWith('--')) throw new Error(`Usage: node scripts/e2d3_build.mjs --site-dir DIR [--contrib-dir DIR --runtime-dir DIR]`); options[key] = process.argv[++i]; }
  build(options);
}
try { main(); } catch (e) { console.error(`E2D3 build failed: ${e.message}`); process.exitCode = 1; }
