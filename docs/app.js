"use strict";

const $ = (id) => document.getElementById(id);
const esc = (s) =>
  s == null ? "" : String(s).replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmt = (n, d = 2) =>
  n == null ? "—" : Number(n).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
const pct = (n) => (n == null ? "—" : (n * 100).toFixed(1) + "%");

async function load() {
  try {
    const res = await fetch("dashboard/state.json", { cache: "no-store" });
    if (!res.ok) throw new Error("HTTP " + res.status);
    render(await res.json());
  } catch (e) {
    $("kpis").innerHTML =
      `<div class="empty">Could not load <code>dashboard/state.json</code> (${esc(e.message)}).<br/>` +
      `Run <code>python -m tradebot.cli run --loop</code> and refresh.</div>`;
  }
}

function render(s) {
  const badge = $("modeBadge");
  badge.textContent = (s.mode || "").toUpperCase();
  badge.className = "badge " + (s.mode === "live" ? "live" : "paper");
  $("generatedAt").textContent = s.generated_at ? new Date(s.generated_at).toLocaleString() : "—";

  const pnlClass = s.realized_pnl >= 0 ? "pos" : "neg";
  $("kpis").innerHTML = [
    kpi("Bankroll", "$" + fmt(s.bankroll), `start $${fmt(s.starting_bankroll)}`),
    kpi("Realized PnL", (s.realized_pnl >= 0 ? "+" : "") + "$" + fmt(s.realized_pnl), "", pnlClass),
    kpi("Win rate", pct(s.win_rate), `${s.n_wins}W / ${s.n_losses}L`),
    kpi("Trades", s.n_trades, `${s.n_open} open`),
    kpi("Brain", s.brain.trained ? "Trained" : "Cold start", `${s.brain.experiences} experiences`,
      s.brain.trained ? "pos" : ""),
  ].join("");

  $("equityChart").innerHTML = equitySvg(s.equity_curve, s.starting_bankroll);
  $("brainPanel").innerHTML = brainPanel(s);
  $("resolvedTable").innerHTML = tradesTable(s.resolved_trades, true);
  $("openTable").innerHTML = tradesTable(s.open_trades, false);
  $("lessons").innerHTML = lessonsHtml(s.lessons);
}

function kpi(label, val, sub = "", cls = "") {
  return `<div class="kpi"><div class="kpi-label">${esc(label)}</div>` +
    `<div class="kpi-val ${cls}">${val}</div><div class="kpi-sub">${esc(sub)}</div></div>`;
}

function equitySvg(points, start) {
  if (!points || !points.length)
    return '<div class="empty">No settled trades yet — run a paper loop.</div>';
  const w = 640, h = 240, pad = 36;
  const ys = points.map((p) => p.cum).concat([start]);
  let minY = Math.min(...ys), maxY = Math.max(...ys);
  if (minY === maxY) { minY -= 1; maxY += 1; }
  const sx = (i) => pad + (w - 2 * pad) * (points.length < 2 ? 0.5 : i / (points.length - 1));
  const sy = (v) => h - pad - (h - 2 * pad) * ((v - minY) / (maxY - minY));
  const line = points.map((p, i) => `${i ? "L" : "M"}${sx(i).toFixed(1)},${sy(p.cum).toFixed(1)}`).join(" ");
  const baseY = sy(start).toFixed(1);
  const area =
    `M${sx(0).toFixed(1)},${baseY} ` +
    points.map((p, i) => `L${sx(i).toFixed(1)},${sy(p.cum).toFixed(1)}`).join(" ") +
    ` L${sx(points.length - 1).toFixed(1)},${baseY} Z`;
  const cls = points[points.length - 1].cum >= start ? "pos" : "neg";
  return `<svg viewBox="0 0 ${w} ${h}" class="equity ${cls}" preserveAspectRatio="xMidYMid meet">
    <line class="base" x1="${pad}" y1="${baseY}" x2="${w - pad}" y2="${baseY}" />
    <path class="area" d="${area}" />
    <path class="line" d="${line}" />
    <text class="axis" x="4" y="${(sy(maxY) + 4).toFixed(1)}">$${fmt(maxY, 0)}</text>
    <text class="axis" x="4" y="${sy(minY).toFixed(1)}">$${fmt(minY, 0)}</text>
  </svg>`;
}

