import { useMemo, useRef, useState } from "react";

/* Charts are hand-rolled SVG on purpose: three small forms, no runtime dependency,
   and full control of the accessibility affordances (table view, direct labels).
   Colors come from the CSS custom properties in styles.css, which are the validated
   palette for each surface — never hard-coded hex here. */

const PAD = { top: 12, right: 16, bottom: 26, left: 30 };

export interface Point {
  x: number; // session id
  y: number; // score
  label: string; // tooltip title
  sub?: string; // tooltip detail
}

/* ------------------------------------------------------------------ line */

export function LineChart({
  points,
  width = 720,
  height = 240,
  yMax = 10,
  yLabel = "score",
}: {
  points: Point[];
  width?: number;
  height?: number;
  yMax?: number;
  yLabel?: string;
}) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const [hover, setHover] = useState<number | null>(null);

  const plotW = width - PAD.left - PAD.right;
  const plotH = height - PAD.top - PAD.bottom;

  const xOf = (index: number) =>
    PAD.left + (points.length <= 1 ? plotW / 2 : (index / (points.length - 1)) * plotW);
  const yOf = (value: number) => PAD.top + plotH - (value / yMax) * plotH;

  const path = useMemo(
    () => points.map((p, i) => `${i === 0 ? "M" : "L"}${xOf(i)},${yOf(p.y)}`).join(" "),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [points, width, height],
  );

  const onMove = (event: React.PointerEvent) => {
    const svg = svgRef.current;
    if (!svg || points.length === 0) return;
    const rect = svg.getBoundingClientRect();
    const x = ((event.clientX - rect.left) / rect.width) * width;
    let best = 0;
    let bestDistance = Infinity;
    points.forEach((_, i) => {
      const distance = Math.abs(xOf(i) - x);
      if (distance < bestDistance) {
        bestDistance = distance;
        best = i;
      }
    });
    setHover(best);
  };

  if (points.length === 0) return null;

  const ticks = [0, 2, 4, 6, 8, 10].filter((t) => t <= yMax);
  const active = hover != null ? points[hover] : null;

  return (
    <div className="chart-holder" ref={wrapRef}>
      <div className="chart">
        <svg
          ref={svgRef}
          viewBox={`0 0 ${width} ${height}`}
          width={width}
          height={height}
          role="img"
          aria-label={`${yLabel} over time, ${points.length} sessions`}
          onPointerMove={onMove}
          onPointerLeave={() => setHover(null)}
        >
          {ticks.map((tick) => (
            <g key={tick}>
              <line
                className="gridline"
                x1={PAD.left}
                x2={width - PAD.right}
                y1={yOf(tick)}
                y2={yOf(tick)}
              />
              <text className="tick" x={PAD.left - 8} y={yOf(tick) + 4} textAnchor="end">
                {tick}
              </text>
            </g>
          ))}
          <line
            className="baseline"
            x1={PAD.left}
            x2={width - PAD.right}
            y1={yOf(0)}
            y2={yOf(0)}
          />

          <path className="series-line" d={path} />

          {points.map((point, i) => (
            <circle
              key={point.x}
              className="series-dot"
              cx={xOf(i)}
              cy={yOf(point.y)}
              r={hover === i ? 6 : 4.5}
            />
          ))}

          {/* First and last are direct-labeled; the rest are on hover, so the chart
              never carries a number on every point. */}
          {[0, points.length - 1]
            .filter((i, idx, all) => all.indexOf(i) === idx)
            .map((i) => (
              <text
                key={`label-${i}`}
                className="mark-label"
                x={xOf(i)}
                y={yOf(points[i].y) - 11}
                textAnchor={i === 0 ? "start" : "end"}
              >
                {points[i].y.toFixed(1)}
              </text>
            ))}

          {hover != null && (
            <line
              className="crosshair"
              x1={xOf(hover)}
              x2={xOf(hover)}
              y1={PAD.top}
              y2={PAD.top + plotH}
            />
          )}

          <text className="tick" x={PAD.left} y={height - 8} textAnchor="start">
            S{points[0].x}
          </text>
          {points.length > 1 && (
            <text className="tick" x={width - PAD.right} y={height - 8} textAnchor="end">
              S{points[points.length - 1].x}
            </text>
          )}
        </svg>
      </div>

      {active && hover != null && (
        <div
          className="tooltip"
          style={{
            left: `${(xOf(hover) / width) * 100}%`,
            top: 0,
            transform: `translateX(${hover > points.length / 2 ? "-105%" : "10px"})`,
          }}
        >
          <div className="t-title">{active.label}</div>
          <div className="tnum">
            {active.y.toFixed(1)} {yLabel}
          </div>
          {active.sub && <div className="secondary small">{active.sub}</div>}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ bars */

export function ScoreBars({
  rows,
  max = 10,
}: {
  rows: { label: string; value: number | null }[];
  max?: number;
}) {
  const present = rows.filter((row) => row.value != null);
  if (present.length === 0) return null;

  const barH = 16;
  const gap = 10; // >= 2px surface gap between adjacent fills
  const labelW = 110;
  const valueW = 34;
  const width = 520;
  const height = present.length * (barH + gap) + 18;
  const plotW = width - labelW - valueW;

  return (
    <div className="chart">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        width={width}
        height={height}
        role="img"
        aria-label="Score by dimension, out of 10"
      >
        {[0, 5, 10].map((tick) => (
          <g key={tick}>
            <line
              className="gridline"
              x1={labelW + (tick / max) * plotW}
              x2={labelW + (tick / max) * plotW}
              y1={0}
              y2={height - 16}
            />
            <text
              className="tick"
              x={labelW + (tick / max) * plotW}
              y={height - 3}
              textAnchor="middle"
            >
              {tick}
            </text>
          </g>
        ))}

        {present.map((row, i) => {
          const y = i * (barH + gap);
          const value = row.value as number;
          const w = Math.max(2, (value / max) * plotW);
          return (
            <g key={row.label}>
              <text className="mark-label" x={labelW - 10} y={y + barH - 3} textAnchor="end">
                {row.label}
              </text>
              <rect
                x={labelW}
                y={y}
                width={w}
                height={barH}
                rx={4}
                fill="var(--series-1)"
                opacity={0.9}
              />
              <text className="mark-label" x={labelW + w + 7} y={y + barH - 3}>
                {value.toFixed(1)}
              </text>
            </g>
          );
        })}
        <line className="baseline" x1={labelW} x2={labelW} y1={0} y2={height - 16} />
      </svg>
    </div>
  );
}

/* ------------------------------------------------------------- sparklines */

export function Sparkline({
  values,
  width = 150,
  height = 34,
  max = 10,
}: {
  values: number[];
  width?: number;
  height?: number;
  max?: number;
}) {
  if (values.length === 0) return null;
  const pad = 3;
  const plotW = width - pad * 2;
  const plotH = height - pad * 2;
  const xOf = (i: number) =>
    pad + (values.length <= 1 ? plotW / 2 : (i / (values.length - 1)) * plotW);
  const yOf = (v: number) => pad + plotH - (v / max) * plotH;
  const path = values.map((v, i) => `${i === 0 ? "M" : "L"}${xOf(i)},${yOf(v)}`).join(" ");

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      width={width}
      height={height}
      aria-hidden="true"
      style={{ overflow: "visible" }}
    >
      <line
        className="gridline"
        x1={pad}
        x2={width - pad}
        y1={yOf(max / 2)}
        y2={yOf(max / 2)}
      />
      <path className="series-line" d={path} />
      {values.length > 0 && (
        <circle className="series-dot" cx={xOf(values.length - 1)} cy={yOf(values.at(-1)!)} r={4} />
      )}
    </svg>
  );
}
