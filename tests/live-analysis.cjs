const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../script.js'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, '../index.html'), 'utf8');
const list = {
  dataset: {}, innerHTML: 'old genres', children: [],
  appendChild(row) { this.children.push(row); },
};
const status = { textContent: '' };
const now = Date.now();
const dj = { username: 'amellashannara', platform: 'TikTok', started_at: new Date(now - 60000).toISOString() };
const context = vm.createContext({
  liveGenresDetected: { style: {} }, liveGenresDetectedList: list,
  liveGenresDetectedStatus: status, liveGenreRequestId: 0, currentFeaturedDJ: dj,
  featuredHeroGenres: [], featuredHeroOverrideActive: false,
  applyFeaturedHeroOverride() {}, getFreshUrl: x => x,
  resetLiveGenreRefreshCountdown() {},
  document: { getElementById: () => null, createElement: () => ({ classList: { add() {} } }) },
  window: {}, escapeHtml: x => x, escapeAttr: x => x, console,
});
vm.runInContext(source.slice(source.indexOf('      const LIVE_AUDIO_MAX_AGE_MS'), source.indexOf('      function getDJGenres')), context);
const current = { detected_at: new Date(now - 10000).toISOString(), genres: [{ genre: 'Electronic---House', confidence: .8 }] };
assert.equal(context.isCurrentLiveAnalysis(current, dj, now), true);
// Exact previous-session timestamp from Amelia's misleading live card.
assert.equal(context.isCurrentLiveAnalysis({ detected_at: '2026-10-02T03:51:49.344634+10:00' }, dj, now), false);
assert.equal(context.isCurrentLiveAnalysis({ detected_at: new Date(now - 90000).toISOString() }, dj, now), false);
assert.equal(context.isCurrentLiveAnalysis({ detected_at: new Date(now - 16 * 60000).toISOString() }, {}, now), false);
assert.equal(context.isCurrentLiveAnalysis({}, dj, now), false);
assert.equal(context.isCurrentLiveAnalysis({ detected_at: new Date(now + 10000).toISOString() }, dj, now), false);

async function run() {
  context.fetch = async () => ({ ok: true, json: async () => ({ detected_at: '2026-10-02T03:51:49.344634+10:00', genres: [{ genre: 'Non-Music---Audiobook', confidence: .28 }] }) });
  await context.updateLiveGenresDetected(null, 'amelia', '', 'TikTok');
  assert.equal(list.innerHTML, '');
  assert.equal(status.textContent, 'Awaiting current audio analysis');
  assert.equal(list.dataset.awaiting, 'true');
  context.fetch = async () => ({ ok: true, json: async () => current });
  await context.updateLiveGenresDetected(null, 'amelia', '', 'TikTok');
  assert.equal(list.children.length, 1);
  assert.match(list.children[0].innerHTML, /House/);
  assert.equal(list.dataset.awaiting, 'false');

  for (const match of html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/gi)) {
    if (!match[1].includes('application/ld+json') && match[2].trim()) new vm.Script(match[2]);
  }
  const start = html.indexOf('      const hasLiveAnalysis');
  const metricCode = html.slice(start, html.indexOf('    } catch (error)', start));
  function metrics(item) {
    const bpmEl = { textContent: '', innerHTML: '' }, talkEl = { classList: { add() {} } };
    vm.runInNewContext(metricCode, { item, bpmEl, talkEl, bpmHealthEl: null, data: {}, formatBpm: x => String(x), scheduleBounds: () => null });
    return { bpmEl, talkEl };
  }
  const missing = metrics({ bpm: null, speech_ratio: 0, speech_confidence: 0, ai_freshness: 0, profile_driven: true });
  assert.match(missing.bpmEl.innerHTML, /—/);
  assert.equal(missing.talkEl.textContent, '—');
  const measured = metrics({ bpm: 115, speech_ratio: 0, speech_confidence: 1, ai_freshness: 1, profile_driven: false });
  assert.match(measured.bpmEl.innerHTML, /115/);
  assert.equal(measured.talkEl.textContent, '0%');
  console.log('PASS: live-analysis freshness, pending card, fresh genres, unavailable metrics, measured zero talk, and inline JavaScript syntax.');
}
run().catch(error => { console.error(error); process.exitCode = 1; });
