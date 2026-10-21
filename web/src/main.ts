import * as maplibregl from "maplibre-gl";
import type {
  DataDrivenPropertyValueSpecification,
  MapLayerMouseEvent,
} from "maplibre-gl";
import type { FeatureCollection } from "geojson";
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?url";
import { Protocol } from "pmtiles";
import "maplibre-gl/dist/maplibre-gl.css";

maplibregl.config.WORKER_URL = workerUrl;

type Extorsion = {
  months: string[];
  rows: Record<string, number[]>;
};

const TILES_URL =
  import.meta.env.VITE_TILES_URL ??
  new URL(`${import.meta.env.BASE_URL}peru.pmtiles`, document.baseURI).href;
const COLORS = ["#ffffb2", "#fecc5c", "#fd8d3c", "#f03b20", "#bd0026"];

const MESES = [
  "ene",
  "feb",
  "mar",
  "abr",
  "may",
  "jun",
  "jul",
  "ago",
  "sep",
  "oct",
  "nov",
  "dic",
];

function mesLabel(month: string): string {
  const [year, m] = month.split("-").map(Number);
  return `${MESES[m - 1]} ${year}`;
}

async function fetchFirst(paths: string[]): Promise<unknown> {
  for (const path of paths) {
    try {
      const response = await fetch(path);
      if (response.ok) return await response.json();
    } catch {
      // A missing file can still answer 200 with the app shell. Try the next.
    }
  }
  throw new Error(`no se pudo cargar ${paths[0]}`);
}

const [districts, extorsion] = (await Promise.all([
  fetchFirst(["data/districts.geojson", "fixtures/districts.geojson"]),
  fetchFirst(["data/extorsion.json", "fixtures/extorsion.json"]),
])) as [FeatureCollection, Extorsion];

const months = extorsion.months;
const rows = extorsion.rows;

const startInput = document.querySelector<HTMLInputElement>("#mes-inicio")!;
const endInput = document.querySelector<HTMLInputElement>("#mes-fin")!;
const startLabel = document.querySelector<HTMLOutputElement>("#label-inicio")!;
const endLabel = document.querySelector<HTMLOutputElement>("#label-fin")!;
const legend = document.querySelector<HTMLElement>("#legend")!;
const fuente = document.querySelector<HTMLElement>("#fuente")!;

startInput.max = endInput.max = String(months.length - 1);
startInput.value = String(Math.max(0, months.length - 12));
endInput.value = String(months.length - 1);
fuente.textContent = `Fuente: SIDPOL (PNP) · datosabiertos.gob.pe · ODC-By · datos a ${mesLabel(months[months.length - 1])}`;

function rangeTotals(lo: number, hi: number): Map<string, number> {
  const totals = new Map<string, number>();
  for (const [ubigeo, counts] of Object.entries(rows)) {
    let total = 0;
    for (let i = lo; i <= hi && i < counts.length; i++) total += counts[i];
    totals.set(ubigeo, total);
  }
  return totals;
}

function quantileBreaks(totals: number[]): number[] {
  const sorted = [...totals].sort((a, b) => a - b);
  const breaks = new Set<number>();
  for (let k = 1; k < COLORS.length; k++) {
    const index = Math.min(
      sorted.length - 1,
      Math.floor((k * sorted.length) / COLORS.length),
    );
    breaks.add(sorted[index]);
  }
  return [...breaks].sort((a, b) => a - b);
}

function colorFor(total: number, breaks: number[]): string {
  let index = 0;
  while (index < breaks.length && total > breaks[index]) index++;
  return COLORS[index];
}

const protocol = new Protocol();
maplibregl.addProtocol("pmtiles", protocol.tile);

const basemapLayers: maplibregl.LayerSpecification[] = [
  { id: "background", type: "background", paint: { "background-color": "#dde5e9" } },
  {
    id: "earth",
    type: "fill",
    source: "protomaps",
    "source-layer": "earth",
    paint: { "fill-color": "#f2efe9" },
  },
  {
    id: "water",
    type: "fill",
    source: "protomaps",
    "source-layer": "water",
    paint: { "fill-color": "#a8c8dd" },
  },
  {
    id: "roads",
    type: "line",
    source: "protomaps",
    "source-layer": "roads",
    paint: { "line-color": "#ffffff", "line-width": 0.6, "line-opacity": 0.7 },
  },
  {
    id: "boundaries",
    type: "line",
    source: "protomaps",
    "source-layer": "boundaries",
    paint: { "line-color": "#9aa4a8", "line-width": 0.8, "line-dasharray": [2, 1] },
  },
];

