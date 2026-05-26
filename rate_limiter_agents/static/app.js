/* ── State ─────────────────────────────────────────────── */
let currentPage = 'overview';
let tlFilter    = 'all';
let agFilter    = 'all';
let tlOffset    = 0;
let selectedApp = '';
let _charts     = {};

/* ── Severity helpers ──────────────────────────────────── */
const SEV = {
  none:     { color:'#888780', bg:'#F1EFE8', text:'#444441' },
  low:      { color:'#378ADD', bg:'#E6F1FB', text:'#0C447C' },
  medium:   { color:'#EF9F27', bg:'#FAEEDA', text:'#633806' },
  high:     { color:'#D85A30', bg:'#FAECE7', text:'#712B13' },
  critical: { color:'#E24B4A', bg:'#FCEBEB', text:'#791F1F' },
};
const SEV_VAL = { none:0, low:1, medium:2, high:3, critical:4 };

function sevBadge(sev) {
  const cls = sev in SEV ? sev : 'none';
  return `<span class="sev-${cls} inline-block px-2 py-0.5 rounded text-xs font-semibold capitalize">${sev || 'none'}</span>`;
}
function actionPill(action) {
  const cls = { monitor:'action-monitor', alert:'action-alert', throttle:'action-throttle', block:'action-block' }[action] || 'action-monitor';
  return `<span class="${cls} inline-block px-2 py-0.5 rounded text-xs font-semibold capitalize">${action}</span>`;
}

/* ── App selector ──────────────────────────────────────── */
async function loadApps() {
  const apps = await apiRaw('/dashboard/apps');
  if (!apps || !apps.length) return;
  const sel = document.getElementById('app-select');
  apps.forEach(a => {
    const opt = document.createElement('option');
    opt.value = a.id;
    opt.textContent = a.display_name || a.service_name || `App ${a.id}`;
    sel.appendChild(opt);
  });
}

function onAppChange() {
  selectedApp = document.getElementById('app-select').value;
  loadPage(currentPage);
}

/* ── Navigation ────────────────────────────────────────── */
function navigate(page) {
  currentPage = page;
  document.querySelectorAll('.nav-btn').forEach(b =>
    b.classList.toggle('active', b.dataset.page === page));
  document.querySelectorAll('.page').forEach(p =>
    p.classList.toggle('hidden', p.id !== `page-${page}`));
  loadPage(page);
}

function loadPage(page) {
  if (page === 'overview')  loadOverview();
  if (page === 'timeline')  { tlOffset = 0; loadTimeline(true); }
  if (page === 'baseline')  loadBaseline();
  if (page === 'cost')      loadCost();
}

/* ── Countdown ─────────────────────────────────────────── */
function startCountdown() {
  setInterval(() => {
    const now  = new Date();
    const secs = now.getMinutes() * 60 + now.getSeconds();
    const next = Math.ceil((secs + 1) / 900) * 900;
    const rem  = next - secs;
    document.getElementById('countdown').textContent =
      String(Math.floor(rem / 60)).padStart(2,'0') + ':' + String(rem % 60).padStart(2,'0');
  }, 1000);
}

/* ── Auto-refresh ──────────────────────────────────────── */
setInterval(() => loadPage(currentPage), 15 * 60 * 1000);

/* ── API helpers ───────────────────────────────────────── */
async function apiRaw(path) {
  try {
    const r = await fetch(path);
    if (!r.ok) throw new Error(r.statusText);
    return r.json();
  } catch(e) {
    console.error('API error', path, e);
    return null;
  }
}

async function api(path) {
  const sep = path.includes('?') ? '&' : '?';
  const url = selectedApp ? `${path}${sep}app_info_id=${selectedApp}` : path;
  return apiRaw(url);
}

/* ── Overview ──────────────────────────────────────────── */
async function loadOverview() {
  const [sum, cost, tl] = await Promise.all([
    api('/dashboard/summary'),
    api('/dashboard/cost'),
    api('/dashboard/timeline?limit=50'),
  ]);

  document.getElementById('ov-total-runs').textContent = sum?.total_agent_runs ?? '--';
  document.getElementById('ov-anomalies').textContent  = sum?.anomaly_count ?? '--';
  document.getElementById('ov-cost').textContent       = sum?.total_cost_usd != null ? `$${sum.total_cost_usd.toFixed(5)}` : '--';
  document.getElementById('ov-severity').innerHTML     = sevBadge(sum?.last_severity || 'none');

  drawSeverityChart(tl || []);
  if (cost) drawTokenChart(cost.by_agent);
  drawAgentCards(tl && tl[0] ? tl[0] : null);
}

