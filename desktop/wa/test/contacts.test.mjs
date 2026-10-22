import test from "node:test";
import assert from "node:assert/strict";
import { match } from "../contacts.mjs";

const C = (name, number = "+910000000000") => ({ name, jid: number.slice(1) + "@s.whatsapp.net", number });
const names = (r) => r.map((c) => c.name).sort();

test("rahul matches both Rahuls", () => {
  const cs = [C("Rahul Sharma", "+911"), C("Rahul K", "+912"), C("Priya", "+913")];
  assert.deepEqual(names(match(cs, "rahul")), ["Rahul K", "Rahul Sharma"]);
});

test("rahul k matches only Rahul K", () => {
  const cs = [C("Rahul Sharma", "+911"), C("Rahul K", "+912")];
  assert.deepEqual(names(match(cs, "rahul k")), ["Rahul K"]);
});

test("exact Mom beats Mommy's shop", () => {
  const cs = [C("Mommy's shop", "+911"), C("Mom", "+912")];
  assert.deepEqual(names(match(cs, "mom")), ["Mom"]);
});

test("number matches by digits", () => {
  const cs = [C("Dad", "+919876543210"), C("Mom", "+911111111111")];
  assert.deepEqual(names(match(cs, "+91 98765 43210")), ["Dad"]);
});

test("nothing matches", () => {
  assert.deepEqual(match([C("Mom")], "zebra"), []);
  assert.deepEqual(match([C("Mom")], "  "), []);
});
