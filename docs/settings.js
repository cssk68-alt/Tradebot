/* Settings page logic — reads/writes from local server (/api/config) */

const SETTINGS = [
  {
    key: "aggressiveness",
    label: "Aggressivität (Risk-Adjuster)",
    unit: "%",
    min: 0, max: 100, step: 5,
    defaultStored: 0.0,
    isPct: true,
    desc: [
      "Ein einziger Regler für die Risikobereitschaft. Er lockert zur Laufzeit die Filter (Brain-Veto, Mindestkonfidenz, Mindest-Edge) und sendet dem BrainManager-Agenten eine Anweisung, mutiger zu sein — OHNE die mathematischen Formeln (Kelly, Brain, Edge) zu verändern.",
      "0% = konservativ: es gelten exakt die unten eingestellten Schwellenwerte. 100% = mutig: das Brain vetot praktisch nicht mehr über seinen Score, die Konfidenz-Hürde fällt auf 50% und die Edge-Hürde auf einen dünnen Mindestwert.",
      "Sicherheitsanker bleiben IMMER aktiv: ein positiver Edge ist Pflicht, der Einsatz pro Trade wird mit steigender Aggressivität KLEINER (mehr, aber kleinere Trades), und der Agent vetot weiter bei echten logischen Widersprüchen.",
      "Tipp: im Paper-Modus hochdrehen, damit genug Trades zustande kommen und das Brain die nötigen ≥8 Ergebnisse zum Lernen bekommt — danach wieder senken, sobald das Brain trainiert ist."
    ],
    example: "0% → nur Trades über allen Standard-Schwellen. 70% (aggressiv) → ein Setup mit dünnem Sentiment und niedrigem Brain-Score wird approved, solange Sentiment der Richtung nicht klar widerspricht — aber mit halbiertem Einsatz."
  },
  {
    key: "bankroll",
    label: "Startkapital (Bankroll)",
    unit: " USD",
    min: 100, max: 10000, step: 50,
    defaultStored: 1000,
    isPct: false,
    desc: [
      "Das Kapital, mit dem der Bot startet — virtuell im Paper-Modus, echtes Geld im Live-Modus.",
      "Alle anderen Prozentsätze (Kelly, Max-Trade, Max-Exposure) berechnen sich relativ zu diesem Betrag.",
      "Für den Live-Modus sollte dieser Wert exakt deinem tatsächlich verfügbaren Kapital auf Polymarket entsprechen."
    ],
    example: "Bei 1.000 USD Bankroll und 5% Max-Trade sind maximal 50 USD pro einzelnem Trade möglich — egal wie gut das Signal ist."
  },
  {
    key: "kelly_fraction",
    label: "Kelly-Bruchteil",
    unit: "",
    min: 0.05, max: 1.0, step: 0.05,
    defaultStored: 0.25,
    isPct: false,
    desc: [
      "Die Kelly-Formel berechnet mathematisch die optimale Einsatzgröße, basierend auf deinem Vorteil (Edge) und den Gewinnchancen.",
      "Ein Wert von 1.0 ist 'volles Kelly' — theoretisch wachstumsmaximal, in der Praxis aber zu riskant wegen starker Schwankungen.",
      "Deshalb nutzt du einen Bruchteil: 0.25 bedeutet, du setzt nur 25% dessen ein, was Kelly empfehlen würde — konservativer und nachhaltiger."
    ],
    example: "Edge 10%, Gewinnchancen 1:1 → Kelly empfiehlt 10% der Bankroll → mit Faktor 0.25 werden daraus 2.5% (= 25 USD bei 1.000 USD Bankroll)."
  },
  {
    key: "max_trade_pct",
    label: "Maximaler Einsatz pro Trade",
    unit: "%",
    min: 0.5, max: 20, step: 0.5,
    defaultStored: 0.05,
    isPct: true,
    desc: [
      "Unabhängig davon was Kelly berechnet, wird nie mehr als dieser Prozentsatz der Bankroll in einem einzigen Trade riskiert.",
      "Das ist ein absoluter Sicherheitsanker gegen Berechnungsfehler oder außergewöhnlich riskante Marktsituationen.",
      "Je kleiner dieser Wert, desto mehr aufeinanderfolgende Verluste kannst du verkraften ohne in ernste Schwierigkeiten zu kommen."
    ],
    example: "5% Max-Trade bei 1.000 USD Bankroll: egal wie stark das Signal ist, werden maximal 50 USD pro Trade eingesetzt."
  },
  {
    key: "max_exposure_pct",
    label: "Maximale Gesamtposition",
    unit: "%",
    min: 10, max: 100, step: 5,
    defaultStored: 0.5,
    isPct: true,
    desc: [
      "Die Summe aller aktuell offenen Positionen darf diesen Anteil der Bankroll nicht überschreiten.",
      "So bleibt immer genug freies Kapital übrig, um auf neue Chancen zu reagieren, anstatt alles in offenen Trades gebunden zu haben.",
      "Ein zu hoher Wert macht dich anfällig, wenn mehrere Märkte gleichzeitig gegen dich laufen."
    ],
    example: "50% bei 1.000 USD: maximal 500 USD in offenen Positionen — die anderen 500 sind jederzeit frei verfügbar."
  },
  {
    key: "min_liquidity",
    label: "Mindest-Liquidität",
    unit: " USDC",
    min: 100, max: 5000, step: 100,
    defaultStored: 500,
    isPct: false,
    desc: [
      "Märkte mit wenig Liquidität haben große Spreads zwischen Kauf- und Verkaufspreis, was jeden Trade sofort teurer macht.",
      "Der Bot ignoriert Märkte unter diesem Grenzwert, um schlechte Ausführungspreise (sogenannte 'schlechte Fills') zu vermeiden.",
      "Höhere Werte reduzieren die Anzahl der Kandidaten, verbessern aber die Ausführungsqualität deutlich."
    ],
    example: "500 USDC Mindestliquidität: Ein Markt mit 800 USDC wird analysiert. Ein Markt mit nur 200 USDC wird übersprungen."
  },
  {
    key: "min_volume_24h",
    label: "Mindest-Volumen (24h)",
    unit: " USDC",
    min: 100, max: 10000, step: 100,
    defaultStored: 1000,
    isPct: false,
    desc: [
      "Das Handelsvolumen der letzten 24 Stunden zeigt, wie aktiv ein Markt ist und ob der Preis die echte Marktmeinung widerspiegelt.",
      "Märkte mit wenig Volumen haben oft veraltete oder verzerrte Preise, die keine verlässlichen Signale für den Bot liefern.",
      "Der Bot benötigt aktive Märkte, damit Edge-Berechnungen aussagekräftig sind und Preisbewegungen zuverlässig erkannt werden."
    ],
    example: "1.000 USDC Mindestvolumen: Ein Markt mit 2.500 USDC Tagesvolumen wird analysiert. Einer mit 300 USDC wird ignoriert."
  },
  {
    key: "max_spread",
    label: "Maximaler Spread (Markt-Filter)",
    unit: "%",
    min: 0.5, max: 8, step: 0.5,
    defaultStored: 0.03,
    isPct: true,
    desc: [
      "Der Spread (Abstand zwischen bestem Kauf- und Verkaufspreis) ist auf Polymarket der dominante Kostenfaktor — er wird beim Ein- und Ausstieg bezahlt. Dieser Regler ersetzt die festen USDC-Schwellen als primärer Qualitätsfilter.",
      "Märkte, deren Spread über diesem Wert liegt, werden gar nicht erst betrachtet — die Round-Trip-Kosten würden jeden kleinen Scalp-Gewinn auffressen. Ist das Orderbuch (Bid/Ask) noch nicht veröffentlicht, greift ersatzweise die Mindest-Liquidität.",
      "Die Orderbuch-TIEFE gegen die geplante Ordergröße wird zusätzlich beim Sizing geprüft (max. 10% der sichtbaren Liquidität pro Order)."
    ],
    example: "3% Max-Spread: Ein Markt mit 1¢ Spread wird gehandelt. Einer mit 7¢ Spread (z.B. dünner Markt) wird übersprungen, weil 7¢ ein 2%-Ziel unmöglich machen."
  },
  {
    key: "edge_threshold",
    label: "Mindest-Edge",
    unit: "%",
    min: 1, max: 30, step: 0.5,
    defaultStored: 0.05,
    isPct: true,
    desc: [
      "Edge ist die Differenz zwischen der vom Bot geschätzten wahren Wahrscheinlichkeit und dem aktuellen Marktpreis.",
      "Nur wenn dieser Unterschied groß genug ist, lohnt sich der Trade nach Transaktionskosten und Modell-Unsicherheit.",
      "Je höher der Schwellenwert, desto seltener werden Trades ausgelöst — aber die ausgelösten haben einen größeren erwarteten Vorteil."
    ],
    example: "5% Mindest-Edge: Marktpreis 60%, Bot-Schätzung 67% → Edge 7% → Trade wird geprüft. Bot-Schätzung 63% → Edge 3% → kein Trade."
  },
  {
    key: "confidence_threshold",
    label: "Mindestkonfidenz",
    unit: "%",
    min: 50, max: 95, step: 1,
    defaultStored: 0.6,
    isPct: true,
    desc: [
      "Neben dem Edge muss das Modell auch eine Mindestkonfidenz in seine Vorhersage haben — also wie sicher es sich ist.",
      "Hoher Edge bei gleichzeitig niedriger Konfidenz deutet auf ein unsicheres Signal hin und wird blockiert.",
      "Dieser Schwellenwert filtert Situationen heraus, in denen XGBoost, Claude und das Brain sich stark widersprechen."
    ],
    example: "60% Mindestkonfidenz: Modellkonfidenz 72% → Trade kann ausgelöst werden. Konfidenz 51% → Trade wird blockiert, egal wie hoch der Edge ist."
  },
  {
    key: "brain_weight",
    label: "Brain-Gewichtung",
    unit: "",
    min: 0, max: 1.0, step: 0.05,
    defaultStored: 0.3,
    isPct: false,
    desc: [
      "Das neuronale Netz (Brain) lernt aus jedem vergangenen Trade und bewertet neue Setups mit einem Score zwischen 0 und 1.",
      "Dieser Wert bestimmt, wie stark der Brain-Score die finale Entscheidung beeinflusst — im Vergleich zu XGBoost und Claude.",
      "0.0 ignoriert das Brain komplett (nur Modelle entscheiden), höhere Werte geben der Erfahrung aus vergangenen Trades mehr Gewicht."
    ],
    example: "BRAIN_WEIGHT=0.3: Finale Wahrscheinlichkeit = 70% Modell + 30% Brain. Je mehr Trades gesammelt, desto wertvoller wird der Brain-Score."
  },
  {
    key: "brain_veto_threshold",
    label: "Brain-Veto-Schwellenwert",
    unit: "",
    min: 0, max: 0.6, step: 0.05,
    defaultStored: 0.35,
    isPct: false,
    desc: [
      "Wenn der Brain-Score unter diesen Wert fällt, blockiert das Brain den Trade komplett — unabhängig von Edge und Konfidenz.",
      "Das verhindert, dass der Bot Fehlermuster wiederholt, aus denen er bereits Verluste gemacht hat.",
      "Niedrigerer Wert = Brain ist nachsichtiger (vetot seltener). Höherer Wert = Brain ist strenger (vetot öfter)."
    ],
    example: "BRAIN_VETO_THRESHOLD=0.35: Brain-Score 0.28 → Trade wird geblockt. Brain-Score 0.42 → Trade läuft weiter durch den Risk-Filter."
  },
  {
    key: "max_slippage",
    label: "Maximaler Slippage",
    unit: "%",
    min: 0.5, max: 10, step: 0.25,
    defaultStored: 0.02,
    isPct: true,
    desc: [
      "Slippage ist die Differenz zwischen dem erwarteten Kaufpreis und dem tatsächlichen Preis, den du beim Fill erhältst.",
      "Der Bot prüft das Orderbuch vor jedem Trade — wenn der erwartete Slippage zu hoch ist, wird der Trade abgebrochen.",
      "Das schützt besonders in wenig liquiden Märkten davor, zu einem deutlich schlechteren Preis als geplant einzusteigen."
    ],
    example: "2% Max-Slippage: Erwartet 0.60, tatsächlicher Fill 0.625 → Slippage 4.2% → abgebrochen. Fill 0.610 → 1.7% → Trade ausgeführt."
  },
  {
    key: "min_days_to_resolution",
    label: "Mindest-Restlaufzeit",
    unit: " Tage",
    min: 0.5, max: 14, step: 0.5,
    defaultStored: 1.0,
    isPct: false,
    desc: [
      "Märkte, die sehr bald auflösen, bieten kaum noch Informationsvorteil — der Preis ist fast immer schon 'richtig'.",
      "Außerdem ist die Zeit zu kurz, um bei einer falschen Position noch reagieren zu können.",
      "Der Bot überspringt solche Kurzläufer systematisch und hält Kapital für Opportunities mit mehr Laufzeit frei."
    ],
    example: "1 Tag Mindestlaufzeit: Ein Markt, der in 12 Stunden endet, wird ignoriert. Einer mit 2 Tagen Restlaufzeit kann gehandelt werden."
  },
  {
    key: "max_days_to_resolution",
    label: "Maximale Restlaufzeit",
    unit: " Tage",
    min: 5, max: 180, step: 5,
    defaultStored: 30.0,
    isPct: false,
    desc: [
      "Sehr langläufige Märkte sind schwerer zu prognostizieren, weil sich in langer Zeit zu viel ändern kann.",
      "Außerdem ist das Kapital für lange Zeit gebunden, was die Jahresrendite reduziert.",
      "Kürzere Zeitfenster sind für den Bot attraktiver, weil Ereignisse besser vorhersagbar sind und Kapital schneller rotiert."
    ],
    example: "30 Tage Maximum: Ein Markt mit 90 Tagen Restlaufzeit wird übersprungen. Einer mit 25 Tagen Restlaufzeit wird analysiert."
  },
  {
    key: "take_profit",
    label: "Gewinnmitnahme (Scalp-Ziel)",
    unit: "%",
    min: 0.5, max: 10, step: 0.5,
    defaultStored: 0.02,
    isPct: true,
    desc: [
      "Beim Scalping schließt der Bot eine Position, sobald der Preis sich um diesen Betrag zu deinen Gunsten bewegt hat, und sichert so den kleinen Gewinn.",
      "Ein niedriger Wert bedeutet sehr häufige, winzige Gewinne — genau die 'viele kleine Trades'-Idee, aber der Spread muss kleiner sein als dieses Ziel, sonst lohnt es sich nicht.",
      "Höhere Werte bringen pro Trade mehr, dauern aber länger und gehen seltener auf."
    ],
    example: "2% Ziel: Einstieg bei 0.60, sobald der Preis 0.62 erreicht, wird verkauft. Ist der Spread 1%, bleibt nach Abzug ~1% Netto-Gewinn übrig."
  },
  {
    key: "stop_loss",
    label: "Verlustbegrenzung (Stop-Loss)",
    unit: "%",
    min: 0.5, max: 10, step: 0.5,
    defaultStored: 0.03,
    isPct: true,
    desc: [
      "Bewegt sich der Preis um diesen Betrag GEGEN dich, schließt der Bot die Position sofort und begrenzt so den Verlust dieses einen Trades.",
      "Das ist die wichtigste Sicherheit beim Scalping: ein einzelner Ausreißer soll nicht die vielen kleinen Gewinne auffressen.",
      "Ein enger Stop (kleiner Wert) verliert pro Fehltrade wenig, löst aber öfter aus; ein weiter Stop gibt dem Preis mehr Spielraum, riskiert aber mehr."
    ],
    example: "3% Stop: Einstieg 0.60, fällt der Preis auf 0.57, wird mit kleinem Verlust verkauft, bevor daraus ein großer Verlust wird."
  },
  {
    key: "min_net_profit",
    label: "Mindest-Netto-Gewinn nach Spread",
    unit: "%",
    min: 0.1, max: 3, step: 0.1,
    defaultStored: 0.005,
    isPct: true,
    desc: [
      "Dies ist der Spread-Schutz: ein Scalp wird nur eröffnet, wenn nach Abzug des Spreads mindestens dieser Gewinn übrig bleibt (Ziel minus Spread).",
      "Märkte, deren Kauf-/Verkaufsspanne das Gewinnziel auffressen würde, werden gar nicht erst gehandelt — genau die Gefahr, die du angesprochen hast.",
      "Höher = strenger (nur sehr enge, liquide Märkte), niedriger = mehr Trades, aber dünnere Margen."
    ],
    example: "0.5% Mindest-Netto bei 2% Ziel: Märkte mit Spread über 1.5% werden übersprungen, weil sonst nach Spread weniger als 0.5% übrig bliebe."
  },
  {
    key: "max_hold_seconds",
    label: "Maximale Haltedauer (Scalp)",
    unit: " Sek.",
    min: 30, max: 600, step: 15,
    defaultStored: 300,
    isPct: false,
    desc: [
      "Hat eine Position nach dieser Zeit weder das Gewinnziel noch den Stop-Loss erreicht, schließt der Bot sie trotzdem zum aktuellen Preis.",
      "So bleibt Kapital nicht in lahmen Trades gebunden und rotiert schnell in neue Gelegenheiten — der Kern der 'unter 5 Minuten'-Strategie.",
      "Kürzere Haltedauer bedeutet mehr, schnellere Trades; längere gibt dem Preis mehr Zeit, dein Ziel zu erreichen."
    ],
    example: "300 Sekunden (5 Min): Tut sich nach 5 Minuten nichts, wird die Position glattgestellt und das Kapital ist wieder frei für den nächsten Trade."
  },
  {
    key: "max_daily_loss_pct",
    label: "Tagesverlust-Limit (Circuit-Breaker)",
    unit: "%",
    min: 0, max: 25, step: 1,
    defaultStored: 0.05,
    isPct: true,
    desc: [
      "Sicherung gegen schlechte Tage: Erreicht der realisierte Verlust eines Tages diesen Anteil der Bankroll, eröffnet der Bot KEINE neuen Trades mehr.",
      "Offene Positionen werden dabei NICHT abgebrochen — sie laufen geordnet zu Ende (kein Abandon). Erst danach steht der Lauf still.",
      "0% schaltet diese Sicherung aus. Der Zähler bezieht sich auf den heutigen Tag (UTC) und setzt sich um Mitternacht zurück."
    ],
    example: "5% bei 1.000 USD: Sind an einem Tag -50 USD realisiert, stoppt der Bot das Eröffnen neuer Trades und fährt die offenen kontrolliert herunter."
  },
  {
    key: "max_consecutive_losses",
    label: "Verlust-Streak-Limit (Circuit-Breaker)",
    unit: "",
    min: 0, max: 20, step: 1,
    defaultStored: 5,
    isPct: false,
    desc: [
      "Zweite Sicherung: Nach so vielen Verlust-Trades in Folge stoppt der Bot das Eröffnen neuer Positionen — ein Zeichen, dass das Marktregime gerade nicht zur Strategie passt.",
      "Ein einzelner Gewinn setzt den Zähler zurück. Stornierte/ungültige Märkte zählen weder als Verlust noch setzen sie zurück.",
      "0 schaltet diese Sicherung aus. Offene Trades werden auch hier geordnet beendet, nie abgebrochen."
    ],
    example: "5 in Folge: Verliert der Bot 5 Trades hintereinander, pausiert das Eröffnen. Gewinnt er zwischendrin einmal, läuft der Zähler wieder bei 0."
  },
  {
    key: "maker_min_edge",
    label: "Maker-First ab Edge (Kostenoptimierung)",
    unit: "%",
    min: 1, max: 15, step: 0.5,
    defaultStored: 0.03,
    isPct: true,
    desc: [
      "Maker-First spart Kosten: Statt sofort den Spread zu zahlen (Taker), legt der Bot bei ausreichend großem Edge eine passive Limit-Order einen Tick innerhalb des Preises und wartet kurz auf Ausführung (Polymarket-Maker-Gebühr = 0).",
      "Dieser Regler bestimmt, AB welchem Edge sich das Warten lohnt. Darunter wird sofort als Taker ausgeführt (Geschwindigkeit vor Ersparnis).",
      "Füllt die Maker-Order nicht innerhalb des Zeitfensters (Standard 60 s, nur Live relevant), schaltet der Bot automatisch auf eine Taker-Order um. Im Paper-Modus wird die gewählte Variante nur protokolliert."
    ],
    example: "3% Schwelle: Ein Trade mit 8% Edge wartet als Maker (spart den Spread). Ein Trade mit nur 2% Edge greift sofort als Taker zu."
  }
];

