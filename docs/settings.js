/* Settings page logic — reads/writes from local server (/api/config) */

const SETTINGS = [
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
    min: 1, max: 20, step: 0.5,
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
  }
];

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
  renderSettings();
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
}

async function saveConfig() {
  const config = {};
  for (const s of SETTINGS) {
    const slider = document.getElementById(`slider-${s.key}`);
    config[s.key] = toStored(parseFloat(slider.value), s);
  }

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
