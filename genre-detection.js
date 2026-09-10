(function (root, factory) {
  const api = factory();

  if (typeof module === "object" && module.exports) {
    module.exports = api;
  } else {
    root.RadioRRRGenreDetection = api;
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  // Keep specific genres before their parent genres so the parent can be
  // suppressed when both are present in the same profile.
  const GENRE_TAXONOMY = [
    { label: "PSYTRANCE", aliases: ["psytrance", "psy trance", "psy-trance"], parent: "TRANCE" },
    { label: "PROGRESSIVE TRANCE", aliases: ["progressive trance"], parent: "TRANCE" },
    { label: "TECH HOUSE", aliases: ["tech house"], parent: "HOUSE" },
    { label: "MELODIC TECHNO", aliases: ["melodic techno"], parent: "TECHNO" },
    { label: "DRUM & BASS", aliases: ["drum & bass", "drum and bass", "dnb", "d&b"] },
    { label: "PROGRESSIVE HOUSE", aliases: ["progressive house"], parent: "HOUSE" },
    { label: "DEEP HOUSE", aliases: ["deep house"], parent: "HOUSE" },
    { label: "80s", aliases: ["80s", "80's"] },
    { label: "ELECTRO", aliases: ["electro"] },
    { label: "SYNTH", aliases: ["synth"] },
    { label: "HOUSE", aliases: ["house"] },
    { label: "TECHNO", aliases: ["techno"] },
    { label: "TRANCE", aliases: ["trance"] },
    { label: "HARDSTYLE", aliases: ["hardstyle"] }
  ];

  function escapeRegExp(value) {
    return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }

  function aliasPattern(alias) {
    const lower = alias.toLowerCase();

    if (lower === "drum & bass" || lower === "drum and bass") {
      return /\bdrum\s*(?:&|and)\s*bass\b/i;
    }

    if (lower === "dnb" || lower === "d&b") {
      return /\bd(?:n\s*b|\s*&\s*b)\b/i;
    }

    if (lower === "psytrance" || lower === "psy trance" || lower === "psy-trance") {
      return /\bpsy[\s-]*trance\b/i;
    }

    const parts = lower.split(/[\s-]+/).filter(Boolean).map(escapeRegExp);
    return new RegExp("\\b" + parts.join("[\\s-]+") + "\\b", "i");
  }

  const compiledTaxonomy = GENRE_TAXONOMY.map(entry => ({
    ...entry,
    patterns: entry.aliases.map(aliasPattern)
  }));

  function hasGenre(text, entry) {
    return entry.patterns.some(pattern => pattern.test(text));
  }

  function detectGenres(text) {
    const source = String(text || "");
    const matches = compiledTaxonomy.filter(entry => hasGenre(source, entry));
    const matchedLabels = new Set(matches.map(entry => entry.label));
    const parentLabels = new Set(
      matches
        .map(entry => entry.parent)
        .filter(parent => parent && matchedLabels.has(parent))
    );

    return matches
      .filter(entry => !parentLabels.has(entry.label))
      .map(entry => entry.label);
  }

  function canonicalGenre(value) {
    const source = String(value || "").trim();
    if (!source) return "";

    const match = compiledTaxonomy.find(entry => hasGenre(source, entry));
    return match ? match.label : source;
  }

  function normalizeGenres(values, bio) {
    const rawValues = [];
    const addValue = value => {
      if (value === undefined || value === null) return;
      if (Array.isArray(value)) {
        value.forEach(addValue);
        return;
      }

      String(value)
        .split(/[|,]/)
        .map(item => item.trim())
        .filter(Boolean)
        .forEach(item => {
          const normalized = canonicalGenre(item);
          if (!rawValues.some(existing => existing.toLowerCase() === normalized.toLowerCase())) {
            rawValues.push(normalized);
          }
        });
    };

    addValue(values);

    const detected = detectGenres(bio);
    detected.forEach(addValue);

    const specificLabels = new Set(
      compiledTaxonomy
        .filter(entry => detected.includes(entry.label) && entry.parent)
        .map(entry => entry.parent)
    );

    return rawValues.filter(value => !specificLabels.has(value));
  }

  return {
    GENRE_TAXONOMY,
    canonicalGenre,
    detectGenres,
    normalizeGenres
  };
});