// --- Presets: 1 frei wählbar ("frei") + 3 feste, empfohlene Setups. -----------
// Werte sind STORED-Werte (nicht Anzeige-Einheiten) und treffen die SETTINGS-Keys.
// `bankroll` fehlt absichtlich — ein Preset darf nie dein Kapital überschreiben.
//
// EVIDENZBASIERT (Opus-Web-Recherche, 2026-06-04). Kernbefunde:
//  - Fractional Kelly: Viertel- bis Halb-Kelly; Full-Kelly überwettet bei
//    Schätzfehlern massiv (Thorp/Ziemba/MacLean).
//  - Einsatz pro Trade 1–2 % der Bankroll; Scalping eher Richtung 0,5 %.
//  - Auf Polymarket ist der SPREAD (1–8¢) der dominante Kostenfaktor → die Edge
//    muss Spread + Fees + Slippage schlagen, bevor ein Trade +EV ist.
//  - Cold-Start (Bandit/RL): explorieren mit GEDECKELTEM Downside → der Aggressiv-
//    Modus lockert die GATES (Edge/Konfidenz/Veto = mehr Trades), hält den EINSATZ
//    aber am KLEINSTEN (kleinster Kelly + kleinster max_trade), um Lern-Daten zu
//    „kaufen" ohne große Verluste.
const PRESETS = {
  vorsichtig: {
    aggressiveness: 0.0, kelly_fraction: 0.25, max_trade_pct: 0.01, max_exposure_pct: 0.10,
    min_liquidity: 3000, min_volume_24h: 5000, max_spread: 0.02, edge_threshold: 0.08,
    confidence_threshold: 0.65, brain_weight: 0.30, brain_veto_threshold: 0.40, max_slippage: 0.01,
    min_days_to_resolution: 1.0, max_days_to_resolution: 30, take_profit: 0.03, stop_loss: 0.045,
    min_net_profit: 0.015, max_hold_seconds: 300,
    max_daily_loss_pct: 0.03, max_consecutive_losses: 4, maker_min_edge: 0.02,
  },
  ausgewogen: {
    aggressiveness: 0.30, kelly_fraction: 0.40, max_trade_pct: 0.02, max_exposure_pct: 0.25,
    min_liquidity: 1500, min_volume_24h: 2500, max_spread: 0.03, edge_threshold: 0.05,
    confidence_threshold: 0.60, brain_weight: 0.30, brain_veto_threshold: 0.30, max_slippage: 0.02,
    min_days_to_resolution: 1.0, max_days_to_resolution: 30, take_profit: 0.04, stop_loss: 0.06,
    min_net_profit: 0.01, max_hold_seconds: 450,
    max_daily_loss_pct: 0.05, max_consecutive_losses: 5, maker_min_edge: 0.03,
  },
  aggressiv: {
    // Lockere GATES (viele Trades), aber KLEINSTER Einsatz (Exploration mit
    // gedeckeltem Downside) — bewusst temporär, bis das Brain ≥8 Ergebnisse hat.
    aggressiveness: 0.70, kelly_fraction: 0.15, max_trade_pct: 0.005, max_exposure_pct: 0.20,
    min_liquidity: 800, min_volume_24h: 1500, max_spread: 0.05, edge_threshold: 0.03,
    confidence_threshold: 0.55, brain_weight: 0.10, brain_veto_threshold: 0.10, max_slippage: 0.02,
    min_days_to_resolution: 0.5, max_days_to_resolution: 30, take_profit: 0.02, stop_loss: 0.03,
    min_net_profit: 0.005, max_hold_seconds: 600,
    max_daily_loss_pct: 0.08, max_consecutive_losses: 8, maker_min_edge: 0.06,
  },
};

