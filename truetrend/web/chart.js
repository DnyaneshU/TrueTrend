// One test's timeline as a chart: the values, the lab's normal range behind them,
// and a tap on a point that opens the report page where that value is printed.
//
// Chart.js is served from vendor/, not a CDN: the app must work with no internet.
// Nothing here computes a value — every number drawn comes from the server.

import * as t from "./text.js";

const INK = {
  line: "#35c6c0",
  lineSoft: "#35c6c016",
  check: "#d9a441",
  band: "#ffffff07",
  bandEdge: "#ffffff22",
  grid: "#ffffff0c",
  text: "#9aa3b2",
};

Chart.defaults.font.family =
  'ui-sans-serif, -apple-system, "Segoe UI", Roboto, Arial, sans-serif';
Chart.defaults.font.size = 11;
Chart.defaults.color = INK.text;

/** The lab's normal range, drawn as a band behind the line when every report agrees on it. */
function rangeBand(points) {
  const lows = new Set(points.map((p) => p.ref_low));
  const highs = new Set(points.map((p) => p.ref_high));
  if (lows.size !== 1 || highs.size !== 1) return null; // labs disagree: drawing one band would lie
  const [low] = lows;
  const [high] = highs;
  return low === null || high === null ? null : { low, high };
}

/** A point is drawn amber and hollow while a person has not confirmed it. */
const pointColour = (p) => (p.status === "verified" ? INK.line : INK.check);

export function drawTimeline(canvas, timeline, onPick) {
  const points = timeline.points;
  const band = rangeBand(points);

  const datasets = [
    {
      label: timeline.name,
      data: points.map((p) => p.value_std ?? p.value),
      borderColor: INK.line,
      backgroundColor: INK.lineSoft,
      borderWidth: 2.5,
      pointBackgroundColor: points.map(pointColour),
      pointBorderColor: points.map(pointColour),
      pointRadius: 6,
      pointHoverRadius: 9,
      pointHitRadius: 22, // a finger, not a mouse
      tension: 0.25,
      fill: true,
      order: 1,
    },
  ];

  // The band is two flat lines filled to each other, so it sits behind the values.
  if (band) {
    const flat = (value) => points.map(() => value);
    datasets.push(
      {
        label: "Upper limit",
        data: flat(band.high),
        borderColor: INK.bandEdge,
        borderWidth: 1,
        borderDash: [5, 5],
        pointRadius: 0,
        pointHitRadius: 0,
        fill: "+1",
        backgroundColor: INK.band,
        order: 3,
      },
      {
        label: "Lower limit",
        data: flat(band.low),
        borderColor: INK.bandEdge,
        borderWidth: 1,
        borderDash: [5, 5],
        pointRadius: 0,
        pointHitRadius: 0,
        fill: false,
        order: 3,
      },
    );
  }

  const chart = new Chart(canvas, {
    type: "line",
    data: { labels: points.map((p) => t.shortDay(p.sample_date)), datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 500 },
      interaction: { mode: "nearest", intersect: false },
      onClick: (_event, hit) => {
        const first = hit.find((h) => h.datasetIndex === 0);
        if (first) onPick?.(points[first.index]);
      },
      // A finger over a point should look tappable.
      onHover: (event, hit) => {
        event.native.target.style.cursor = hit.length ? "pointer" : "default";
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: "#12151bf5",
          borderColor: "#242a34",
          borderWidth: 1,
          padding: 11,
          displayColors: false,
          callbacks: {
            // Only the value's own dataset gets a tooltip; the band lines are scenery.
            filter: (item) => item.datasetIndex === 0,
            title: (items) => t.day(points[items[0].dataIndex].sample_date),
            label: (item) => {
              const p = points[item.dataIndex];
              const unit = p.unit_std || p.unit || "";
              const lines = [`${p.value_text} ${unit}`.trim()];
              if (p.lab_name) lines.push(p.lab_name);
              if (p.status !== "verified") lines.push(t.T.needs_check);
              return lines;
            },
            afterBody: () => "Tap to see it in the report",
          },
        },
      },
      scales: {
        x: { grid: { color: INK.grid }, ticks: { maxRotation: 0, autoSkipPadding: 12 } },
        y: {
          grid: { color: INK.grid },
          title: { display: Boolean(timeline.unit), text: timeline.unit, color: INK.text },
        },
      },
    },
  });
  return chart;
}
