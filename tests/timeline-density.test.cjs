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