const PRESET_BUTTONS = [
  { key: "frei", label: "① Frei", sub: "eigene Regler",
    title: "Die Regler bleiben wie eingestellt — du bestimmst jeden Wert selbst." },
  { key: "vorsichtig", label: "② Vorsichtig", sub: "Qualität vor Menge",
    title: "Viertel-Kelly, 1% pro Trade, hohe Edge-Hürde (8pp), strenge Liquidität, Brain-Veto aktiv. Wenige, aber hochwertige Trades — Kapitalerhalt." },
  { key: "ausgewogen", label: "③ Ausgewogen", sub: "Standard",
    title: "≈0,4-Kelly, 2% pro Trade, 5pp Edge. Mittlere Schwellen — der empfohlene Allround-Startpunkt." },
  { key: "aggressiv", label: "④ Lern-/Aggressiv", sub: "viele winzige Trades",
    title: "Lockere GATES (3pp Edge, niedriges Brain-Veto, Aggressivität 70%) für VIELE Trades — aber KLEINSTER Einsatz (0,15-Kelly, 0,5% pro Trade), damit die Cold-Start-Exploration keine großen Verluste baut. Bewusst temporär, bis das Brain ≥8 Ergebnisse hat." },
];

let activePreset = "frei";

function renderPresetBar() {
  const bar = document.getElementById("presetBar");
  if (!bar) return;
  bar.innerHTML =
    `<div class="preset-intro"><strong>Voreinstellungen.</strong> Wähle ein fertiges Setup ` +
    `oder stelle frei ein. Sobald du einen Regler bewegst, springt die Auswahl auf „Frei".</div>` +
    `<div class="preset-btns">` +
    PRESET_BUTTONS.map((b) =>
      `<button type="button" class="preset-btn${b.key === activePreset ? " active" : ""}" ` +
      `data-preset="${b.key}" title="${b.title}" onclick="onPreset('${b.key}')">` +
      `<span class="preset-name">${b.label}</span><span class="preset-sub">${b.sub}</span></button>`
    ).join("") +
    `</div>`;
}

