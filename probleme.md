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
