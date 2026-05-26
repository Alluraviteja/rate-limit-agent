/* ── State ─────────────────────────────────────────────── */
let currentPage = 'overview';
let tlFilter    = 'all';
let agFilter    = 'all';
let tlOffset    = 0;
let selectedApp = '';
let _charts     = {};
let _timeBaselineData = [];
let _selectedDow = new Date().getDay(); // 0=Sun … 6=Sat (JS convention)

/* ── Severity helpers ──────────────────────────────────── */
const SEV = {
  none:     { color:'#888780', bg:'#F1EFE8', text:'#444441' },
  low:      { color:'#378ADD', bg:'#E6F1FB', text:'#0C447C' },
  medium:   { color:'#EF9F27', bg:'#FAEEDA', text:'#633806' },
  high:     { color:'#D85A30', bg:'#FAECE7', text:'#712B13' },
  critical: { color:'#E24B4A', bg:'#FCEBEB', text:'#791F1F' },
};
const SEV_VAL = { none:0, low:1, medium:2, high:3, critical:4 };
const DOW_LABELS = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];

function sevBadge(sev) {
  const cls = sev in SEV ? sev : 'none';
  return `<span class="sev-${cls} inline-block px-2 py-0.5 rounded text-xs font-semibold capitalize">${sev || 'none'}</span>`;
}
function actionPill(action) {
  const cls = { monitor:'action-monitor', alert:'action-alert', throttle:'action-throttle', block:'action-block' }[action] || 'action-monitor';
  return `<span class="${cls} inline-block px-2 py-0.5 rounded text-xs font-semibold capitalize">${action}</span>`;
}
function outcomePill(resolved, severityAfter) {
  if (resolved === null || resolved === undefined) {
    return `<span class="inline-block px-2 py-0.5 rounded text-xs font-medium bg-gray-100 text-gray-400">Pending</span>`;
  }
  if (resolved) {
    return `<span class="inline-block px-2 py-0.5 rounded text-xs font-medium bg-emerald-50 text-emerald-700">✓ Resolved → ${severityAfter || '?'}</span>`;
  }
  return `<span class="inline-block px-2 py-0.5 rounded text-xs font-medium bg-red-50 text-red-600">↑ Worsened → ${severityAfter || '?'}</span>`;
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
  if (page === 'outcomes')  loadOutcomes();
  if (page === 'evals')     loadEvals();
}

/* ── Countdown — matches AGENT_INTERVAL_MINUTES=1 ─────── */
function startCountdown() {
  setInterval(() => {
    const secs = new Date().getSeconds();
    const rem  = 60 - secs;
    document.getElementById('countdown').textContent =
      '00:' + String(rem).padStart(2, '0');
  }, 1000);
}

/* ── Auto-refresh every 1 minute ──────────────────────── */
setInterval(() => loadPage(currentPage), 60 * 1000);

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
  const [sum, tl, oc, appStatus] = await Promise.all([
    api('/dashboard/summary'),
    api('/dashboard/timeline?limit=50'),
    api('/dashboard/outcomes'),
    apiRaw('/dashboard/status'),
  ]);

  const activeIncidents = (appStatus || []).filter(a => a.anomaly_detected).length;
  document.getElementById('ov-active-incidents').textContent = activeIncidents;
  document.getElementById('ov-active-incidents').className =
    `text-2xl font-bold ${activeIncidents > 0 ? 'text-red-600' : 'text-emerald-600'}`;

  document.getElementById('ov-total-runs').textContent = sum?.total_agent_runs ?? '--';
  document.getElementById('ov-last-run').textContent =
    sum?.last_run_at ? `Last: ${relativeTime(new Date(sum.last_run_at))}` : '';

  document.getElementById('ov-anomalies').textContent = sum?.anomaly_count ?? '--';
  document.getElementById('ov-anomalies-sub').textContent =
    sum ? `${sum.critical_count} critical, ${sum.high_count} high` : '';

  if (oc && oc.total_measured > 0) {
    document.getElementById('ov-resolution').textContent     = `${oc.resolution_rate_pct}%`;
    document.getElementById('ov-resolution-sub').textContent = `${oc.resolved_count} / ${oc.total_measured} measured`;
  } else {
    document.getElementById('ov-resolution').textContent     = '--';
    document.getElementById('ov-resolution-sub').textContent = 'No outcomes yet';
  }

  drawAppStatusGrid(appStatus || []);
  drawSeverityChart(tl || []);
}