function brainPanel(s) {
  const b = s.brain, total = b.experiences || 0;
  const wp = total ? Math.round((100 * b.wins) / total) : 0;
  return (
    `<div class="brain-state ${b.trained ? "on" : "off"}">` +
    (b.trained ? "● Active — learning from outcomes" : "○ Cold start — needs ≥8 resolved trades") +
    `</div>` +
    `<div class="bar"><div class="bar-win" style="width:${wp}%"></div></div>` +
    `<div class="brain-legend"><span>${b.wins} wins</span><span>${b.losses} losses</span></div>` +
    `<p class="muted">The brain scores every setup; below <b>${esc(s.config.brain_veto_threshold)}</b> ` +
    `it vetoes the trade. Learned weights carry over from paper to live.</p>`
  );
}

function tradesTable(rows, resolved) {
  if (!rows || !rows.length) return '<div class="empty">None.</div>';
  const head = resolved
    ? "<tr><th>Market</th><th>Side</th><th>Entry</th><th>Edge</th><th>Brain</th><th>PnL</th><th>Result</th></tr>"
    : "<tr><th>Market</th><th>Side</th><th>Entry</th><th>Size</th><th>Edge</th><th>Brain</th></tr>";
  const body = rows.slice().reverse().map((t) => {
    const side = `<span class="tag ${t.side === "YES" ? "yes" : "no"}">${esc(t.side)}</span>`;
    if (resolved) {
      const res = t.won ? '<span class="tag win">WIN</span>' : '<span class="tag loss">LOSS</span>';
      const pnl = `<span class="${t.pnl >= 0 ? "pos" : "neg"}">${t.pnl >= 0 ? "+" : ""}${fmt(t.pnl)}</span>`;
      return `<tr><td class="q" title="${esc(t.question)}">${esc(t.question)}</td><td>${side}</td>` +
        `<td>${fmt(t.entry, 2)}</td><td>${fmt(t.edge, 2)}</td><td>${fmt(t.brain, 2)}</td><td>${pnl}</td><td>${res}</td></tr>`;
    }
    return `<tr><td class="q" title="${esc(t.question)}">${esc(t.question)}</td><td>${side}</td>` +
      `<td>${fmt(t.entry, 2)}</td><td>${fmt(t.size, 0)}</td><td>${fmt(t.edge, 2)}</td><td>${fmt(t.brain, 2)}</td></tr>`;
  }).join("");
  return `<table>${head}${body}</table>`;
}

function lessonsHtml(ls) {
  if (!ls || !ls.length) return '<div class="empty">No lessons yet.</div>';
  return '<ul class="lessons">' + ls.map((l) =>
    `<li><span class="tag ${l.category === "win" ? "win" : "loss"}">${esc(l.category)}</span> ` +
    `<b>${esc(l.cause)}</b> → ${esc(l.recommendation)}</li>`).join("") + "</ul>";
}

load();

// --- run controls (Start/Stop via the local serve() API) ---
let _apiOk = false;

async function _postJSON(path, body) {
  const r = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  return r.json();
}

function renderRunStatus(st) {
  const badge = $("runStatus");
  const running = !!(st && st.running);
  const eur = st && st.cost ? ` · €${Number(st.cost).toFixed(4)}` : "";
  badge.textContent = running
    ? `running · ${st.mode} · Zyklus ${st.cycle}${eur}`
    : (st && st.error ? "Fehler" : "idle");
  badge.className = "badge " + (running ? (st.mode === "live" ? "live" : "paper") : "");
  const start = $("startBtn"), stop = $("stopBtn");
  start.disabled = running;
  stop.disabled = !running;
  start.style.opacity = running ? 0.5 : 1;
  stop.style.opacity = running ? 1 : 0.5;
  $("runMsg").textContent = (st && st.error)
    ? "⚠ " + st.error
    : (st && st.stop_reason && !running) ? "Gestoppt: " + st.stop_reason
    : (st && st.last) || "";
}

