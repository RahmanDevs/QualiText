import { Badge } from '@/components/ui/Badge';
import { useTranslation } from '@/lib/i18n';

interface DiffViewerProps {
  before: string;
  after: string;
  changed?: boolean;
  title?: string;
}

export function DiffViewer({ before, after, changed, title }: DiffViewerProps) {
  const { t } = useTranslation();
  const isChanged = changed ?? before !== after;

  return (
    <div className="space-y-2 rounded-control border border-border bg-surface p-3">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-semibold text-text-primary">
          {title ?? t('explorer.diffPreviewTitle')}
        </span>
        <Badge tone={isChanged ? 'info' : 'neutral'}>
          {isChanged ? t('diff.changed') : t('diff.unchanged')}
        </Badge>
      </div>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <div className="space-y-1">
          <p className="text-xs font-medium text-text-muted">{t('diff.before')}</p>
          <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-control border border-border bg-canvas p-2.5 font-mono text-xs text-text-secondary">
            {before || '—'}
          </pre>
        </div>
        <div className="space-y-1">
          <p className="text-xs font-medium text-text-muted">{t('diff.after')}</p>
          <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-control border border-border bg-canvas p-2.5 font-mono text-xs text-text-primary">
            {after || '—'}
          </pre>
        </div>
      </div>
    </div>
  );
}
