# Aktueller Stand

Stand: 2026-06-05 · `main`

## Bugfixes: Veto-Score Logik & LLM Exception-Logging (2026-06-05)

### Fix 1 — MLP Veto-Score Widerspruch (`llm/client.py`)
- **Problem:** `brain_score = 0.000` wurde bei NO-Trades fälschlicherweise als "high confidence"
  interpretiert (LLM verwechselte den Score mit P(YES)). Beim gleichen Score auf YES-Trades hieß es
  korrekt "no confidence" — inkonsistent.
- **Ursache:** System-Prompt beschrieb den Score nur als "(higher = more confident the trade wins)"
  ohne klarzustellen, dass es P(traded side wins) ist, NICHT P(YES). Das LLM las low score + NO-Trade
  als "MLP bestätigt die NO-Seite" statt als "MLP sagt dieser Trade verliert".
- **Fix:**
  - System-Prompt explizit: "P(the TRADED side wins); NOT P(YES). On a NO trade, a score of 0.0
    means the MLP expects the NO bet to LOSE."
  - User-Prompt zeigt jetzt `MLP veto score P(NO wins): 0.000` statt nur `MLP veto score: 0.000`
    → LLM kann den Score nie mehr in der falschen Richtung interpretieren.

### Fix 2 — LLM Exceptions stumm verschluckt (`llm/deepseek.py`, `llm/claude.py`)
- **Problem:** `_complete()` fing alle Exceptions ohne Logging — bei Timeout/5xx/Netzfehler sah man
  nur den INFO-Log "(no answer - call failed)", aber nie die Fehlerursache. Freeze-Debugging war
  unmöglich.
- **Fix:** `except Exception as e: get_logger("llm").warning("DeepSeek/Anthropic call failed: %s", e)`
  → Fehlerursache (Timeout, HTTP-Fehler, etc.) ist jetzt im Log sichtbar.

### Fix 3 — Freeze-Log Lücke: Postmortem startet unsichtbar (`agents/postmortem.py`)
- **Problem:** Zwischen "Gamma: fetched 300 active markets" und "Scan: X/300 markets passed filters"
  laufen ggf. postmortem-LLM-Aufrufe (für aufgelöste Trades in `manage_open` → `_after_resolved`).
  Diese Phase war komplett stumm → bei LLM-Hänger sah es aus wie ein Freeze ohne Ursache.
- **Fix:** `run()` loggt jetzt bei Beginn: "Postmortem: analyzing N resolved trade(s) via LLM"
  → der Freeze-Ort ist sofort im Log erkennbar.

---

Stand: 2026-06-04 · `main` (Refactor gemerged aus `claude/inspiring-keller-tRIBa`)

## Brain-Learning: Counterfactuals (Veto/Mirror), Validierung & Feature-Importance (neu, 2026-06-04)

Antwort auf drei Probleme: **Survivorship Bias** (Brain lernt nur aus selbst gemachten Trades),
**keine Validierung** (keine Feature-Importance, kein Out-of-Sample, keine bewusste Gewichtung),
**zu wenig Daten**. Alle Logik in reinen, getesteten Modulen.

### Problem 1 — Counterfactual-Learning (Veto + Mirror)
- **Mechanismus (vom Nutzer geklärt):** NICHT „halten bis Auflösung". Stattdessen: was hätte der
  Trade — oder die Gegenseite — **in unserem Scalp-Stil über dasselbe kurze Fenster** gebracht?
  Abgewickelt über die reale Preis-Bahn aus `snapshots` (jetzt mit `spread`-Spalte) mit der
  EXISTIERENDEN Scalp-Logik (TP/SL/max_hold). Kein simuliertes Outcome — nur die Position ist
  hypothetisch, die Preise sind echt.
- **`brain/counterfactual.py::settle_scalp_path`** (rein): replayed einen Scalp über
  `[(ts, yes_price, spread)]` → `settled|pending|expired` + exit_price/pnl/won/reason.
- **NUR ECHTE WERTE — nie geraten.** Ein Counterfactual wird ausschließlich abgewickelt auf
  (a) einer realen TP/SL-Überschreitung im Fenster oder (b) dem ersten realen Snapshot bei/nach
  `max_hold` und höchstens `2×max_hold` (rechtzeitiger Time-Exit). Bei Datenlücke (Markt nicht mehr
  gescannt) oder zu spätem Tick wird NICHTS erfunden → `pending` (Fenster offen) bzw. `expired`
  (Ausgang unbekannt, kein Lernen). Der synthetische `backtest` ist vollständig isoliert (eigener
  Predictor/Bankroll, kein Zugriff auf Store/Brain) und kann das Brain nie verschmutzen.
- **Capture (Orchestrator `_record_counterfactuals`):** pro Signal → vetot/sized-out: eigene Seite
  (Source `veto`, mit Reason) + Gegenseite (`mirror`); real getradet: nur Gegenseite. `BrainManager`
  legt dazu `self.decisions` (Signal, approved, reason) ab. Nur im Scalp-Modus.
- **Settle (`settle_counterfactuals`, in `run_once` nach `manage_open`):** pending CF gegen
  `snapshots_between` abwickeln; bei `settled` → geflaggte `Experience(is_counterfactual=True)` →
  Brain retrainiert. `learn_from_vetos`-Setting (Default an) steuert das Training; das Scoreboard
  füllt sich unabhängig.
