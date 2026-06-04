"use strict";

const $ = (id) => document.getElementById(id);
const esc = (s) =>
  s == null ? "" : String(s).replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmt = (n, d = 2) =>
  n == null ? "—" : Number(n).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
const pct = (n) => (n == null ? "—" : (n * 100).toFixed(1) + "%");

// Relative Zeit: "2m 14s", "1h 5m", "12s"
function relTime(iso) {
  if (!iso) return "—";
  const s = Math.max(0, Math.floor((Date.now() - new Date(iso)) / 1000));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

// Kurze Uhrzeit: "14:32"
function fmtTime(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
}

// Gelbe Warnung bei Trades die länger offen sind als 2x max_hold
function stuckWarning(openTrades, maxHoldSec) {
  const limit = (maxHoldSec || 300) * 2;
  const stuck = (openTrades || []).filter(t => t.opened_at &&
    (Date.now() - new Date(t.opened_at)) / 1000 > limit);
  if (!stuck.length) return "";
  return `<div class="warn-banner">
    ⚠️ <strong>${stuck.length} Trade(s) konnten nicht automatisch geschlossen werden</strong><br>
    Diese Positionen sind länger offen als das doppelte Scalp-Limit (${limit}s).
    Mögliche Ursachen: kein aktueller Marktpreis mehr verfügbar, oder die Sell-Order wurde
    vom Exchange nicht angenommen. <strong>Bitte auf Polymarket manuell prüfen.</strong>
    <ul>${stuck.map(t =>
      `<li><b>${esc(t.question)}</b> — ${t.side} — offen seit <b>${relTime(t.opened_at)}</b></li>`
    ).join("")}</ul>
  </div>`;
}

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
  // Schutz: state.json ist leer oder noch nicht geschrieben (z.B. nach Reset)
  if (!s || !s.mode) {
    const msg =
      `<div class="empty" style="padding:24px 0">
        ⏳ <strong>Noch keine Daten.</strong><br/>
        Starte den Bot: <code>Start.bat</code> öffnen, dann oben auf <b>▶ Start</b> klicken.<br/>
        <span style="font-size:12px;color:var(--muted)">
          Das Dashboard füllt sich nach dem ersten abgeschlossenen Zyklus automatisch.
        </span>
      </div>`;
    $("kpis").innerHTML = msg;
    ["equityChart","brainPanel","diagnostics","brainDiag","resolvedTable","openTable","lessons"]
      .forEach(id => { const el = $(id); if (el) el.innerHTML = '<div class="empty">—</div>'; });
    return;
  }

  const badge = $("modeBadge");
  badge.textContent = (s.mode || "").toUpperCase();
  badge.className = "badge " + (s.mode === "live" ? "live" : "paper");
  $("generatedAt").textContent = s.generated_at ? new Date(s.generated_at).toLocaleString() : "—";

  const brain  = s.brain  || {};
  const config = s.config || {};
  const pnlClass = (s.realized_pnl || 0) >= 0 ? "pos" : "neg";
  $("kpis").innerHTML = [
    kpi("Bankroll", "$" + fmt(s.bankroll), `start $${fmt(s.starting_bankroll)}`),
    kpi("Realized PnL", ((s.realized_pnl || 0) >= 0 ? "+" : "") + "$" + fmt(s.realized_pnl), "", pnlClass),
    kpi("Win rate", pct(s.win_rate), `${s.n_wins || 0}W / ${s.n_losses || 0}L`),
    kpi("Trades", s.n_trades || 0,
      `${s.n_open || 0} open` + (s.n_pending_maker ? ` · ${s.n_pending_maker} Maker ruht` : "")),
    kpi("Brain", brain.trained ? "Trained" : "Cold start", `${brain.experiences || 0} experiences`,
      brain.trained ? "pos" : ""),
  ].join("");

  $("equityChart").innerHTML = equitySvg(s.equity_curve, s.starting_bankroll);
  $("brainPanel").innerHTML = brainPanel(s);
  if ($("diagnostics")) $("diagnostics").innerHTML = diagnosticsHtml(s);
  if ($("brainDiag")) $("brainDiag").innerHTML = brainDiagHtml(s);
  $("resolvedTable").innerHTML = tradesTable(s.resolved_trades, true);
  const maxHold = config.max_hold_seconds || 300;
  const makerNote = s.n_pending_maker
    ? `<div class="empty" style="text-align:left;padding:6px 0;color:var(--muted)">⏳ ${s.n_pending_maker} Maker-Gebot(e) ruhen — Fill wird aus dem echten Preispfad bestätigt (noch keine Position).</div>`
    : "";
  $("openTable").innerHTML = makerNote + stuckWarning(s.open_trades, maxHold) + tradesTable(s.open_trades, false);
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
  const b = s.brain || {}, cfg = s.config || {};
  const total = b.experiences || 0;
  const wp = total ? Math.round((100 * (b.wins || 0)) / total) : 0;
  return (
    `<div class="brain-state ${b.trained ? "on" : "off"}">` +
    (b.trained ? "● Active — learning from outcomes" : "○ Cold start — needs ≥8 resolved trades") +
    `</div>` +
    `<div class="bar"><div class="bar-win" style="width:${wp}%"></div></div>` +
    `<div class="brain-legend"><span>${b.wins || 0} wins</span><span>${b.losses || 0} losses</span></div>` +
    `<p class="muted">The brain scores every setup; below <b>${esc(cfg.brain_veto_threshold ?? "—")}</b> ` +
    `it vetoes the trade. Learned weights carry over from paper to live.</p>`
  );
}

