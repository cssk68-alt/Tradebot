# Aktueller Stand

Stand: 2026-06-04 · `main` (Refactor gemerged aus `claude/inspiring-keller-tRIBa`)

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