async function pollStatus() {
  try {
    const st = await fetch("/api/status", { cache: "no-store" }).then((r) => r.json());
    _apiOk = true;
    renderRunStatus(st);
    if (st.running) load(); // refresh KPIs/tables while the loop runs
  } catch (e) {
    if (!_apiOk) {
      // Not served by serve() (e.g. GitHub Pages) — controls only work locally.
      $("runStatus").textContent = "—";
      $("startBtn").disabled = $("stopBtn").disabled = true;
      $("startBtn").style.opacity = $("stopBtn").style.opacity = 0.5;
      $("runMsg").textContent = "Steuerung nur lokal: python -m tradebot.cli serve";
    }
  }
}

function wireControls() {
  const modeSel = $("runMode");
  modeSel.addEventListener("change", () => {
    $("liveGuard").hidden = modeSel.value !== "live";
  });
  const eur = $("maxEur"), rt = $("maxRuntime"), iv = $("runInterval");
  const showEur = () => ($("eurOut").textContent = +eur.value === 0 ? "aus" : "€" + (+eur.value).toFixed(2));
  const showRt = () => ($("rtOut").textContent = +rt.value === 0 ? "aus" : +rt.value + " min");
  eur.addEventListener("input", showEur);
  rt.addEventListener("input", showRt);

  // Persist the run controls to the backend (data/config.json via /api/config) —
  // the same place Seite 2 uses — so they survive a page reload instead of
  // snapping back to the HTML defaults. Saved on 'change' (slider released /
  // number committed) to avoid a POST on every drag tick.
  const saveRunParams = async () => {
    try {
      await _postJSON("/api/config", {
        run_interval: parseFloat(iv.value) || 60,
        run_max_eur: parseFloat(eur.value) || 0,
        run_max_runtime_min: parseFloat(rt.value) || 0,
      });
    } catch (e) { /* offline / GitHub Pages: keep local values */ }
  };
  [eur, rt, iv].forEach((el) => el.addEventListener("change", saveRunParams));

  const loadRunParams = async () => {
    try {
      const cfg = await fetch("/api/config", { cache: "no-store" }).then((r) => r.json());
      if (cfg.run_interval != null) iv.value = cfg.run_interval;
      if (cfg.run_max_eur != null) eur.value = cfg.run_max_eur;
      if (cfg.run_max_runtime_min != null) rt.value = cfg.run_max_runtime_min;
    } catch (e) { /* not served locally: keep the HTML defaults */ }
    showEur();
    showRt();
  };
  loadRunParams();
  $("startBtn").addEventListener("click", async () => {
    const mode = modeSel.value;
    const interval = parseFloat($("runInterval").value) || 60;
    const body = {
      mode, strategy: "scalp", interval,
      max_eur: parseFloat($("maxEur").value) || 0,
      max_runtime: (parseFloat($("maxRuntime").value) || 0) * 60, // min -> sec
    };
    if (mode === "live") {
      if (!$("liveAck").checked || $("liveConfirm").value.trim() !== "LIVE") {
        $("runMsg").textContent = "Live abgebrochen: Häkchen setzen und LIVE eintippen.";
        return;
      }
      if (!window.confirm("WIRKLICH live mit ECHTEM GELD starten?")) return;
      body.confirm = "LIVE";
    }
    const r = await _postJSON("/api/run", body);
    if (!r.ok) $("runMsg").textContent = "Start fehlgeschlagen: " + (r.error || "");
    pollStatus();
  });
  $("stopBtn").addEventListener("click", async () => {
    await _postJSON("/api/stop", {});
    $("runMsg").textContent = "Stop angefordert — endet nach dem laufenden Zyklus.";
    pollStatus();
  });
}

wireControls();
pollStatus();
setInterval(pollStatus, 4000);
