// These exact versions are shared by all previews on the page and loaded only on demand.
const MERMAID_URL = 'https://cdn.jsdelivr.net/npm/mermaid@12.3.0/dist/mermaid.esm.min.mjs';
const HIGHLIGHT_URL = 'https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.12.0/es/highlight.min.js';
const THREE_URL = 'https://esm.sh/three@0.187.0';
const STL_LOADER_URL = 'https://esm.sh/three@0.187.0/examples/jsm/loaders/STLLoader.js';
const ORBIT_CONTROLS_URL = 'https://esm.sh/three@0.187.0/examples/jsm/controls/OrbitControls.js';
const MAX_STL_BYTES = 25 * 1024 * 1024;
const MAX_WEBGL_CONTEXTS = 6;

let mermaidModule;
let highlightModule;
let threeModules;
let diagramSequence = 0;
const diagramPrefix = `greenlog-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;

function status(section, message) {
  const element = section.querySelector('.main-code-preview-status');
  if (!element) return;
  element.textContent = message;
  element.hidden = !message;
}

function fail(section, message) {
  status(section, message);
  const source = section.querySelector('.main-code-preview-source');
  if (source) source.open = true;
}

function sourceText(section) {
  const code = section.querySelector('.main-code-preview-source pre > code');
  if (!code) throw new Error('ソースがありません');
  return code.textContent;
}

function getMermaid() {
  if (!mermaidModule) {
    mermaidModule = import(MERMAID_URL).then(({ default: mermaid }) => {
      mermaid.initialize({
        startOnLoad: false,
        securityLevel: 'strict',
        suppressErrorRendering: true,
        htmlLabels: false,
        flowchart: { htmlLabels: false },
        theme: 'neutral',
      });
      return mermaid;
    });
  }
  return mermaidModule;
}

async function renderMermaid(section) {
  try {
    const display = section.querySelector('.main-code-preview-display');
    if (!display) throw new Error('表示領域がありません');
    const code = sourceText(section);
    status(section, '図を読み込んでいます。');
    const mermaid = await getMermaid();
    let id;
    do { id = `${diagramPrefix}-${++diagramSequence}`; } while (document.getElementById(id));
    const { svg } = await mermaid.render(id, code);
    // Mermaid's strict mode sanitizes the SVG. Do not call bindFunctions: no user callbacks.
    display.innerHTML = svg;
    status(section, '');
  } catch (error) {
    console.warn('Mermaid preview failed:', error);
    fail(section, 'Mermaid図を表示できません。構文またはネットワーク接続を確認してください。');
  }
}

function watchSvg(section) {
  const image = section.querySelector('.main-code-preview-display img');
  if (!image) {
    fail(section, 'SVG画像を表示できません。元のコードを確認してください。');
    return;
  }
  const onError = () => fail(section, 'SVG画像を表示できません。元のコードを確認してください。');
  image.addEventListener('error', onError, { once: true });
  // An image can have failed before this module runs.
  if (image.complete && image.naturalWidth === 0) onError();
}

function getHighlight() {
  if (!highlightModule) {
    highlightModule = import(HIGHLIGHT_URL).then(({ default: hljs }) => {
      hljs.registerLanguage('mermaid', () => ({
        name: 'Mermaid',
        keywords: { keyword: 'flowchart graph subgraph end direction sequenceDiagram participant actor note over activate deactivate classDiagram class stateDiagram stateDiagram-v2 state erDiagram journey gantt pie timeline mindmap gitGraph quadrantChart xychart-beta block-beta sankey-beta as title section style classDef linkStyle click' },
        contains: [
          { scope: 'comment', begin: /%%/, end: /$/ },
          hljs.QUOTE_STRING_MODE,
          { scope: 'number', begin: /\b\d+(?:\.\d+)?\b/ },
          { scope: 'operator', match: /-->|---|==>|-\.->|-->>|->>|->|<--|\|/ },
        ],
      }));
      hljs.registerLanguage('stl', () => ({
        name: 'ASCII STL',
        case_insensitive: true,
        keywords: { keyword: 'solid endsolid facet normal outer loop endloop endfacet vertex' },
        contains: [{ scope: 'number', begin: /[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?/ }],
      }));
      return hljs;
    });
  }
  return highlightModule;
}

async function highlightCode(body) {
  // The build inserts this marker before any article content, independently of
  // the template. The key is absent from the original Markdown and template.
  const marker = body.querySelector(':scope > span[data-greenlog-syntax-key]');
  const key = marker && marker.getAttribute('data-greenlog-syntax-key');
  if (!key || !/^glsyntax[0-9a-f]{64}$/.test(key)) return;
  const eligible = code => body.contains(code) && code.parentElement && code.parentElement.tagName === 'PRE' &&
    code.parentElement.getAttribute('data-syntax-highlight') === key &&
    !code.closest('.main-code-preview') && code.childElementCount === 0 &&
    /^(mermaid|stl|svg)$/i.test((code.parentElement.getAttribute('lang') || '').trim());
  // A same-named attribute on rawhtml or handwritten HTML is not sufficient.
  // In addition, never destroy DOM children, including interactive controls.
  const blocks = Array.from(body.querySelectorAll('pre[data-syntax-highlight] > code'))
    .filter(eligible);
  if (!blocks.length) return;
  try {
    const hljs = await getHighlight();
    for (const code of blocks) {
      try {
        // Content can change while the highlight library is being imported.
        if (!eligible(code)) continue;
        const requested = (code.parentElement.getAttribute('lang') || '').trim().toLowerCase();
        const language = requested === 'svg' ? 'xml' : requested;
        // Never auto-detect unknown languages or change their original content.
        if (!language || !hljs.getLanguage(language)) continue;
        const original = code.textContent;
        const result = hljs.highlight(original, { language, ignoreIllegals: true });
        const template = document.createElement('template');
        template.innerHTML = result.value;
        if (template.content.textContent !== original) continue;
        code.replaceChildren(template.content);
        code.classList.add('hljs');
      } catch (error) {
        console.warn('Code highlighting failed:', error);
      }
    }
  } catch (error) {
    // Original escaped text is already present and remains usable without the CDN.
    console.warn('Highlight library unavailable:', error);
  }
}

function getThree() {
  if (!threeModules) {
    threeModules = Promise.all([
      import(THREE_URL), import(STL_LOADER_URL), import(ORBIT_CONTROLS_URL),
    ]).then(([THREE, { STLLoader }, { OrbitControls }]) => ({ THREE, STLLoader, OrbitControls }));
  }
  return threeModules;
}

async function readStl(code, signal) {
  const text = code.trim();
  if (/^solid(?:\s|$)/i.test(text)) {
    // STLLoader recognizes a lowercase 'solid' header close to the beginning.
    const bytes = new TextEncoder().encode(text.replace(/^solid/i, 'solid'));
    if (bytes.byteLength > MAX_STL_BYTES) throw new Error('STLが大きすぎます');
    return bytes.buffer;
  }
  if (!text || /\s/.test(text)) throw new Error('STLまたはURLが無効です');
  const url = new URL(text, document.baseURI);
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) {
    throw new Error('HTTP(S) URLではありません');
  }
  const response = await fetch(url.href, { signal, mode: 'cors', credentials: 'same-origin' });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  if (Number(response.headers.get('content-length')) > MAX_STL_BYTES) throw new Error('STLが大きすぎます');
  // Bound streaming downloads even if a server omits or lies about Content-Length.
  if (!response.body) {
    const buffer = await response.arrayBuffer();
    if (buffer.byteLength > MAX_STL_BYTES) throw new Error('STLが大きすぎます');
    return buffer;
  }
  const reader = response.body.getReader();
  const chunks = [];
  let size = 0;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > MAX_STL_BYTES) { await reader.cancel(); throw new Error('STLが大きすぎます'); }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  return bytes.buffer;
}

function validateGeometry(geometry) {
  const positions = geometry.getAttribute('position');
  if (!positions || positions.count < 3 || positions.count % 3 !== 0) throw new Error('三角形がありません');
  const array = positions.array;
  for (const value of array) if (!Number.isFinite(value)) throw new Error('座標が無効です');
  let hasArea = false;
  for (let i = 0; i + 8 < array.length; i += 9) {
    const ax = array[i + 3] - array[i], ay = array[i + 4] - array[i + 1], az = array[i + 5] - array[i + 2];
    const bx = array[i + 6] - array[i], by = array[i + 7] - array[i + 1], bz = array[i + 8] - array[i + 2];
    if (ay * bz - az * by || az * bx - ax * bz || ax * by - ay * bx) { hasArea = true; break; }
  }
  if (!hasArea) throw new Error('面積のある三角形がありません');
  geometry.computeBoundingBox();
  const box = geometry.boundingBox;
  if (!box || box.isEmpty()) throw new Error('モデルが空です');
  const normals = geometry.getAttribute('normal');
  if (!normals || normals.array.some(value => !Number.isFinite(value))) geometry.computeVertexNormals();
}

function setupStlPreviews(sections) {
  if (!sections.length) return;
  let activeCount = 0;
  let suspended = false;
  const queue = [];
  const states = sections.map(section => ({ section, phase: 'idle', inRange: false, token: 0, slot: false }));

  function release(state) {
    state.token++;
    if (state.abort) state.abort.abort();
    state.abort = null;
    if (state.resizeObserver) state.resizeObserver.disconnect();
    if (state.resizeHandler) window.removeEventListener('resize', state.resizeHandler);
    if (state.controls) state.controls.dispose();
    if (state.canvas && state.keyHandler) state.canvas.removeEventListener('keydown', state.keyHandler);
    if (state.canvas && state.lostHandler) state.canvas.removeEventListener('webglcontextlost', state.lostHandler);
    if (state.geometry) state.geometry.dispose();
    if (state.material) state.material.dispose();
    if (state.grid) {
      state.grid.geometry.dispose();
      const materials = Array.isArray(state.grid.material) ? state.grid.material : [state.grid.material];
      for (const material of materials) material.dispose();
    }
    if (state.renderer) {
      state.renderer.dispose();
      state.renderer.forceContextLoss();
    }
    const display = state.section.querySelector('.main-code-preview-display');
    if (display) display.replaceChildren();
    state.resizeObserver = state.resizeHandler = state.controls = state.canvas = null;
    state.keyHandler = state.lostHandler = state.geometry = state.material = state.renderer = state.grid = null;
    if (state.slot) { state.slot = false; activeCount--; }
  }

  function pump() {
    if (suspended) return;
    while (activeCount < MAX_WEBGL_CONTEXTS && queue.length) {
      const state = queue.shift();
      if (!state.inRange || state.phase !== 'queued') continue;
      state.phase = 'loading';
      state.slot = true;
      activeCount++;
      const token = ++state.token;
      state.abort = new AbortController();
      status(state.section, '3Dモデルを読み込んでいます。');
      createViewer(state, token).catch(error => {
        if (state.token !== token) return;
        console.warn('STL preview failed:', error);
        release(state);
        state.phase = 'failed';
        fail(state.section, '3Dモデルを表示できません。STLの内容、接続先のCORS設定、またはWebGLの利用可否を確認してください。');
        pump();
      });
    }
  }

  async function createViewer(state, token) {
    const display = state.section.querySelector('.main-code-preview-display');
    if (!display) throw new Error('表示領域がありません');
    const text = sourceText(state.section);
    const [{ THREE, STLLoader, OrbitControls }, buffer] = await Promise.all([
      getThree(), readStl(text, state.abort.signal),
    ]);
    if (state.token !== token) return;
    state.geometry = new STLLoader().parse(buffer);
    validateGeometry(state.geometry);
    const box = state.geometry.boundingBox;
    const center = box.getCenter(new THREE.Vector3());
    const size = box.getSize(new THREE.Vector3());
    const extent = Math.max(size.x, size.y, size.z);
    if (!Number.isFinite(extent) || extent <= 0) throw new Error('モデルのサイズが無効です');
    state.geometry.translate(-center.x, -center.y, -center.z);

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0xf1f6f1);
    const camera = new THREE.PerspectiveCamera(40, 1, extent / 1000, extent * 100);
    camera.position.set(extent * 1.6, extent * 1.25, extent * 1.8);
    scene.add(new THREE.HemisphereLight(0xffffff, 0x81998b, 2.1));
    const light = new THREE.DirectionalLight(0xffffff, 2.2);
    light.position.set(2, 3, 4);
    scene.add(light);
    state.material = new THREE.MeshStandardMaterial({ color: 0x508fc5, roughness: 0.72, metalness: 0.04, side: THREE.DoubleSide });
    scene.add(new THREE.Mesh(state.geometry, state.material));
    state.grid = new THREE.GridHelper(extent * 2.5, 10, 0x9baeb5, 0xc3d2d3);
    state.grid.position.y = -size.y / 2 - extent * 0.02;
    scene.add(state.grid);
    state.renderer = new THREE.WebGLRenderer({ antialias: true });
    state.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    const canvas = state.canvas = state.renderer.domElement;
    canvas.tabIndex = 0;
    canvas.setAttribute('role', 'img');
    canvas.setAttribute('aria-label', '3Dモデル。ドラッグまたは矢印キーで回転、ホイールまたはプラス・マイナスキーで拡大縮小できます。');
    const help = document.createElement('p');
    help.className = 'main-code-preview-help';
    help.textContent = 'ドラッグまたは矢印キーで回転、右ドラッグで移動、ホイール・ピンチまたは＋／－キーで拡大縮小';
    const toolbar = document.createElement('div');
    toolbar.className = 'main-code-preview-toolbar';
    const reset = document.createElement('button');
    reset.type = 'button';
    reset.textContent = '表示をリセット';
    const wireLabel = document.createElement('label');
    const wire = document.createElement('input');
    wire.type = 'checkbox';
    wireLabel.append(wire, document.createTextNode(' ワイヤーフレーム'));
    toolbar.append(reset, wireLabel);
    display.replaceChildren(canvas, toolbar, help);
    state.controls = new OrbitControls(camera, canvas);
    state.controls.enableDamping = false;
    state.controls.enablePan = true;
    state.controls.minDistance = extent * 0.1;
    state.controls.maxDistance = extent * 20;
    const render = () => state.renderer && state.renderer.render(scene, camera);
    state.controls.addEventListener('change', render);
    state.controls.saveState();
    reset.addEventListener('click', () => { state.controls.reset(); render(); });
    wire.addEventListener('change', () => { state.material.wireframe = wire.checked; render(); });
    const resize = () => {
      if (!state.renderer) return;
      const width = Math.max(1, display.clientWidth);
      const height = canvas.clientHeight || 300;
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
      state.renderer.setSize(width, height, false);
      render();
    };
    if ('ResizeObserver' in window) {
      state.resizeObserver = new ResizeObserver(resize);
      state.resizeObserver.observe(display);
    } else {
      state.resizeHandler = resize;
      window.addEventListener('resize', resize);
    }
    state.keyHandler = event => {
      const spherical = new THREE.Spherical().setFromVector3(camera.position.clone().sub(state.controls.target));
      switch (event.key) {
        case 'ArrowLeft': spherical.theta -= 0.12; break;
        case 'ArrowRight': spherical.theta += 0.12; break;
        case 'ArrowUp': spherical.phi -= 0.12; break;
        case 'ArrowDown': spherical.phi += 0.12; break;
        case '+': case '=': spherical.radius *= 0.85; break;
        case '-': case '_': spherical.radius *= 1.18; break;
        default: return;
      }
      event.preventDefault();
      spherical.phi = Math.max(0.08, Math.min(Math.PI - 0.08, spherical.phi));
      spherical.radius = Math.max(state.controls.minDistance, Math.min(state.controls.maxDistance, spherical.radius));
      camera.position.setFromSpherical(spherical).add(state.controls.target);
      camera.lookAt(state.controls.target);
      state.controls.update();
      render();
    };
    canvas.addEventListener('keydown', state.keyHandler);
    state.lostHandler = event => {
      event.preventDefault();
      if (state.token !== token) return;
      release(state);
      state.phase = 'failed';
      fail(state.section, 'WebGLの表示が失われました。元のコードを確認してください。');
      pump();
    };
    canvas.addEventListener('webglcontextlost', state.lostHandler);
    state.controls.update();
    resize();
    state.phase = 'ready';
    status(state.section, '');
  }

  function enter(state) {
    if (state.inRange) return;
    state.inRange = true;
    state.phase = 'queued';
    queue.push(state);
    status(state.section, '3Dプレビューの表示を待っています。');
    pump();
  }
  function leave(state) {
    if (!state.inRange) return;
    state.inRange = false;
    release(state);
    state.phase = 'idle';
    status(state.section, '');
    pump();
  }

  let observer;
  let checkFallback;
  if ('IntersectionObserver' in window) {
    const byElement = new Map(states.map(state => [state.section, state]));
    observer = new IntersectionObserver(entries => {
      for (const entry of entries) {
        const state = byElement.get(entry.target);
        if (entry.isIntersecting) enter(state); else leave(state);
      }
    }, { rootMargin: '250px 0px' });
    for (const state of states) observer.observe(state.section);
  } else {
    checkFallback = () => {
      for (const state of states) {
        const rect = state.section.getBoundingClientRect();
        if (rect.bottom >= -250 && rect.top <= window.innerHeight + 250) enter(state); else leave(state);
      }
    };
    window.addEventListener('scroll', checkFallback, { passive: true });
    window.addEventListener('resize', checkFallback);
    checkFallback();
  }
  window.addEventListener('pagehide', () => {
    suspended = true;
    if (observer) observer.disconnect();
    for (const state of states) leave(state);
  });
  window.addEventListener('pageshow', event => {
    if (!event.persisted) return;
    suspended = false;
    if (observer) for (const state of states) observer.observe(state.section);
    else checkFallback();
  });
}

export function initPreviews() {
  const body = document.querySelector('.main-article-body');
  if (!body) return;
  // Keep each kind independent: a malformed preview must not stop the others.
  for (const section of body.querySelectorAll('.main-code-preview[data-preview="mermaid"]')) renderMermaid(section);
  for (const section of body.querySelectorAll('.main-code-preview[data-preview="svg"]')) {
    try { watchSvg(section); } catch (error) { fail(section, 'SVG画像を表示できません。'); }
  }
  try { setupStlPreviews(Array.from(body.querySelectorAll('.main-code-preview[data-preview="stl"]'))); }
  catch (error) {
    console.warn('STL preview initialization failed:', error);
    for (const section of body.querySelectorAll('.main-code-preview[data-preview="stl"]')) fail(section, '3Dプレビューを初期化できません。');
  }
  highlightCode(body);
}
