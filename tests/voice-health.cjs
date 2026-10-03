const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('script.js', 'utf8');
let result, requestedUrl, payload, fail = false;
const element = { removeAttribute() {} };
const context = vm.createContext({
  document: { getElementById: () => element }, URL, Date, Number,
  getFreshUrl: url => url,
  setToolHealth: (id, healthy, good, bad) => { result = { id, healthy, text: healthy ? good : bad }; },
  fetch: async url => { requestedUrl = new URL(url); return { ok: !fail, json: async () => payload }; }
});
vm.runInContext(source.slice(source.indexOf('      async function refreshVoiceDetection('), source.indexOf('      async function refreshToolsData(')), context);
async function run() {
  const station = { relay_username: 'example', relay_platform: 'Twitch' };
  payload = { detected_at: new Date().toISOString(), genres: [{ analysis: 'speech', speech_ratio: 0 }] };
  await context.refreshVoiceDetection(station);
  assert.equal(result.healthy, true, 'Measured silence is a valid voice-analysis result');
  assert.equal(requestedUrl.searchParams.get('platform'), 'Twitch');
  assert.equal(requestedUrl.searchParams.get('username'), 'example');
  payload.detected_at = new Date(Date.now() - 11 * 60000).toISOString();
  await context.refreshVoiceDetection(station);
  assert.equal(result.text, 'Not reporting');
  payload = { detected_at: new Date().toISOString(), genres: [] };
  await context.refreshVoiceDetection(station);
  assert.equal(result.text, 'No reading');
  fail = true;
  await context.refreshVoiceDetection(station);
  assert.equal(result.text, 'Unavailable');
  await context.refreshVoiceDetection(null);
  assert.equal(result.text, 'No reading');
  assert.match(source, /const monitoredHealthIds = .*"toolVoiceDetection"/);
  console.log('PASS: voice-health monitoring, measured silence, stale/missing samples, API errors and overall health inclusion.');
}
run().catch(error => { console.error(error); process.exitCode = 1; });
