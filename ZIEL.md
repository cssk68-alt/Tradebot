# Ziel des Projekts

## Vision

Ein **autonomer Multi-Agenten-Trading-Bot für Prediction Markets (Polymarket)**,
der eigenständig Märkte scannt, recherchiert, Wahrscheinlichkeiten kalibriert,
Positionen risikobewusst dimensioniert, Trades ausführt und **aus jedem Ergebnis
lernt** — und der über ein einfaches Web-Dashboard transparent macht, was er tut.

## Die 5-stufige Pipeline

1. **Scan** — aus hunderten aktiven Polymarket-Märkten nach Liquidität, Volumen
   und Restlaufzeit filtern; Auffälligkeiten (Preissprünge, weite Spreads) markieren.
2. **Research** — pro Kandidat parallel kostenlose Quellen (News-RSS, öffentliches
   Reddit) auswerten, Sentiment bestimmen, Narrativ vs. Marktpreis vergleichen.
3. **Prediction** — XGBoost + Claude (LLM) + Brain-Score zu einer kalibrierten
   „wahren" Wahrscheinlichkeit verbinden und den **Edge** (true_prob − Preis) berechnen.
4. **Risk & Execution** — Positionsgröße per fraktioniertem **Kelly** mit harten
   Caps und **Brain-Veto**; dann Order ausführen (Paper-Fill oder echte Order).
5. **Brain (Lernen)** — ein **neuronales Netz** lernt aus jedem aufgelösten Trade
   (Gewinn **und** Verlust), verhindert Wiederholungsfehler, erkennt erfolgreiche
   Muster und speist seinen Score in Stufe 3 + 4 zurück.

## Leitprinzipien

- **Sicher per Default:** Standardmodus ist **Paper** (Simulation, kein echtes
  Geld). **Live** handelt echtes USDC auf Polygon — **nur nach Bestätigung pro Trade**.
- **Modusübergreifendes Lernen:** Was der Bot im Paper-Modus lernt (Gewichte +
  Erfahrungs-DB), nimmt er **direkt mit in den Echtgeld-Modus**.
- **Edge kommt aus Markt-Ineffizienz:** Der Bot ist nur dann profitabel, wenn der
  Markt eine Information unterbewertet, die sein Signal aufdeckt. Das zeigt auch
  der Backtest: bei einem effizienten Markt bleibt trotz hoher Trefferquote kein
  Gewinn — Trefferquote allein entscheidet nicht.
- **Frei & schlank lauffähig:** nur kostenlose Datenquellen; schwere Abhängigkeiten
  (PyTorch, XGBoost, feedparser) sind optional mit leichten Fallbacks.
- **Transparenz:** statisches GitHub-Pages-Dashboard zeigt Bankroll/PnL, Equity-
  Kurve, Gehirn-Status, Trades und gelernte Lessons.

## Nicht-Ziele / ehrliche Grenzen

- Keine Gewinngarantie. Das Projekt liefert eine saubere, getestete Engine —
  ob daraus real Profit entsteht, hängt von echtem Edge, Gebühren und Disziplin ab.
- Kein Hochfrequenz-/On-Chain-MEV-Bot; Fokus liegt auf Research-getriebenen
  Wahrscheinlichkeits-Trades mit klarem Risikomanagement.
