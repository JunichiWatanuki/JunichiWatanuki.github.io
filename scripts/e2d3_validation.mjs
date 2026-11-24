// Template-specific checks for parsed CSV tables. CSV syntax, input limits and
// the bipartite template's special two-header format are handled by the caller.
// The implementations below are based on e2d3-contrib commit d48bd8256e95cfcc393d37e3e7769000dd738fc9.

const DECIMAL = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/;
// ChartDataKeyValueList.typing() converts this E2D3 numeric form, including
// thousands separators, before piechart-javascript passes it to D3.
const TYPED_NUMBER = /^[-+]?(\d{1,3}(,?\d{3})*(\.\d+)?|\.\d+)([eE][-+]?\d+)?$/;

function number(value) {
  return typeof value === 'string' && value.trim() !== '' &&
    DECIMAL.test(value.trim()) && Number.isFinite(Number(value));
}

function typedNumber(value) {
  if (typeof value !== 'string' || value.trim() === '') return NaN;
  // E2D3 tests the untrimmed value when converting thousands separators; D3
  // then applies unary +, which accepts surrounding whitespace for decimals.
  const numeric = TYPED_NUMBER.test(value) ? value.replaceAll(',', '') : value;
  return number(numeric) ? Number(numeric) : NaN;
}

function tableShape(rows) {
  if (!Array.isArray(rows) || rows.length < 2 || !Array.isArray(rows[0]) ||
      rows[0].length < 1 || rows.some(row => !Array.isArray(row) ||
        row.length !== rows[0].length || row.some(value => typeof value !== 'string'))) {
    throw new Error('Expected a rectangular table with a header and at least one data row');
  }
}

// D3 accepts and normalizes out-of-range dates, which silently changes the
// source data; require the documented YYYYMMDD form and a real calendar date.
function calendarDate(value) {
  if (!/^\d{8}$/.test(value)) return false;
  const year = Number(value.slice(0, 4));
  const month = Number(value.slice(4, 6));
  const day = Number(value.slice(6, 8));
  if (month < 1 || month > 12 || day < 1) return false;
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  return day <= days[month - 1];
}

function validateMultiSeries(rows) {
  tableShape(rows);
  const header = rows[0];
  const dateColumn = header.indexOf('date');
  if (header.length < 2 || dateColumn === -1) {
    throw new Error('Expected a date column and at least one numeric series');
  }
  // toList() creates plain objects by assigning the supplied column names.
  // Duplicate keys overwrite values and __proto__ is not an ordinary own key.
  if (header.some(name => !name.trim() || name === '__proto__') || new Set(header).size !== header.length) {
    throw new Error('Series headers must be nonempty and unique');
  }
  let min = Infinity, max = -Infinity;
  for (const row of rows.slice(1)) {
    if (!calendarDate(row[dateColumn])) throw new Error('date must be a valid YYYYMMDD calendar date');
    for (let i = 0; i < row.length; i++) {
      if (i === dateColumn) continue;
      if (!number(row[i])) throw new Error(`Column ${i + 1} must be finite numeric data`);
      const value = Number(row[i]);
      min = Math.min(min, value);
      max = Math.max(max, value);
    }
  }
  if (!Number.isFinite(max - min)) throw new Error('Numeric series range exceeds the rendering limit');
}

function validatePie(rows) {
  tableShape(rows);
  // transpose().toList({header:['name','age']}) uses exactly the first two
  // original rows; later rows are ignored, not additional observations.
  if (rows.length !== 2) throw new Error('Expected one category row and one value row');
  if (rows[0].some(name => !name.trim())) throw new Error('Category labels must not be empty');
  let sum = 0;
  for (const value of rows[1]) {
    const n = typedNumber(value);
    if (!Number.isFinite(n) || n < 0) throw new Error('Pie values must be finite non-negative numbers');
    sum += n;
  }
  if (!(sum > 0) || !Number.isFinite(sum) || !Number.isFinite(2 * Math.PI / sum)) {
    throw new Error('Pie values must have a positive finite renderable total');
  }
}

function validateInfiniteElement(rows) {
  // Unlike toList() templates, this chart consumes every row, including row 0.
  // It creates a dense left x right matrix and draws 14px bars with a 1px
  // margin on each side, in a 600px high plot. 37 is floor(600 / 16).
  const maxCategories = 37;
  if (!Array.isArray(rows) || rows.length < 1 || rows.length > maxCategories ** 2 ||
      rows.some(row => !Array.isArray(row) || row.length !== 4 ||
        row.some(value => typeof value !== 'string'))) {
    throw new Error('Expected a bounded, headerless four-column table');
  }
  const left = new Set(), right = new Set(), pairs = new Set();
  let sum = 0;
  for (const [a, b, value] of rows) {
    if (!a.trim() || !b.trim()) throw new Error('Categories must not be empty');
    if (!number(value) || Number(value) < 0) throw new Error('Values must be finite and non-negative');
    const pair = JSON.stringify([a, b]);
    if (pairs.has(pair)) throw new Error('Duplicate category pair');
    pairs.add(pair); left.add(a); right.add(b);
    sum += Number(value);
    if (!Number.isFinite(sum)) throw new Error('Category total must be finite');
    if (left.size > maxCategories || right.size > maxCategories) {
      throw new Error('Category count exceeds the safe rendering limit');
    }
  }
  // Column 4 is deliberately ignored: partData(sales_data, 2) never reads it.
}

