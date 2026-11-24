import test from 'node:test';
import assert from 'node:assert/strict';
import { validateTemplate } from './e2d3_validation.mjs';

// Small inline fixtures reproduce the official template shapes. Tests never
// need an upstream checkout, the network, the browser, or npm dependencies.
const line = 'multi-series-line-chart';
const pie = 'piechart-javascript';
const lineDefaults = [
  ['date', 'New York', 'San Francisco', 'Austin'],
  ['20111001', '63.4', '62.7', '72.2'],
  ['20111002', '58.0', '59.9', '67.7'],
];
const pieDefaults = [
  ['A', 'B', 'C', 'D', 'E', 'F'],
  ['32', '52', '15', '42', '30', '68'],
];
const ok = (template, rows, defaults) => assert.equal(validateTemplate(template, rows, defaults), undefined);
const bad = (template, rows, defaults, pattern) => assert.throws(
  () => validateTemplate(template, rows, defaults),
  error => error instanceof Error && (!pattern || pattern.test(error.message)),
);

test('official default-shaped records pass without modification', () => {
  for (const [template, rows] of [[line, lineDefaults], [pie, pieDefaults]]) {
    const before = structuredClone(rows);
    ok(template, rows, rows);
    assert.deepEqual(rows, before);
  }
});

test('line uses the exact date key rather than generic column count', () => {
  const reportedBug = [
    ['wrong', 'A', 'B', 'C'],
    ['20260101', '1', '2', '3'],
    ['20260102', '4', '5', '6'],
  ];
  bad(line, reportedBug, lineDefaults, /date/);
  for (const dateKey of ['Date', ' date', 'date ', 'DATE']) {
    bad(line, [[dateKey, 'A'], ['20260101', '1']], lineDefaults, /date/);
  }
  ok(line, [['date', 'Osaka'], ['20260101', '1'], ['20260102', '-2.5']], lineDefaults);
  // A different count/order of series is supported by d3.keys(data[0]).
  ok(line, [['A', 'date', 'B', 'C', 'D'], ['.5', '20240229', '0', '3e2', '-4']], lineDefaults);
});

test('line requires real YYYYMMDD dates and finite numeric values in every series', () => {
  for (const date of ['2026-01-01', '202601', '202601010', '20260101 ', '', '20260229', '19000229', '20260001', '20261301', '20260431']) {
    bad(line, [['date', 'A'], [date, '1']], lineDefaults, /date/);
  }
  for (const date of ['20000229', '20240229', '20261231', '00000229']) {
    ok(line, [['date', 'A'], [date, '1']], lineDefaults);
  }
  for (const value of ['', ' ', 'NaN', 'Infinity', '1e999', 'abc', '0x10']) {
    bad(line, [['date', 'A', 'B'], ['20260101', '1', value]], lineDefaults, /numeric/);
  }
  bad(line, [['date', 'A'], ['20260101', '-1e308'], ['20260102', '1e308']], lineDefaults, /range/);
});

test('line prevents missing, overwritten or lost data object keys', () => {
  for (const header of [['date'], ['date', ''], ['date', 'A', 'A'], ['date', 'date'], ['date', '__proto__']]) {
    bad(line, [header, header.map((_, i) => i === 0 ? '20260101' : '1')], lineDefaults);
  }
});

test('pie accepts different numbers of categories and retains all supplied values', () => {
  const inputs = [
    [['One'], ['5']],
    [['東京', '大阪'], ['1', '2']],
    [['A', 'B', 'C'], ['0', '5', '10.25']],
    [['A', 'B', 'C', 'D', 'E', 'F', 'G'], ['1', '2', '3', '4', '5', '6', '7']],
    [['same', 'same'], ['1e2', ' 2.5 ']],
    [['A', 'B'], ['1,234', '.75']], // E2D3 typed conversion before D3
  ];
  for (const rows of inputs) {
    const before = structuredClone(rows);
    ok(pie, rows, pieDefaults);
    assert.deepEqual(rows, before);
  }
});

