const assert = require("node:assert/strict");
const test = require("node:test");
const buildAxis = require("../app/static/timeline-density.js");

test("dense dates get longer segments while sparse dates remain reachable", () => {
  const axis = buildAxis([
    { date: "2026-10-08", count: 100 },
    { date: "2026-10-07", count: 1 },
    { date: "2006-01-01", count: 4 },
  ]);
  const lengths = axis.bands.map(band => band.end - band.start);
  assert.ok(Math.abs(lengths[0] / lengths[1] - 10) < 1e-10);
  assert.ok(lengths[2] > lengths[1]);
  assert.equal(axis.bands[0].start, 0);
  assert.equal(axis.bands.at(-1).end, 1000);
  for (const band of axis.bands) {
    assert.equal(axis.dateFor(band.position), band.date);
    assert.equal(axis.dateFor(band.start), band.date);
    assert.equal(axis.valueFor(band.date), band.position);
  }
});

test("year allocation follows total record density, including dates outside the rendered batch", () => {
  const axis = buildAxis([
    { date: "2026-10-08", count: 81 },
    { date: "2026-10-07", count: 81 },
    { date: "2025-01-01", count: 1 },
  ]);
  assert.ok(axis.bands[1].end > 900);
  assert.equal(axis.dateFor(999), "2025-01-01");
});

test("empty, single-day and equal density archives produce valid positions", () => {
  assert.equal(buildAxis([]).dateFor(100), null);
  const single = buildAxis([{ date: "2020-01-01", count: 5 }]);
  assert.equal(single.valueFor("2020-01-01"), 500);
  assert.equal(single.dateFor(-100), "2020-01-01");
  assert.equal(single.dateFor(2000), "2020-01-01");
  const equal = buildAxis([{ date: "2026-01-01", count: 1 }, { date: "2024-01-01", count: 1 }]);
  assert.equal(equal.valueFor("2026-01-01"), 250);
  assert.equal(equal.valueFor("2024-01-01"), 750);
});

test("adaptive tick lengths preserve count ordering and a visible lower endpoint", () => {
  const axis = buildAxis([
    { date: "2026-10-18", count: 16 },
    { date: "2026-10-17", count: 4 },
    { date: "2026-10-16", count: 1 },
  ]);
  assert.equal(axis.bands[0].density, 1);
  assert.equal(axis.bands[2].density, .25);
  assert.ok(axis.densityFor("2026-10-17") > axis.densityFor("2026-10-16"));
  assert.ok(axis.densityFor("2026-10-17") < axis.densityFor("2026-10-18"));
  assert.equal(axis.densityFor("missing"), 0);
  assert.equal(buildAxis([]).densityFor("missing"), 0);
  assert.equal(buildAxis([{ date: "2026-10-18", count: 3 }]).densityFor("2026-10-18"), .625);
  for (const band of axis.bands) assert.equal(axis.dateFor(axis.valueFor(band.date)), band.date);
});

test("heat spans the filtered count range and equal counts share a neutral hue", () => {
  const axis = buildAxis([
    { date: "2026-10-18", count: 7 },
    { date: "2026-10-17", count: 3 },
    { date: "2026-10-16", count: 1 },
  ]);
  [1, .5, 0].forEach((heat, index) => assert.ok(Math.abs(axis.bands[index].heat - heat) < 1e-10));
  assert.equal(axis.heatFor("2026-10-18"), 1);
  assert.equal(axis.heatFor("2026-10-16"), 0);
  assert.equal(buildAxis([{ date: "2026-01-01", count: 4 }, { date: "2025-01-01", count: 4 }]).heatFor("2025-01-01"), .5);
  assert.equal(buildAxis([]).heatFor("missing"), .5);
  for (const band of axis.bands) assert.equal(axis.dateFor(band.position), band.date);
});

test("rare count spikes do not flatten the middle of the density ruler", () => {
  const entries = [...Array(9)].map((_, index) => ({ date: `2026-10-${18 - index}`, count: index + 1 }));
  entries.push({ date: "2025-01-01", count: 100000 });
  const axis = buildAxis(entries);
  assert.equal(axis.bands.at(-1).density, 1);
  assert.ok(axis.bands.at(-1).end - axis.bands.at(-1).start < 250);
  assert.ok(axis.bands[8].density > .8);
  for (let index = 1; index < axis.bands.length; index++) {
    assert.ok(axis.bands[index].density >= axis.bands[index - 1].density);
    assert.ok(axis.bands[index].heat >= axis.bands[index - 1].heat);
  }
  const repeated = buildAxis([...Array(12)].map((_, index) => ({ date: String(index), count: index === 11 ? 100000 : 2 })));
  assert.ok(repeated.bands.at(-1).end - repeated.bands.at(-1).start < 150);
});

test("the easing curve is gentle at both ends and more sensitive in the middle", () => {
  const counts = [1, 1.01, 2.99, 3, 3.01, 6.99, 7];
  const axis = buildAxis(counts.map((count, index) => ({ date: String(index), count })));
  const normalized = count => (Math.log1p(count) - Math.log(2)) / (Math.log(8) - Math.log(2));
  const slope = (from, to) => (axis.bands[to].heat - axis.bands[from].heat) / (normalized(counts[to]) - normalized(counts[from]));
  assert.ok(slope(2, 4) > 1.4);
  assert.ok(slope(0, 1) < .1);
  assert.ok(slope(5, 6) < .1);
});
