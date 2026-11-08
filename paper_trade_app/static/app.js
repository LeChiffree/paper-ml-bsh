const $ = (id) => document.getElementById(id);
const money = new Intl.NumberFormat('tr-TR',{style:'currency',currency:'TRY',maximumFractionDigits:2});
const num = new Intl.NumberFormat('tr-TR');
let timer, searchTimer;

async function api(path){const r=await fetch(path,{cache:'no-store'});if(!r.ok)throw new Error(`${r.status} ${path}`);return r.json()}
function duration(s){s=Math.max(0,s||0);const h=Math.floor(s/3600),m=Math.floor(s%3600/60),x=s%60;return [h,m,x].map(v=>String(v).padStart(2,'0')).join(':')}
function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[c]))}
function dt(v){if(!v)return '—';try{return new Date(v).toLocaleString('tr-TR',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit'})}catch{return v}}

function renderStatus(s){
  $('engineState').textContent=(s.state||'initializing').toUpperCase();
  $('statusDot').className=['running','shadow-ready'].includes(s.state)?'ok':s.state==='error'?'error':'';
  $('uptime').textContent=duration(s.uptime_seconds);$('universe').textContent=`${s.symbols||0} coin · ${s.models||0} model`;
  $('lastUpdate').textContent=s.last_cycle_at?`Son tarama ${dt(s.last_cycle_at)}`:'Geçmiş veriler hazırlanıyor';
  const tfs=['5m','15m','1h','4h'];
  $('timeframes').innerHTML=tfs.map(tf=>{const c=s.coverage?.[tf]||0,p=Math.min(100,c/(s.symbols||106)*100);return `<div class="tf"><strong>${tf}</strong><div class="cov">${c} / ${s.symbols||106} piyasa</div><div class="track"><i style="width:${p}%"></i></div><time>${esc(s.last_close?.[tf]||'bekleniyor')}</time></div>`}).join('');
}
function renderSummary(d){
  const t=d.totals||{}, pos=Object.fromEntries((d.positions||[]).map(x=>[x.status,x]));
  const open=(pos.OPEN?.count||0)+(pos.PENDING?.count||0), reserved=(pos.OPEN?.notional||0)+(pos.PENDING?.notional||0);
  $('pnl').textContent=money.format(t.realized_pnl||0);$('pnl').className=(t.realized_pnl||0)>=0?'positive':'negative';
  $('openPositions').textContent=num.format(open);$('reserved').textContent=`${money.format(reserved)} ayrılmış sermaye`;
  $('trades').textContent=num.format(t.trades||0);$('winRate').textContent=t.trades?`%${((t.wins/t.trades)*100).toFixed(1)} kazanma oranı`:'— kazanma oranı';
  $('signals').textContent=num.format(d.signals_24h||0);
  const max=Math.max(1,...d.timeframes.map(x=>Math.abs(x.realized_pnl||0)));
  $('bars').innerHTML=d.timeframes.map(x=>`<div class="barrow"><span>${x.timeframe}</span><div class="bartrack"><i class="${x.realized_pnl<0?'neg':''}" style="width:${Math.max(1,Math.abs(x.realized_pnl||0)/max*100)}%"></i></div><b class="${x.realized_pnl>=0?'positive':'negative'}">${money.format(x.realized_pnl||0)}</b></div>`).join('');
}
async function loadScenarios(){
  const q=new URLSearchParams();if($('tfFilter').value)q.set('timeframe',$('tfFilter').value);if($('stateFilter').value!=='')q.set('enabled',$('stateFilter').value);if($('search').value)q.set('search',$('search').value);
  const rows=await api('/api/scenarios?'+q);
  $('scenarioRows').innerHTML=rows.length?rows.map(x=>`<tr><td>${x.scenario_id}</td><td><span class="tag">${x.timeframe}</span></td><td title="${esc(x.scenario)}">${esc(x.strategy)}</td><td>${esc(x.filter_name)}</td><td><span class="tag ${x.enabled?'':'blocked'}">${x.enabled?'aktif':'bloklu'}</span></td><td>${x.trade_count}</td><td>${x.win_rate==null?'—':'%'+x.win_rate.toFixed(1)}</td><td class="${x.realized_pnl>=0?'positive':'negative'}">${money.format(x.realized_pnl)}</td><td class="${x.return_pct>=0?'positive':'negative'}">%${x.return_pct.toFixed(2)}</td></tr>`).join(''):'<tr><td colspan="9" class="empty">Eşleşen strateji yok</td></tr>';
}
function renderPositions(rows){$('positionCount').textContent=rows.length;$('positions').innerHTML=rows.length?rows.map(x=>`<div class="feeditem"><i></i><div><p><b>${esc(x.symbol)}</b> · ${esc(x.strategy)}</p><div class="posmeta"><span>${x.timeframe}</span><span>${x.status}</span><span>${money.format(x.notional)}</span><span>TP skor ${(x.tp_score||0).toFixed(3)}</span></div></div><time>#${x.scenario_id}</time></div>`).join(''):'<div class="empty">Henüz açık veya bekleyen pozisyon yok</div>'}
function renderEvents(rows){$('events').innerHTML=rows.length?rows.map(x=>`<div class="feeditem"><i class="${x.level}"></i><div><p>${esc(x.message)}</p><small>${esc(x.category)}</small></div><time>${dt(x.created_at)}</time></div>`).join(''):'<div class="empty">Akış bekleniyor…</div>'}

async function refresh(){
  try{const [s,m,p,e]=await Promise.all([api('/api/status'),api('/api/summary'),api('/api/positions?status=ACTIVE&limit=100'),api('/api/events?limit=60')]);renderStatus(s);renderSummary(m);renderPositions(p);renderEvents(e);await loadScenarios()}
  catch(err){$('engineState').textContent='BAĞLANTI HATASI';$('statusDot').className='error';$('lastUpdate').textContent=err.message}
}
$('tfFilter').addEventListener('change',loadScenarios);$('stateFilter').addEventListener('change',loadScenarios);$('search').addEventListener('input',()=>{clearTimeout(searchTimer);searchTimer=setTimeout(loadScenarios,250)});
refresh();timer=setInterval(refresh,5000);
