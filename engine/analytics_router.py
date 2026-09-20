"""
Analytics dashboard endpoint — real-time experiment results.
Mount on the FastAPI server.
"""
from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(prefix="/analytics", tags=["analytics"])

# Injected from server.py
_tracker = None

def set_tracker(tracker):
    global _tracker
    _tracker = tracker


@router.get("/summary")
async def get_experiment_summary():
    """Returns live A/B/C group behaviour counts."""
    if not _tracker:
        return {}
    return await _tracker.get_summary()


@router.get("/dashboard", response_class=HTMLResponse)
async def analytics_dashboard():
    return HTMLResponse(content="""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>NearByClub — Experiment Dashboard</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
  <style>
    body { font-family: -apple-system,sans-serif; background:#0e1621; color:#fff; padding:24px; margin:0; }
    h1 { font-size:20px; margin-bottom:24px; opacity:.9; }
    .groups { display:grid; grid-template-columns:repeat(3,1fr); gap:16px; margin-bottom:32px; }
    .card { background:#17212b; border-radius:16px; padding:20px; border:1px solid rgba(255,255,255,.07); }
    .card h2 { font-size:14px; opacity:.5; margin:0 0 4px; }
    .card .total { font-size:32px; font-weight:700; color:#5288c1; }
    .bars { margin-top:12px; display:flex; flex-direction:column; gap:6px; }
    .bar-row { display:flex; align-items:center; gap:8px; font-size:12px; }
    .bar-label { width:90px; opacity:.6; }
    .bar-track { flex:1; height:8px; background:rgba(255,255,255,.08); border-radius:4px; overflow:hidden; }
    .bar-fill { height:100%; border-radius:4px; transition:width .6s; }
    .approach { background:#4CAF50; }
    .avoid    { background:#f44336; }
    .no_change{ background:#FF9800; }
    .lost     { background:#555; }
    canvas { max-width:500px; margin:0 auto; display:block; }
    .refresh { margin-top:24px; font-size:12px; opacity:.35; text-align:center; }
  </style>
</head>
<body>
<h1>🧪 Crossing Experiment — Live Results</h1>

<div class="groups" id="groups-container">
  <div class="card"><h2>Group A</h2><div class="total" id="a-total">—</div><div class="bars" id="a-bars"></div></div>
  <div class="card"><h2>Group B</h2><div class="total" id="b-total">—</div><div class="bars" id="b-bars"></div></div>
  <div class="card"><h2>Group C (control)</h2><div class="total" id="c-total">—</div><div class="bars" id="c-bars"></div></div>
</div>

<canvas id="chart" height="200"></canvas>
<div class="refresh">Auto-refresh every 10s</div>

<script>
const LABELS = { APPROACH:'🟢 Пошли навстречу', AVOID:'🔴 Уклонились', NO_CHANGE:'🟡 Не изменили курс', LOST:'⚫ Остановились' };
const COLORS = { APPROACH:'#4CAF50', AVOID:'#f44336', NO_CHANGE:'#FF9800', LOST:'#555' };

const ctx = document.getElementById('chart').getContext('2d');
const chart = new Chart(ctx, {
  type: 'bar',
  data: {
    labels: Object.values(LABELS),
    datasets: [
      { label: 'Group A', data: [0,0,0,0], backgroundColor: '#5288c155' },
      { label: 'Group B', data: [0,0,0,0], backgroundColor: '#9c27b055' },
      { label: 'Group C', data: [0,0,0,0], backgroundColor: '#ffffff22' },
    ]
  },
  options: {
    responsive: true,
    plugins: { legend: { labels: { color:'#fff' } } },
    scales: {
      x: { ticks: { color:'#aaa' }, grid: { color:'rgba(255,255,255,.05)' } },
      y: { ticks: { color:'#aaa' }, grid: { color:'rgba(255,255,255,.05)' }, beginAtZero: true }
    }
  }
});

function renderGroup(groupId, data) {
  const key = groupId.toUpperCase();
  const d = data[key] || {};
  document.getElementById(`${groupId}-total`).textContent = d.total ?? 0;
  const barsEl = document.getElementById(`${groupId}-bars`);
  const total = d.total || 1;
  barsEl.innerHTML = ['APPROACH','AVOID','NO_CHANGE','LOST'].map(k => `
    <div class="bar-row">
      <div class="bar-label">${LABELS[k]}</div>
      <div class="bar-track"><div class="bar-fill ${k.toLowerCase()}" style="width:${Math.round((d[k]||0)/total*100)}%"></div></div>
      <span style="font-size:11px;opacity:.5">${d[k]||0}</span>
    </div>`).join('');
}

async function refresh() {
  try {
    const r = await fetch('/analytics/summary');
    const data = await r.json();
    ['a','b','c'].forEach(g => renderGroup(g, data));
    const keys = ['APPROACH','AVOID','NO_CHANGE','LOST'];
    chart.data.datasets[0].data = keys.map(k => (data['A']||{})[k]||0);
    chart.data.datasets[1].data = keys.map(k => (data['B']||{})[k]||0);
    chart.data.datasets[2].data = keys.map(k => (data['C']||{})[k]||0);
    chart.update();
  } catch(e) { console.warn('fetch failed', e); }
}

refresh();
setInterval(refresh, 10000);
</script>
</body>
</html>""")
