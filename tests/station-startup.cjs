const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '..');
const server = http.createServer((req, res) => {
  let file = path.resolve(root, '.' + new URL(req.url, 'http://localhost').pathname);
  if (file !== root && !file.startsWith(root + path.sep)) return res.writeHead(403).end();
  if (fs.existsSync(file) && fs.statSync(file).isDirectory()) file = path.join(file, 'index.html');
  if (!fs.existsSync(file)) return res.writeHead(404).end();
  res.setHeader('Content-Type', {'.html':'text/html', '.js':'text/javascript', '.css':'text/css'}[path.extname(file)] || 'application/octet-stream');
  fs.createReadStream(file).pipe(res);
});
async function run() {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  const browser = await chromium.launch({headless:true, ...(process.platform === 'win32' ? {channel:'msedge'} : {})});
  try {
    for (const scenario of ['missing relay', 'slow catalogue', 'slow ranking']) {
      const page = await browser.newPage();
      let relay = scenario === 'slow ranking' ? {username:'fixture', name:'Fixture DJ', platform:'TikTok'} : null;
      let release;
      const gate = new Promise(resolve => { release = resolve; });
      await page.addInitScript(() => {
        window.hlsFixture = {attached:0, destroyed:0, urls:[]};
        const realNow = Date.now;
        window.fixtureTimeOffset = 0;
        Date.now = () => realNow() + window.fixtureTimeOffset;
        window.Hls = class {
          static Events = {MANIFEST_PARSED:'manifest', ERROR:'error'};
          static ErrorDetails = {BUFFER_STALLED_ERROR:'stall'};
          static ErrorTypes = {NETWORK_ERROR:'network', MEDIA_ERROR:'media'};
          static isSupported() {return true;}
          loadSource(url) {window.hlsFixture.urls.push(url);}
          attachMedia(video) {
            window.hlsFixture.attached++;
            const canvas = document.createElement('canvas');
            canvas.width = canvas.height = 64;
            this.timer = setInterval(() => canvas.getContext('2d').fillRect(0,0,64,64), 40);
            video.srcObject = canvas.captureStream(25);
          }
          on(event, callback) {if(event === 'manifest') setTimeout(callback, 0);}
          destroy() {window.hlsFixture.destroyed++; clearInterval(this.timer);}
          startLoad() {}
          recoverMediaError() {}
        };
      });
      await page.route('**/*', async route => {
        const url = route.request().url();
        if (url.startsWith(base)) return route.continue();
        if (url.includes('/api/live?') || url.endsWith('/api/live')) {
          if (scenario === 'slow catalogue') await gate;
          return route.fulfill({json:{relay, live:relay ? [relay] : [], favourites:[]}});
        }
        if (url.includes('/api/genre-match')) {
          if (scenario === 'slow ranking') await gate;
          return route.fulfill({json:{ranked:[]}});
        }
        if (url.includes('api.radiorrr.com')) return route.fulfill({json:{}});
        return route.abort();
      });
      await page.goto(base);
      await page.waitForFunction(() => document.getElementById('liveDjVideo').currentTime > 0.2);
      let fixture = await page.evaluate(() => window.hlsFixture);
      assert.equal(fixture.attached, 1, `${scenario}: stream starts without metadata`);
      assert.equal(fixture.destroyed, 0);
      assert.match(fixture.urls[0], /^https:\/\/stream\.radiorrr\.com\/api\/live-stream\?/);
      await page.locator('#liveDjUnmute').click();
      const before = await page.locator('#liveDjVideo').evaluate(v => v.currentTime);
      relay = {username:'fixture', name:'Fixture DJ', platform:'TikTok'};
      release();
      await page.evaluate(() => window.dispatchEvent(new Event('pageshow')));
      await page.waitForFunction(() => document.getElementById('randomLiveDjName').textContent.includes('Fixture DJ'));
      fixture = await page.evaluate(() => window.hlsFixture);
      assert.equal(fixture.attached, 1, 'DJ metadata must keep the shared HLS connection');
      assert.equal(fixture.destroyed, 0);
      assert.equal(await page.locator('#liveDjVideo').evaluate(v => v.muted), false);
      await page.waitForFunction(t => document.getElementById('liveDjVideo').currentTime > t, before + 0.2);
      // A station handoff updates metadata while the shared feed remains connected.
      relay = {username:'second', name:'Second DJ', platform:'YouTube'};
      await page.evaluate(() => window.dispatchEvent(new Event('pageshow')));
      await page.waitForFunction(() => document.getElementById('randomLiveDjName').textContent.includes('Second DJ'));
      assert.equal(await page.evaluate(() => window.hlsFixture.attached), 1);
      assert.equal(await page.locator('#liveDjVideo').evaluate(v => v.muted), false);
      relay = null;
      await page.evaluate(() => window.dispatchEvent(new Event('pageshow')));
      await page.waitForTimeout(250);
      await page.evaluate(() => {
        window.fixtureTimeOffset = 121000;
        window.dispatchEvent(new Event('pageshow'));
      });
      await page.waitForFunction(() => document.getElementById('randomLiveDjName').textContent.includes('waiting for DJ details'));
      assert.equal(await page.evaluate(() => window.hlsFixture.attached), 1);
      assert.equal(await page.evaluate(() => window.hlsFixture.destroyed), 0);
      assert.equal(await page.locator('#liveDjVideo').evaluate(v => v.muted), false);
      console.log(`PASS: ${scenario}; playback advances before metadata, preserves HLS and unmute across metadata, DJ handoff and expired metadata grace`);
      await page.close();
    }
  } finally {await browser.close();}
}
run().catch(error => {console.error(error); process.exitCode=1;}).finally(() => server.close());
