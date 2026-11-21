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