const map = new maplibregl.Map({
  container: "map",
  style: {
    version: 8,
    sources: {
      protomaps: { type: "vector", url: `pmtiles://${TILES_URL}` },
      districts: { type: "geojson", data: districts },
    },
    layers: [
      ...basemapLayers,
      {
        id: "districts-fill",
        type: "fill",
        source: "districts",
        paint: { "fill-color": COLORS[0], "fill-opacity": 0.75 },
      },
      {
        id: "districts-line",
        type: "line",
        source: "districts",
        paint: { "line-color": "#ffffff", "line-width": 0.5 },
      },
    ],
  },
  attributionControl: { compact: false, customAttribution: "© OpenStreetMap" },
});

declare global {
  interface Window {
    __map?: maplibregl.Map;
  }
}
window.__map = map;

map.addControl(new maplibregl.NavigationControl(), "top-right");

let bounds: maplibregl.LngLatBounds | null = null;
for (const feature of districts.features) {
  const geometry = feature.geometry;
  if (geometry.type === "GeometryCollection") continue;
  const flat = (coords: unknown): number[][] =>
    typeof (coords as number[])[0] === "number"
      ? [coords as number[]]
      : (coords as number[][]).flatMap(flat);
  for (const [lon, lat] of flat(geometry.coordinates)) {
    bounds = bounds
      ? bounds.extend([lon, lat])
      : new maplibregl.LngLatBounds([lon, lat], [lon, lat]);
  }
}
if (bounds) map.fitBounds(bounds, { padding: 60, duration: 0 });

function currentRange(): [number, number] {
  const lo = Math.min(Number(startInput.value), Number(endInput.value));
  const hi = Math.max(Number(startInput.value), Number(endInput.value));
  return [lo, hi];
}

function update(): void {
  const [lo, hi] = currentRange();
  startLabel.value = mesLabel(months[lo]);
  endLabel.value = mesLabel(months[hi]);

  const totals = rangeTotals(lo, hi);
  const breaks = quantileBreaks([...totals.values()]);

  const match: unknown[] = ["match", ["get", "ubigeo"]];
  for (const feature of districts.features) {
    const ubigeo = String(feature.properties?.ubigeo);
    match.push(ubigeo, colorFor(totals.get(ubigeo) ?? 0, breaks));
  }
  match.push(COLORS[0]);
  map.setPaintProperty(
    "districts-fill",
    "fill-color",
    match as DataDrivenPropertyValueSpecification<string>,
  );

  const edges = [0, ...breaks, Infinity];
  legend.replaceChildren(
    ...COLORS.slice(0, breaks.length + 1).map((color, i) => {
      const low = edges[i];
      const high = edges[i + 1];
      const text = !Number.isFinite(high)
        ? `más de ${low}`
        : low === high
          ? `${low}`
          : `${low} – ${high}`;
      const row = document.createElement("div");
      row.className = "row";
      const swatch = document.createElement("span");
      swatch.className = "swatch";
      swatch.style.background = color;
      row.append(swatch, document.createTextNode(text));
      return row;
    }),
  );
}

function sparkline(counts: number[]): SVGSVGElement {
  const width = 160;
  const height = 36;
  const max = Math.max(1, ...counts);
  const step = counts.length > 1 ? width / (counts.length - 1) : 0;
  const points = counts
    .map((c, i) => `${i * step},${height - (c / max) * (height - 4) - 2}`)
    .join(" ");
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("width", String(width));
  svg.setAttribute("height", String(height));
  const polyline = document.createElementNS(
    "http://www.w3.org/2000/svg",
    "polyline",
  );
  polyline.setAttribute("points", points);
  polyline.setAttribute("fill", "none");
  polyline.setAttribute("stroke", "#bd0026");
  polyline.setAttribute("stroke-width", "1.5");
  svg.append(polyline);
  return svg;
}

map.on("click", "districts-fill", (event: MapLayerMouseEvent) => {
  const feature = event.features?.[0];
  if (!feature) return;
  const ubigeo = String(feature.properties?.ubigeo);
  const nombre = String(feature.properties?.nombre ?? ubigeo);
  const counts = rows[ubigeo] ?? months.map(() => 0);
  const [lo, hi] = currentRange();
  const total = counts.slice(lo, hi + 1).reduce((a, b) => a + b, 0);

  const content = document.createElement("div");
  const title = document.createElement("h2");
  title.textContent = nombre;
  const totalLine = document.createElement("p");
  totalLine.className = "total";
  totalLine.textContent = `${total.toLocaleString("es-PE")} denuncias`;
  const caption = document.createElement("p");
  caption.className = "total";
  caption.textContent = `${mesLabel(months[lo])} – ${mesLabel(months[hi])}`;
  content.append(title, totalLine, caption, sparkline(counts));
  new maplibregl.Popup().setLngLat(event.lngLat).setDOMContent(content).addTo(map);
});

map.on("mouseenter", "districts-fill", () => {
  map.getCanvas().style.cursor = "pointer";
});
map.on("mouseleave", "districts-fill", () => {
  map.getCanvas().style.cursor = "";
});

map.on("load", update);
startInput.addEventListener("input", update);
endInput.addEventListener("input", update);