const FORCE_HEADER = ['id', 'label', 'source', 'target', 'value', 'color', 'size', 'width', 'distance'];
const FROST_HEADER = ['athlete', 'HH', 'MM', 'SS', 'acolor'];
function exactHeader(rows, expected) {
  tableShape(rows);
  if (rows[0].length !== expected.length || rows[0].some((value, i) => value !== expected[i])) {
    throw new Error(`Expected exact header: ${expected.join(',')}`);
  }
}

// SVG/CSS lengths can be unitless or carry a common length/percentage unit.
// Bound magnitudes to avoid extreme SVG geometry and unstable force ticks.
function safeLength(value) {
  const match = /^([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)(?:px|pt|pc|mm|cm|in|em|ex|rem|%)?$/i.exec(value.trim());
  return !!match && Number.isFinite(Number(match[1])) && Number(match[1]) >= 0 && Number(match[1]) <= 1e6;
}

const CSS_COLORS = new Set(`
aliceblue antiquewhite aqua aquamarine azure beige bisque black blanchedalmond blue blueviolet brown burlywood
cadetblue chartreuse chocolate coral cornflowerblue cornsilk crimson cyan darkblue darkcyan darkgoldenrod
darkgray darkgreen darkgrey darkkhaki darkmagenta darkolivegreen darkorange darkorchid darkred darksalmon
darkseagreen darkslateblue darkslategray darkslategrey darkturquoise darkviolet deeppink deepskyblue dimgray
dimgrey dodgerblue firebrick floralwhite forestgreen fuchsia gainsboro ghostwhite gold goldenrod gray green
greenyellow grey honeydew hotpink indianred indigo ivory khaki lavender lavenderblush lawngreen lemonchiffon
lightblue lightcoral lightcyan lightgoldenrodyellow lightgray lightgreen lightgrey lightpink lightsalmon
lightseagreen lightskyblue lightslategray lightslategrey lightsteelblue lightyellow lime limegreen linen magenta
maroon mediumaquamarine mediumblue mediumorchid mediumpurple mediumseagreen mediumslateblue mediumspringgreen
mediumturquoise mediumvioletred midnightblue mintcream mistyrose moccasin navajowhite navy oldlace olive
olivedrab orange orangered orchid palegoldenrod palegreen paleturquoise palevioletred papayawhip peachpuff peru
pink plum powderblue purple rebeccapurple red rosybrown royalblue saddlebrown salmon sandybrown seagreen seashell
sienna silver skyblue slateblue slategray slategrey snow springgreen steelblue tan teal thistle tomato turquoise
violet wheat white whitesmoke yellow yellowgreen transparent currentcolor none
canvas canvastext linktext visitedtext activetext buttonface buttontext buttonborder field fieldtext highlight
highlighttext selecteditem selecteditemtext mark marktext graytext accentcolor accentcolortext
`.trim().split(/\s+/));
function safeColor(value) {
  const color = value.trim().toLowerCase();
  if (CSS_COLORS.has(color) || /^#(?:[0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})$/.test(color)) return true;
  // Recognize common CSS color functions without accepting malformed values
  // that the browser silently ignores. URL paints and nested expressions are
  // excluded: they can reference external resources or unresolved CSS state.
  const match = /^(rgb|rgba|hsl|hsla|hwb|lab|lch|oklab|oklch|color)\(([^()]*)\)$/.exec(color);
  if (!match) return false;
  const [, fn, contents] = match;
  const comma = contents.includes(',');
  if (comma && (!['rgb', 'rgba', 'hsl', 'hsla'].includes(fn) || contents.includes('/'))) return false;
  const halves = comma ? [contents] : contents.split('/');
  if (halves.length > 2) return false;
  let components = comma ? halves[0].split(',').map(x => x.trim()) : halves[0].trim().split(/\s+/);
  let alpha = halves[1]?.trim();
  if (comma && components.length === 4) alpha = components.pop();
  if (fn === 'color') {
    const profile = components.shift();
    if (!['srgb', 'srgb-linear', 'display-p3', 'a98-rgb', 'prophoto-rgb', 'rec2020', 'xyz', 'xyz-d50', 'xyz-d65'].includes(profile)) return false;
  }
  if (components.length !== 3 || alpha === '') return false;
  const component = (text, angle = false, percentOnly = false) => {
    if (!comma && text === 'none') return true;
    const part = /^([+-]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][+-]?\d+)?)(%|deg|grad|rad|turn)?$/.exec(text);
    return !!part && Number.isFinite(Number(part[1])) &&
      (angle ? !part[2] || part[2] !== '%' : !part[2] || part[2] === '%') &&
      (!percentOnly || part[2] === '%');
  };
  if (alpha !== undefined && !component(alpha)) return false;
  if (!components.every((text, i) => component(text,
    (fn === 'hsl' || fn === 'hsla' || fn === 'hwb') && i === 0 || (fn === 'lch' || fn === 'oklch') && i === 2,
    (fn === 'hsl' || fn === 'hsla' || fn === 'hwb') && i > 0))) return false;
  // Legacy comma-separated RGB requires either all numbers or all percentages.
  if (comma && (fn === 'rgb' || fn === 'rgba') && new Set(components.map(x => x.endsWith('%'))).size !== 1) return false;
  return true;
}