function destroyChart(key) {
  if (_charts[key]) { _charts[key].destroy(); delete _charts[key]; }
}

function drawSeverityChart(rows) {
  destroyChart('severity');
  const labels = rows.map(r => {
    const d = new Date(r.run_at);
    return d.toLocaleTimeString([], { hour:'2-digit', minute:'2-digit' });
  });
  const values      = rows.map(r => SEV_VAL[r.final_severity] ?? 0);
  const pointColors = rows.map(r => (SEV[r.final_severity] || SEV.none).color);
  _charts.severity  = new Chart(document.getElementById('severityChart'), {
    type: 'line',
    data: {
      labels,
      datasets: [{
        data: values,
        borderColor: '#6366f1',
        borderWidth: 1.5,
        pointBackgroundColor: pointColors,
        pointRadius: 4,
        pointHoverRadius: 6,
        tension: 0.25,
        fill: false,
      }]
    },
    options: {
      animation: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { maxTicksLimit: 8, font: { size: 10 } }, grid: { display: false } },
        y: {
          min: 0, max: 4,
          ticks: {
            stepSize: 1,
            font: { size: 10 },
            callback: v => ['none','low','med','high','crit'][v] ?? v,
          }
        }
      }
    }
  });
}

function drawTokenChart(byAgent) {
  destroyChart('token');
  if (!byAgent || !byAgent.length) return;
  const labels = byAgent.map(a => a.agent_name.replace(/_/g,' '));
  const data   = byAgent.map(a => a.tokens_today);
  const colors = ['#6366f1','#22d3ee','#f59e0b','#10b981'];
  _charts.token = new Chart(document.getElementById('tokenChart'), {
    type: 'doughnut',
    data: {
      labels,
      datasets: [{ data, backgroundColor: colors, borderWidth: 2 }]
    },
    options: {
      animation: false,
      plugins: {
        legend: { position: 'right', labels: { font: { size: 10 }, boxWidth: 10 } }
      },
      cutout: '60%',
    }
  });
}

function drawAgentCards(run) {
  const defs = [
    {
      id: 'card-error',
      label: 'Error pattern',
      key: 'error',
      metric: r => `Block rate: ${r.block_rate_pct?.toFixed(1)}%`,
      extra:  r => `Total: ${r.total_requests ?? 0} reqs`,
    },
    {
      id: 'card-token',
      label: 'Token bucket health',
      key: 'token',
      metric: r => `Avg remaining: ${r.avg_remaining?.toFixed(0)}`,
      extra:  r => `Total: ${r.total_requests ?? 0} reqs`,
    },
    {
      id: 'card-paths',
      label: 'Top paths',
      key: 'paths',
      metric: r => `Block rate: ${r.block_rate_pct?.toFixed(1)}%`,
      extra:  r => `Unique paths: ${r.unique_paths ?? 0}`,
    },
  ];
  defs.forEach(({ id, label, key, metric, extra }) => {
    const el  = document.getElementById(id);
    const sub = run ? run[key] : null;
    const sev = sub?.severity || 'none';
    el.innerHTML = `
      <div class="flex items-center justify-between mb-2">
        <div class="text-xs font-semibold text-gray-500">${label}</div>
        ${sevBadge(sev)}
      </div>
      ${sub ? `
        <div class="text-sm text-gray-700 mb-1">${metric(sub)}</div>
        <div class="text-xs text-gray-400 mb-2">${extra(sub)}</div>
        <div class="text-xs text-gray-500 italic mb-2">${sub.reason || '—'}</div>
        <div class="flex items-center gap-2">${actionPill(sub?.action || 'monitor')}
          <span class="text-xs text-gray-400">${sub?.tokens_used ?? 0} tokens</span>
        </div>
      ` : '<div class="text-sm text-gray-400">No data yet</div>'}
    `;
  });
}

/* ── Timeline ──────────────────────────────────────────── */
function setTlFilter(val) {
  tlFilter = val;
  document.querySelectorAll('.tl-filter').forEach(b =>
    b.classList.toggle('active', b.dataset.val === val));
  document.querySelectorAll('.tl-filter.active').forEach(b => {
    b.style.background = '#e0e7ff'; b.style.color = '#3730a3';
  });
  document.querySelectorAll('.tl-filter:not(.active)').forEach(b => {
    b.style.background = ''; b.style.color = '';
  });
  tlOffset = 0; loadTimeline(true);
}