function onPreset(name) {
  // Frei = nur Auswahl markieren; ein festes Preset schreibt seine Werte in alle
  // passenden Slider (Keys, die es nicht kennt — z.B. bankroll — bleiben unberührt).
  if (name !== "frei" && PRESETS[name]) {
    const p = PRESETS[name];
    for (const s of SETTINGS) {
      if (p[s.key] === undefined) continue;
      const slider = document.getElementById(`slider-${s.key}`);
      if (!slider) continue;
      const dispVal = toDisplay(p[s.key], s);
      slider.value = dispVal;
      document.getElementById(`val-${s.key}`).textContent = fmt(dispVal, s) + s.unit;
      slider.style.setProperty("--fill", fillPct(dispVal, s));
    }
  }
  setActivePreset(name);
}

function setActivePreset(name) {
  activePreset = name;
  document.querySelectorAll(".preset-btn").forEach((btn) =>
    btn.classList.toggle("active", btn.dataset.preset === name));
}

let currentConfig = {};

function fmt(val, s) {
  if (s.step < 1 && !s.isPct) return val.toFixed(1);
  if (s.isPct) return val.toFixed(1);
  return Math.round(val);
}

function toDisplay(stored, s) {
  return s.isPct ? Math.round(stored * 1000) / 10 : stored;
}

