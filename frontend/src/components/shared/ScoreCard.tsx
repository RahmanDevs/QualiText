import { Badge } from '@/components/ui/Badge';
import { Card } from '@/components/ui/Card';
import { formatNumber, useTranslation } from '@/lib/i18n';

interface DimensionItem {
  dimension: string;
  label: string;
  score: number;
  weight: number;
  contribution: number;
  findings_count: number;
}

interface ScoreCardProps {
  dqiTotal: number | null;
  statusLabel?: string;
  isPartial?: boolean;
  criticalCapped?: boolean;
  dimensions?: DimensionItem[];
  onDimensionClick?: (dimension: string) => void;
}

function getScoreTone(score: number | null): 'success' | 'info' | 'warning' | 'danger' | 'neutral' {
  if (score === null) return 'neutral';
  if (score >= 90) return 'success';
  if (score >= 75) return 'info';
  if (score >= 60) return 'warning';
  return 'danger';
}

export function ScoreCard({
  dqiTotal,
  statusLabel,
  isPartial = false,
  criticalCapped = false,
  dimensions = [],
  onDimensionClick,
}: ScoreCardProps) {
  const { t, locale } = useTranslation();
  const tone = getScoreTone(dqiTotal);

  return (
    <Card className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-xs font-medium uppercase tracking-wider text-text-muted">
            {t('scoreCard.title')}
          </p>
          <div className="mt-1 flex items-baseline gap-3">
            <span className="text-4xl font-bold tabular-nums text-text-primary">
              {dqiTotal !== null ? dqiTotal.toFixed(1) : '—'}
            </span>
            <span className="text-sm text-text-muted">/ 100</span>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={tone}>{statusLabel ?? t('scoreCard.unassessed')}</Badge>
          {isPartial && <Badge tone="warning">{t('scoreCard.partialBadge')}</Badge>}
          {criticalCapped && <Badge tone="danger">{t('scoreCard.criticalCapBadge')}</Badge>}
        </div>
      </div>

      {dimensions.length > 0 && (
        <div className="space-y-3 border-t border-border pt-4">
          {dimensions.map((dim) => {
            const content = (
              <>
                <div className="flex items-center justify-between text-xs">
                  <span className="font-medium text-text-primary">
                    {dim.label}{' '}
                    <span className="text-text-muted">({Math.round(dim.weight * 100)}%)</span>
                  </span>
                  <span className="tabular-nums text-text-secondary">
                    {dim.score.toFixed(1)} ·{' '}
                    {t('scoreCard.findingsCount', {
                      count: formatNumber(dim.findings_count, locale),
                    })}
                  </span>
                </div>
                <div className="h-2 w-full overflow-hidden rounded-full bg-subtle">
                  <div
                    className="h-full rounded-full bg-brand-600 transition-all duration-150"
                    style={{ width: `${Math.min(100, Math.max(0, dim.score))}%` }}
                  />
                </div>
              </>
            );

            if (onDimensionClick) {
              return (
                <button
                  key={dim.dimension}
                  type="button"
                  onClick={() => onDimensionClick(dim.dimension)}
                  className="block w-full space-y-1 rounded-control p-1 text-left transition-colors hover:bg-subtle focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-600"
                >
                  {content}
                </button>
              );
            }

            return (
              <div key={dim.dimension} className="space-y-1">
                {content}
              </div>
            );
          })}
          {onDimensionClick && (
            <p className="text-xs text-text-muted">{t('scoreCard.drillDownHint')}</p>
          )}
        </div>
      )}
    </Card>
  );
}
