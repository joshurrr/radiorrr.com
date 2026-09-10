const assert = require("assert");
const {
  canonicalGenre,
  detectGenres,
  normalizeGenres
} = require("./genre-detection.js");

assert.deepStrictEqual(detectGenres("psytrance producer"), ["PSYTRANCE"]);
assert.deepStrictEqual(detectGenres("psy trance DJ"), ["PSYTRANCE"]);
assert.deepStrictEqual(detectGenres("psy-trance producer"), ["PSYTRANCE"]);
assert.deepStrictEqual(detectGenres("PsyTrance producer & visual artist"), ["PSYTRANCE"]);
assert.deepStrictEqual(detectGenres("trance DJ"), ["TRANCE"]);
assert.deepStrictEqual(detectGenres("progressive trance DJ"), ["PROGRESSIVE TRANCE"]);
assert.deepStrictEqual(detectGenres("tech-house and melodic techno"), ["TECH HOUSE", "MELODIC TECHNO"]);
assert.deepStrictEqual(detectGenres("drum and bass / DnB"), ["DRUM & BASS"]);
assert.deepStrictEqual(detectGenres("trance-like sound"), ["TRANCE"]);
assert.deepStrictEqual(detectGenres("entranced producer"), []);
assert.deepStrictEqual(detectGenres("housekeeping and technology"), []);
assert.strictEqual(canonicalGenre("psy-trance"), "PSYTRANCE");
assert.deepStrictEqual(
  normalizeGenres(["TRANCE"], "psytrance producer"),
  ["PSYTRANCE"]
);

console.log("Genre detection tests passed");