function toStored(display, s) {
  return s.isPct ? Math.round(display / 100 * 100000) / 100000 : display;
}

function fillPct(val, s) {
  return ((val - s.min) / (s.max - s.min) * 100).toFixed(1) + "%";
}

async function loadConfig() {
  try {
    const r = await fetch("/api/config", { signal: AbortSignal.timeout(3000) });
    if (!r.ok) throw new Error();
    currentConfig = await r.json();
    document.getElementById("serverWarning").style.display = "none";
  } catch {
    document.getElementById("serverWarning").style.display = "block";
    currentConfig = {};
  }
  const known = ["frei", ...Object.keys(PRESETS)];
  activePreset = known.includes(currentConfig.preset) ? currentConfig.preset : "frei";
  renderSettings();
  renderPresetBar();
}

function renderSettings() {
  const grid = document.getElementById("settingsGrid");
  grid.innerHTML = "";

  for (const s of SETTINGS) {
    const storedVal = currentConfig[s.key] !== undefined ? currentConfig[s.key] : s.defaultStored;
    const dispVal = toDisplay(storedVal, s);
    const fp = fillPct(dispVal, s);

    const card = document.createElement("div");
    card.className = "setting-card";
    card.innerHTML = `
      <div class="setting-header">
        <div class="setting-label">${s.label}</div>
        <div class="setting-value" id="val-${s.key}">${fmt(dispVal, s)}${s.unit}</div>
      </div>
      <div class="slider-row">
        <span class="slider-bound">${fmt(s.min, s)}${s.unit}</span>
        <input type="range" id="slider-${s.key}"
          min="${s.min}" max="${s.max}" step="${s.step}" value="${dispVal}"
          style="--fill:${fp}"
          oninput="onSlide('${s.key}', this.value)"
        />
        <span class="slider-bound">${fmt(s.max, s)}${s.unit}</span>
      </div>
      <div class="setting-desc">
        ${s.desc.map(p => `<p>${p}</p>`).join("")}
      </div>
      <div class="setting-example">
        <strong>Beispiel:</strong> ${s.example}
      </div>
    `;
    grid.appendChild(card);
  }
}

