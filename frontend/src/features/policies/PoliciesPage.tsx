import { useState, type FormEvent } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { Input } from '@/components/ui/Input';
import { apiFetch } from '@/lib/api';
import { useAuthStore } from '@/lib/auth';

interface PolicyPayload {
  min_dqi: number;
  block_if_critical_pii: boolean;
  is_active: boolean;
}

export function PoliciesPage() {
  const { accessToken, activeWorkspaceId } = useAuthStore();
  const [minDqi, setMinDqi] = useState(60);
  const [blockPii, setBlockPii] = useState(true);
  const [saved, setSaved] = useState(false);

  useQuery({
    queryKey: ['policy', activeWorkspaceId],
    enabled: Boolean(activeWorkspaceId && accessToken),
    queryFn: async () => {
      const res = await apiFetch<PolicyPayload>(
        `/workspaces/${activeWorkspaceId}/policy`,
        { method: 'GET' },
        accessToken,
      );
      setMinDqi(res.min_dqi);
      setBlockPii(res.block_if_critical_pii);
      return res;
    },
  });

  const saveMutation = useMutation({
    mutationFn: () =>
      apiFetch<PolicyPayload>(
        `/workspaces/${activeWorkspaceId}/policy`,
        {
          method: 'PUT',
          body: JSON.stringify({
            min_dqi: minDqi,
            block_if_critical_pii: blockPii,
            is_active: true,
          }),
        },
        accessToken,
      ),
    onSuccess: () => setSaved(true),
  });

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setSaved(false);
    saveMutation.mutate();
  }

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold text-text-primary">Kebijakan Kualitas (Quality Gate)</h1>
      <Card className="max-w-lg">
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label htmlFor="min-dqi" className="mb-1 block text-sm font-medium text-text-primary">
              Ambang Batas Minimum DQI (0–100)
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

          <label className="flex items-center gap-2 text-sm text-text-primary">
            <input
              type="checkbox"
              checked={blockPii}
              onChange={(e) => setBlockPii(e.target.checked)}
            />
            <span>Blokir ekspor bila masih ada PII mentah (kritis)</span>
          </label>

          <Button type="submit" disabled={saveMutation.isPending}>
            Simpan Kebijakan
          </Button>
          {saved && <p className="text-xs text-success-600">Kebijakan berhasil disimpan.</p>}
        </form>
      </Card>
    </div>
  );
}
