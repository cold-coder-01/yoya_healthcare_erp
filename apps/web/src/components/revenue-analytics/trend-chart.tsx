"use client";

import { useState, type CSSProperties, type MouseEvent } from "react";

import {
  DELIVERED_LABEL,
  RECOGNIZED_LABEL,
  bucketLabel,
  formatCompactMoney,
  formatMoney,
  labelIndexes,
  trendGeometry,
} from "@/lib/revenue-analytics-format";
import type { TrendGrain, TrendPoint } from "@/types/revenue-analytics";

/**
 * Recognized Revenue vs Delivered Value over time.
 *
 * Plain SVG, no chart dependency. ONE shared y-axis (same unit, same scale);
 * two validated categorical hues (blue / orange, both >= 3:1 on white, CVD
 * separation 24.7); identity never by colour alone -- a legend, direct
 * end-labels and a dashed stroke for Delivered Value. A crosshair + tooltip
 * on hover; `showTable` renders the exact figures beneath.
 */
const SERIES_STYLE = {
  "--series-recognized": "#2a78d6",
  "--series-delivered": "#eb6834",
} as CSSProperties;

export default function TrendChart({
  points,
  grain,
  currency,
  showTable = false,
}: {
  points: TrendPoint[];
  grain: TrendGrain;
  currency: string;
  showTable?: boolean;
}) {
  const [hover, setHover] = useState<number | null>(null);

  if (points.length === 0) {
    return <p className="text-sm text-slate-500">No buckets in this period.</p>;
  }

  const geometry = trendGeometry(points);
  const last = points.length - 1;
  const labels = labelIndexes(points.length);
  const hovered = hover === null ? null : points[hover];

  const onMove = (event: MouseEvent<SVGRectElement>) => {
    const box = event.currentTarget.getBoundingClientRect();
    const ratio = (event.clientX - box.left) / box.width;
    const plotX = geometry.left + ratio * (geometry.right - geometry.left);
    let nearest = 0;
    let distance = Infinity;
    points.forEach((_point, index) => {
      const gap = Math.abs(geometry.x(index) - plotX);
      if (gap < distance) {
        distance = gap;
        nearest = index;
      }
    });
    setHover(nearest);
  };

  return (
    <div style={SERIES_STYLE}>
      <div className="mb-2 flex flex-wrap items-center gap-4 text-xs text-slate-700" aria-label="Legend">
        <span className="flex items-center gap-1.5">
          <svg width="22" height="8" aria-hidden="true">
            <line x1="1" y1="4" x2="21" y2="4" stroke="var(--series-recognized)" strokeWidth="2" strokeLinecap="round" />
          </svg>
          {RECOGNIZED_LABEL}
        </span>
        <span className="flex items-center gap-1.5">
          <svg width="22" height="8" aria-hidden="true">
            <line x1="1" y1="4" x2="21" y2="4" stroke="var(--series-delivered)" strokeWidth="2" strokeDasharray="5 3" strokeLinecap="round" />
          </svg>
          {DELIVERED_LABEL}
        </span>
        <span className="text-slate-500">{currency}</span>
      </div>

      <div className="relative">
        <svg
          viewBox={`0 0 ${geometry.width} ${geometry.height}`}
          className="h-auto w-full"
          role="img"
          aria-label={`${RECOGNIZED_LABEL} and ${DELIVERED_LABEL} by ${grain}`}
        >
          {geometry.ticks.map((tick) => (
            <g key={tick}>
              <line
                x1={geometry.left}
                x2={geometry.right}
                y1={geometry.y(tick)}
                y2={geometry.y(tick)}
                stroke={tick === 0 ? "#94a3b8" : "#e2e8f0"}
                strokeWidth="1"
              />
              <text x={geometry.left - 8} y={geometry.y(tick) + 4} textAnchor="end" className="fill-slate-500 text-[10px]">
                {formatCompactMoney(tick)}
              </text>
            </g>
          ))}
          {labels.map((index) => (
            <text
              key={index}
              x={geometry.x(index)}
              y={geometry.bottom + 18}
              textAnchor="middle"
              className="fill-slate-500 text-[10px]"
            >
              {bucketLabel(points[index].bucket, grain)}
            </text>
          ))}

          <path d={geometry.deliveredPath} fill="none" stroke="var(--series-delivered)" strokeWidth="2" strokeDasharray="6 4" strokeLinejoin="round" strokeLinecap="round" />
          <path d={geometry.recognizedPath} fill="none" stroke="var(--series-recognized)" strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />

          {/* Direct end-labels: identity without relying on colour. */}
          <text x={geometry.x(last) + 8} y={geometry.y(points[last].recognized) + 4} className="fill-slate-800 text-[10px] font-semibold">
            Recognized
          </text>
          <text x={geometry.x(last) + 8} y={geometry.y(points[last].delivered) + 4} className="fill-slate-600 text-[10px]">
            Delivered
          </text>

          {hovered && hover !== null && (
            <g pointerEvents="none">
              <line x1={geometry.x(hover)} x2={geometry.x(hover)} y1={geometry.top} y2={geometry.bottom} stroke="#94a3b8" strokeWidth="1" />
              <circle cx={geometry.x(hover)} cy={geometry.y(hovered.recognized)} r="4" fill="var(--series-recognized)" stroke="#fff" strokeWidth="2" />
              <circle cx={geometry.x(hover)} cy={geometry.y(hovered.delivered)} r="4" fill="var(--series-delivered)" stroke="#fff" strokeWidth="2" />
            </g>
          )}

          <rect
            x={geometry.left}
            y={geometry.top}
            width={geometry.right - geometry.left}
            height={geometry.bottom - geometry.top}
            fill="transparent"
            onMouseMove={onMove}
            onMouseLeave={() => setHover(null)}
          />
        </svg>

        {hovered && hover !== null && (
          <div
            className="pointer-events-none absolute top-1 rounded border border-slate-200 bg-white px-2 py-1 text-xs text-slate-800 shadow"
            style={{ left: `${(geometry.x(hover) / geometry.width) * 100}%`, transform: "translateX(-50%)" }}
          >
            <div className="font-semibold">{bucketLabel(hovered.bucket, grain)}</div>
            <div>{RECOGNIZED_LABEL}: {formatMoney(hovered.recognized)}</div>
            <div>{DELIVERED_LABEL}: {formatMoney(hovered.delivered)}</div>
          </div>
        )}
      </div>

      {showTable && (
        <table className="mt-3 w-full text-xs">
          <thead>
            <tr className="border-b border-slate-200 text-left text-slate-500">
              <th className="py-1 font-semibold">Period starting</th>
              <th className="py-1 text-right font-semibold">{RECOGNIZED_LABEL}</th>
              <th className="py-1 text-right font-semibold">{DELIVERED_LABEL}</th>
            </tr>
          </thead>
          <tbody>
            {points.map((point) => (
              <tr key={point.bucket} className="border-b border-slate-100">
                <td className="py-1 text-slate-700">{bucketLabel(point.bucket, grain)}</td>
                <td className="py-1 text-right tabular-nums text-slate-900">{formatMoney(point.recognized)}</td>
                <td className="py-1 text-right tabular-nums text-slate-700">{formatMoney(point.delivered)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
