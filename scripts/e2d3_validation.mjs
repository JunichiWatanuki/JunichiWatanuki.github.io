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
  return validateGeneric(rows, defaults);
}