function onSlide(key, rawVal) {
  const s = SETTINGS.find(x => x.key === key);
  const dispVal = parseFloat(rawVal);
  document.getElementById(`val-${key}`).textContent = fmt(dispVal, s) + s.unit;
  const slider = document.getElementById(`slider-${key}`);
  slider.style.setProperty("--fill", fillPct(dispVal, s));
  // Any manual move means the config no longer matches a fixed preset -> "Frei".
  setActivePreset("frei");
}

async function saveConfig() {
  const config = {};
  for (const s of SETTINGS) {
    const slider = document.getElementById(`slider-${s.key}`);
    config[s.key] = toStored(parseFloat(slider.value), s);
  }
  config.preset = activePreset;  // remember the UI choice (slider values stay source of truth)

  const msg = document.getElementById("saveMsg");
  try {
    const r = await fetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(config),
    });
    if (!r.ok) throw new Error();
    msg.textContent = "✓ Gespeichert — wirkt beim nächsten Bot-Start.";
    msg.className = "save-msg ok";
  } catch {
    msg.textContent = "Fehler: Server nicht erreichbar. Läuft Start.bat?";
    msg.className = "save-msg err";
  }
  setTimeout(() => { msg.textContent = ""; msg.className = "save-msg"; }, 5000);
}

loadConfig();