function drawAppStatusGrid(apps) {
  const grid = document.getElementById('ov-app-grid');
  if (!apps.length) {
    grid.innerHTML = '<div class="text-sm text-gray-400">No app data yet.</div>';
    return;
  }
  grid.className = `grid gap-3 ${apps.length === 1 ? 'grid-cols-1' : apps.length <= 3 ? 'grid-cols-3' : 'grid-cols-2 md:grid-cols-3'}`;
  grid.innerHTML = apps.map(a => {
    const sev = a.final_severity || 'none';
    const s = SEV[sev] || SEV.none;
    const trendIcon = { escalating: '↑', recovering: '↓', stable: '—' }[a.trend_direction] || '—';
    const trendColor = { escalating: 'text-red-500', recovering: 'text-emerald-500', stable: 'text-gray-400' }[a.trend_direction] || 'text-gray-400';
    const agentDots = ['error_severity','token_severity','path_severity'].map(k => {
      const sv = a[k] || 'none';
      return `<span title="${k.replace('_severity','')} agent" class="inline-block w-2 h-2 rounded-full" style="background:${(SEV[sv]||SEV.none).color}"></span>`;
    }).join('');
    return `
    <div class="bg-white rounded-xl shadow-sm p-4 border-l-4" style="border-color:${s.color}">
      <div class="flex items-center justify-between mb-2">
        <div class="text-sm font-semibold text-gray-700 truncate">${a.app_name || `App ${a.app_info_id}`}</div>
        ${sevBadge(sev)}
      </div>
      <div class="flex items-center gap-3 mb-2">
        ${agentDots}
        <span class="text-xs text-gray-400">${a.agents_firing} of 3 agents firing</span>
        <span class="text-xs font-semibold ${trendColor} ml-auto">${trendIcon} ${a.trend_direction || 'stable'}</span>
      </div>
      ${a.spike_multiplier != null ? `
        <div class="text-xs text-gray-500 mb-1">
          <span class="font-semibold text-gray-700">${a.spike_multiplier}×</span> above baseline block rate
        </div>` : ''}
      <div class="flex items-center justify-between">
        ${actionPill(a.action || 'monitor')}
        <span class="text-xs text-gray-400">${a.run_at ? relativeTime(new Date(a.run_at)) : '—'}</span>
      </div>
      ${a.reason ? `<div class="text-xs text-gray-500 italic mt-2 line-clamp-2">${a.reason}</div>` : ''}
    </div>`;
  }).join('');
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
  const s   = SEV[sev] || SEV.none;
  const trendIcon  = { escalating: '↑', recovering: '↓', stable: '—' }[row.trend_direction] || '—';
  const trendColor = { escalating: 'text-red-500', recovering: 'text-emerald-500', stable: 'text-gray-400' }[row.trend_direction] || 'text-gray-400';
  const spikeTag = row.spike_multiplier != null
    ? `<span class="text-xs font-semibold text-orange-600 bg-orange-50 px-1.5 py-0.5 rounded">${row.spike_multiplier}×</span>`
    : '';
  const agentsBadge = row.agents_firing > 0
    ? `<span class="text-xs text-gray-400">${row.agents_firing}/3 agents</span>`
    : '';
  return `
  <div class="bg-white rounded-xl shadow-sm overflow-hidden" style="border-left:3px solid ${s.color}">
    <div class="flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-gray-50 select-none tl-header">
      <div class="flex-1 min-w-0">
        <div class="flex items-center gap-2 mb-0.5 flex-wrap">
          ${sevBadge(sev)}
          ${spikeTag}
          ${agentsBadge}
          <span class="text-xs ${trendColor} font-medium">${trendIcon} ${row.trend_direction || 'stable'}</span>
          <span class="text-xs text-gray-400">${ts.toLocaleString()} · ${rel}</span>
          ${outcomePill(row.anomaly_resolved, row.severity_after)}
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
  const [data, timeBl] = await Promise.all([
    api('/dashboard/baseline'),
    api('/dashboard/time-baseline'),
  ]);

  if (data && data.length) {
    const container = document.getElementById('bl-cards');
    container.innerHTML = '';
    data.forEach((b, idx) => {
      const header = data.length > 1
        ? `<div class="col-span-3 text-xs font-semibold text-gray-500 mt-${idx > 0 ? 4 : 0}">App ${b.app_info_id}</div>`
        : '';
      const cards = [
        { label: 'Avg RPS (EWMA)',        val: parseFloat(b.avg_rps_7d        ?? 0).toFixed(2), unit: 'req/s' },
        { label: 'Avg block rate (EWMA)', val: parseFloat(b.avg_block_rate_7d ?? 0).toFixed(2), unit: '%' },
        { label: 'Avg bot ratio (EWMA)',  val: parseFloat(b.avg_bot_ratio_7d  ?? 0).toFixed(2), unit: '%' },
        { label: 'Spike threshold',       val: parseFloat(b.spike_threshold   ?? 3).toFixed(1), unit: 'x' },
        { label: 'Sample count',          val: b.sample_count ?? 0,                              unit: '/ 9,999' },
        { label: 'Last updated',          val: b.last_updated ? new Date(b.last_updated).toLocaleString() : '—', unit: '' },
      ];
      container.innerHTML += header + cards.map(c => `
        <div class="bg-white rounded-xl shadow-sm p-4">
          <div class="text-xs text-gray-400 mb-1">${c.label}</div>
          <div class="text-xl font-bold text-gray-800">${c.val}<span class="text-sm font-normal text-gray-400 ml-1">${c.unit}</span></div>
        </div>`).join('');
    });
  }

  _timeBaselineData = timeBl || [];
  renderTimeBaselineTabs();
}

function renderTimeBaselineTabs() {
  const tabsEl = document.getElementById('bl-dow-tabs');
  const emptyEl = document.getElementById('bl-time-empty');

  if (!_timeBaselineData.length) {
    tabsEl.innerHTML = '';
    emptyEl.classList.remove('hidden');
    destroyChart('timeBaseline');
    return;
  }
  emptyEl.classList.add('hidden');

  // Build tabs for days that have at least one bucket
  const daysPresent = [...new Set(_timeBaselineData.map(r => r.day_of_week))].sort();
  // Convert Python weekday (0=Mon) to JS day label
  const pyDowToLabel = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];

  tabsEl.innerHTML = daysPresent.map(dow => {
    const active = dow === _selectedDow ? 'bg-indigo-600 text-white' : 'bg-gray-100 text-gray-500 hover:bg-gray-200';
    return `<button class="px-3 py-1 rounded text-xs font-medium ${active}" data-dow="${dow}" onclick="selectTimeBaselineDow(${dow})">${pyDowToLabel[dow]}</button>`;
  }).join('');

  drawTimeBaselineChart(_selectedDow);
}

function selectTimeBaselineDow(dow) {
  _selectedDow = dow;
  renderTimeBaselineTabs();
}

function drawTimeBaselineChart(dow) {
  destroyChart('timeBaseline');
  const buckets = _timeBaselineData.filter(r => r.day_of_week === dow);
  if (!buckets.length) return;

  const byHour = Object.fromEntries(buckets.map(r => [r.hour_of_day, r]));
  const labels = Array.from({ length: 24 }, (_, h) => `${String(h).padStart(2,'0')}:00`);
  const values = Array.from({ length: 24 }, (_, h) => {
    const b = byHour[h];
    return b ? parseFloat(b.avg_block_rate_ewma ?? 0) : null;
  });
  const bgColors = values.map(v => {
    if (v === null) return '#e5e7eb';
    if (v > 30) return '#fca5a5';
    if (v > 15) return '#fde68a';
    return '#a7f3d0';
  });

  _charts.timeBaseline = new Chart(document.getElementById('timeBaselineChart'), {
    type: 'bar',
    data: {
      labels,
      datasets: [{
        label: 'Avg block rate %',
        data: values,
        backgroundColor: bgColors,
        borderRadius: 3,
      }]
    },
    options: {
      animation: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { maxTicksLimit: 12, font: { size: 9 } }, grid: { display: false } },
        y: {
          min: 0,
          ticks: { font: { size: 10 }, callback: v => `${v}%` },
          title: { display: true, text: 'Block rate %', font: { size: 10 } },
        }
      }
    }
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

/* ── Outcomes ──────────────────────────────────────────── */
async function loadOutcomes() {
  const data = await api('/dashboard/outcomes');
  if (!data) return;

  document.getElementById('oc-total').textContent    = data.total_measured;
  document.getElementById('oc-resolved').textContent = data.resolved_count;

  if (data.total_measured > 0) {
    document.getElementById('oc-rate').textContent        = `${data.resolution_rate_pct}%`;
    document.getElementById('oc-rate-bar').style.width    = `${data.resolution_rate_pct}%`;
  } else {
    document.getElementById('oc-rate').textContent = '--';
  }

  const tbody  = document.getElementById('oc-table');
  const emptyEl = document.getElementById('oc-empty');
  const recent = data.recent || [];

  if (!recent.length) {
    tbody.innerHTML = '';
    emptyEl.classList.remove('hidden');
    return;
  }
  emptyEl.classList.add('hidden');
  tbody.innerHTML = recent.map(r => `
    <tr>
      <td class="px-4 py-2.5 text-xs text-gray-500">${r.created_at ? new Date(r.created_at).toLocaleString() : '—'}</td>
      <td class="px-4 py-2.5 text-xs text-gray-500">App ${r.app_info_id ?? '—'}</td>
      <td class="px-4 py-2.5">${actionPill(r.action_taken || 'monitor')}</td>
      <td class="px-4 py-2.5">${sevBadge(r.severity_at_action || 'none')}</td>
      <td class="px-4 py-2.5">${r.severity_after ? sevBadge(r.severity_after) : '<span class="text-xs text-gray-400">—</span>'}</td>
      <td class="px-4 py-2.5">${outcomePill(r.anomaly_resolved, r.severity_after)}</td>
    </tr>`).join('');
}

/* ── Evals ─────────────────────────────────────────────── */
async function loadEvals() {
  const [trend, results] = await Promise.all([
    apiRaw('/evals/summary'),
    apiRaw('/evals/results?limit=1'),
  ]);

  const trendData = trend || [];
  const chartEmpty = document.getElementById('evals-chart-empty');
  if (!trendData.length) {
    chartEmpty.classList.remove('hidden');
    destroyChart('evals');
  } else {
    chartEmpty.classList.add('hidden');
    drawEvalsChart(trendData);
  }

  const latestRun = results && results.length ? results[0] : null;
  const runAtEl = document.getElementById('evals-run-at');
  runAtEl.textContent = latestRun?.run_at ? `Run: ${new Date(latestRun.run_at).toLocaleString()}` : '';

  const tbody  = document.getElementById('evals-table');
  const emptyEl = document.getElementById('evals-empty');
  const checks = latestRun?.checks || [];

  if (!checks.length) {
    tbody.innerHTML = '';
    emptyEl.classList.remove('hidden');
    return;
  }
  emptyEl.classList.add('hidden');
  tbody.innerHTML = checks.map(c => `
    <tr>
      <td class="px-4 py-2.5 text-xs font-medium text-gray-700">${c.scenario}</td>
      <td class="px-4 py-2.5 text-xs text-gray-500">${c.agent.replace(/_/g,' ')}</td>
      <td class="px-4 py-2.5">${sevBadge(c.expected_severity || 'none')}</td>
      <td class="px-4 py-2.5">${sevBadge(c.actual_severity || 'none')}</td>
      <td class="px-4 py-2.5 text-xs text-gray-500">${c.expected_action || '—'}</td>
      <td class="px-4 py-2.5 text-xs text-gray-500">${c.actual_action || '—'}</td>
      <td class="px-4 py-2.5 text-center">${c.severity_correct
        ? '<span class="text-emerald-600 font-bold">✓</span>'
        : '<span class="text-red-500 font-bold">✗</span>'}</td>
      <td class="px-4 py-2.5 text-center">${c.action_correct
        ? '<span class="text-emerald-600 font-bold">✓</span>'
        : '<span class="text-red-500 font-bold">✗</span>'}</td>
    </tr>`).join('');
}

function drawEvalsChart(trend) {
  destroyChart('evals');
  const labels = trend.map(r => r.run_at ? new Date(r.run_at).toLocaleDateString() : r.run_id.slice(0, 8));
  _charts.evals = new Chart(document.getElementById('evalsChart'), {
    type: 'line',
    data: {
      labels,
      datasets: [
        {
          label: 'Severity accuracy %',
          data: trend.map(r => r.severity_accuracy_pct),
          borderColor: '#6366f1',
          borderWidth: 2,
          pointRadius: 4,
          tension: 0.2,
          fill: false,
        },
        {
          label: 'Action accuracy %',
          data: trend.map(r => r.action_accuracy_pct),
          borderColor: '#10b981',
          borderWidth: 2,
          pointRadius: 4,
          tension: 0.2,
          fill: false,
        },
      ]
    },
    options: {
      animation: false,
      plugins: { legend: { labels: { font: { size: 10 }, boxWidth: 10 } } },
      scales: {
        x: { ticks: { font: { size: 10 } }, grid: { display: false } },
        y: {
          min: 0, max: 100,
          ticks: { font: { size: 10 }, callback: v => `${v}%` },
        }
      }
    }
  });
}

async function runEvals() {
  const btn = document.getElementById('evals-run-btn');
  btn.disabled = true;
  btn.textContent = 'Running…';
  try {
    const res = await fetch('/evals/run', { method: 'POST' });
    if (!res.ok) throw new Error(res.statusText);
    await loadEvals();
  } catch(e) {
    console.error('Eval run failed', e);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Run evals now';
  }
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

  document.getElementById('evals-run-btn').addEventListener('click', runEvals);

  startCountdown();
  applyFilterStyles();
  loadApps().then(() => loadOverview());
});
