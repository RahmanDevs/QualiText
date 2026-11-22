import { ShieldAlert } from 'lucide-react';

const PII_TOKEN_RE =
  /(\[(?:NIK|NPWP|TELEPON|EMAIL|REKENING|KARTU|NAMA|ALAMAT|PII_[A-Z_]+:[0-9a-f]+)\])/g;
const PII_TOKEN_EXACT_RE =
  /^\[(?:NIK|NPWP|TELEPON|EMAIL|REKENING|KARTU|NAMA|ALAMAT|PII_[A-Z_]+:[0-9a-f]+)\]$/;

interface HighlightedTextProps {
  text: string;
  className?: string;
}

export function HighlightedText({ text, className = '' }: HighlightedTextProps) {
  if (!text) {
    return <span className="text-text-muted">—</span>;
  }

  const parts = text.split(PII_TOKEN_RE);
  return (
    <span className={`whitespace-pre-wrap break-words font-mono text-xs ${className}`}>
      {parts.map((part, idx) => {
        const isToken = PII_TOKEN_EXACT_RE.test(part);
        if (!isToken) {
          return <span key={idx}>{part}</span>;
        }
        return (
          <mark
            key={idx}
            className="mx-0.5 inline-flex items-center gap-1 rounded-control border-b-2 border-danger-600 bg-subtle px-1 font-semibold text-danger-600"
          >
            <ShieldAlert className="h-3 w-3 shrink-0" aria-hidden="true" />
            <span>{part}</span>
          </mark>
        );
      })}
    </span>
  );
}