function diagnosticsHtml(s) {
  const hr = s.hold_recommendation || {};
  const cb = s.circuit_breaker || {};
  const dirTag = { raise: "↑ erhöhen", lower: "↓ senken", keep: "✓ passt" }[hr.direction] || "";
  const holdNums = hr.status === "ok"
    ? `<div class="muted" style="font-size:12px;margin-top:4px">aktuell ${hr.current}s · P50 ${hr.p50}s · ` +
      `P75 ${hr.p75}s · P95 ${hr.p95}s · <b>empfohlen ${hr.recommended}s</b> (${esc(dirTag)})</div>`
    : "";
  const hold =
    `<div style="padding:10px 0;border-bottom:1px solid #eef2f7">` +
    `<b>Haltedauer-Empfehlung</b>` +
    `<div class="muted">${esc(hr.message || "—")}</div>${holdNums}</div>`;

  const tripped = !!cb.tripped;
  const cbLine = tripped
    ? `<span class="neg">■ AUSGELÖST — ${esc(cb.reason || "")}</span>`
    : `<span class="pos">● aktiv und ruhig</span>`;
  const breaker =
    `<div style="padding:10px 0">` +
    `<b>Circuit-Breaker (Verlustschutz)</b><div style="margin-top:4px">${cbLine}</div>` +
    `<div class="muted" style="font-size:12px;margin-top:4px">heute realisiert ` +
    `$${fmt(cb.realized_today != null ? cb.realized_today : 0)} · ` +
    `${cb.consecutive_losses || 0} Verluste in Folge</div></div>`;

  return hold + breaker;
}

