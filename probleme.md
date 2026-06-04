# Bekannte Probleme & offene Punkte

Dinge, die in dieser Sandbox nicht vollständig lösbar waren (meist Netzwerk-/
Umgebungsgrenzen), mit Wissensstand und Lösungsweg für deinen Rechner.

## 1. Gamma-API liefert 403 in dieser Cloud-Sandbox
- **Symptom:** `GET https://gamma-api.polymarket.com/markets` → `403 Forbidden`.
- **Versucht:** Standard-User-Agent, dann Browser-User-Agent + `Accept`/
  `Accept-Language`-Header. Beide weiterhin 403.
- **Diagnose:** Kein Code-Bug. Die Antwort kommt sofort (kein Timeout) → der
  Egress dieser Sandbox/der Proxy blockt Polymarket. Öffentliche Gamma-Endpunkte
  brauchen **keinen** Key; lokal/aus einer netzoffenen Umgebung funktionieren sie.
- **Auffangnetz (aktiv):** `data/gamma.py` fällt bei jedem Fehler auf
  `data/fixtures.py` (8 Beispielmärkte) zurück — die komplette Pipeline läuft
  dadurch trotzdem end-to-end.
- **Auf deinem Rechner prüfen:**
  `python -c "import httpx;print(httpx.get('https://gamma-api.polymarket.com/markets',params={'limit':1}).status_code)"`
  → erwartet `200`. Dann liefert `python -m tradebot.cli scan` echte Märkte.

## 2. `feedparser` baut nicht (Abhängigkeit `sgmllib3k`)
- **Symptom:** `pip install` bricht ab: „Failed building wheel for sgmllib3k".
- **Diagnose:** `sgmllib3k` ist ein altes sdist-Paket ohne passendes Wheel und
  scheitert hier am Wheel-Build.
- **Lösung (aktiv):** `feedparser` ist aus den Pflicht-Abhängigkeiten entfernt und
  optional (`pip install -e ".[news]"`). `data/rss.py` parst RSS sonst mit der
  Standardbibliothek (`xml.etree`). Kein Funktionsverlust.

## 3. Live-Polymarket-Pfad hier nicht real testbar
- **Grund:** Kein Wallet/keine Keys + geblockter Egress.
- **Status:** `exchange/polymarket.py` ist defensiv geschrieben (Methoden-Namen von
  `py-clob-client` variieren je Version; `create_or_derive_api_creds` *und*
  `create_or_derive_api_key` werden probiert). Der `--dry-run`-Pfad baut die Order
  und loggt sie, ohne zu senden.
- **Empfehlung:** Auf deinem Rechner zuerst
  `python scripts/derive_api_creds.py` und `--mode live --dry-run` testen, bevor
  echte Orders gesendet werden. Ggf. exakte Aufrufe an deine installierte
  `py-clob-client`-Version anpassen.

## 4. Research-Quellen (Reddit / Google News) in der Sandbox vermutlich geblockt
- **Folge:** `data/sentiment.py` nutzt dann den deterministischen Offline-Prior,
  sodass trotzdem Signale/Trades entstehen. Lokal liefern die Quellen echte Texte
  (VADER- oder Claude-Sentiment).

## 5. PyTorch-Backend — implementiert und verifiziert (Install war zäh)
- **Stand:** Das „Gehirn" hat **zwei Backends** mit identischer Schnittstelle und
  **gleichem `.npz`-Format** (`brain/network.py`): das numpy-MLP (Fallback) und ein
  **PyTorch-MLP** (`TorchBrain`). `make_brain()` wählt automatisch PyTorch, sobald
  `torch` installiert ist. Beide lernen aus Wins **und** Losses; Gewichte
  persistieren und gelten paper- wie live-übergreifend.
