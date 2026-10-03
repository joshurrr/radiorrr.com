const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('script.js','utf8');
let calls=[],fail=false;
const context=vm.createContext({Map,Date,getDJUsername:d=>d.username,getDJPlatform:d=>d.platform,console:{warn(){}},fetch:async(url,options)=>{calls.push({url,data:JSON.parse(options.body)});return {ok:!fail,status:503};}});
vm.runInContext(source.slice(source.indexOf('      const priorityAudioRequestTimes'),source.indexOf('      function showLiveDjPlayer')),context);
async function run(){
 const dj={username:'kamilla_beriya',platform:'TikTok'};
 context.requestSelectedDjAudioScan(dj);await new Promise(r=>setImmediate(r));
 assert.equal(calls.length,1);assert.equal(calls[0].data.username,dj.username);assert.match(calls[0].url,/scan-priority$/);
 context.requestSelectedDjAudioScan(dj);assert.equal(calls.length,1);
 fail=true;const other={username:'other',platform:'Twitch'};context.requestSelectedDjAudioScan(other);await new Promise(r=>setImmediate(r));
 context.requestSelectedDjAudioScan(other);await new Promise(r=>setImmediate(r));assert.equal(calls.length,3);
 assert.match(source,/if \(isManualSelection && username\) requestSelectedDjAudioScan\(dj\)/);
 console.log('PASS: browser-selected DJ priority request, duplicate suppression and retry after failure.');
}
run().catch(e=>{console.error(e);process.exitCode=1;});