function brainDiagHtml(s) {
  const d = s.brain_diagnostics || {};
  const oos = d.oos || {};
  const cf = d.counterfactuals || {};
  const ex = d.experiences || {};
  const imp = d.feature_importance || [];
  const oosBlock = oos.status === "ok"
    ? `Accuracy ${pct(oos.accuracy)} · LogLoss ${fmt(oos.logloss, 3)} · AUC ${fmt(oos.auc, 3)} ` +
      `<span class="muted">(Train ${oos.n_train} / Test ${oos.n_test})</span>`
    : `<span class="muted">zu wenig Daten (Train ${oos.n_train || 0} / Test ${oos.n_test || 0})</span>`;
  const impBlock = imp.length
    ? `<ul class="lessons">` + imp.map((f) =>
        `<li><b>${esc(f.name)}</b> <span class="${f.importance >= 0 ? "pos" : "neg"}">` +
        `${f.importance >= 0 ? "+" : ""}${fmt(f.importance, 3)}</span></li>`).join("") + `</ul>`
    : `<div class="muted">noch keine Feature-Importance (zu wenig Daten)</div>`;
  const row = (label, body) =>
    `<div style="padding:8px 0;border-bottom:1px solid #eef2f7"><b>${label}</b>` +
    `<div style="margin-top:4px">${body}</div></div>`;
  return (
    row("Out-of-Sample (auf ungesehenen Trades)", oosBlock) +
    row("Lerndaten", `<span class="muted">${ex.total || 0} gesamt · ${ex.real || 0} echt · ` +
      `${ex.counterfactual || 0} counterfactual (Veto/Mirror)</span>`) +
    row("Veto-Scoreboard", `${cf.brain_right || 0} Vetos richtig (hätten verloren) · ` +
      `<span class="neg">${cf.brain_wrong || 0} zu streng</span> (hätten gewonnen) · ` +
      `<span class="muted">${cf.pending || 0} offen</span>`) +
    `<div style="padding:8px 0"><b>Wichtigste Features</b><div style="margin-top:4px">${impBlock}</div></div>`
  );
}

