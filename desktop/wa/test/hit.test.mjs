import test from "node:test";
import assert from "node:assert/strict";
import hit from "../../hit.js";

const win = { x: 680, y: 0, width: 560, height: 660 };
const rect = { x: 220, y: 10, w: 120, h: 38 };   // the island, in window coordinates

test("the cursor over the island (screen coordinates) hits", () => {
  assert.equal(hit.over({ x: 680 + 280, y: 30 }, win, rect), true);
});
test("a little margin around the pill counts", () => {
  assert.equal(hit.over({ x: 680 + 215, y: 30 }, win, rect), true);
});
test("elsewhere in the window does not", () => {
  assert.equal(hit.over({ x: 680 + 20, y: 400 }, win, rect), false);
});
test("no rect reported yet means no hit", () => {
  assert.equal(hit.over({ x: 960, y: 30 }, win, null), false);
});
