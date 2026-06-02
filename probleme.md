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

## 5. Neuronales Netz: numpy statt PyTorch
- **Hinweis:** Das „Gehirn" (`brain/network.py`) ist ein eigenständiges numpy-MLP
  (ReLU + Sigmoid, BCE, Backprop) — bewusst leichtgewichtig und immer lauffähig,
  ohne den großen `torch`-Download. Gleiche Schnittstelle; PyTorch kann später
  dahinter getauscht werden. Funktional erfüllt: lernt aus Wins **und** Losses,
  Gewichte persistieren (`data/brain.npz`) und gelten paper- wie live-übergreifend.
