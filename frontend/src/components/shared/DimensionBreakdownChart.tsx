import { Card } from '@/components/ui/Card';
import { formatNumber, useTranslation } from '@/lib/i18n';

export interface DimensionBarItem {
  dimension: string;
  label: string;
  score: number;
  weight: number;
  findings_count: number;
}

interface DimensionBreakdownChartProps {
  dimensions: DimensionBarItem[];
  onDimensionClick?: (dimension: string) => void;
}

function barColorVar(score: number): string {
  if (score >= 90) return 'var(--success-600)';
  if (score >= 75) return 'var(--info-600)';
  if (score >= 60) return 'var(--warning-600)';
  return 'var(--danger-600)';
}

export function DimensionBreakdownChart({
  dimensions,
  onDimensionClick,
}: DimensionBreakdownChartProps) {
  const { t, locale } = useTranslation();

  if (dimensions.length === 0) {
    return null;
  }

  return (
    <Card className="space-y-3 p-4">
      <h2 className="text-sm font-semibold text-text-primary">{t('charts.dimensionTitle')}</h2>
      <div className="space-y-2.5">
        {dimensions.map((d) => {
          const pct = Math.max(0, Math.min(100, d.score));
          const rowBody = (
            <>
              <div className="flex items-center justify-between text-xs">
                <span className="font-medium text-text-primary">{d.label}</span>
                <span className="tabular-nums text-text-secondary">
                  <strong className="text-text-primary">{formatNumber(d.score, locale)}</strong> / 100
                  {' · '}
                  {Math.round(d.weight * 100)}%
                </span>
              </div>
              <div className="mt-1 h-2.5 w-full overflow-hidden rounded-full bg-subtle">
                <div
                  className="h-full rounded-full transition-all duration-150"
                  style={{ width: `${pct}%`, backgroundColor: barColorVar(d.score) }}
                />
              </div>
            </>
          );

          if (onDimensionClick) {
            return (
              <button
                key={d.dimension}
                type="button"
                onClick={() => onDimensionClick(d.dimension)}
                className="block w-full rounded-control p-1.5 text-left transition-colors hover:bg-subtle focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-600"
              >
                {rowBody}
              </button>
            );
          }

          return (
            <div key={d.dimension} className="p-1.5">
              {rowBody}
            </div>
          );
        })}
      </div>
    </Card>
  );
}