function validateForce(rows) {
  exactHeader(rows, FORCE_HEADER);
  const links = [];
  let nodeCount = 0;
  for (const [id, label, source, target, value, color, size, width, distance] of rows.slice(1)) {
    // The template tests String(id) + String(label), not trimmed values.
    if (id !== '' || label !== '') {
      if (!id.trim() || !label.trim() || !number(id) || !Number.isSafeInteger(Number(id)) || Number(id) !== nodeCount) {
        throw new Error('Node id and label must both exist, with id equal to the node array index');
      }
      if (!safeLength(size)) throw new Error('Node size must be a safe non-negative SVG length');
      nodeCount++;
    }
    // With the exact header all properties exist. Number('') is 0, so even
    // an entirely blank edge creates a 0 -> 0 link, not a node-only row.
    if (![source, target, value].every(number)) throw new Error('Every row must contain a complete finite edge');
    const s = Number(source), t = Number(target), v = Number(value);
    if (!Number.isSafeInteger(s) || s < 0 || !Number.isSafeInteger(t) || t < 0) {
      throw new Error('Edge endpoints must be non-negative integer indices');
    }
    // The template silently skips a link if this sum equals its -3 sentinel.
    if (s + t + v === -3) throw new Error('Edge conflicts with the template sentinel');
    links.push([s, t]);
    // Empty color removes the CSS stroke (default is none), not the template's
    // undefined-only fallback. Empty width removes the style (SVG default 1),
    // and empty distance is converted by D3's unary + to 0; both are safe.
    if (!safeColor(color)) throw new Error('Edge color must be a safe CSS color');
    if (width.trim() && !safeLength(width)) throw new Error('Edge width must be a safe non-negative SVG length');
    if (distance.trim() && (!number(distance) || Number(distance) < 0 || Number(distance) > 1e6)) {
      throw new Error('Edge distance must be finite and non-negative within the rendering limit');
    }
  }
  // D3 resolves links only after gathering every node, so forward references
  // are valid. Duplicate links are meaningful and intentionally accepted.
  if (links.some(([s, t]) => s >= nodeCount || t >= nodeCount)) {
    throw new Error('Edge endpoint does not reference an existing node');
  }
}

function validateFrostbite(rows) {
  // update() starts at data[1]; a real header is needed to preserve the first
  // athlete, even though this template does not use toList().
  exactHeader(rows, FROST_HEADER);
  for (const [athlete, hh, mm, ss, color] of rows.slice(1)) {
    if (!athlete.trim()) throw new Error('Athlete name must not be empty');
    if (![hh, mm, ss].every(part => /^\d+$/.test(part.trim()))) {
      throw new Error('Time components must be decimal integers');
    }
    const [h, m, s] = [hh, mm, ss].map(part => Number(part.trim()));
    const seconds = h * 3600 + m * 60 + s;
    if (m > 59 || s > 59 || !(seconds > 0 && seconds <= 3 * 3600)) {
      throw new Error('Time must be greater than zero and at most three hours, with minutes and seconds below 60');
    }
    if (!/^[0-9a-f]{6}$/i.test(color)) throw new Error('Color must be six hex digits without #');
  }
}

// Keep the existing heuristic for all other templates: compare width against
// the default and infer numeric columns from its complete data records.
function validateGeneric(rows, defaults) {
  tableShape(rows);
  tableShape(defaults);
  const width = defaults[0].length;
  if (rows.some(row => row.length !== width) || rows[0].some(value => !value.trim())) {
    throw new Error('Column count/header differs from the template');
  }
  for (let c = 1; c < width; c++) {
    const reference = defaults.slice(1).map(row => row[c]);
    if (reference.length && reference.every(number) && rows.slice(1).some(row => !number(row[c]))) {
      throw new Error(`Column ${c + 1} must be numeric`);
    }
  }
}

/**
 * Validate an already parsed CSV table against the selected template.
 * rows/defaults are arrays of string arrays, including the first row. Returns
 * undefined when accepted and throws Error when incompatible. The caller owns
 * parsing, limits, bipartite validation, fallback, and data serialization.
 */
export function validateTemplate(template, rows, defaults) {
  if (template === 'multi-series-line-chart') return validateMultiSeries(rows);
  if (template === 'piechart-javascript') return validatePie(rows);
  if (template === 'InfiniteElementMethod_E2D3-LIG_03') return validateInfiniteElement(rows);
  if (template === 'force-directed-graph') return validateForce(rows);
  if (template === 'virtual-frostbite-halfmarathon') return validateFrostbite(rows);
  return validateGeneric(rows, defaults);
}