test('pie rejects missing labels, bad values and rows ignored by the template', () => {
  for (const rows of [
    [['A', ''], ['1', '2']],
    [[' ', 'B'], ['1', '2']],
    [['A', 'B'], ['1', 'no']],
    [['A', 'B'], ['1', '-2']],
    [['A', 'B'], ['1', '']],
    [['A', 'B'], ['1', 'Infinity']],
    [['A', 'B'], ['1', '1e999']],
    [['A', 'B'], ['1', ' 1,000 ']], // typed conversion does not trim grouped numbers
    [['A', 'B'], ['0', '0']],
    [['A', 'B'], ['1e308', '1e308']],
    [['A', 'B'], ['5e-324', '0']],
    [['A', 'B'], ['1', '2'], ['3', '4']],
  ]) bad(pie, rows, pieDefaults);
});

test('generic templates retain width/header/numeric-column heuristic', () => {
  const defaults = [['label', 'numeric', 'mixed'], ['A', '1', 'unknown'], ['B', '2', '3']];
  ok('other', [['different', 'header', 'labels'], ['C', '-1.25e2', 'whatever']], defaults);
  bad('other', [['label', 'numeric'], ['C', '1']], defaults, /Column count/);
  bad('other', [['label', '', 'mixed'], ['C', '1', 'anything']], defaults, /header/);
  bad('other', [['label', 'numeric', 'mixed'], ['C', 'oops', '3']], defaults, /numeric/);
  // This old heuristic intentionally does not infer a numeric first column.
  ok('other', [['value', 'numeric', 'mixed'], ['not numeric', '5', 'x']], defaults);
});

test('malformed tables are rejected instead of causing accidental acceptance', () => {
  for (const template of [line, pie, 'other']) {
    for (const rows of [[], [['A', 'B']], [['A', 'B'], ['1']], [['A', 'B'], ['1', 2]], null]) {
      bad(template, rows, lineDefaults, /table/);
    }
  }
});

const infinite = 'InfiniteElementMethod_E2D3-LIG_03';
const force = 'force-directed-graph';
const frost = 'virtual-frostbite-halfmarathon';
const forceHeader = ['id', 'label', 'source', 'target', 'value', 'color', 'size', 'width', 'distance'];
const frostHeader = ['athlete', 'HH', 'MM', 'SS', 'acolor'];
const forceRow = (overrides = {}) => {
  const row = ['0', 'First', '0', '0', '1', '#ff0000', '30', '3', '100'];
  for (const [index, value] of Object.entries(overrides)) row[Number(index)] = value;
  return row;
};

test('headerless infinite template consumes the first record and accepts the 17 x 9 default shape', () => {
  const rows = Array.from({ length: 17 }, (_, i) =>
    Array.from({ length: 9 }, (_, j) => [`left${i}`, `right${j}`, `${i + j}`, 'anything'])).flat();
  assert.equal(rows.length, 153);
  const before = structuredClone(rows);
  ok(infinite, rows, rows);
  assert.deepEqual(rows, before);
  // The validator itself supports one record; the CSV caller currently requires two.
  ok(infinite, [['A', 'B', '0', '']], rows);
  ok(infinite, [['A', 'B', '.1e2', 'not a number'], ['C', 'D', ' 2.5 ', 'Infinity']], rows);
  for (const first of [
    ['', 'B', '1', '1'], ['A', ' ', '1', '1'], ['A', 'B', '', '1'],
    ['A', 'B', '-1', '1'], ['A', 'B', 'NaN', '1'], ['A', 'B', '1e999', '1'],
    ['left', 'right', 'value', 'unused'],
  ]) bad(infinite, [first, ['C', 'D', '2', '']], rows);
});