function setAgFilter(val) {
  agFilter = val;
  document.querySelectorAll('.ag-filter').forEach(b =>
    b.classList.toggle('active', b.dataset.val === val));
  document.querySelectorAll('.ag-filter.active').forEach(b => {
    b.style.background = '#e0e7ff'; b.style.color = '#3730a3';
  });
  document.querySelectorAll('.ag-filter:not(.active)').forEach(b => {
    b.style.background = ''; b.style.color = '';
  });
  tlOffset = 0; loadTimeline(true);
}

async function loadTimeline(reset) {
  if (reset) { tlOffset = 0; document.getElementById('tl-list').innerHTML = ''; }
  const rows = await api(
    `/dashboard/timeline?filter=${tlFilter}&agent=${agFilter}&limit=15&offset=${tlOffset}`
  );
  if (!rows) return;

  const list = document.getElementById('tl-list');
  rows.forEach(row => list.insertAdjacentHTML('beforeend', buildTlRow(row)));

  tlOffset += rows.length;
  const moreBtn = document.getElementById('tl-more');
  moreBtn.classList.toggle('hidden', rows.length < 15);
}

function loadMoreTimeline() { loadTimeline(false); }

function buildTlRow(row) {
  const ts  = new Date(row.run_at);
  const rel = relativeTime(ts);
  const sev = row.final_severity || 'none';
  return `
  <div class="bg-white rounded-xl shadow-sm border-l-4 border-l-${sev} overflow-hidden">
    <div class="flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-gray-50 select-none tl-header">
      <div class="flex-1 min-w-0">
        <div class="flex items-center gap-2 mb-0.5">
          ${sevBadge(sev)}
          <span class="text-xs text-gray-400">${ts.toLocaleString()} · ${rel}</span>
        </div>
        <div class="text-sm text-gray-700 truncate">${row.reason || '—'}</div>
      </div>
      <div class="flex items-center gap-2 shrink-0">
        ${actionPill(row.action)}
        <span class="text-xs text-gray-400">$${row.cost_usd?.toFixed(5)}</span>
        <svg class="expand-chevron w-4 h-4 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/>
        </svg>
      </div>
    </div>
    <div class="tl-expand hidden border-t border-gray-100 px-4 py-3 bg-gray-50">
      <div class="grid grid-cols-3 gap-3">
        ${buildSubCard('Error pattern',        row.error, s => `Block: ${s.block_rate_pct?.toFixed(1)}%`,    s => `Reqs: ${s.total_requests ?? 0}`)}
        ${buildSubCard('Token bucket health',  row.token, s => `Avg rem: ${s.avg_remaining?.toFixed(0)}`,    s => `Reqs: ${s.total_requests ?? 0}`)}
        ${buildSubCard('Top paths',            row.paths, s => `Block: ${s.block_rate_pct?.toFixed(1)}%`,    s => `Paths: ${s.unique_paths ?? 0}`)}
      </div>
    </div>
  </div>`;
}

function buildSubCard(title, sub, metric, extra) {
  const sev = sub?.severity || 'none';
  return `
    <div class="bg-white rounded-lg p-3 border border-gray-100">
      <div class="flex items-center justify-between mb-1.5">
        <div class="text-xs font-semibold text-gray-500">${title}</div>
        ${sevBadge(sev)}
      </div>
      ${sub ? `
        <div class="text-xs text-gray-700">${metric(sub)}</div>
        <div class="text-xs text-gray-400">${extra(sub)}</div>
        <div class="text-xs text-gray-500 italic mt-1 line-clamp-2">${sub.reason || '—'}</div>
      ` : '<div class="text-xs text-gray-400">No data</div>'}
    </div>`;
}

function toggleTl(header) {
  const row    = header.parentElement;
  const expand = row.querySelector('.tl-expand');
  expand.classList.toggle('hidden');
  row.classList.toggle('expanded', !expand.classList.contains('hidden'));
}

