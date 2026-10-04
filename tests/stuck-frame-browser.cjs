// Real browser check of track selection, preview playback and audio upload.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const http = require('node:http');
const {chromium} = require('playwright');
const root = path.resolve(__dirname,'..');
const server = http.createServer((req,res)=>{
 let file = path.resolve(root,'.'+new URL(req.url,'http://localhost').pathname);
 if(!file.startsWith(root+path.sep)) {res.writeHead(403).end();return;}
 if(fs.existsSync(file) && fs.statSync(file).isDirectory()) file=path.join(file,'index.html');
 if(!fs.existsSync(file)) {res.writeHead(404).end();return;}
 res.setHeader('Content-Type',({'.html':'text/html','.js':'text/javascript','.css':'text/css','.mp3':'audio/mpeg','.png':'image/png'})[path.extname(file)]||'application/octet-stream');
 fs.createReadStream(file).pipe(res);
});
async function run(){
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const base='http://127.0.0.1:'+server.address().port;
 const browser=await chromium.launch({headless:true,...(process.platform==='win32'?{channel:'msedge'}:{})});
 let upload;
 try{
  const page=await browser.newPage({viewport:{width:1280,height:900}});
  await page.route('**/*',route=>{
   if(route.request().url().endsWith('/api/tools/stuck-frame-effect')){
    upload=route.request();
    return route.fulfill({status:422,contentType:'application/json',body:JSON.stringify({detail:{code:'no_audio',message:'No audio'}})});
   }
   return route.request().url().startsWith(base)?route.continue():route.abort();
  });
  await page.goto(base+'/tools/stuck-frame-effect/');
  await page.locator('#stuckFrameVideo').setInputFiles({name:'silent.mp4',mimeType:'video/mp4',buffer:Buffer.alloc(2048,7)});
  await page.locator('#stuckFrameAudioOffer').waitFor({state:'visible'});
  await page.locator('#stuckFrameStart').click();
  await page.waitForFunction(()=>document.getElementById('stuckFrameStatus').textContent.includes('no audio track'));
  assert(await page.locator('#stuckFrameStart').isDisabled());
  await page.locator('#stuckFrameMusic').selectOption('techno');
  assert(!(await page.locator('#stuckFrameStart').isDisabled()));
  await page.locator('#stuckFrameMusicPreview').evaluate(async audio=>{await audio.play();});
  await page.waitForFunction(()=>document.getElementById('stuckFrameMusicPreview').currentTime>0);
  await page.locator('#stuckFrameMusicPreview').evaluate(audio=>audio.pause());
  await page.locator('.tool-stuck-frame-effect').screenshot({path:path.join(os.tmpdir(),'radiorrr-music-desktop.png')});
  await page.locator('#stuckFrameStart').click();
  await page.waitForFunction(()=>document.getElementById('stuckFrameStart').textContent==='Create Effect');
  assert.equal(upload.headers()['x-rrr-video-bytes'],'2048');
  assert.equal(upload.headers()['x-rrr-music-preset'],'techno');
  assert.equal(upload.postDataBuffer().length,2048+fs.statSync(path.join(root,'audio/stuck-frame/techno.mp3')).size);
  await page.setViewportSize({width:390,height:844});
  await page.locator('#stuckFrameMusic').selectOption('chill');
  assert(await page.locator('#stuckFrameMusicPreview').isVisible());
  const overflow=await page.locator('.tool-stuck-frame-effect').evaluate(el=>el.scrollWidth>el.clientWidth+1);
  assert.equal(overflow,false,'Music panel overflows mobile card');
  await page.locator('.tool-stuck-frame-effect').screenshot({path:path.join(os.tmpdir(),'radiorrr-music-mobile.png')});
  console.log('PASS: desktop/mobile music picker, no-audio retry, real preview playback and combined upload');
  console.log(path.join(os.tmpdir(),'radiorrr-music-desktop.png'));
  console.log(path.join(os.tmpdir(),'radiorrr-music-mobile.png'));
 }finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
}
run().catch(e=>{console.error(e);server.close();process.exitCode=1;});
