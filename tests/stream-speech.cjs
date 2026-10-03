const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('script.js','utf8');
const ids=['streamSpeechForm','streamSpeechUrl','streamSpeechStart','streamSpeechResult','streamSpeechValue','streamSpeechDetails','streamSpeechStatus'];
const nodes=Object.fromEntries(ids.map(id=>[id,{value:'',hidden:true,events:{},classList:{remove(){},add(){}},focus(){this.focused=true;},addEventListener(k,f){this.events[k]=f;}}]));
let reading={speech_ratio:.375,sample_seconds:30},calls=0,failed=false;
const context=vm.createContext({URL,AbortController,setTimeout,clearTimeout,document:{getElementById:id=>nodes[id]},fetch:async(url,options)=>{calls++;assert.equal(url,'https://api.radiorrr.com/api/tools/speech');assert.equal(JSON.parse(options.body).url,'https://example.com/live.m3u8');return {ok:!failed,json:async()=>reading};}});
vm.runInContext(source.slice(source.indexOf('      /* User-selected stream speech analysis'),source.indexOf('      /* RRR TOOLS — DJ TALK DETECTOR */')),context);
async function run(){
 nodes.streamSpeechUrl.value='javascript:alert(1)';await context.analyseStreamSpeech();assert.equal(calls,0);assert.equal(nodes.streamSpeechUrl.focused,true);
 nodes.streamSpeechUrl.value='https://example.com/live.m3u8';await context.analyseStreamSpeech();assert.equal(nodes.streamSpeechValue.textContent,'38% speech');assert.equal(nodes.streamSpeechResult.hidden,false);
 reading={speech_ratio:0,sample_seconds:30};await context.analyseStreamSpeech();assert.equal(nodes.streamSpeechValue.textContent,'0% speech');
 reading={speech_ratio:null,sample_seconds:30};await context.analyseStreamSpeech();assert.equal(nodes.streamSpeechResult.hidden,true);assert.match(nodes.streamSpeechStatus.textContent,/did not return/);
 failed=true;reading={detail:'Stream is offline'};await context.analyseStreamSpeech();assert.equal(nodes.streamSpeechStatus.textContent,'Stream is offline');assert.equal(nodes.streamSpeechStart.disabled,false);
 for(const file of ['index.html',...fs.readdirSync('tools',{recursive:true}).filter(f=>f.endsWith('index.html')).map(f=>'tools/'+f)]){
  const html=fs.readFileSync(file,'utf8');assert.equal((html.match(/id="streamSpeechForm"/g)||[]).length,1);assert(html.indexOf('id="streamSpeechForm"')<html.indexOf('id="talkDetectorStart"'));
 }
 console.log('PASS: speech URL validation, API request, percentage, measured zero, missing reading, failure recovery and form placement.');
}
run().catch(e=>{console.error(e);process.exitCode=1;});
