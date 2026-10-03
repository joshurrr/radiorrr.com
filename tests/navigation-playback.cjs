// Browser regression: navigation must retain playing audio/video and mute choices.
// Run with Playwright available on NODE_PATH. On Windows this uses installed Edge.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '..');
const server = http.createServer((req, res) => {
  let file = path.resolve(root, '.' + new URL(req.url, 'http://localhost').pathname);
  if (!file.startsWith(root + path.sep) && file !== root) { res.writeHead(403).end(); return; }
  if (fs.existsSync(file) && fs.statSync(file).isDirectory()) file = path.join(file, 'index.html');
  if (!fs.existsSync(file)) { res.writeHead(404).end(); return; }
  const type = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.png': 'image/png' }[path.extname(file)];
  res.setHeader('Content-Type', type || 'application/octet-stream');
  fs.createReadStream(file).pipe(res);
});

async function run() {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  const browser = await chromium.launch({ headless: true, ...(process.platform === 'win32' ? { channel: 'msedge' } : {}) });
  try {
    const page = await browser.newPage();
    // No live broadcast or third-party API is needed to test the playback lifecycle.
    await page.route('**/*', route => route.request().url().startsWith(base) ? route.continue() : route.abort());
    for (const entry of ['/', '/tools/', '/tools/bpm-detector/']) {
      await page.goto(base + entry);
      await page.waitForTimeout(200);
      await page.evaluate(async () => {
        const context = new AudioContext();
        await context.resume();
        const oscillator = context.createOscillator();
        const destination = context.createMediaStreamDestination();
        oscillator.connect(destination);
        oscillator.start();
        const canvas = document.createElement('canvas');
        canvas.width = canvas.height = 64;
        const videoStream = canvas.captureStream(20);
        videoStream.addTrack(destination.stream.getAudioTracks()[0]);
        const timer = setInterval(() => {
          const ctx = canvas.getContext('2d');
          ctx.fillStyle = Date.now() % 2 ? 'red' : 'blue';
          ctx.fillRect(0, 0, 64, 64);
        }, 40);
        const audio = document.getElementById('liveStream');
        const video = document.getElementById('liveDjVideo');
        audio.srcObject = destination.stream;
        video.srcObject = videoStream;
        video.muted = true;
        document.getElementById('liveDjUnmute').click();
        await Promise.all([audio.play(), video.play()]);
        window.playbackFixture = { audio, video, context, oscillator, timer };
      });
      const verify = async muted => {
        await page.waitForTimeout(100);
        const state = await page.evaluate(() => {
          const { audio, video } = window.playbackFixture;
          return { sameAudio: audio === document.getElementById('liveStream'), sameVideo: video === document.getElementById('liveDjVideo'), audioPaused: audio.paused, videoPaused: video.paused, videoMuted: video.muted, audioMuted: audio.muted, time: audio.currentTime };
        });
        assert.equal(state.sameAudio, true);
        assert.equal(state.sameVideo, true);
        assert.equal(state.audioPaused, false);
        assert.equal(state.videoPaused, false);
        assert.equal(state.audioMuted, false);
        assert.equal(state.videoMuted, muted);
        return state.time;
      };
      const initialTime = await verify(false);
      for (const target of ['tools-section', 'events-section', 'stream-section', 'live-section', 'tools-section']) {
        await page.locator(`.nav-tabs a[data-target="${target}"]`).click();
        await verify(false);
      }
      assert.equal(await page.locator('.tools-intro').isVisible(), true);
      assert.equal(await page.locator('.tools-page').count(), 1);
      for (const route of ['djs', 'live-djs', 'bpm-detector', 'dj-speech-detector', 'stream-health', 'stuck-frame-effect']) {
        await page.locator(`#toolsHubGrid [data-tool-route-link="${route}"]`).click();
        assert.equal(new URL(page.url()).pathname, `/tools/${route}/`);
        assert.equal(await page.locator('#toolsHubGrid').isVisible(), false);
        await verify(false);
        if (route === 'bpm-detector') assert.equal(await page.locator('#bpmDetectorUrl').isVisible(), true);
        const back = page.locator('[data-tool-back]:visible').first();
        if (route === 'stream-health') await page.locator('#streamHealthClose').click();
        else if (await back.count()) await back.click();
        else await page.locator('#liveDjsToolClose').click();
        assert.equal(new URL(page.url()).pathname, '/tools/');
        await verify(false);
      }
      await page.goBack();
      assert.equal(new URL(page.url()).pathname, '/tools/stuck-frame-effect/');
      await verify(false);
      await page.goForward();
      await verify(false);
      assert.ok((await verify(false)) > initialTime, 'Playback time must keep advancing');
      await page.evaluate(() => {
        document.getElementById('liveDjUnmute').click();
      });
      await page.locator('.nav-tabs a[data-target="events-section"]').click();
      await verify(true);
      await page.goBack();
      await verify(true);
      // Modified clicks retain the normal browser link behavior.
      assert.equal(await page.locator('#toolsHubGrid [data-tool-route-link="bpm-detector"]').evaluate(el => el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, ctrlKey: true, button: 0 }))), true);
      await page.evaluate(() => {
        const { context, oscillator, timer } = window.playbackFixture;
        clearInterval(timer); oscillator.stop(); context.close();
      });
      console.log(`PASS: continuous playback, tools, history, explicit mute and modified clicks from ${entry}`);
    }
  } finally { await browser.close(); }
}
run().catch(error => { console.error(error); process.exitCode = 1; }).finally(() => server.close());
