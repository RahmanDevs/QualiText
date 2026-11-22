import { useState } from 'react';
import { Badge } from '@/components/ui/Badge';
import { Card } from '@/components/ui/Card';
import { formatNumber, useTranslation } from '@/lib/i18n';

export interface ClusterSummary {
  cluster_id: number;
  count: number;
  top_terms: string[];
}

export interface ClusterPoint {
  row_id: string;
  x: number;
  y: number;
  cluster_id: number;
  text_preview: string;
}

interface ClusterScatterChartProps {
  clusters: ClusterSummary[];
  points: ClusterPoint[];
  entropy?: number;
}

const CLUSTER_COLORS = [
  'var(--brand-600)',
  'var(--success-600)',
  'var(--info-600)',
  'var(--warning-600)',
  'var(--danger-600)',
];

export function ClusterScatterChart({ clusters, points, entropy }: ClusterScatterChartProps) {
  const { t, locale } = useTranslation();
  const [selectedPoint, setSelectedPoint] = useState<ClusterPoint | null>(null);

  if (points.length === 0) {
    return (
      <Card className="p-4">
        <p className="text-xs text-text-muted">{t('charts.clusterEmpty')}</p>
      </Card>
    );
  }

  const width = 520;
  const height = 260;
  const pad = 24;
  const innerW = width - pad * 2;
  const innerH = height - pad * 2;

  const toSvgX = (x: number) => pad + ((Math.max(-1, Math.min(1, x)) + 1) / 2) * innerW;
  const toSvgY = (y: number) => pad + ((1 - Math.max(-1, Math.min(1, y))) / 2) * innerH;

  return (
    <Card className="space-y-4 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-text-primary">{t('charts.clusterTitle')}</h3>
        {typeof entropy === 'number' && (
          <Badge tone="info">
            {t('charts.clusterEntropy', { value: formatNumber(entropy, locale) })}
          </Badge>
        )}
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <svg
            viewBox={`0 0 ${width} ${height}`}
            className="h-60 w-full rounded-control border border-border bg-canvas"
            role="img"
            aria-label={t('charts.clusterTitle')}
          >
            <line
              x1={pad}
              y1={height / 2}
              x2={width - pad}
              y2={height / 2}
              stroke="var(--border-default)"
              strokeDasharray="3 3"
            />
            <line
              x1={width / 2}
              y1={pad}
              x2={width / 2}
              y2={height - pad}
              stroke="var(--border-default)"
              strokeDasharray="3 3"
            />
            {points.map((pt) => {
              const color = CLUSTER_COLORS[pt.cluster_id % CLUSTER_COLORS.length];
              const isSelected = selectedPoint?.row_id === pt.row_id;
              return (
                <circle
                  key={pt.row_id}
                  cx={toSvgX(pt.x)}
                  cy={toSvgY(pt.y)}
                  r={isSelected ? 6 : 4}
                  fill={color}
                  fillOpacity={0.85}
                  stroke={isSelected ? 'var(--text-primary)' : 'var(--bg-surface)'}
                  strokeWidth={isSelected ? 2 : 1}
                  className="cursor-pointer"
                  onClick={() => setSelectedPoint(pt)}
                />
              );
            })}
          </svg>
        </div>

        <div className="space-y-3">
          <p className="text-xs font-medium text-text-secondary">{t('charts.clusterLegend')}</p>
          <ul className="space-y-2 text-xs">
            {clusters.map((c) => {
              const color = CLUSTER_COLORS[c.cluster_id % CLUSTER_COLORS.length];
              return (
                <li
                  key={c.cluster_id}
                  className="flex items-start gap-2 rounded-control border border-border bg-subtle p-2"
                >
                  <span
                    className="mt-1 h-2.5 w-2.5 shrink-0 rounded-full"
                    style={{ backgroundColor: color }}
                    aria-hidden="true"
                  />
                  <div className="min-w-0">
                    <div className="font-medium text-text-primary">
                      #{c.cluster_id + 1} · {formatNumber(c.count, locale)} {t('charts.rowsLabel')}
                    </div>
                    <div className="truncate text-text-muted">
                      {c.top_terms.join(', ') || '—'}
                    </div>
                  </div>
                </li>
              );
            })}
          </ul>

          {selectedPoint && (
            <div className="rounded-control border border-border bg-canvas p-2 text-xs">
              <div className="font-mono font-semibold text-text-primary">
                {selectedPoint.row_id}
              </div>
              <p className="mt-1 line-clamp-3 text-text-secondary">{selectedPoint.text_preview}</p>
            </div>
          )}
        </div>
      </div>
    </Card>
  );
}