function tradesTable(rows, resolved) {
  if (!rows || !rows.length) return '<div class="empty">None.</div>';
  const head = resolved
    ? "<tr><th>Market</th><th>Side</th><th>Entry</th><th>Edge</th><th>Brain</th><th>PnL</th><th>Result</th><th>Geschlossen</th></tr>"
    : "<tr><th>Market</th><th>Side</th><th>Entry</th><th>Size</th><th>Edge</th><th>Brain</th><th>Offen seit</th></tr>";
  const body = rows.slice().reverse().map((t) => {
    const side = `<span class="tag ${t.side === "YES" ? "yes" : "no"}">${esc(t.side)}</span>`;
    if (resolved) {
      const res = t.won ? '<span class="tag win">WIN</span>' : '<span class="tag loss">LOSS</span>';
      const pnl = `<span class="${t.pnl >= 0 ? "pos" : "neg"}">${t.pnl >= 0 ? "+" : ""}${fmt(t.pnl)}</span>`;
      const ts  = `<td class="ts">${fmtTime(t.resolved_at)}</td>`;
      return `<tr><td class="q" title="${esc(t.question)}">${esc(t.question)}</td><td>${side}</td>` +
        `<td>${fmt(t.entry, 2)}</td><td>${fmt(t.edge, 2)}</td><td>${fmt(t.brain, 2)}</td><td>${pnl}</td><td>${res}</td>${ts}</tr>`;
    }
    const ts = `<td class="ts">${relTime(t.opened_at)}</td>`;
    return `<tr><td class="q" title="${esc(t.question)}">${esc(t.question)}</td><td>${side}</td>` +
      `<td>${fmt(t.entry, 2)}</td><td>${fmt(t.size, 0)}</td><td>${fmt(t.edge, 2)}</td><td>${fmt(t.brain, 2)}</td>${ts}</tr>`;
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
  const running  = !!(st && st.running);
  const draining = !!(st && st.draining);
  const hasError = !!(st && st.error);
  const eur = st && st.cost ? ` · €${Number(st.cost).toFixed(4)}` : "";

  // --- Ampel: rein visuell ---
  const state = !_apiOk  ? "disabled" :
                hasError ? "panic"    :
                draining ? "draining" :
                running  ? "running"  : "stopped";
  const tl = $("trafficLight"), lbl = $("tlLabel");
  if (tl)  tl.className     = "tl-housing " + state;
  if (lbl) lbl.textContent  =
    state === "disabled" ? "Offline"    :
    state === "panic"    ? "Fehler"     :
    state === "draining" ? "Stoppt …"  :
    state === "running"  ? "Läuft"     : "Gestoppt";

  // --- Buttons ---
  const start = $("startBtn"), stop = $("stopBtn");
  if (start && stop) {
    start.disabled     = running;
    stop.disabled      = !running;
    stop.textContent   = draining ? "■ Sofort abbrechen" : "■ Stop";
    start.style.opacity = running ? 0.5 : 1;
    stop.style.opacity  = running ? 1   : 0.5;
  }

  // --- Info-Badge ---
  const badge = $("runStatus");
  if (badge) {
    badge.textContent = draining
      ? `beende Trades…${eur}`
      : running
      ? `${st.mode} · Zyklus ${st.cycle}${eur}`
      : (hasError ? "Fehler" : "—");
    badge.className = "badge " + (running ? (st.mode === "live" ? "live" : "paper") : "");
  }
  $("runMsg").textContent = hasError
    ? "⚠ " + st.error
    : (st && st.stop_reason && !running)
    ? "Gestoppt: " + st.stop_reason
    : (st && st.last) || "";
}

async function pollStatus() {
  try {
    const st = await fetch("/api/status", { cache: "no-store" }).then((r) => r.json());
    _apiOk = true;
    renderRunStatus(st);
    if (st.running) load();
  } catch (e) {
    if (!_apiOk) {
      const tl = $("trafficLight"), lbl = $("tlLabel");
      if (tl)  tl.className    = "tl-housing disabled";
      if (lbl) lbl.textContent = "Offline";
      const badge = $("runStatus");
      if (badge) badge.textContent = "—";
      const start = $("startBtn"), stop = $("stopBtn");
      if (start) { start.disabled = true; start.style.opacity = 0.5; }
      if (stop)  { stop.disabled  = true; stop.style.opacity  = 0.5; }
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
  const showRt  = () => ($("rtOut").textContent  = +rt.value  === 0 ? "aus" : +rt.value + " min");
  eur.addEventListener("input", showEur);
  rt.addEventListener("input", showRt);

  const saveRunParams = async () => {
    try {
      await _postJSON("/api/config", {
        run_interval: parseFloat(iv.value) || 60,
        run_max_eur: parseFloat(eur.value) || 0,
        run_max_runtime_min: parseFloat(rt.value) || 0,
      });
    } catch (e) { /* offline */ }
  };
  [eur, rt, iv].forEach((el) => el.addEventListener("change", saveRunParams));

  const loadRunParams = async () => {
    try {
      const cfg = await fetch("/api/config", { cache: "no-store" }).then((r) => r.json());
      if (cfg.run_interval       != null) iv.value  = cfg.run_interval;
      if (cfg.run_max_eur        != null) eur.value = cfg.run_max_eur;
      if (cfg.run_max_runtime_min!= null) rt.value  = cfg.run_max_runtime_min;
    } catch (e) { /* nicht lokal */ }
    showEur(); showRt();
  };
  loadRunParams();

  // --- Start-Button ---
  $("startBtn").addEventListener("click", async () => {
    const mode     = modeSel.value;
    const interval = parseFloat(iv.value) || 60;
    const body = {
      mode, strategy: "scalp", interval,
      max_eur:     parseFloat(eur.value) || 0,
      max_runtime: (parseFloat(rt.value) || 0) * 60,
    };
    if (mode === "live") {
      if (!$("liveAck").checked || $("liveConfirm").value.trim() !== "LIVE") {
        $("runMsg").textContent = "Live abgebrochen: Häkchen setzen und LIVE eintippen.";
        return;
      }
      if (!window.confirm("WIRKLICH live mit ECHTEM GELD starten?")) return;
      body.confirm = "LIVE";
    }
    // Ampel schon mal auf Gelb setzen während API antwortet
    const tl = $("trafficLight"), lbl = $("tlLabel");
    if (tl)  tl.className    = "tl-housing starting";
    if (lbl) lbl.textContent = "Startet …";
    const r = await _postJSON("/api/run", body);
    if (!r.ok) $("runMsg").textContent = "Start fehlgeschlagen: " + (r.error || "");
    pollStatus();
  });

  // --- Stop-Button ---
  $("stopBtn").addEventListener("click", async () => {
    const r = await _postJSON("/api/stop", {});
    $("runMsg").textContent = (r && r.forcing)
      ? "Hart-Stopp: laufende Trades werden NICHT mehr beendet (settle übernimmt)."
      : "Stop: keine neuen Trades. Offene Positionen werden noch zu Ende geführt.";
    pollStatus();
  });
}

wireControls();
pollStatus();
setInterval(pollStatus, 4000);
