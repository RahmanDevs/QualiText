import { useEffect, useState, type FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { Input } from '@/components/ui/Input';
import { Skeleton } from '@/components/ui/Skeleton';
import { ErrorState } from '@/components/shared/ErrorState';
import { ApiError, apiFetch } from '@/lib/api';
import { useAuthStore } from '@/lib/auth';
import { useTranslation } from '@/lib/i18n';

interface PolicyPayload {
  min_dqi: number;
  block_if_critical_pii: boolean;
  is_active: boolean;
  critical_cap?: number;
  dimension_thresholds?: Record<string, number>;
  dimension_weights?: Record<string, number>;
}

const DIMENSION_FIELDS = [
  { key: 'structure_validity', labelKey: 'policies.dimStructure' },
  { key: 'cleanliness_privacy', labelKey: 'policies.dimCleanliness' },
  { key: 'uniqueness_diversity', labelKey: 'policies.dimUniqueness' },
  { key: 'instruction_quality', labelKey: 'policies.dimInstruction' },
  { key: 'safety_bias', labelKey: 'policies.dimSafety' },
] as const;

export function PoliciesPage() {
  const { accessToken, activeWorkspaceId } = useAuthStore();
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [minDqi, setMinDqi] = useState(60);
  const [criticalCap, setCriticalCap] = useState(60);
  const [dimThresholds, setDimThresholds] = useState<Record<string, number>>({});
  const [dimWeights, setDimWeights] = useState<Record<string, number>>({});
  const [blockPii, setBlockPii] = useState(true);
  const [isActive, setIsActive] = useState(true);
  const [saved, setSaved] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['policy', activeWorkspaceId],
    enabled: Boolean(activeWorkspaceId && accessToken),
    queryFn: () =>
      apiFetch<PolicyPayload>(
        `/workspaces/${activeWorkspaceId}/policy`,
        { method: 'GET' },
        accessToken,
      ),
  });

  useEffect(() => {
    if (data) {
      setMinDqi(data.min_dqi);
      setBlockPii(data.block_if_critical_pii);
      setIsActive(data.is_active);
      setCriticalCap(data.critical_cap ?? 60);
      setDimThresholds(data.dimension_thresholds ?? {});
      setDimWeights(data.dimension_weights ?? {});
    }
  }, [data]);

  const saveMutation = useMutation({
    mutationFn: () =>
      apiFetch<PolicyPayload>(
        `/workspaces/${activeWorkspaceId}/policy`,
        {
          method: 'PUT',
          body: JSON.stringify({
            min_dqi: minDqi,
            block_if_critical_pii: blockPii,
            is_active: isActive,
            critical_cap: criticalCap,
            dimension_thresholds: dimThresholds,
            dimension_weights: dimWeights,
          }),
        },
        accessToken,
      ),
    onSuccess: () => {
      setSaveError(null);
      setSaved(true);
      void queryClient.invalidateQueries({ queryKey: ['policy', activeWorkspaceId] });
    },
    onError: (err) => {
      setSaved(false);
      setSaveError(err instanceof ApiError ? err.message : t('policies.saveError'));
    },
  });

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setSaved(false);
    setSaveError(null);
    saveMutation.mutate();
  }

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold text-text-primary">{t('policies.title')}</h1>

      {isLoading && <Skeleton className="h-48 max-w-xl" />}
      {error && (
        <ErrorState
          message={error instanceof ApiError ? error.message : t('policies.loadError')}
          onRetry={() => refetch()}
        />
      )}

      {!isLoading && !error && (
        <Card className="max-w-xl">
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div>
                <label htmlFor="min-dqi" className="mb-1 block text-sm font-medium text-text-primary">
                  {t('policies.minDqiLabel')}
                </label>
                <Input
                  id="min-dqi"
                  type="number"
                  min={0}
                  max={100}
                  value={minDqi}
                  onChange={(e) => setMinDqi(Number(e.target.value))}
                />
              </div>

              <div>
                <label
                  htmlFor="critical-cap"
                  className="mb-1 block text-sm font-medium text-text-primary"
                >
                  {t('policies.criticalCapLabel')}
                </label>
                <Input
                  id="critical-cap"
                  type="number"
                  min={0}
                  max={100}
                  value={criticalCap}
                  onChange={(e) => setCriticalCap(Number(e.target.value))}
                />
              </div>
            </div>

            <div className="space-y-2 rounded-control border border-border bg-subtle p-3">
              <p className="text-xs font-semibold text-text-primary">
                {t('policies.dimThresholdsTitle')}
              </p>
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                {DIMENSION_FIELDS.map(({ key, labelKey }) => (
                  <div key={key}>
                    <label
                      htmlFor={`dim-${key}`}
                      className="mb-1 block text-xs text-text-secondary"
                    >
                      {t(labelKey)}
                    </label>
                    <Input
                      id={`dim-${key}`}
                      type="number"
                      min={0}
                      max={100}
                      value={dimThresholds[key] ?? 0}
                      onChange={(e) =>
                        setDimThresholds((prev) => ({
                          ...prev,
                          [key]: Number(e.target.value),
                        }))
                      }
                    />
                  </div>
                ))}
              </div>
            </div>

            <label className="flex items-center gap-2 text-sm text-text-primary">
              <input
                type="checkbox"
                checked={blockPii}
                onChange={(e) => setBlockPii(e.target.checked)}
              />
              <span>{t('policies.blockPiiLabel')}</span>
            </label>

            <label className="flex items-center gap-2 text-sm text-text-primary">
              <input
                type="checkbox"
                checked={isActive}
                onChange={(e) => setIsActive(e.target.checked)}
              />
              <span>{t('policies.activeLabel')}</span>
            </label>

            <Button type="submit" disabled={saveMutation.isPending}>
              {t('policies.saveButton')}
            </Button>
            {saved && <p className="text-xs text-success-600">{t('policies.savedMessage')}</p>}
            {saveError && (
              <p role="alert" className="text-xs text-danger-600">
                {saveError}
              </p>
            )}
          </form>
        </Card>
      )}
    </div>
  );
}
