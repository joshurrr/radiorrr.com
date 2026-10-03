const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('script.js', 'utf8');
class Element {
  constructor() { this.value = ''; this.children = []; this.attrs = {}; this.events = {}; this.textContent = ''; }
  set innerHTML(value) { this.html = value; this.children = []; }
  get innerHTML() { return this.html || ''; }
  setAttribute(name, value) { this.attrs[name] = value; }
  addEventListener(name, callback) { this.events[name] = callback; }
  appendChild(child) { this.children.push(child); }
  replaceChildren() { this.children = []; }
}
const ids = ['allDjsSearch','allDjsPlatform','allDjsLive','allDjsGenres','allDjsResults','allDjsStatus','allDjsRefresh','allDjsClear'];
const elements = Object.fromEntries(ids.map(id => [id, new Element()]));
const catalogue = [
  {username:'offline',name:'Offline DJ',platform:'TikTok',live:false,rrr_learned_genres:['House','Techno','Trance','Disco','Garage','Electro','Rare Genre'],bio:'Brisbane DJ'},
  {username:'live',name:'Live DJ',platform:'Twitch',live:true,genre:'Electronic---House',profile_url:'javascript:alert(1)'},
  {username:'empty',name:'No genres',platform:'YouTube',live:false}
];
catalogue.push(...[1,2,3].map(i=>({username:'extra'+i,name:'Extra '+i,platform:'TikTok',live:false,rrr_learned_genres:['Rare Genre','House']})));
let fails = false;
const context = vm.createContext({ document:{getElementById:id=>elements[id],createElement:()=>new Element()},
  getDJPlatform:dj=>dj.platform, getDJUsername:dj=>dj.username, getOfflineDjProfileUrl:dj=>dj.profile_url || '',
  getGenreNeonClass:()=> 'genre-neon-hot-pink', getGenrePillHtml:g=>'<span>'+g+'</span>',
  escapeAttr:s=>s, escapeHtml:s=>s, getFreshUrl:s=>s, URL, console:{error(){}},
  fetch:async()=> { if(fails) throw new Error('offline'); return {ok:true,json:async()=>({favourites:catalogue})}; }
});
vm.runInContext(source.slice(source.indexOf('      /* All-DJ catalogue:'),source.indexOf('      /* RRR TOOLS — PASSIVE STREAM HEALTH TEST')),context);
async function run() {
  await context.loadAllDjsTool();
  assert.equal(elements.allDjsResults.children.length,6);
  assert.equal(elements.allDjsGenres.children.length,2);
  const rare = elements.allDjsGenres.children.find(b=>b.textContent.startsWith('Rare Genre'));
  rare.events.click();
  assert.equal(rare.attrs['aria-pressed'],'true');
  assert.equal(elements.allDjsResults.children.length,4);
  assert(elements.allDjsResults.children.some(card=>card.innerHTML.includes('Offline DJ')));
  assert.match(rare.className,/dj-genre-pill.*genre-neon/);
  assert(!elements.allDjsGenres.children.some(b=>b.textContent.startsWith('Techno')));
  const house = elements.allDjsGenres.children.find(b=>b.textContent.startsWith('House'));
  house.events.click();
  assert.equal(elements.allDjsResults.children.length,5);
  elements.allDjsLive.value='live';elements.allDjsLive.events.change();
  assert.equal(elements.allDjsResults.children.length,1);
  assert.doesNotMatch(elements.allDjsResults.children[0].innerHTML,/javascript:/);
  elements.allDjsClear.events.click();
  elements.allDjsSearch.value='@offline Brisbane';elements.allDjsSearch.events.input();
  assert.equal(elements.allDjsResults.children.length,1);
  elements.allDjsPlatform.value='Twitch';elements.allDjsPlatform.events.change();
  assert.match(elements.allDjsResults.children[0].textContent,/No DJs match/);
  elements.allDjsClear.events.click();
  fails=true;await context.loadAllDjsTool();
  assert.match(elements.allDjsStatus.textContent,/previously loaded catalogue/);
  assert.equal(elements.allDjsResults.children.length,6);
  assert.equal(elements.allDjsRefresh.disabled,false);
  for (const file of ['index.html',...fs.readdirSync('tools',{recursive:true}).filter(f=>f.endsWith('index.html')).map(f=>'tools/'+f)]) {
    const html=fs.readFileSync(file,'utf8');
    assert.equal((html.match(/id="allDjsToolPanel"/g)||[]).length,1,file);
    assert.match(html,/data-tool-route-link="djs" href="\/tools\/djs\/"/);
    assert.match(html,/src="\/script.js\?v=20261004.6"/);
    for(const match of html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/gi)) {
      if(match[1].includes('application/ld+json')) JSON.parse(match[2]);
      else if(match[2].trim()) new vm.Script(match[2]);
    }
  }
  console.log('PASS: all DJ inclusion, complete genres, multi-genre selection, combined filters, clearing, unsafe URL rejection, refresh failures, page links and script syntax.');
}
run().catch(e=>{console.error(e);process.exitCode=1});