test('infinite template rejects duplicate pairs, overflow, malformed shapes and oversized matrices', () => {
  bad(infinite, [['A', 'B', '1', ''], ['A', 'B', '2', 'other']], [], /Duplicate/);
  ok(infinite, [['A', 'B', '1', ''], ['B', 'A', '2', '']], []);
  bad(infinite, [['A', 'B', '1e308', ''], ['A', 'C', '1e308', '']], [], /finite/);
  for (const rows of [[], null, [['A', 'B', '1']], [['A', 'B', 1, '']], [['A', 'B', '1', ''], ['C']]]) {
    bad(infinite, rows, [], /table/);
  }
  const left = Array.from({ length: 38 }, (_, i) => [`L${i}`, 'R', '1', '']);
  const right = Array.from({ length: 38 }, (_, i) => ['L', `R${i}`, '1', '']);
  ok(infinite, left.slice(0, 37), []);
  ok(infinite, right.slice(0, 37), []);
  bad(infinite, left, [], /Category count/);
  bad(infinite, right, [], /Category count/);
  const matrix = Array.from({ length: 37 }, (_, i) =>
    Array.from({ length: 37 }, (_, j) => [`L${i}`, `R${j}`, '1', ''])).flat();
  ok(infinite, matrix, []);
  bad(infinite, [...matrix, ['another', 'pair', '1', '']], [], /bounded/);
});

test('force accepts official-shaped 48 nodes and 59 edges, forward references and duplicate edges', () => {
  const rows = [forceHeader, ...Array.from({ length: 59 }, (_, i) => [
    i < 48 ? `${i}` : '', i < 48 ? `node${i}` : '',
    '0', '47', '1', i % 2 ? 'red' : '#00ff00', i < 48 ? '15' : '', '1', '75',
  ])];
  const before = structuredClone(rows);
  ok(force, rows, rows);
  assert.deepEqual(rows, before);
  ok(force, [forceHeader, forceRow({ 2: '1', 3: '0' }), forceRow({ 0: '1', 1: 'second' }),
    forceRow({ 0: '', 1: '', 2: '1', 3: '0', 6: '' })], rows);
});

test('force requires the precise header, paired node fields and sequential node IDs', () => {
  for (const header of [forceHeader.slice(0, -1), [...forceHeader, 'extra'],
    ['ID', ...forceHeader.slice(1)], ['label', 'id', ...forceHeader.slice(2)],
    forceHeader.map((x, i) => i === 0 ? ` ${x}` : x)]) {
    bad(force, [header, header.map(() => '1')], [], /header/);
  }
  for (const row of [forceRow({ 0: '', 1: 'name' }), forceRow({ 0: '0', 1: '' }),
    forceRow({ 0: ' ', 1: ' ' }), forceRow({ 0: '1' }), forceRow({ 0: 'x' }),
    forceRow({ 0: '0.5' })]) bad(force, [forceHeader, row], [], /Node/);
  bad(force, [forceHeader, forceRow(), forceRow({ 0: '0', 1: 'again' })], [], /Node/);
  bad(force, [forceHeader, forceRow(), forceRow({ 0: '2', 1: 'gap' })], [], /Node/);
  // An edge-only row does not consume a node index.
  ok(force, [forceHeader, forceRow(), forceRow({ 0: '', 1: '', 6: 'ignored' }),
    forceRow({ 0: '1', 1: 'second' })], []);
});

test('force rejects incomplete, non-finite, nonexistent and silently skipped edges', () => {
  for (const row of [forceRow({ 2: '', 3: '', 4: '' }), forceRow({ 2: '' }), forceRow({ 3: ' ' }),
    forceRow({ 4: '' }), forceRow({ 2: '-1' }), forceRow({ 2: '0.5' }),
    forceRow({ 3: 'NaN' }), forceRow({ 3: 'Infinity' }), forceRow({ 4: '1e999' }),
    forceRow({ 4: '-3' }), forceRow({ 2: '1' })]) bad(force, [forceHeader, row], []);
  bad(force, [forceHeader, forceRow({ 2: '1', 3: '0', 4: '-4' }), forceRow({ 0: '1', 1: 'B' })], [], /sentinel/);
  ok(force, [forceHeader, forceRow({ 4: '-2' })], []); // value is not required to be positive
  bad(force, [forceHeader, forceRow({ 0: '', 1: '', 6: '' })], [], /existing node/);
});

