# Aktueller Stand

Stand: 2026-06-02 · Branch `claude/confident-johnson-NL99E`

## Was fertig & getestet ist

### Kern-Pipeline (alle 5 Stufen, end-to-end lauffähig)
- **Scan → Research (async/parallel) → Prediction (XGBoost + Claude + Brain) →
  Risk (Kelly + Caps + Veto) → Brain (Lernen) + LLM-Postmortem.**
- **Dual-Modus:** Paper (Default, simuliert) und Live (Polymarket via
  `py-clob-client`) hinter **Bestätigung pro Trade**; Secrets nur via `.env`.
- Läuft komplett **offline** dank Fixtures-Fallback (Gamma ist in der Sandbox geblockt).

### Gehirn (Stufe 5)
- **numpy-MLP** (Fallback) **und** **PyTorch-MLP** (installiert & verifiziert),
  gleiches `.npz`-Format (Gewichte zwischen den Backends austauschbar).
- Lernt aus **Wins + Losses**, Gewichte persistieren, **modusübergreifend Paper→Live**.

### Neu in dieser Runde
- **Backtest-Modus** (`tradebot/backtest.py`, CLI `backtest`): Monte-Carlo über
  synthetische Märkte. Zeigt sauber: Edge kommt aus **Markt-Ineffizienz** — bei
  effizientem Markt bleibt **trotz ~69 % Trefferquote** kein Gewinn (ROI ≈ −0,35),
  bei ineffizientem Markt klar profitabel.
- **Live-Settlement-Polling** (CLI `settle [--loop --interval]`): pollt die
  Auflösung offener Trades, settled sie, schreibt Erfahrung + Lessons, trainiert
  Brain/Predictor neu. In Paper sofort, in Live über Gamma-Resolution.
- **PyTorch-Backend** fürs Gehirn (auto-aktiv, sobald `torch` installiert ist).

### Dashboard (GitHub Pages)
- `docs/` statische UI (HTML/CSS/JS, kein Build, keine CDNs) liest
  `docs/dashboard/state.json` (vom Bot bei jedem `run`/`export` geschrieben).
- Headless getestet: **Voll-, Leer- und Fetch-Fehler-Zustand** rendern fehlerfrei.
- Nach Pages-Aktivierung live unter **https://cssk68-alt.github.io/Tradebot/**
  (Settings → Pages → Branch `claude/confident-johnson-NL99E`, Ordner `/docs`).

### Tests
- **26 Tests** (Kelly, Scan-Filter, Paper-Fills, Edge-/Seiten-Logik,
  Modelle/Zeitzonen, Brain-Lernen + Persistenz, Backtest, Torch-Interop).
- **26 grün** (inkl. Torch-Interop; `torch` wurde via Default-PyPI installiert).
- Zusätzlich verifiziert: Paper-Loop (Brain **und** XGBoost trainieren ohne Fehler),
  Backtest-Sanity (effizient vs. ineffizient), `settle`-Befehl.

## Offen / hier nicht testbar
- **Live-Polymarket-Ausführung:** braucht Wallet + API-Keys + Netz. Defensiv
  codiert (Methoden-Namen je `py-clob-client`-Version), `--dry-run` verifiziert.
- **Gamma-API:** in der Sandbox 403 (Egress) → Fixtures; lokal echte Märkte.
- **PyTorch:** CPU-Index war geblockt, via Default-PyPI aber installiert und
  end-to-end verifiziert (Backend = `TorchBrain`). Details in `probleme.md`.

## Nächste sinnvolle Schritte
- Live-Settlement gegen echte Gamma-Resolutions mit Keys end-to-end testen.
- Echte historische Daten in den Backtest (statt synthetisch), sobald Gamma erreichbar.
- Backtest-Ergebnis zusätzlich im Dashboard anzeigen.

## Befehle
```bash
pip install -e .
python -m tradebot.cli scan
python -m tradebot.cli run --loop --iterations 12
python -m tradebot.cli backtest --n 500 --signal 0.6
python -m tradebot.cli settle --mode live --loop --interval 300
pytest -q
```
