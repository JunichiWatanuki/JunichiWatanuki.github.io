// Patch only the pinned e2d3-contrib virtual-frostbite-halfmarathon/main.js
// (commit d48bd8256e95cfcc393d37e3e7769000dd738fc9). The map and the
// official course coordinates are both 1200 x 1200.
import { createHash } from 'node:crypto';

const SHA256 = '5cd95063f2cdbbea1a0b9d3863e39c0a68dc2b786c61106bb9b99f30ef08c4e3';

function replaceOnce(source, before, after) {
  const at = source.indexOf(before);
  if (at < 0 || source.indexOf(before, at + 1) >= 0) {
    throw new Error('virtual-frostbite-halfmarathon: expected source fragment not found exactly once');
  }
  return source.slice(0, at) + after + source.slice(at + before.length);
}

// Input and output are Buffers (a UTF-8 string is also accepted as input).
// The caller must select only this template's main.js; no other files change.
export function patchFrostbiteMain(input) {
  if (!Buffer.isBuffer(input) && typeof input !== 'string') {
    throw new TypeError('virtual-frostbite-halfmarathon: main.js must be a Buffer or string');
  }
  const bytes = Buffer.isBuffer(input) ? input : Buffer.from(input, 'utf8');
  if (createHash('sha256').update(bytes).digest('hex') !== SHA256) {
    throw new Error('virtual-frostbite-halfmarathon: unexpected official main.js SHA-256');
  }
  let source = bytes.toString('utf8');
  const initial = `//初期データを登録する。
\tvar rr = 25;
\tvar ref1 = 8;
\teval ("c" + 1 + "= [\\"Osone\\",\\"0\\",\\"58\\",\\"23\\",\\"ffffff\\",0, 0, rr,\\"id1\\"]");
\teval ("c" + 2 + "= [\\"Tajitsu\\",\\"1\\",\\"12\\",\\"34\\",\\"ffffff\\",0, 0, rr,\\"id2\\"]");
\teval ("c" + 3 + "= [\\"Wat\\",\\"2\\",\\"10\\",\\"12\\",\\"ffffff\\",0, 0, rr,\\"id3\\"]");

\tvar arrInitial = new Array();
\tarrInitial.push(c1);
\tarrInitial.push(c2);
\tarrInitial.push(c3);

\tmovecircle(arrInitial);`;
  source = replaceOnce(source, initial, 'var rr = 25;\nvar ref1 = 8;');
  const marker = 'function movecircle(data){';
  const at = source.indexOf(marker);
  if (at < 0 || source.indexOf(marker, at + 1) >= 0 || !source.endsWith('\n\t};')) {
    throw new Error('virtual-frostbite-halfmarathon: unexpected drawing function');
  }
  source = source.slice(0, at) + `// Keep one SVG, including the map, course, and runners, in map coordinates.
// A generation guard and interruption also prevent an old timer from restarting
// after a subsequent E2D3 update.
var frostbiteGeneration = 0;
function movecircle(data) {
  var generation = ++frostbiteGeneration;
  var container = d3.select(root);
  container.selectAll('*').interrupt();
  container.selectAll('*').remove();

  var svg = container.append('svg').attr({
    width: 1200, height: 1200, viewBox: '0 0 1200 1200'
  });
  svg.append('image').attr({
    x: 0, y: 0, width: 1200, height: 1200,
    'xlink:href': baseUrl + '/map.jpg'
  });
  var course = svg.append('path').attr({
    d: pathdata, fill: 'none', stroke: 'lightgreen', 'stroke-width': 5
  });
  var length = course.node().getTotalLength();
  var start = course.node().getPointAtLength(0);
  var color = d3.scale.category10();
  color(0); // Reserve the original hidden reference clock's first color.
  // data[0] is the original invisible three-hour reference clock, not a runner.
  var runners = svg.selectAll('g.runner').data(data.slice(1)).enter()
    .append('g').attr('class', 'runner').attr('id', function(d) { return d[8]; });
  runners.append('circle').attr('r', function(d) { return parseInt(d[7], 10); })
    .attr('fill', function(d, i) { return color(i + 1); });
  runners.append('text').attr({ 'text-anchor': 'middle', dy: '.35em', fill: 'black' })
    .text(function(d) { return d[0]; });

  function startRace() {
    if (generation !== frostbiteGeneration) return;
    runners.attr('transform', 'translate(' + start.x + ',' + start.y + ')');
    runners.filter(function(d) {
      return d[0] !== '' && d[1] !== '' && d[2] !== '' && d[3] !== '' && d[4] !== '';
    }).transition()
      .duration(function(d) { return (parseInt(d[1], 10) * 3600 + parseInt(d[2], 10) * 60 + parseInt(d[3], 10)) * 20; })
      .ease('linear')
      .attrTween('transform', function() {
        return function(t) {
          var p = course.node().getPointAtLength(length * t);
          return 'translate(' + p.x + ',' + p.y + ')';
        };
      });
    // Match the original three-hour reference runner's repeat interval.
    svg.transition().duration(3 * 3600 * 20).ease('linear').each('end', startRace);
  }
  startRace();
}
`;
  return Buffer.from(source, 'utf8');
}
