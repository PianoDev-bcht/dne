/* Dashboard Assistant IA : JavaScript léger : carte Leaflet (aplats par académie),
   graphiques Chart.js, panneau « signaux » / académie, fenêtres secondaires.
   Données : endpoints internes uniquement. Les règles (signaux, seuils) sont
   calculées côté Python ; ce fichier ne fait que les afficher. */
(function () {
  "use strict";

  const URLS = window.DASHBOARD_URLS;
  const NNBSP = " ";
  const intFmt = new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 0 });
  const decFmt = new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 1 });
  const pctFmt = new Intl.NumberFormat("fr-FR", { minimumFractionDigits: 1, maximumFractionDigits: 1 });

  const fmtInt = (v) => (v == null ? "n.d." : intFmt.format(v));
  const fmtSigned = (v) => (v == null ? "" : (v > 0 ? "+" : "") + intFmt.format(v).replace("-", "−"));
  const fmtPct = (v) => {
    if (v == null) return "n.d.";
    const s = pctFmt.format(Math.abs(v) * 100) + NNBSP + "%";
    return v > 0 ? "+" + s : v < 0 ? "−" + s : s;
  };
  const fmtPct0 = (v) => (v == null ? "n.d." : (v > 0 ? "+" : v < 0 ? "−" : "") + intFmt.format(Math.abs(v) * 100) + NNBSP + "%");
  const fmtDec = (v) => (v == null ? "n.d." : v >= 100 ? intFmt.format(v) : decFmt.format(v));
  // Seuil « stable » unique, fourni par le serveur (metrics.STABLE_THRESHOLD) :
  // flèches, synthèse et classe neutre de la carte utilisent la même valeur.
  const STABLE = window.DASHBOARD_CONFIG.stable;
  const STABLE_PCT = Math.round(STABLE * 100);
  const trend = (v) => (v == null || Math.abs(v) < STABLE ? "flat" : v > 0 ? "up" : "down");
  const ARROWS = { up: "↗", down: "↘", flat: "→" };
  const escapeHtml = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const state = { scope: "national", mapFamily: "users", mapMode: "change", chart: null, chartData: null };
  let locations = [];
  let national = null;  // évolutions et intensité nationales, pour le contexte « France » du panneau

  // ---------------------------------------------------------------- couches de carte
  // Comptes | Messages | Intensité, chacun en Évolution | Volume. Pour l'intensité (un taux),
  // le bouton « Volume » s'appelle « Niveau ».
  const FAMILY = {
    users: { volume: "new_users", change: "new_users_change_pct", noun: "nouveaux comptes", title: "Nouveaux comptes" },
    messages: { volume: "messages", change: "messages_change_pct", noun: "messages", title: "Messages envoyés" },
    // Intensité : bornes fixes, lisibles et stables d'un jour à l'autre.
    intensity: { volume: "activity_per_100_accounts", change: "activity_change_pct", noun: "messages / 100 comptes",
      title: "Messages / 100 comptes", breaks: [100, 150, 200] },
  };
  const fam = () => FAMILY[state.mapFamily];
  const isIntensity = () => state.mapFamily === "intensity";
  const isSequential = () => state.mapMode === "volume";
  const currentMetric = () => fam()[state.mapMode];

  const SEQUENTIAL = ["#c4dbf8", "#6da7ec", "#256abf", "#0d366b"];  // jusqu’à 4 classes (classBreaks)
  // Divergente : brun-orangé (baisse) / gris neutre / bleu (hausse). Pas de vert/rouge.
  const DIVERGING = [
    { max: -0.25, color: "#a04a12", label: "< −25 %" },
    { max: -STABLE, color: "#f0b48f", label: `−25 à −${STABLE_PCT} %` },
    { max: STABLE, color: "#d9d8d4", label: `±${STABLE_PCT} %` },
    { max: 0.25, color: "#86b6ef", label: `+${STABLE_PCT} à +25 %` },
    { max: Infinity, color: "#1c5cab", label: "> +25 %" },
  ];
  const NO_DATA = "#ffffff";
  const DIMMED = "#f1f1ef";  // académies hors de la classe survolée dans la légende

  /** Bornes de classes de largeur égale et rondes (volumes et intensité).

      Pas = le plus petit multiple « rond » (1, 2, 2,5, 5 × 10^k) qui place au plus
      3 bornes entre le 1er et le 9e décile des académies : les valeurs extrêmes
      (ex. une académie très basse) n'élargissent pas les classes, elles tombent
      dans les classes ouvertes « < x » et « ≥ y ». */
  function classBreaks(values) {
    const v = values.filter((x) => x != null).sort((a, b) => a - b);
    if (v.length < 2) return [];
    const lo = v[Math.floor(v.length * 0.1)], hi = v[Math.floor(v.length * 0.9)];
    if (hi <= lo) return [];
    for (let pow = 10 ** Math.floor(Math.log10((hi - lo) / 10 || 1)); ; pow *= 10) {
      for (const m of [1, 2, 2.5, 5]) {
        const step = m * pow;
        const breaks = [];
        for (let b = Math.ceil(lo / step) * step; b <= hi; b += step) breaks.push(b);
        if (breaks.length && breaks.length <= 3) return breaks;
        if (!breaks.length) return [Math.round(((lo + hi) / 2) / step) * step];
      }
    }
  }

  /** Couleur de la classe i parmi n classes, réparties sur la palette séquentielle. */
  const seqColor = (i, n) => SEQUENTIAL[n <= 1 ? SEQUENTIAL.length - 1 : Math.round((i * (SEQUENTIAL.length - 1)) / (n - 1))];

  /** Classe de légende d'une académie (-1 si pas de donnée) : sert à la couleur et au survol de la légende. */
  function classOf(l, breaks) {
    const value = l[currentMetric()];
    if (value == null) return -1;
    if (!isSequential()) return DIVERGING.findIndex((b) => value < b.max);
    let i = 0;
    while (i < breaks.length && value >= breaks[i]) i++;
    return i;
  }

  function fillFor(l, breaks) {
    const i = classOf(l, breaks);
    if (i < 0) return NO_DATA;
    return isSequential() ? seqColor(i, breaks.length + 1) : DIVERGING[i].color;
  }

  function renderLegend(breaks) {
    const f = fam();
    const el = document.getElementById("legend");
    const sw = (fill, cls = "") => `<span class="sw ${cls}" style="${fill ? `background:${fill}` : ""}"></span>`;
    let items;
    if (!isSequential()) {
      items = DIVERGING.map((b, i) => `<span class="item" data-cls="${i}">${sw(b.color)}${b.label}</span>`);
      el.innerHTML = `<span class="title">Évolution vs 7 jours précédents</span>${items.join("")}`;
    } else {
      // Classes en intervalles semi-ouverts : « < 120 », « 120–150 », …, « ≥ 170 ».
      if (!locations.some((l) => l[currentMetric()] != null)) { el.innerHTML = ""; return; }
      const n = breaks.length + 1;
      items = Array.from({ length: n }, (_, i) => {
        const label = n === 1 ? "toutes"
          : i === 0 ? `< ${fmtInt(breaks[0])}`
          : i === n - 1 ? `≥ ${fmtInt(breaks[n - 2])}`
          : `${fmtInt(breaks[i - 1])}–${fmtInt(breaks[i])}`;
        return `<span class="item" data-cls="${i}">${sw(seqColor(i, n))}${label}</span>`;
      });
      el.innerHTML = `<span class="title">${f.title}, 7 derniers jours</span>${items.join("")}`;
    }
  }

  // ---------------------------------------------------------------- signaux
  const METRIC_NOUN = { new_users: "Comptes", messages: "Messages" };

  /** Valeur fléchée et colorée, avec les mêmes conventions que les KPI. */
  function arrowValue(pct) {
    const t = trend(pct);
    return `<span class="change ${t}"><span aria-hidden="true">${ARROWS[t]}</span> ${fmtPct0(pct)}</span>`;
  }

  /** Phrase complète d'un signal (encadré du panneau académie). */
  function signalSentence(s) {
    if (s.kind === "divergence") {
      return `comptes ${arrowValue(s.evolution_users)}, messages ${arrowValue(s.evolution_messages)} (sens opposés)`;
    }
    const noun = s.metric === "new_users" ? "nouveaux comptes" : "messages";
    return `${noun} ${arrowValue(s.evolution)} en 7 jours (moyenne nat. ${fmtPct0(s.national)})`;
  }

  // Un bloc par mesure ; le repère « France » n'apparaît qu'une fois, dans l'en-tête du bloc.
  const SIGNAL_BLOCKS = [
    { key: "new_users", title: "Nouveaux comptes", match: (s) => s.kind !== "divergence" && s.metric === "new_users" },
    { key: "messages", title: "Messages", match: (s) => s.kind !== "divergence" && s.metric === "messages" },
    { key: "divergence", title: "Comptes et messages en sens inverse", match: (s) => s.kind === "divergence" },
  ];

  function renderSignals(signals) {
    const box = document.getElementById("signals");
    if (!signals.length) {
      box.innerHTML = `<p class="signals-empty">Aucune académie ne se démarque cette semaine.</p>`;
      return;
    }
    const rows = signals.flatMap((g) => g.signals.map((s) => ({ id: g.id, name: g.name, s })));
    box.innerHTML = SIGNAL_BLOCKS.map((blk) => {
      const items = rows.filter((r) => blk.match(r.s)).sort((a, b) => b.s.severity - a.s.severity);
      if (!items.length) return "";
      const france = blk.key === "divergence" ? "" :
        `<span class="sig-france">Moyenne nat. : ${fmtPct0(items[0].s.national)}</span>`;
      return `<div class="sig-head"><span>${blk.title}</span>${france}</div><ul class="signals">` + items.map((r) => {
        const value = r.s.kind === "divergence"
          ? `<span class="sig-div">comptes ${arrowValue(r.s.evolution_users)} · messages ${arrowValue(r.s.evolution_messages)}</span>`
          : arrowValue(r.s.evolution);
        return `<li><button type="button" class="sig-row" data-id="${r.id}"><span class="sig-name">${escapeHtml(r.name)}</span>` +
          `${value}<span class="chev" aria-hidden="true">›</span></button></li>`;
      }).join("") + "</ul>";
    }).join("");
  }

  // ---------------------------------------------------------------- panneau académie
  function changeHtml(pct) {
    const t = trend(pct);
    return `<span class="change ${t}"><span aria-hidden="true">${pct == null ? "" : ARROWS[t]}</span> ${fmtPct(pct)}</span>`;
  }

  function renderLocationPanel(d) {
    const k = d.kpis;
    const nat = d.national || national || {};
    document.getElementById("panel").classList.remove("is-national");
    document.getElementById("panel-national").hidden = true;
    document.getElementById("panel-location").hidden = false;
    document.getElementById("panel-name").textContent = d.full_name;
    // Si l'académie est signalée, la raison s'affiche en une ligne (même format que la liste).
    const note = document.getElementById("location-signal-note");
    const signals = d.signals || [];
    note.hidden = !signals.length;
    note.innerHTML = signals.length ? `Se démarque : ${signals.map(signalSentence).join(" ; ")}` : "";
    const set = (key, html) => { document.querySelector(`#panel-location [data-stat="${key}"]`).innerHTML = html; };
    set("new_users", `${fmtInt(k.new_users)} ${changeHtml(k.new_users_change_pct)}`);
    set("new_users_context", `moyenne nat. ${fmtPct(nat.new_users_change_pct)}`);
    set("messages", `${fmtInt(k.messages)} ${changeHtml(k.messages_change_pct)}`);
    set("messages_context", `moyenne nat. ${fmtPct(nat.messages_change_pct)}`);
    set("cumulative_users", fmtInt(k.cumulative_users));
    set("activity_per_100_accounts", fmtDec(k.activity_per_100_accounts));
    set("activity_context", `moyenne nat. ${fmtDec(nat.activity_per_100_accounts)}`);
  }

  function showNationalPanel() {
    document.getElementById("panel").classList.add("is-national");
    document.getElementById("panel-national").hidden = false;
    document.getElementById("panel-location").hidden = true;
    document.getElementById("academy-select").value = "";
  }

  // ---------------------------------------------------------------- graphiques
  // Comptes et messages ont des ordres de grandeur très différents : plutôt qu'un
  // double axe (trompeur), chaque courbe est indexée sur sa propre moyenne (= 100).
  // Les valeurs brutes restent dans l'infobulle.
  const SERIES = [
    { key: "users", label: "Nouveaux comptes", color: "#2a78d6" },
    { key: "messages", label: "Messages envoyés", color: "#1baf7a" },
  ];

  // Une seule annotation : la rupture majeure du calendrier scolaire.
  const ANNOTATIONS = [{ from: "2026-07-04", to: "2026-08-31", label: "Vacances d'été" }];

  // Repère « Moyenne » sur la ligne 100 et bande des vacances d'été (plugin local, sans dépendance).
  const referencePlugin = {
    id: "reference",
    beforeDatasetsDraw(chart) {
      const { ctx, chartArea: a, scales: { x, y } } = chart;
      const labels = chart.data.labels;
      ctx.save();
      ANNOTATIONS.forEach((n) => {
        const i0 = labels.findIndex((d) => d >= n.from);
        const i1 = labels.findLastIndex((d) => d <= n.to);
        if (i0 < 0 || i1 < i0) return;
        const left = x.getPixelForValue(i0), right = x.getPixelForValue(i1);
        ctx.fillStyle = "rgba(111,110,105,.07)";
        ctx.fillRect(left, a.top, right - left, a.bottom - a.top);
        ctx.fillStyle = "#6f6e69";
        ctx.font = "11px system-ui, sans-serif";
        ctx.textAlign = "center";
        ctx.fillText(n.label, (left + right) / 2, a.top + 12);
      });
      const y100 = y.getPixelForValue(100);
      if (y100 >= a.top && y100 <= a.bottom) {
        ctx.strokeStyle = "#52514e";
        ctx.lineWidth = 1.25;
        ctx.setLineDash([]);
        ctx.beginPath();
        ctx.moveTo(a.left, y100);
        ctx.lineTo(a.right, y100);
        ctx.stroke();
        ctx.fillStyle = "#52514e";
        ctx.font = "600 11px system-ui, sans-serif";
        ctx.textAlign = "right";
        ctx.fillText("Moyenne", a.right - 4, y100 + 14);
      }
      ctx.restore();
    },
  };

  // Les fenêtres de 7 jours contenant des jours non publiés ne sont pas tracées :
  // le cumul reporté y donnerait un creux puis un pic de rattrapage artificiels.
  const observed = (p) => (p.has_gap ? null : p.value);

  function indexed(points) {
    const values = points.map(observed).filter((v) => v != null);
    const mean = values.reduce((a, b) => a + b, 0) / (values.length || 1);
    return points.map((p) => (observed(p) == null || mean <= 0 ? null : (100 * p.value) / mean));
  }

  function chartData(d) {
    const base = d.users || [];
    return {
      labels: base.map((p) => p.date),
      datasets: SERIES.map((s) => {
        const points = d[s.key] || [];
        return {
          label: s.label,
          data: indexed(points),
          raw: points.map(observed),
          borderColor: s.color,
          backgroundColor: s.color,
          borderWidth: 2,
          pointRadius: 0,
          pointHoverRadius: 5,
          tension: 0.2,
          spanGaps: false,
        };
      }),
    };
  }

  function renderChart() {
    const data = chartData(state.chartData || {});
    if (state.chart) {
      state.chart.data = data;
      state.chart.update();
      return;
    }
    state.chart = new Chart(document.getElementById("chart"), {
      type: "line",
      plugins: [referencePlugin],
      data,
      options: {
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              title: (items) => "7 jours se terminant le " + new Date(items[0].label).toLocaleDateString("fr-FR"),
              label: (item) => `${item.dataset.label} : ${fmtInt(item.dataset.raw[item.dataIndex])} · indice ${fmtInt(item.raw)}`,
            },
          },
        },
        scales: {
          x: {
            grid: { display: false },
            ticks: { color: "#6f6e69", maxTicksLimit: 8, maxRotation: 0,
              callback(v) { return new Date(this.getLabelForValue(v)).toLocaleDateString("fr-FR", { day: "2-digit", month: "2-digit" }); } },
          },
          y: { beginAtZero: true, grid: { color: "#eeeeea" }, border: { display: false },
            title: { display: true, text: "Indice", color: "#6f6e69", font: { size: 11 } },
            ticks: { color: "#6f6e69", maxTicksLimit: 6, callback: (v) => fmtInt(v) } },
        },
      },
    });
  }

  // ---------------------------------------------------------------- navigation
  const getJSON = (url) => fetch(url).then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); });

  async function showNational() {
    state.scope = "national";
    showNationalPanel();
    document.getElementById("chart-scope").textContent = "France entière";
    state.chartData = await getJSON(`${URLS.timeseries}?scope=national`);
    renderChart();
    highlightSelection();
  }

  async function showLocation(id) {
    state.scope = String(id);
    const d = await getJSON(`${URLS.location}${id}/`);
    renderLocationPanel(d);
    document.getElementById("chart-scope").textContent = d.full_name;
    state.chartData = d.chart;
    renderChart();
    highlightSelection();
  }

  // ---------------------------------------------------------------- carte
  // Pas de fond de carte : seuls les aplats des académies sont dessinés.
  // Zoom au pavé tactile / à la molette quand le curseur est sur la carte.
  const map = L.map("map", {
    zoomSnap: 0.1, zoomDelta: 0.5, scrollWheelZoom: true, wheelPxPerZoomLevel: 120,
    attributionControl: false, zoomControl: true,
  });
  const METROPOLE = [[41.9, -4.9], [51.1, 9.0]];
  map.fitBounds(METROPOLE, { padding: [4, 4] });
  window.addEventListener("resize", () => map.fitBounds(METROPOLE, { padding: [4, 4] }));
  map.setMinZoom(map.getZoom() - 1);  // éviter de dézoomer jusqu'à perdre la France
  // Clic hors d'une académie : retour à la France entière.
  map.on("click", () => { if (state.scope !== "national") showNational(); });

  const layers = {};   // id -> couche Leaflet (aplat)
  const byId = {};     // id -> données de l'académie

  function tooltipHtml(l) {
    const f = fam();
    const name = `<strong>${escapeHtml(l.full_name)}</strong><br>`;
    return `${name}${fmtInt(l[f.volume])} ${f.noun} · ${fmtPct(l[f.change])} vs 7 j préc.`;
  }

  function baseStyle(l, breaks) {
    return { fillColor: fillFor(l, breaks), fillOpacity: 1, color: "#ffffff", weight: 1 };
  }

  let currentBreaks = [];

  function renderMap() {
    currentBreaks = !isSequential() ? [] : fam().breaks || classBreaks(locations.map((l) => l[currentMetric()]));
    Object.entries(layers).forEach(([id, layer]) => {
      const l = byId[id];
      layer.setStyle(baseStyle(l, currentBreaks));
      layer.setTooltipContent(tooltipHtml(l));
    });
    const overseas = document.getElementById("overseas-list");
    overseas.innerHTML = "";
    locations.filter((l) => !layers[l.id] || !isMetropole(l)).forEach((l) => {
      const fill = fillFor(l, currentBreaks);
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "chip";
      chip.dataset.id = l.id;
      chip.setAttribute("aria-pressed", "false");
      chip.title = tooltipHtml(l).replace(/<br>/g, " · ").replace(/<[^>]+>/g, "");
      chip.innerHTML = `<span class="dot" style="background:${fill}"></span>${escapeHtml(l.name)}`;
      chip.addEventListener("click", () => showLocation(l.id));
      overseas.appendChild(chip);
    });
    renderLegend(currentBreaks);
    highlightSelection();
  }

  const isMetropole = (l) => l.lat > 40 && l.lat < 52 && l.lon > -6 && l.lon < 10;

  function highlightSelection() {
    const national = state.scope === "national";
    Object.entries(layers).forEach(([id, layer]) => {
      const l = byId[id];
      const style = baseStyle(l, currentBreaks);
      if (id === state.scope) {
        Object.assign(style, { color: "#000091", weight: 3.5 });
        layer.setStyle(style);
        layer.bringToFront();
      } else {
        layer.setStyle(Object.assign(style, { fillOpacity: national ? 1 : 0.45 }));
      }
    });
    document.querySelectorAll(".chip").forEach((c) => c.setAttribute("aria-pressed", String(c.dataset.id === state.scope)));
  }

  function buildMap(shapes) {
    L.geoJSON(shapes, {
      filter: (f) => byId[f.id] && isMetropole(byId[f.id]),
      style: { fillOpacity: 1 },
      onEachFeature: (f, layer) => {
        layers[f.id] = layer;
        layer.bindTooltip("", { sticky: true, direction: "top", offset: [0, -6] });
        // Un clic ne doit pas donner le focus à l'aplat : sinon Leaflet recentre
        // l'infobulle au milieu de l'académie et le navigateur dessine un cadre.
        layer.on("add", () => layer.getElement().addEventListener("mousedown", (e) => e.preventDefault()));
        layer.on("click", (e) => {
          L.DomEvent.stopPropagation(e);  // ne pas déclencher le clic « hors région » de la carte
          showLocation(f.id);
        });
        layer.on("mouseover", () => { if (String(f.id) !== state.scope) layer.setStyle({ weight: 2.5, color: "#52514e" }); });
        layer.on("mouseout", () => highlightSelection());
      },
    }).addTo(map);
  }

  // ---------------------------------------------------------------- contrôles
  function pressOnly(selector, button) {
    document.querySelectorAll(selector).forEach((b) => b.setAttribute("aria-pressed", String(b === button)));
  }
  function setMapFamily(family) {
    state.mapFamily = family;
    pressOnly("[data-map-family]", document.querySelector(`[data-map-family="${family}"]`));
    document.querySelector('[data-map-mode="volume"]').textContent = isIntensity() ? "Niveau" : "Volume";
    // Le mode (Évolution | Volume/Niveau) est conservé d'une mesure à l'autre.
    if (locations.length) renderMap();
  }
  document.querySelectorAll("[data-map-family]").forEach((b) => b.addEventListener("click", () => setMapFamily(b.dataset.mapFamily)));
  document.querySelectorAll("[data-map-mode]").forEach((b) => b.addEventListener("click", () => {
    state.mapMode = b.dataset.mapMode; pressOnly("[data-map-mode]", b); renderMap();
  }));
  const signalsBox = document.getElementById("signals");
  signalsBox.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-id]");
    if (b) showLocation(b.dataset.id);
  });
  // Survol d'une ligne de signal : l'académie est surlignée sur la carte.
  signalsBox.addEventListener("mouseover", (e) => {
    const b = e.target.closest("button[data-id]");
    const layer = b && layers[b.dataset.id];
    if (layer) { layer.setStyle({ weight: 2.5, color: "#161616" }); layer.bringToFront(); }
  });
  signalsBox.addEventListener("mouseout", (e) => { if (e.target.closest("button[data-id]")) highlightSelection(); });
  document.getElementById("academy-select").addEventListener("change", (e) => { if (e.target.value) showLocation(e.target.value); });
  document.getElementById("back-national").addEventListener("click", showNational);

  // Survol d'une classe de la légende : les académies de cette classe restent pleines, les autres s'estompent.
  const legendEl = document.getElementById("legend");
  legendEl.addEventListener("mouseover", (e) => {
    const item = e.target.closest("[data-cls]");
    if (!item) return;
    const cls = Number(item.dataset.cls);
    legendEl.querySelectorAll("[data-cls]").forEach((it) => it.classList.toggle("is-active", it === item));
    Object.entries(layers).forEach(([id, layer]) => {
      // Hors classe : même gris très clair pour tous, quelle que soit la couleur d'origine
      // (une couleur foncée reste trop visible si on se contente de baisser l'opacité).
      const l = byId[id];
      const match = classOf(l, currentBreaks) === cls;
      layer.setStyle(match
        ? { fillColor: fillFor(l, currentBreaks), fillOpacity: 1, opacity: 1 }
        : { fillColor: DIMMED, fillOpacity: 1, opacity: 1 });
      if (match) layer.bringToFront();
    });
    document.querySelectorAll(".chip").forEach((c) => {
      c.classList.toggle("is-dim", classOf(byId[c.dataset.id], currentBreaks) !== cls);
    });
  });
  legendEl.addEventListener("mouseleave", () => {
    legendEl.querySelectorAll("[data-cls]").forEach((it) => it.classList.remove("is-active"));
    document.querySelectorAll(".chip").forEach((c) => c.classList.remove("is-dim"));
    highlightSelection();
  });

  // Fenêtres secondaires (méthodologie, qualité, hors carte)
  document.querySelectorAll("[data-open]").forEach((b) => b.addEventListener("click", () => {
    document.getElementById(b.dataset.open).showModal();
  }));
  document.querySelectorAll("dialog").forEach((d) => {
    d.addEventListener("click", (e) => {
      const r = d.getBoundingClientRect();
      const outside = e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom;
      if ((e.target === d && outside) || e.target.closest("[data-close]")) d.close();
    });
  });

  // ---------------------------------------------------------------- démarrage
  Promise.all([getJSON(URLS.locations), getJSON(URLS.shapes)]).then(([d, shapes]) => {
    locations = d.locations;
    national = d.national;
    locations.forEach((l) => { byId[l.id] = l; });
    buildMap(shapes);
    renderMap();
    renderSignals(d.signals);
  }).catch(() => { document.getElementById("legend").textContent = "Données cartographiques indisponibles."; });

  const params = new URLSearchParams(window.location.search);
  if (params.get("vue") === "intensite") setMapFamily("intensity");  // lien direct vers la vue Intensité
  const initial = params.get("academie");
  (initial ? showLocation(initial) : showNational())
    .catch(() => { document.getElementById("chart-scope").textContent = "Données indisponibles"; });
})();
