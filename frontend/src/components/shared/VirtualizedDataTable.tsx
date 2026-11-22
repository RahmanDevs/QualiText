import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Card } from '@/components/ui/Card';

export interface VirtualColumn<T> {
  key: string;
  header: ReactNode;
  className?: string;
  render: (row: T, index: number) => ReactNode;
}

interface VirtualizedDataTableProps<T> {
  rows: T[];
  getRowId: (row: T) => string;
  columns: VirtualColumn<T>[];
  density?: 'comfortable' | 'compact';
  maxHeight?: number;
  activeRowId?: string | null;
  onRowClick?: (row: T) => void;
  emptyText?: string;
}

export function VirtualizedDataTable<T>({
  rows,
  getRowId,
  columns,
  density = 'comfortable',
  maxHeight = 520,
  activeRowId,
  onRowClick,
  emptyText = '—',
}: VirtualizedDataTableProps<T>) {
  const [scrollTop, setScrollTop] = useState(0);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const rowHeight = density === 'compact' ? 32 : 40;
  const overscan = 6;
  const visibleCount = Math.ceil(maxHeight / rowHeight);
  const startIdx =
    rows.length === 0
      ? 0
      : Math.min(Math.max(0, rows.length - 1), Math.max(0, Math.floor(scrollTop / rowHeight) - overscan));
  const endIdx = Math.min(rows.length, startIdx + visibleCount + overscan * 2);
  const topPad = startIdx * rowHeight;
  const bottomPad = Math.max(0, (rows.length - endIdx) * rowHeight);
  const visibleRows = rows.slice(startIdx, endIdx);

  useEffect(() => {
    const maxScroll = Math.max(0, rows.length * rowHeight - maxHeight);
    if (scrollTop > maxScroll) {
      setScrollTop(0);
      if (containerRef.current) {
        containerRef.current.scrollTop = 0;
      }
    }
  }, [rows.length, rowHeight, maxHeight, scrollTop]);

  return (
    <Card className="p-0">
      <div
        ref={containerRef}
        className="overflow-auto"
        style={{ maxHeight: `${maxHeight}px` }}
        onScroll={(e) => setScrollTop(e.currentTarget.scrollTop)}
      >
        <table className="w-full border-collapse text-left text-xs">
          <thead className="sticky top-0 z-10 border-b border-border bg-subtle text-text-secondary">
            <tr>
              {columns.map((col) => (
                <th key={col.key} className={`px-3 py-2 font-medium ${col.className ?? ''}`}>
                  {col.header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {rows.length === 0 ? (
              <tr>
                <td colSpan={columns.length} className="p-6 text-center text-text-muted">
                  {emptyText}
                </td>
              </tr>
            ) : (
              <>
                {topPad > 0 && (
                  <tr aria-hidden="true">
                    <td colSpan={columns.length} style={{ height: `${topPad}px`, padding: 0 }} />
                  </tr>
                )}
                {visibleRows.map((row, relIdx) => {
                  const absIdx = startIdx + relIdx;
                  const rid = getRowId(row);
                  const isActive = activeRowId === rid;
                  return (
                    <tr
                      key={rid}
                      data-row-id={rid}
                      style={{ height: `${rowHeight}px` }}
                      onClick={() => onRowClick?.(row)}
                      className={`cursor-pointer transition-colors ${
                        isActive ? 'bg-brand-50' : 'hover:bg-subtle'
                      }`}
                    >
                      {columns.map((col) => (
                        <td
                          key={col.key}
                          className={`px-3 ${density === 'compact' ? 'py-1' : 'py-2'} ${
                            col.className ?? ''
                          }`}
                        >
                          {col.render(row, absIdx)}
                        </td>
                      ))}
                    </tr>
                  );
                })}
                {bottomPad > 0 && (
                  <tr aria-hidden="true">
                    <td colSpan={columns.length} style={{ height: `${bottomPad}px`, padding: 0 }} />
                  </tr>
                )}
              </>
            )}
          </tbody>
        </table>
      </div>
    </Card>
  );
}