/* ── Baseline ──────────────────────────────────────────── */
async function loadBaseline() {
  const data = await api('/dashboard/baseline');
  if (!data || !data.length) return;
  const container = document.getElementById('bl-cards');
  container.innerHTML = '';
  data.forEach((b, idx) => {
    const header = data.length > 1
      ? `<div class="col-span-3 text-xs font-semibold text-gray-500 mt-${idx > 0 ? 4 : 0}">App ${b.app_info_id}</div>`
      : '';
    const cards = [
      { label: 'Avg RPS (7d)',        val: parseFloat(b.avg_rps_7d        ?? 0).toFixed(2), unit: 'req/s' },
      { label: 'Avg block rate (7d)', val: parseFloat(b.avg_block_rate_7d ?? 0).toFixed(2), unit: '%' },
      { label: 'Avg bot ratio (7d)',  val: parseFloat(b.avg_bot_ratio_7d  ?? 0).toFixed(2), unit: '%' },
      { label: 'Spike threshold',     val: parseFloat(b.spike_threshold   ?? 3).toFixed(1), unit: 'x' },
      { label: 'Sample count',        val: b.sample_count ?? 0,                              unit: '/ 672' },
      { label: 'Last updated',        val: b.last_updated ? new Date(b.last_updated).toLocaleString() : '—', unit: '' },
    ];
    container.innerHTML += header + cards.map(c => `
      <div class="bg-white rounded-xl shadow-sm p-4">
        <div class="text-xs text-gray-400 mb-1">${c.label}</div>
        <div class="text-xl font-bold text-gray-800">${c.val}<span class="text-sm font-normal text-gray-400 ml-1">${c.unit}</span></div>
      </div>`).join('');
  });
}

/* ── Cost ──────────────────────────────────────────────── */
async function loadCost() {
  const data = await api('/dashboard/cost');
  if (!data) return;

  const tbody = document.getElementById('cost-table');
  tbody.innerHTML = (data.by_agent || []).map(a => `
    <tr>
      <td class="px-4 py-2.5 font-medium capitalize">${a.agent_name.replace(/_/g,' ')}</td>
      <td class="px-4 py-2.5 text-right tabular-nums">${a.runs_today}</td>
      <td class="px-4 py-2.5 text-right tabular-nums">${a.tokens_today.toLocaleString()}</td>
      <td class="px-4 py-2.5 text-right tabular-nums text-indigo-600 font-medium">$${a.cost_today.toFixed(5)}</td>
    </tr>`).join('');

  drawCostChart(data.daily_series || []);
}

function drawCostChart(series) {
  destroyChart('cost');
  const labels = series.map(s => s.date);
  const agents = ['error_pattern','token_bucket_health','top_paths','orchestrator'];
  const colors = ['#6366f1','#22d3ee','#f59e0b','#10b981'];
  _charts.cost = new Chart(document.getElementById('costChart'), {
    type: 'bar',
    data: {
      labels,
      datasets: agents.map((name, i) => ({
        label: name.replace(/_/g,' '),
        data:  series.map(s => s[name] || 0),
        backgroundColor: colors[i],
        borderRadius: 3,
      }))
    },
    options: {
      animation: false,
      plugins: { legend: { labels: { font: { size: 10 }, boxWidth: 10 } } },
      scales: {
        x: { stacked: true, ticks: { font: { size: 10 } }, grid: { display: false } },
        y: {
          stacked: true,
          ticks: {
            font: { size: 10 },
            callback: v => `$${v.toFixed(3)}`,
          }
        }
      }
    }
  });
}

/* ── Utilities ─────────────────────────────────────────── */
function relativeTime(date) {
  const diff = Math.floor((Date.now() - date) / 1000);
  if (diff < 60)    return `${diff}s ago`;
  if (diff < 3600)  return `${Math.floor(diff/60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff/3600)}h ago`;
  return `${Math.floor(diff/86400)}d ago`;
}

function applyFilterStyles() {
  document.querySelectorAll('.tl-filter.active, .ag-filter.active').forEach(b => {
    b.style.background = '#e0e7ff'; b.style.color = '#3730a3';
  });
}

/* ── Init ──────────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', function () {
  document.getElementById('app-select').addEventListener('change', onAppChange);

  document.querySelector('nav').addEventListener('click', e => {
    const btn = e.target.closest('.nav-btn');
    if (btn) navigate(btn.dataset.page);
  });

  document.querySelector('.tl-filter')?.closest('div').addEventListener('click', e => {
    const btn = e.target.closest('.tl-filter');
    if (btn) setTlFilter(btn.dataset.val);
  });

  document.querySelector('.ag-filter')?.closest('div').addEventListener('click', e => {
    const btn = e.target.closest('.ag-filter');
    if (btn) setAgFilter(btn.dataset.val);
  });

  document.getElementById('tl-more').addEventListener('click', loadMoreTimeline);

  document.getElementById('tl-list').addEventListener('click', e => {
    const header = e.target.closest('.tl-header');
    if (header) toggleTl(header);
  });

  startCountdown();
  applyFilterStyles();
  loadApps().then(() => loadOverview());
});
