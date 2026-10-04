const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const source = fs.readFileSync('script.js', 'utf8');
const ids = ['stuckFrameVideo','stuckFrameStart','stuckFrameFileName','stuckFrameStatus','stuckFrameProgress','stuckFrameDownload','stuckFrameAudioOffer','stuckFrameAudio','stuckFrameAudioName'];
const nodes = Object.fromEntries(ids.map(id => [id, {files:[], hidden:true, events:{}, classList:{remove(){},add(){}}, addEventListener(k,f){this.events[k]=f;},removeAttribute(){}}]));
let response, request;
const ctx = vm.createContext({Blob, URL:{createObjectURL:()=> 'blob:result',revokeObjectURL(){}},document:{getElementById:id=>nodes[id]},fetch:async(url,options)=>{request=options;return response;}});
vm.runInContext(source.slice(source.indexOf('      /* RRR TOOLS — STUCK FRAME EFFECT */'),source.indexOf('      /* User-selected stream speech analysis')),ctx);
const video = Object.assign(new Blob(['video bytes']),{name:'silent.mp4'});
const audio = Object.assign(new Blob(['music bytes']),{name:'music.wav'});
async function run(){
 nodes.stuckFrameVideo.files=[video];nodes.stuckFrameVideo.events.change();
 response={ok:false,json:async()=>({detail:{code:'no_audio',message:'This video has no audio track'}})};
 await ctx.createStuckFrameEffect();
 assert.match(nodes.stuckFrameStatus.textContent,/no audio track/);assert.equal(nodes.stuckFrameAudioOffer.hidden,false);assert.equal(nodes.stuckFrameStart.disabled,true);
 nodes.stuckFrameAudio.files=[audio];nodes.stuckFrameAudio.events.change();assert.equal(nodes.stuckFrameStart.disabled,false);
 response={ok:true,blob:async()=>new Blob(['mp4'])};await ctx.createStuckFrameEffect();
 assert.equal(request.headers['X-RRR-Video-Bytes'],String(video.size));assert.equal(await request.body.text(),'video bytesmusic bytes');
 assert.equal(nodes.stuckFrameDownload.hidden,false);assert.equal(nodes.stuckFrameDownload.download,'silent-stuck-frame.mp4');
 nodes.stuckFrameAudio.files=[{size:101*1024*1024,name:'large.wav'}];nodes.stuckFrameAudio.events.change();assert.equal(nodes.stuckFrameStart.disabled,true);
 nodes.stuckFrameAudio.files=[];nodes.stuckFrameVideo.events.change();assert.equal(nodes.stuckFrameAudioOffer.hidden,true);assert.equal(nodes.stuckFrameStart.disabled,false);
 response={ok:false,json:async()=>({detail:'[out#0/wav] Output file does not contain any stream'})};await ctx.createStuckFrameEffect();assert.match(nodes.stuckFrameStatus.textContent,/no audio track/);
 assert.equal(nodes.stuckFrameVideo.disabled,false);assert.equal(nodes.stuckFrameAudio.disabled,false);
 for(const file of ['index.html',...fs.readdirSync('tools',{recursive:true}).filter(f=>f.endsWith('index.html')).map(f=>'tools/'+f)]){
  const html=fs.readFileSync(file,'utf8');assert.equal((html.match(/id="stuckFrameAudio"/g)||[]).length,1);
 }
 console.log('PASS: missing audio offer, audio retry upload, download, limits, reset and old-server error');
}
run().catch(e=>{console.error(e);process.exitCode=1;});
