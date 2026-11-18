import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ScoreCard } from '@/components/shared/ScoreCard';

describe('ScoreCard', () => {
  it('renders DQI score, partial badge, critical cap badge, and dimension breakdown', () => {
    render(
      <ScoreCard
        dqiTotal={60.0}
        statusLabel="Perlu Perbaikan"
        isPartial={true}
        criticalCapped={true}
        dimensions={[
          {
            dimension: 'cleanliness_privacy',
            label: 'Kebersihan & Privasi',
            score: 50.0,
            weight: 0.4167,
            contribution: 20.8,
            findings_count: 2,
          },
        ]}
      />,
    );

    expect(screen.getByText('60.0')).toBeInTheDocument();
    expect(screen.getByText('Perlu Perbaikan')).toBeInTheDocument();
    expect(screen.getByText('Skor Parsial')).toBeInTheDocument();
    expect(screen.getByText(/Dibatasi Penalti Kritis/i)).toBeInTheDocument();
    expect(screen.getByText(/Kebersihan & Privasi/i)).toBeInTheDocument();
  });
});