- **Install-Stolperstein:** `download.pytorch.org/whl/cpu` ist in der Sandbox
  geblockt („No matching distribution found"). Über den **Default-PyPI-Index**
  (`pip install torch`) hat es schließlich geklappt (großer Download, dauert).
- **Verifiziert:** torch 2.12 installiert, `make_brain()` liefert `TorchBrain`, der
  Paper-Loop läuft damit fehlerfrei, und `tests/test_brain_torch.py` (Lernen +
  numpy↔torch-Interop) ist grün. Auf deinem Rechner genügt `pip install torch`.

## 6. Scalping / „unter 5 Minuten" — gebaut, aber live nicht voll verifizierbar
- **Was gebaut wurde:** Simulierte Trade-Ausgänge wurden komplett entfernt
  (`PaperExchange._simulate_yes` ist weg). Paper lernt jetzt aus echten Daten —
  Scalp-Exit zum **echten aktuellen Preis netto nach Spread** (`close`) bzw.
  Hold-to-Event über die **echte Gamma-Auflösung** (`settle`). Default
  `STRATEGY=scalp`, Schließen per Take-Profit / Stop-Loss / max. Haltedauer.
- **Spread-Schutz:** Eintrittsfilter in `predict.py`
  (`spread > take_profit − min_net_profit` → kein Trade) **und** Spread-Abzug in der
  PnL-Formel. Unit-getestet (`test_scalp_close_profit_net_of_spread`,
  `test_scalp_flat_price_loses_the_spread`, NO-Seite).
- **Grenze hier (Sandbox):** Gamma ist mit 403 geblockt → keine echten Live-Preise,
  also kein echter Minuten-Loop mit bewegten Preisen testbar. Offline bewegen sich
  Fixtures-Preise nicht, daher schließen Scalps dort nur per Zeitlimit mit kleinem
  Spread-Verlust (genau das ehrliche „flat → −Spread"-Verhalten).
- **Realitäts-Check, den DU lokal machen musst:** Polymarket ist primär ein
  Event-Markt; echte Sub-5-Minuten-Bewegung + enge Spreads gibt es v. a. in sehr
  liquiden Märkten (z. B. stündliche Krypto-Märkte). Ob „viele kleine sichere
  Trades" dort netto nach Spread profitabel sind, zeigt erst der Paper-Lauf mit
  echten Preisen — **kostet kein Geld**, genau dafür ist es gebaut. Erst wenn das
  Gehirn dort echten Vorsprung lernt, lohnt der Live-Schritt.
- **Live-Scalp-Verkauf** (`PolymarketExchange.close`): postet eine SELL-Order
  (defensiv, `dry_run` loggt nur). Ohne Keys/Netz hier nicht real gesendet.
- **Behoben — Trade bleibt nie mehr still „offen", wenn der Markt aus
  `list_markets()` verschwindet** (z. B. UFC-Kampf vorbei → Settling, oder Markt
  unter den Liquiditätsfilter gefallen). Früher: `manage_open` machte bei fehlendem
  Bulk-Preis ein stilles `continue` → Trade ewig offen, kein Log. Jetzt holt sich
  `Orchestrator._close_missing` aktiv per **Einzel-Direktabruf**, was zum Schließen
  nötig ist: (1) echte Resolution über `gamma.get_resolution` → real setteln; (2)
  noch offener Markt → `gamma.fetch_market` (neuer Einzel-Preisabruf `/markets/{id}`)
  → normaler Scalp-Trigger (max_hold garantiert Schließen); (3) erst wenn weder
  Resolution noch Preis verfügbar sind, lautes Error-Log statt stillem Schlucken.
  Gilt in Paper UND Live sowie im Wind-down. Tests: `tests/test_close_missing.py`.
- **Maker-First jetzt ehrlich in Paper modelliert** (vorher: Paper füllte immer zum
  Referenzpreis und protokollierte den Stil nur — der Maker-Nutzen war unsichtbar).
  Eine Maker-Order wird in Paper **nicht** auf Verdacht zum besseren Preis gefüllt
  (das wäre zu optimistisch: ein ruhendes Gebot füllt gerade dann, wenn der Preis
  kippt → adverse selection). Stattdessen ruht sie als `pending_maker` zum Limit, und
  `Orchestrator.resolve_pending_makers` bestätigt den Fill **aus dem echten Preispfad**
  (Snapshots + aktueller Live-Preis): erreicht der Preis das Gebot im Fenster → Fill
  zum besseren Limit (Scalp-Uhr startet beim Fill); sonst **verpasst → Taker** zum
  aktuellen Preis (echter Live-Fallback); stale/Markt weg → storniert (`canceled`,
  nicht ins Brain). Kein erfundener Vorteil — exakt die „nur echte Daten"-Linie wie
  Counterfactuals. Grenze (ehrlich): Snapshot-Takt ~1×/Zyklus ist grob, daher eher
  konservativ; für feinere Auflösung Intervall senken. Live unverändert (echtes CLOB
  bestätigt den Fill in `place_order`). Tests: `tests/test_maker_fill.py`.
- **Behoben — Billig-Markt-Scalp verlor ein Vielfaches des Einsatzes** (realer Fall:
  „Elon 260-279 tweets", Entry 0.0015, Einsatz $2,52 → **−$16,80**). Ursache: der
  Spread-Floor war ein flacher Absolutwert `min_spread_cost=0.01`, angewandt auf einen
  Markt unter 1 Cent. Weil `size = Einsatz/Preis` bei winzigem Preis explodiert
  (1679 Anteile), wurde `size × 0.01` zum Vielfachen des Einsatzes. Drei Trades:
  $9 Einsatz → −$91 PnL. Dreifach-Fix:
  **(A)** Verlust-Deckel `pnl = max(pnl, -size*entry)` — ein Long kann nie mehr als den
  Einsatz verlieren (in beiden `close()` + `settle_scalp_path`).
  **(B)** Spread-Floor = `min(min_spread_cost, get_tick_size(entry))` — ein Tick ist das
  realistische Minimum; Normalbereich unverändert (0,01), Extreme 0,001.
  **(C)** Einstiegs-Guard in `predict.py`: kein Scalp wenn `price ≤ stop_loss` (Stop läge
  unter 0, unerreichbar) oder `price + take_profit ≥ 1` — solche Longshots auch wegen der
  Size-Explosion meiden. Hold-to-event (resolve) ist davon nicht betroffen.
  Wirkung auf die 3 realen Trades: −$91,22 → −$8,18 (und C hätte alle drei verhindert).
  Gleicher Fix in der Counterfactual-Replay → das Gehirn lernt keine Phantom-Verluste
  mehr. Tests: `test_paper_exchange.py`, `test_counterfactual.py`, `test_predict_tick_guard.py`.