- **Neu:** Tabelle `counterfactuals` + Model `Counterfactual`; `Experience.is_counterfactual`;
  `snapshots.spread`; Store-CRUD (`save/pending/update_counterfactual`, `counterfactual_stats`,
  `snapshots_between`). Counterfactuals speisen NUR das Brain (Scalp-PnL-Label), NICHT den
  resolution-basierten XGBoost-Predictor.

### Problem 2 — Validierung
- **`brain/validation.py`** (rein): `time_split` (chronologisch, kein Lookahead),
  `evaluate_oos` (frisches Netz auf Train, Messung Accuracy/LogLoss/AUC auf ungesehenem Test),
  `permutation_importance` (Feature mischen → LogLoss-Verschlechterung = Wichtigkeit), `diagnose`
  (beides aus EINEM Held-out-Fit, kein Leak). Produktiv-Brain trainiert weiter auf ALLEN Daten;
  diese Splits sind nur Reporting.
- **L2-Regularisierung** in beiden Brain-Backends (`brain_l2`, Default 1e-4) → Rausch-Features
  werden Richtung 0 gedrückt (das Netz „lernt selbst, welche Features zählen"). Verifiziert:
  synthetisches Signal-Feature bekam Importance ~+4.3, Rauschen ~0; OOS-Accuracy/AUC = 1.0.

### Problem 3 — Mehr Daten
- Counterfactuals liefern **1–2 echte Labels pro Signal pro Zyklus** (Veto + Mirror) statt ~0–1
  Trade. ≥8-Trainingsschwelle wird schneller und mit ausgewogeneren Klassen erreicht; die Schwelle
  wurde NICHT gesenkt (Overfitting-Schutz).

### Surfacing
- `state.json` → `brain_diagnostics` { oos, feature_importance, counterfactuals (Veto-Scoreboard:
  richtig/zu-streng/offen), experiences (real vs counterfactual) }. Dashboard-Panel „Brain-Diagnose"
  (`index.html`/`app.js`). CLI: `reset` wiped auch `counterfactuals`; neuer Befehl `brain-report`.
  Settings (config-only): `learn_from_vetos`, `brain_l2`.

### Tests
- **153 grün** (vorher 130, +23). Neu: `test_counterfactual.py`, `test_validation.py`,
  `test_store_counterfactual.py`, `test_counterfactual_orchestrator.py`; Ergänzungen in
  `test_hardfail_and_settlement.py` (BrainManager.decisions) und `test_dashboard_state.py`.
- Brain + Dashboard erneut gecleart (Schema-Erweiterung), `state.json` neu (leer, mit
  `brain_diagnostics`).

## Microstructure-Verbesserungen: Tick / Spread / Circuit-Breaker / Maker-First / Hold-Analyse (neu, 2026-06-04)

Umsetzung der `IMPLEMENTIERUNGSVORGABEN` — 5 Punkte umgesetzt, 2 bewusst ausgelassen.
Jede Logik steckt in einem **reinen, getesteten Modul**; die Agenten/Exchange rufen es nur auf.

### Punkt 7 — Tick-Size-Awareness (Teil B.5) — `tradebot/exchange/ticks.py`
- `get_tick_size(price)`: 1¢-Raster im Normalbereich, 0,1¢-Raster nahe den Extremen
  (≤0.05 / ≥0.95). `round_to_tick(price)`: Snap aufs gültige Raster (round-half-up).
- `targets_collapse(entry, tp, sl)`: blockiert Scalps, deren TP/SL nach dem Runden auf
  den Einstieg zusammenfallen (Move < ½ Tick → Trigger könnte nie feuern). Eingebaut in
  `agents/predict.py` (neben dem Spread-Guard). Live-Orders (`polymarket.py`) submitten
  jetzt mit `round_to_tick` statt `round(.,2)` → keine Reject wegen ungültiger Preise.

### Punkt 1 — Spread-/Tiefen-Filter (Teil A.1) — `tradebot/risk/liquidity.py`
- `passes_spread_filter(market, max_spread, min_liq)`: ersetzt die festen USDC-Gates im
  Scan. Buch bekannt → Gate auf `spread <= max_spread` (dominanter Round-Trip-Kostenfaktor);
  Buch unbekannt → Fallback auf Liquiditäts-Mindestfloor (nie einen Markt handeln, den man
  nicht bepreisen kann). `min_volume_24h` ist **kein hartes Gate mehr** (nur noch Ranking).
- **Orderbuch-Tiefe vs. Ordergröße** beim Sizing (`risk/kelly.py`): `depth_too_thin` lehnt
  ab, wenn 10 % der sichtbaren Liquidität < $1; `max_order_for_depth` deckelt die Order auf
  10 % der Tiefe. Neue Setting **`max_spread`** (Default 0.03), Slider auf Seite 2.
- Wide-Spread-Flag ist jetzt relativ (`>= max(0.05, 0.6·max_spread)`).

### Punkt 4 — Circuit-Breaker (Teil B.2) — `tradebot/risk/circuit_breaker.py`
- `circuit_breaker_reason(realized_today, bankroll, streak, settings)`: trippt bei
  **Tagesverlust** ≥ `max_daily_loss_pct` (Default 5 %) ODER **Verlust-Streak** ≥
  `max_consecutive_losses` (Default 5). 0 = aus.
- Store: `realized_pnl_today` (ab 00:00 UTC) + `consecutive_losses` (zählt Verluste bis zum
  ersten Win; Voids zählen nicht). `orchestrator.run_once` prüft NACH `manage_open` und VOR
  dem Öffnen → bei Trip: **kein neuer Trade**, `breaker_reason` gesetzt; `server._loop` bricht
  ab und **fährt offene Trades sauber herunter (kein Abandon)**. CLI-`scalp`-Loop ebenso.

### Punkt 5 — Maker-First-Execution (Teil B.3) — `tradebot/exchange/execution_style.py`
- `decide_execution_style(price, edge, spread, settings)` → `ExecPlan(style, limit_price, reason)`.
  Edge < `maker_min_edge` (Default 0.03) → **Taker** (sofort Liquidität). Edge groß genug →
  **Maker**: passive Order 1 Tick innerhalb (≈ mid − 1 Tick), füllt sie nicht in
  `maker_timeout_seconds` (Default 60 s) → Cancel + Taker-Fallback.
- `Order` trägt jetzt `edge`/`spread` (von `RiskAgent` gesetzt, Spread über neue
  `spread_by_market`-Map aus dem Orchestrator). `Trade.exec_style` (`"maker"|"taker"`) +
  DB-Spalte + Migration + Dashboard. **Paper** protokolliert nur die gewählte Variante
  (kein erfundener Maker-Vorteil — „no simulated outcomes"); **Live** (`polymarket.py`,
  pragma no cover) fährt den echten Maker→Taker-Flow inkl. Poll/Cancel.

### Punkt 2 — Max-Hold-Analyse (Teil A.2) — `tradebot/brain/hold_analysis.py`
- `recommend_max_hold(won_holds, lost_holds, current)`: P50/P75/P95 der **Gewinner**-Haltezeiten,
  Empfehlung ≈ P75 × 1.2 (clamped 30–600 s), Richtung erhöhen/senken/passt (10 %-Totband).
  `scalp_hold_seconds(trades)` zieht die Haltezeiten aus resolved Scalps. **Nur Empfehlung** —
  der Slider bleibt beim Nutzer. Ausgabe in Logs (`_after_resolved`) + `state.json`
  (`hold_recommendation`) + Dashboard-Panel „Empfehlungen & Schutz".

### Bewusst NICHT umgesetzt
- **Punkt 3** (ε-Decay-Explorationsbudget): „Bot hat ausreichend trainiert" → entfällt.
- **Punkt 6 der Vorgaben war Edge-Shrinkage bei kaltem Brain (Teil B.4): nicht gewünscht.**

### Settings/UI
- Neu in `config.py` + Server-DEFAULTS + `settings.js`: `max_spread`, `max_daily_loss_pct`,
  `max_consecutive_losses`, `maker_min_edge` (Slider) sowie `maker_first`/`maker_timeout_seconds`
  (config-only). Alle 3 Presets (vorsichtig/ausgewogen/aggressiv) um die neuen Keys ergänzt,
  alle Werte rasterkonform verifiziert. Dashboard zeigt Hold-Empfehlung + Circuit-Breaker-Status.

### Tests
- **130 grün** (vorher 98, +32). Neu: `test_ticks.py`, `test_liquidity.py`,
  `test_circuit_breaker.py`, `test_execution_style.py`, `test_hold_analysis.py`,
  `test_predict_tick_guard.py`, `test_risk_exec_style.py`, `test_dashboard_state.py`.
  `test_scan_filters.py` auf den neuen Spread-Filter umgeschrieben.

### Kürzestes Intervall (Frage aus dem Vorlauf)
- Harte Untergrenze im Server: **5 s** (`max(5.0, interval)`). Praktisch begrenzt der
  serielle LLM-Zyklus (Scan→Research→Predict→BrainManager→Risk) die effektive Mindestdauer
  auf einige Sekunden pro Markt — schneller bringt nichts, da jeder Zyklus auf die
  Agent-Antworten wartet.


## Kontext-Handoff, Brain-Feature, Presets + Log-Trennung (neu, 2026-06-04)

Vier Sachen in einem Rutsch — Antwort auf „wir machen alle 3 Empfehlungen + Presets".

### 1. Test-Log-Trennung
- `llm/client.py`: Transcript-Pfad kommt aus `_llm_log_path()` und ist per Env-Var
  **`TRADEBOT_LLM_LOG`** überschreibbar (pro Aufruf aufgelöst). `tests/conftest.py`
  (neu) leitet das Log session-weit in ein Temp-File. Tests verschmutzen das echte
  `data/llm_log.jsonl` nicht mehr (verifiziert: 2417 Zeilen vor/nach Testlauf).

### 2. Numerisches Brain-Feature (Empfehlung 1)
- Neues Feature **`sentiment_agreement`** (ml/features.py, ans Ende angehängt):
  stimmen die *populierten* Kanäle RSS/Social/Web überein? 1.0 = Konsens, 0.0 =
  Gegenextreme, 0.5 = nichts zu vergleichen. Ein **numerisches** Signal, das das
  Brain wirklich verwerten kann (Text kann es nicht — es frisst nur Zahlen).
- Schema: `FEATURE_DIM` 19→20, `BRAIN_FEATURE_DIM` 21→22. ⚠️ Folge: altes
  `brain.npz` (21-dim) lädt nicht mehr → Cold-Start; alte Experiences werden vom
  Drift-Guard übersprungen; XGBoost fällt bis ≥20 neue Trades auf die Heuristik.
  **Empfehlung: einmal `reset --yes`** für eine saubere Basis.

### 3. Strukturiertes Kontext-Feld forecast → BrainManager (Empfehlung 2)
- Der Forecaster (`estimate_prob`) liefert sein `reason` jetzt als **≤2-Satz-Handoff**
  (Haupttreiber + Hauptrisiko, für den Risk-Manager geschrieben). Es reitet auf dem
  vorhandenen Return-Tuple → **keine Signaturänderung**, landet in `Signal.rationale`.
- `BrainManager` reicht `sig.rationale` als neues `forecast_context` an
  `decide_execution` weiter; dort wird es nur bei Inhalt als „Forecaster's note:"
  an den User-Prompt gehängt (sonst byte-identisch wie vorher).
- **`max_tokens` je Task +10%** (Puffer gegen abgeschnittenes JSON): sentiment
  200→220, forecast 250→275, brainmanager 200→220, postmortem 250→275.

### 4. Presets auf Seite 2 (Empfehlung 3 / „mehr Trades")
- Freie Regler bleiben, plus **4 Modi**: ① **Frei** (eigene Werte) + 3 feste,
  **evidenzbasierte** Setups (Opus-Web-Recherche, Quellen unten) — ② **Vorsichtig**,
  ③ **Ausgewogen**, ④ **Lern-/Aggressiv**.
- **Werte aus der Recherche** (Thorp/Ziemba zu Fractional Kelly, Polymarket-Fee/
  Spread-Doku, Bandit/RL zum Cold-Start). Kernkorrektur: ④ bekommt die **kleinste**
  Positionsgröße (0,15-Kelly, 0,5 % pro Trade), nicht die größte — „aggressiv" =
  **lockere Gates** (3 pp Edge, niedriges Brain-Veto, Aggressivität 70 %) für viele
  Trades, aber **winzige Einsätze**, um Lern-Daten ohne große Verluste zu „kaufen".
  ② nutzt Viertel-Kelly/1 %/8 pp-Edge (Kapitalerhalt), ③ ≈0,4-Kelly/2 %/5 pp.
- Klick auf ②③④ schreibt die Werte in alle Slider (außer **bankroll** — Kapital
  bleibt). Jede manuelle Reglerbewegung springt zurück auf ① Frei. Die Auswahl wird
  als `preset` in `data/config.json` gemerkt (Server-DEFAULTS; Settings ignoriert
  den Key). `docs/settings.js` + `settings.html`. Slider-Untergrenze
  „Max-Einsatz/Trade" 1 % → 0,5 % gesenkt, damit ④ darstellbar ist.
- **Recherche-Quellen** u. a.: Kelly/Ziemba (Stochastic Optimization in Finance, ch.1),
  Polymarket Fees & Liquidity-Rewards (docs.polymarket.com), QuantJourney Fee-Analyse,
  Multi-Armed-Bandit/Exploration (gibberblot RL-Notes). Schwach belegt & als
  Ermessens-Entscheid markiert: absolute USDC-Liquiditätsschwellen und `max_hold_seconds`
  (an unsere Slider-Range angepasst statt der höheren Roh-Empfehlung).
- **Vom Agenten zusätzlich empfohlen (noch NICHT umgesetzt):** Explorations-Budget mit
  ε-Decay (8→~50 Trades), Tages-Verlustlimit (Circuit-Breaker), Maker-First-Execution
  (Polymarket-Maker = 0 Fees), Edge-Shrinkage bei kaltem Brain, Tick-Size-Awareness.

### Tests
- **98 grün.** Neu: `tests/test_features.py` (5), `tests/conftest.py`, je +2 in
  `test_llm_client.py` (Kontext-Feld) und +1 in `test_hardfail_and_settlement.py`
  (Handoff), +2 Log-Isolation.

## Graceful Stop: offene Trades werden fertig geführt (neu, 2026-06-04)

**Problem:** Stop beendete den Loop und machte nur EINEN `manage_open`-Sweep. Im
Scalp-Modus schließt der aber nur Positionen, die schon Take-Profit/Stop-Loss/
Max-Hold erreicht haben — alle anderen blieben offen, und danach pollte niemand
mehr die Preise. → Offene Trades „verliefen im Leeren".

**Fix (`server.py`):** Stop löst jetzt eine **Wind-Down-Phase** aus
(`_Runner._wind_down`): keine neuen Zyklen/Positionen mehr, aber die offenen
Positionen werden weiter gepollt und geschlossen.
- **Scalp:** Drain-Loop, bis das Buch leer ist — spätestens nach `max_hold_seconds`
  (+ Sicherheits-Deadline), dann Übergabe an `settle`.
- **Resolve:** ein Settle-Sweep; nicht auflösbare Positionen **persistieren** in der
  DB für den `settle`-Poller (nicht force-geschlossen, nicht verloren).
- Gilt auch beim Erreichen von Budget-/Laufzeit-Limit (kein Abandon mehr).
- **Zweiter Stop-Klick** während des Wind-Downs = **Hart-Abbruch** (`_force`),
  Rest geht an `settle`.
- Frontend: Badge „beende offene Trades …", Stop-Button wird zu „■ Sofort
  abbrechen", ehrliche Meldungstexte (`docs/app.js`).
- Tests: `tests/test_wind_down.py` (4) — drain-bis-leer, resolve-Übergabe,
  Hart-Abbruch, Stop-Semantik. Kein echtes Sleep/Netz.

## Mehr Trades: Risk-Adjuster + freie Social-Quellen + Slider-Persistenz (neu, 2026-06-03)

Diese Runde zielt auf **mehr und schneller lernende Trades** — über drei Hebel,
plus ein Reset auf null.

### 1. Aggressivitäts-Regler (Risk-Adjuster)
- Neues Modul `tradebot/risk/adjuster.py`: EIN Regler `aggressiveness ∈ [0,1]`
  (Settings + Slider auf Seite 2) lockert zur Laufzeit die Filter, OHNE den
  mathematischen Kern (Kelly/MLP/Edge) anzufassen — es verschiebt nur die
  Vergleichsschwellen. `risk_profile(settings)` liefert effektive Werte:
  Brain-Veto → linear auf 0, Konfidenz → Boden 0.5, Edge → Boden 0.02; dazu
  `size_factor` (mehr, aber KLEINERE Trades) und einen Klartext-„Ping" für den
  BrainManager-Prompt.
- Verdrahtet in `kelly.size_position` (Veto/Konfidenz/Größe), `predict` (Edge-Gate)
  und `brain_manager`/`llm.client.decide_execution` (Agent-Anweisung). Default
  `0.0` = unverändertes Verhalten. Tests: `tests/test_adjuster.py`.
- Bricht den Cold-Start-Teufelskreis: mehr Approvals → mehr settled Trades →
  Brain erreicht die ≥8-Schwelle und lernt endlich. (Lokal auf `0.7` gesetzt in
  `data/config.json`, das gitignored ist.)

### 2. Freie Social-Quellen als Reddit-Ersatz
- Reddits öffentliche API ist faktisch dicht (OAuth/403). Neues Modul
  `tradebot/data/social.py` aggregiert drei **keyless, dauerhaft kostenlose**
  Quellen: **Bluesky** (public AppView), **Hacker News** (Algolia), **Lemmy** (v3).
  Best-effort, jede Quelle gekapselt → eine tote Quelle bricht nie einen Zyklus.
- In `research.py` füllt der bisherige „Reddit"-Kanal jetzt ein breiter
  **Social-Kanal** (Reddit nur falls Creds + Bluesky/HN/Lemmy). KEIN
  Feature-Schema-Wechsel — nutzt den vorhandenen `reddit_*`-Slot weiter, kein
  Retraining nötig. Labels „Reddit"→„Social" in Narrativ, Agent-Prompt, Dashboard.
- Live verifiziert: je ~10 echte Snippets pro Frage, ganz ohne Key. Tests:
  `tests/test_social.py`. Wirkung: der „n_sources==0 → skip"-Fall (`predict.py`)
  tritt kaum noch auf, und es gibt mehr Sentiment-Signal für handelbare Edges.

### 3. Slider-Persistenz Seite 1 + Config-Merge
- Die Run-Slider (Pause/Zyklus, Euro-Budget, Laufzeit) auf Seite 1 sprangen beim
  Reload zurück. Jetzt laden/speichern sie über `/api/config` (Keys `run_interval`,
  `run_max_eur`, `run_max_runtime_min`) — front + back an derselben Stelle wie Seite 2.
- `POST /api/config` **merged** jetzt (statt zu überschreiben), damit Seite 1 und
  Seite 2 sich nicht gegenseitig die Werte löschen.

### 4. Reset auf null
- Historie genullt (`reset --yes`): trades/experiences/lessons/snapshots +
  `brain.npz` entfernt; Dashboard via `export` regeneriert. Der Bot lernt ab jetzt
  nur aus neuen, echten Ergebnissen. (Das `manager_decisions`-Audit bleibt bewusst.)

### Tests
- **88 grün** (1 Warnung). Neu in dieser Runde: `tests/test_adjuster.py` (6),
  `tests/test_social.py` (5), `tests/test_wind_down.py` (4).

## Live-Verifikation mit Netz + DeepSeek (neu, 2026-06-03)

Erstmals mit Internet + echtem DeepSeek-Key getestet (`LLM_PROVIDER=deepseek`,
`DEEPSEEK_MODEL=deepseek-chat` = die schnelle, **nicht-denkende** Variante;
`deepseek-reasoner` wäre die denkende).

- **DeepSeek-Smoke-Test bestanden** — der Vorbehalt aus `ERKENNTNISSE` ist aufgelöst:
  Raw-`POST /chat/completions` → `200`, Response-Form `choices[0].message.content`
  bestätigt; `sentiment()` auf echten RSS-Schlagzeilen → `(-0.85, "...outflows...")`.
  Modell-Echo `deepseek-v4-flash`, `reasoning_content=False`.
- **Daten-Layer:** Gamma **200** (300 Märkte, Scan ~44-49 passen die Filter);
  RSS ✅ 8 Schlagzeilen; Reddit ⚠️ **403** (Reddit verlangt inzwischen OAuth — der
  öffentliche `.json`-Search ist dicht; Code degradiert sauber auf `[]`).
- **Bugfix — Brain Feature-Schema-Drift:** alte Experiences/Trades (10-dim Features
  aus dem Schema *vor* den 5 quellengetrennten Signalen) crashten das 17-dim-Netz
  (`matmul 12≠17`). Fix (nicht-destruktiv): `Brain.train_from_experiences` filtert
  Zeilen ≠ `net.input_dim` (mit Log), und `NeuralBrain/TorchBrain.train` verweigern
  einen Breiten-Mismatch statt zu crashen. Eine Schema-Erweiterung bricht damit nie
  mehr einen laufenden Zyklus.
- **Paper-Zyklus End-to-End grün:** `research → predict → BrainManager → risk` mit
  DeepSeek; der BrainManager liefert echte Vetos/Approves (1/3 approved); 0 Trades,
  weil das Risk-Gate den approved Trade bei Confidence < Threshold zurückhielt
  (korrektes Verhalten, kein Bug).
- **Tests:** 71 passed, 1 skipped (Python 3.14.5).
- **Offen:** optional `reset --yes` entsorgt die 18 inkompatiblen Alt-Trades (10-dim);
  längerer Paper-/Scalp-Lauf (B-3); UI-Vorschau (Punkt 7); Polymarket-Live (B-4).

## Provider-agnostischer LLM-Client + Agent-Pflicht (neu, 2026-06-03)

Option B umgesetzt: der LLM-Zugriff läuft jetzt über eine **provider-agnostische
Schicht** (`tradebot/llm/`), und der Agent ist **verbindlich** (Hard-Fail ohne Key).

- **`tradebot/llm/client.py`** — abstraktes `LLMClient`-Interface; ALLE Prompts/
  Parsing leben hier. Ein Provider liefert nur `available` + `_complete()`.
- **`AnthropicClient`** (`llm/claude.py`, Alias `Claude`) und **`DeepSeekClient`**
  (`llm/deepseek.py`, OpenAI-kompatibel über `httpx`, ~10x günstiger).
- **Factory** `make_client(settings)` (`llm/__init__.py`) wählt per `LLM_PROVIDER`
  (`anthropic` | `deepseek`, **Default: deepseek**).
- **Hard-Fail (Brain+Agent gekoppelt):** `Orchestrator.__init__` bricht VOR dem
  Zyklus mit `LLMUnavailableError` ab, wenn kein Agent verfügbar ist; die CLI gibt
  eine klare Meldung + Exit 1. „Agent da → alles; Agent weg → nix."
- **BrainManager: kein Auto-Approve mehr** — ohne Agent **fail-closed (Veto)**,
  auch im Paper-Modus.
- **Config:** `.env` → `LLM_PROVIDER`, `DEEPSEEK_API_KEY`, `ANTHROPIC_API_KEY`,
  `*_MODEL`; Properties `has_llm` / `llm_api_key`.
- **Tests:** `tests/test_llm_client.py` (15 neu) — Interface, Factory-Dispatch,
  `available`-Gate, DeepSeek-Transport (gemockt), Orchestrator-Hard-Fail.

## Refactor: Hard-Fail-Architektur + BrainManager (neu, 2026-06-03)

Der große Umbau in dieser Runde — der Bot handelt ab jetzt **nur noch mit echten
Daten**. Es gibt keine erfundenen Notfall-Signale mehr.

### 1. Hard-Fail — keine erfundenen Daten mehr
- **`_pseudo()` raus** (`data/sentiment.py`): kein deterministischer Offline-Prior
  mehr (vorher: Sentiment aus einem Hash der Frage).
- **VADER raus** (`data/sentiment.py` + `pyproject.toml`): kein Ersatz-Scorer mehr.
  Ohne echte Texte ist das Sentiment strikt **neutral `0.0`** → kein erfundener Edge.
- **Fixtures-Fallback raus** (`data/gamma.py`): `fetch_markets()` wirft
  `DataUnavailableError`, statt 8 Beispiel-Märkte zu liefern. `fixtures.py` bleibt
  nur als Hilfe für **einen** Scan-Unittest — der Handels-Pfad nutzt sie nie.
- **Regel:** Fehlen echte Daten (Gamma / RSS / Reddit), **bricht der Zyklus ab**
  (`orchestrator.run_once` → `DataUnavailableError`). Ein Handelszyklus findet ohne
  echte externe Signale **nie** statt.
- **Folge für die Sandbox:** Hier ist Gamma mit 403 geblockt → der Bot stoppt jetzt
  bewusst (vorher lief er auf Fixtures). Lokal mit Netz liefert Gamma echte Märkte.

### 2. BrainManager — Claude Haiku als Meta-Controller (Stufe 5)
- Neuer Agent **`agents/brain_manager.py`**, läuft zwischen Predict (3) und
  Risk/Execution (4). Vor jeder Order bekommt Haiku: getrennte **Reddit/RSS**-
  Sentiments, die **XGBoost**-Wahrscheinlichkeit und den **MLP-Veto-Score**.
- Haiku prüft auf **logische Widersprüche** und entscheidet final
  **„Execution Approved" / „Execution Vetoed"**. Die Begründung jeder Entscheidung
  wird **zwingend in die DB** geschrieben (Tabelle `manager_decisions`).
- **Fail-closed, immer:** unbrauchbare LLM-Antwort → Veto; **kein Agent → Veto**
  (kein Auto-Approve mehr, auch nicht im Paper-Modus). In der Praxis bricht der
  Orchestrator ohne Agent ohnehin beim Start ab (Hard-Fail) — das ist Defense-in-Depth.
- Provider-agnostisch über `LLMClient` (Default DeepSeek; Anthropic-Modell
  `claude-haiku-4-5-20251001`). Siehe Abschnitt „Provider-agnostischer LLM-Client" oben.

### 3. Behobene Schwachstellen (aus dem Vulnerability-Assessment)
- **Live-Close (Prio 1):** `PolymarketExchange.close` markiert einen Trade nur nach
  **bestätigtem SELL** (oder im Dry-Run) als geschlossen. Schlägt der Verkauf fehl
  → Trade bleibt **offen** (kein Phantom-Close mehr).
- **Live-BUY (Prio 2):** eine Order wird erst als Trade gespeichert, wenn
  **`filled_size > 0`** bestätigt ist (`_parse_execution` / `ExecutionResult`).
- **Settlement-Enum (Prio 4):** `get_resolution()` liefert statt `True/False/None`
  jetzt `ResolutionStatus` (**OPEN / YES / NO / CANCELED / AMBIGUOUS / ERROR**).
  API-Fehler, abgesagte und unklare Märkte sind unterscheidbar und werden geloggt,
  statt verschluckt zu werden. CANCELED = Refund (PnL 0, nicht fürs Lernen genutzt).
- **Source-Split (Prio 5):** `ResearchReport` und `FEATURE_NAMES` haben jetzt
  getrennte Felder für **RSS vs. Reddit** (`rss_sentiment`, `reddit_sentiment`,
  `rss_sources`, `reddit_sources`, `source_quality`) — das Gehirn kann
  quellspezifisches Rauschen lernen.
- **Brain-Seite (Prio 6 / Bug 1.3):** der Brain-Feature-Vektor trägt jetzt die
  gehandelte **Richtung (`is_yes`) + Edge** (`build_brain_features`,
  `BRAIN_FEATURE_DIM = 17`, getrennt vom Predictor) — YES/NO-Setups werden nicht
  mehr vermischt.
- **brain_weight (Bug 1.2):** das konfigurierte `brain_weight` wirkt jetzt wirklich
  in der Confidence (vorher hart `0.3`).
- **Atomare Writes (Prio 8):** `state.json` und `config.json` werden über Temp-Datei
  + `os.replace` geschrieben (kein halb-geschriebener Zustand mehr).
- **SQLite robuster (Prio 9):** `PRAGMA journal_mode=WAL` + `busy_timeout=30000`
  für parallelen Bot-/Server-/Settle-Zugriff.

## Was sonst fertig & getestet ist

### Kern-Pipeline (end-to-end lauffähig, mit echten Daten)
- **Scan → Research (async, RSS/Reddit getrennt) → Prediction (XGBoost + Claude +
  Brain) → BrainManager (Haiku-Veto) → Risk (Kelly + Caps + Veto) → Brain (Lernen)
  + LLM-Postmortem.**
- **Dual-Modus:** Paper (Default, kein echtes Geld — **echte Ausgänge**, kein Würfel)
  und Live (Polymarket via `py-clob-client`) hinter **Bestätigung pro Trade**;
  Secrets nur via `.env`.
- **Wichtig:** läuft **nicht** mehr ohne echte Datenquellen (siehe Hard-Fail oben).

### Gehirn (Stufe 5)
- **numpy-MLP** (Fallback) **und** **PyTorch-MLP**, gleiches `.npz`-Format (Gewichte
  zwischen den Backends austauschbar). `make_brain()` wählt PyTorch, sobald `torch`
  installiert ist.
- Lernt aus **Wins + Losses** (jetzt seiten-/edge-bewusst), Gewichte persistieren,
  **modusübergreifend Paper→Live**.

### Backtest & Settlement
- **Backtest-Modus** (`tradebot/backtest.py`, CLI `backtest`): Monte-Carlo über
  **synthetische** Märkte — ein **separates Analyse-Werkzeug**, kein Handels-Pfad.
  Zeigt: Edge kommt aus **Markt-Ineffizienz** (effizient ≈ kein Gewinn trotz hoher
  Trefferquote; ineffizient klar profitabel).
- **Settlement-Polling** (CLI `settle [--loop --interval]`): pollt offene Trades über
  das neue `ResolutionStatus`-Enum, settled YES/NO/CANCELED, loggt ERROR/AMBIGUOUS
  und lässt solche Trades offen; schreibt Erfahrung + Lessons, trainiert neu.

### Dashboard (GitHub Pages, Fokus jetzt Localhost)
- `docs/` statische UI (HTML/CSS/JS, kein Build, keine CDNs) liest
  `docs/dashboard/state.json` (vom Bot bei jedem `run`/`export` **atomar** geschrieben).
- **Live** unter **https://cssk68-alt.github.io/Tradebot/**. GitHub Pages bleibt im
  Setup, der primäre Entwicklungs-/Testfokus liegt aber auf der **lokalen** Umgebung.

### Lokaler Server + Einstellungs-UI
- **`Start.bat`** (Windows): Doppelklick-Starter (findet `python`/`py`, installiert
  Abhängigkeiten, startet Server, öffnet Browser).
- **`tradebot/server.py`**: lokaler HTTP-Server **nur mit der Standardbibliothek**.
  Serviert `docs/` und bietet `GET/POST /api/config` (jetzt **atomarer** Write) +
  `GET /api/state`. Start via `python -m tradebot.cli serve`.
- **`docs/settings.html` + `settings.js`**: Slider + Erklärungen für die Strategie-
  Parameter; „Speichern" schreibt nach `data/config.json`.
- **`tradebot/config.py`**: `get_settings()` legt `data/config.json` über die
  `.env`-Defaults.

### Scalping / Kurz-Horizont
- **Kein simulierter Ausgang.** Scalp-Exit (`close`) realisiert PnL aus dem **echten
  aktuellen Marktpreis netto nach Spread**; Hold-to-Event (`settle`) nutzt die
  **echte** Gamma-Auflösung.
- Default `STRATEGY=scalp`; Schließen per Take-Profit / Stop-Loss / max. Haltedauer.
- **Spread-Schutz:** Eintrittsfilter (`predict.py`, Scalp nur wenn `Take-Profit −
  Spread ≥ MIN_NET_PROFIT`) **und** Spread-Abzug in der PnL-Formel (`paper.py`).
- **Knöpfe** (`.env` + UI): `STRATEGY`, `MAX_HOLD_SECONDS`, `TAKE_PROFIT`,
  `STOP_LOSS`, `MIN_NET_PROFIT`, `MIN_SPREAD_COST`.
- **DB:** Trades mit `kind` (scalp/resolve) + `exit_price`; Experiences mit `is_yes`;
  neue Tabelle `manager_decisions`; automatische Migration für bestehende DBs.

### Tests
- **56 grün, 1 übersprungen** (`pytest -q`). Übersprungen = Torch-Interop, weil
  `torch` in dieser Umgebung nicht installiert ist (optional; mit `torch` grün).
- Bestehende 29 Tests unverändert grün. **27 neue** für: Hard-Fail (Gamma wirft,
  Sentiment neutral, kein `_pseudo`/VADER), Live-Close-Fehler lässt Trade offen,
  Live-BUY-Fill-Prüfung, `ResolutionStatus`-Mapping + Settlement-Verhalten,
  Brain-Seiten-Feature, BrainManager Approve/Veto/Fallback + DB-Eintrag.
- Zusätzlich verifiziert: voller Offline-Zyklus (8 Märkte → 8 Signale → 8 Manager-
  Entscheidungen geloggt → 8 Trades → atomarer Dashboard-Write) und der Hard-Fail-
  Abbruch bei fehlenden Marktdaten.

## Offen / hier nicht testbar
- **Live-Polymarket-Ausführung:** braucht Wallet + API-Keys + Netz. Defensiv codiert
  (Methoden-/Response-Formen je `py-clob-client`-Version; `_parse_execution` ggf. an
  die installierte Version anpassen), `--dry-run` verifiziert.
- **Gamma-API:** in der Sandbox 403 (Egress) → Bot **stoppt** (Hard-Fail); lokal mit
  Netz echte Märkte. Siehe `probleme.md` Punkt 1.
- **Sub-5-Minuten auf echten Märkten:** hängt von liquiden, eng-spreadigen Märkten
  mit schneller Preisbewegung ab; nur lokal mit echten Preisen prüfbar.
- **PyTorch:** CPU-Index war früher geblockt; via Default-PyPI installierbar und
  end-to-end verifiziert. Details in `probleme.md`.

## Offen aus dem Assessment (P2, bewusst nicht in dieser Runde)
- XGBoost-Modell persistieren (statt bei jedem Start neu trainieren).
- Research-Concurrency begrenzen + Ergebnisse cachen.
- Echter **ausführbarer** Edge inkl. `best_ask`/`best_bid`, Slippage und Book-Depth
  (Bug 1.1 / 4.6).
- `httpx.Client` wiederverwenden; Snapshot-Retention.

## Setup beim Nutzer (lokal)
1. `git clone` / `git pull` des Repos.
2. **LLM-Agent ist Pflicht:** `LLM_PROVIDER` wählen und den passenden Key in `.env`
   eintragen — Default `deepseek` → `DEEPSEEK_API_KEY`, alternativ `anthropic` →
   `ANTHROPIC_API_KEY` (Vorlage: `.env.example`; `.env` ist gitignored). **Ohne Key
   startet der Bot nicht** (Hard-Fail, kein Auto-Approve, keine Fallback-Signale —
   VADER/Fixtures/Offline-Prior sind entfernt). Zusätzlich braucht es echten
   Netzzugang zu Gamma / Google News / Reddit.
3. **Doppelklick `Start.bat`** → installiert Abhängigkeiten, öffnet Dashboard unter
   `http://localhost:8080`, Einstellungen unter `/settings.html`.

## Nächste sinnvolle Schritte
- APIs lokal scharf schalten und einzeln testen (Anthropic, Gamma, RSS, Reddit),
  weil der Bot ohne echte Quellen bewusst stoppt.
- Live-Settlement gegen echte Gamma-Resolutions mit Keys end-to-end testen.
- P2-Punkte aus dem Assessment nachziehen (XGBoost-Persistenz, Slippage/Book-Depth).

## Befehle
```bash
pip install -e .
python -m tradebot.cli serve                 # lokales Dashboard + Einstellungen
python -m tradebot.cli scan                  # braucht echtes Gamma (sonst Hard-Fail)
python -m tradebot.cli scalp --minutes 30 --interval 60   # Kurz-Trades, echte Preise
python -m tradebot.cli reset --yes           # alte Historie loeschen, sauber starten
python -m tradebot.cli run --strategy scalp  # 1 Zyklus (oeffnet/schliesst per Preis)
python -m tradebot.cli run --strategy resolve --loop --iterations 12
python -m tradebot.cli backtest --n 500 --signal 0.6   # synthetisch, separates Tool
python -m tradebot.cli settle --mode live --loop --interval 300
pytest -q
```
