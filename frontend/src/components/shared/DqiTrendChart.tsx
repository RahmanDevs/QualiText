import { useState } from 'react';
import { Card } from '@/components/ui/Card';
import { formatDate, formatNumber, useTranslation } from '@/lib/i18n';

export interface DqiTrendPoint {
  run_id: string;
  version_number: number;
  dataset_name?: string;
  dqi_total: number | null;
  completed_at?: string | null;
}

interface DqiTrendChartProps {
  points: DqiTrendPoint[];
  title?: string;
  emptyLabel?: string;
}

export function DqiTrendChart({ points, title, emptyLabel }: DqiTrendChartProps) {
  const { t, locale } = useTranslation();
  const valid = points.filter((p): p is DqiTrendPoint & { dqi_total: number } => p.dqi_total !== null);
  const [activeIdx, setActiveIdx] = useState<number | null>(null);

  if (valid.length === 0) {
    return (
      <Card className="p-4">
        {title && <h2 className="mb-2 text-sm font-semibold text-text-primary">{title}</h2>}
        <p className="text-xs text-text-muted">{emptyLabel ?? t('charts.trendEmpty')}</p>
      </Card>
    );
  }

  const width = 600;
  const height = 180;
  const padX = 36;
  const padY = 20;
  const innerW = width - padX * 2;
  const innerH = height - padY * 2;

  const coords = valid.map((pt, idx) => {
    const x = valid.length === 1 ? padX + innerW / 2 : padX + (idx / (valid.length - 1)) * innerW;
    const clamped = Math.max(0, Math.min(100, pt.dqi_total));
    const y = padY + (1 - clamped / 100) * innerH;
    return { x, y, pt };
  });

  const linePoints = coords.map((c) => `${c.x.toFixed(1)},${c.y.toFixed(1)}`).join(' ');
  const areaPoints =
    coords.length > 1
      ? `${coords[0].x.toFixed(1)},${padY + innerH} ${linePoints} ${coords[coords.length - 1].x.toFixed(1)},${padY + innerH}`
      : '';
  const safeIdx =
    activeIdx !== null && activeIdx < coords.length ? activeIdx : coords.length - 1;
  const selected = coords[safeIdx];

  return (
    <Card className="space-y-3 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-text-primary">
          {title ?? t('charts.trendTitle')}
        </h2>
        {selected && (
          <span className="text-xs text-text-secondary tabular-nums">
            {selected.pt.dataset_name ? `${selected.pt.dataset_name} · ` : ''}v
            {selected.pt.version_number} — DQI{' '}
            <strong className="text-text-primary">
              {formatNumber(selected.pt.dqi_total, locale)}
            </strong>
            {selected.pt.completed_at ? ` (${formatDate(selected.pt.completed_at, locale)})` : ''}
          </span>
        )}
      </div>

      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="h-44 w-full"
        role="img"
        aria-label={title ?? t('charts.trendTitle')}
      >
        {[0, 50, 75, 100].map((val) => {
          const y = padY + (1 - val / 100) * innerH;
          return (
            <g key={val}>
              <line
                x1={padX}
                y1={y}
                x2={width - padX}
                y2={y}
                stroke="var(--border-default)"
                strokeDasharray={val === 75 ? '4 3' : undefined}
                strokeWidth="1"
              />
              <text x="4" y={y + 4} fill="var(--text-muted)" fontSize="10">
                {val}
              </text>
            </g>
          );
        })}

        {areaPoints && (
          <polygon points={areaPoints} fill="var(--brand-50)" opacity="0.65" />
        )}
        {coords.length > 1 && (
          <polyline
            fill="none"
            stroke="var(--brand-600)"
            strokeWidth="2.5"
            strokeLinejoin="round"
            points={linePoints}
          />
        )}

        {coords.map((c, idx) => (
          <circle
            key={c.pt.run_id}
            cx={c.x}
            cy={c.y}
            r={activeIdx === idx ? 6 : 4}
            fill="var(--brand-600)"
            stroke="var(--bg-surface)"
            strokeWidth="1.5"
            tabIndex={0}
            className="cursor-pointer outline-none"
            aria-label={`v${c.pt.version_number}: ${c.pt.dqi_total}`}
            onMouseEnter={() => setActiveIdx(idx)}
            onFocus={() => setActiveIdx(idx)}
          />
        ))}
      </svg>
    </Card>
  );
}
