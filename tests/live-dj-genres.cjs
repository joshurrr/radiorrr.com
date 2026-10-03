const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('script.js', 'utf8');
class Element {
  constructor() { this.children=[]; this.attrs={}; this.events={}; this.dataset={}; this.value=''; }
  setAttribute(k,v) { this.attrs[k]=v; }
  addEventListener(k,v) { this.events[k]=v; }
  appendChild(el) { this.children.push(el); }
  replaceChildren() { this.children=[]; }
  set innerHTML(v) { this.html=v; this.children=[]; }
  get innerHTML() { return this.html; }
}
const genres = new Element(), results = new Element(), search = new Element(), status = new Element();
const ctx=vm.createContext({liveDjsToolGenres:genres,liveDjsToolResults:results,liveDjsToolSearch:search,liveDjsToolStatus:status,
  liveDjsToolLoading:false,liveDjsToolSelectedGenre:'',
  liveDjsToolItems: Array.from({length:5},(_,i)=>({dj:{username:'dj'+i,name:'DJ '+i,platform:'TikTok',genres:i<4?['House','house','Techno']:['Techno','Trance']}})),
  getLiveAIGenres:dj=>Array.from(new Map(dj.genres.map(g=>[g.toLowerCase(),g])).values()),
  getGenreNeonClass:g=>g.toLowerCase()==='house'?'genre-neon-hot-pink':'genre-neon-electric-blue',
  getDJUsername:dj=>dj.username,getDJPlatform:dj=>dj.platform,getDJIdentity:dj=>dj.username,
  getDJPlatformBadgeHtml:()=>'',escapeHtml:String,escapeAttr:String,
  document:{createElement:()=>new Element()}
});
vm.runInContext(source.slice(source.indexOf('      function renderLiveDjsToolGenres()'),source.indexOf('      async function loadLiveDjsTool()')),ctx);
ctx.renderLiveDjsToolGenres();ctx.renderLiveDjsTool();
assert.equal(results.children.length,5);
assert.equal(genres.children.length,3); // All, House (4), Techno (5); Trance (1) is hidden.
const house=genres.children.find(b=>b.textContent.startsWith('house')||b.textContent.startsWith('House'));
assert.match(house.textContent,/ · 4$/);
assert.match(house.className,/dj-genre-pill.*genre-neon-hot-pink/);
house.events.click();assert.equal(results.children.length,4);
assert.equal(genres.children.find(b=>b.textContent===house.textContent).attrs['aria-pressed'],'true');
search.value='dj0';ctx.renderLiveDjsTool();assert.equal(results.children.length,1);
search.value='';genres.children[0].events.click();assert.equal(results.children.length,5);
search.value='trance';ctx.renderLiveDjsTool();assert.equal(results.children.length,1);
ctx.liveDjsToolItems=ctx.liveDjsToolItems.slice(0,3);ctx.renderLiveDjsToolGenres();assert.equal(genres.children.length,1);
assert.match(genres.children[0].textContent,/at least four/);
console.log('PASS: live genre frequency threshold, site colours, exact genre filtering, combined search, clearing and low-count empty state.');