test('force validates used SVG properties while respecting empty width/distance behavior', () => {
  for (const color of ['red', 'steelblue', '#abc', '#12345678', 'rgb(20, 40, 60)', 'rgba(20,40,60,.5)',
    'rgb(20% 40% 60% / 50%)', 'hsl(120 100% 50%)', 'hwb(120deg 10% 20%)', 'color(display-p3 1 0 0)']) {
    ok(force, [forceHeader, forceRow({ 5: color, 6: '1.5em', 7: '2px', 8: '0' })], []);
  }
  ok(force, [forceHeader, forceRow({ 6: '0', 7: '', 8: '' })], []);
  for (const color of ['', ' ', '#xyz', '#12', 'not-a-color', 'rgb(1)', 'rgb(1 2 nonsense)',
    'hsl(1 2 3)', 'rgba(1,2,3,)', 'url(https://example.com/a)', 'red; fill: blue']) {
    bad(force, [forceHeader, forceRow({ 5: color })], [], /color/);
  }
  for (const size of ['', ' ', '-1', 'NaN', 'Infinity', '1e999', '1000001']) {
    bad(force, [forceHeader, forceRow({ 6: size })], [], /size/);
  }
  for (const width of ['-1', 'NaN', 'Infinity', '1e999', '1000001']) {
    bad(force, [forceHeader, forceRow({ 7: width })], [], /width/);
  }
  for (const distance of ['-1', 'NaN', 'Infinity', '1e999', '1000001', '10px']) {
    bad(force, [forceHeader, forceRow({ 8: distance })], [], /distance/);
  }
  // On an edge-only row the size field is never read.
  ok(force, [forceHeader, forceRow(), forceRow({ 0: '', 1: '', 6: 'ignored' })], []);
});

test('frostbite preserves the first athlete with the exact official header and decimal times', () => {
  const rows = [frostHeader, ['Osone', '0', '58', '23', 'ffffff'],
    ['Mike', '1', '10\t', '24', 'ffffff'], ['Boundary', '03', '00', '00', 'ABCdef']];
  const before = structuredClone(rows);
  ok(frost, rows, rows);
  assert.deepEqual(rows, before);
  ok(frost, [frostHeader, ['One second', ' 0 ', ' 00 ', ' 01 ', 'abcdef']], rows);
  ok(frost, [frostHeader, ['Leading zero', '00', '08', '09', '000000']], rows);
  bad(frost, [['Osone', '0', '58', '23', 'ffffff'], ['Next', '1', '0', '0', 'ffffff']], rows, /header/);
  bad(frost, [['athlete', 'HH', 'MM', 'SS', 'color'], ...rows.slice(1)], rows, /header/);
  bad(frost, [frostHeader, ['', '0', '58', '23', 'ffffff'], rows[2]], rows, /name/);
  bad(frost, [frostHeader, ['First', 'bad', '58', '23', 'ffffff'], rows[2]], rows, /Time/);
});

test('frostbite rejects invalid times, names, colors and table shapes', () => {
  for (const time of [
    ['', '1', '1'], [' ', '1', '1'], ['+1', '0', '0'], ['-1', '0', '0'],
    ['1.0', '0', '0'], ['1e0', '0', '0'], ['0x1', '0', '0'], ['1h', '0', '0'],
    ['0', '60', '0'], ['0', '0', '60'], ['0', '0', '0'], ['3', '0', '1'],
    ['4', '0', '0'], ['9'.repeat(400), '0', '0'],
  ]) bad(frost, [frostHeader, ['Runner', ...time, 'ffffff']], [], /Time/);
  for (const color of ['', '#ffffff', 'fff', 'gggggg', ' ffffff', 'ffffff ']) {
    bad(frost, [frostHeader, ['Runner', '1', '0', '0', color]], [], /Color/);
  }
  for (const rows of [[], null, [frostHeader], [frostHeader, ['Runner', '1', '0']],
    [frostHeader, ['Runner', '1', '0', 0, 'ffffff']]]) bad(frost, rows, [], /table/);
});
