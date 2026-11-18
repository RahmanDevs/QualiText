import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { App } from '@/app/App';

describe('QualiText Phase 0 Foundation UI', () => {
  it('renders Indonesian login screen by default and supports language & theme switching', () => {
    render(<App />);
    expect(screen.getByRole('heading', { name: 'Masuk ke QualiText' })).toBeInTheDocument();

    const langButton = screen.getByRole('button', { name: 'Bahasa' });
    fireEvent.click(langButton);
    expect(screen.getByRole('heading', { name: 'Sign in to QualiText' })).toBeInTheDocument();

    const themeButton = screen.getByRole('button', { name: /Mode/i });
    fireEvent.click(themeButton);
    expect(document.documentElement.classList.contains('dark')).toBe(true);
  });
});
