import { useEffect, useRef } from 'react';
import {
  Chart,
  BarController, LineController, PieController,
  CategoryScale, LinearScale, PointElement, LineElement,
  BarElement, ArcElement,
  Tooltip, Legend, Title,
} from 'chart.js';

// Register only what we use (keeps bundle small)
Chart.register(
  BarController, LineController, PieController,
  CategoryScale, LinearScale, PointElement, LineElement,
  BarElement, ArcElement,
  Tooltip, Legend, Title,
);

// Arvind Fashions palette
const PALETTE = [
  '#dc2626', '#2563eb', '#10b981', '#f59e0b',
  '#8b5cf6', '#06b6d4', '#f97316', '#14b8a6',
];

export interface ChartDataPayload {
  chart_type: string;
  title: string;
  labels?: string[];
  datasets?: Array<{
    label: string;
    data: number[];
    backgroundColor?: string | string[];
    borderColor?: string;
    borderWidth?: number;
    fill?: boolean;
    tension?: number;
  }>;
  columns?: string[];
  rows?: Array<unknown[]>;
}

interface Props {
  data: ChartDataPayload;
}

export function ChartView({ data }: Props) {
  if (data.chart_type === 'table') {
    return <TableView data={data} />;
  }
  return <CanvasChart data={data} />;
}

// ── Canvas chart (bar / line / pie) ───────────────────────────────────────────
function CanvasChart({ data }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const chartRef = useRef<Chart | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    // Destroy previous instance
    chartRef.current?.destroy();

    const isPie = data.chart_type === 'pie';
    const datasets = (data.datasets ?? []).map((ds, i) => ({
      ...ds,
      backgroundColor: isPie ? PALETTE : PALETTE[i % PALETTE.length],
      borderColor: isPie ? '#fff' : PALETTE[i % PALETTE.length],
      borderWidth: isPie ? 2 : 2,
      tension: 0.3,
      fill: false,
      pointRadius: 4,
      pointHoverRadius: 6,
    }));

    chartRef.current = new Chart(canvas, {
      type: data.chart_type as 'bar' | 'line' | 'pie',
      data: {
        labels: data.labels ?? [],
        datasets,
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: {
            display: isPie,
            labels: { color: '#374151', font: { family: 'Inter, Segoe UI, sans-serif', size: 12 } },
          },
          title: {
            display: true,
            text: data.title,
            color: '#1f2937',
            font: { family: 'Inter, Segoe UI, sans-serif', size: 13, weight: 'normal' as const },
            padding: { bottom: 12 },
          },
          tooltip: {
            callbacks: {
              label: (ctx) => {
                const val = ctx.parsed.y ?? ctx.parsed;
                return ` ${ctx.dataset.label}: ${Number(val).toLocaleString('en-IN')}`;
              },
            },
          },
        },
        scales: isPie ? {} : {
          x: {
            ticks: { color: '#6b7280', font: { size: 11 }, maxRotation: 45 },
            grid: { color: '#f3f4f6' },
          },
          y: {
            ticks: {
              color: '#6b7280',
              font: { size: 11 },
              callback: (v) => Number(v).toLocaleString('en-IN'),
            },
            grid: { color: '#f3f4f6' },
            beginAtZero: true,
          },
        },
      },
    });

    return () => { chartRef.current?.destroy(); };
  }, [data]);

  return (
    <div className="chart-container">
      <div style={{ height: 320, padding: '16px 16px 8px' }}>
        <canvas ref={canvasRef} />
      </div>
    </div>
  );
}

// ── Table view ────────────────────────────────────────────────────────────────
function TableView({ data }: Props) {
  const columns = data.columns ?? [];
  const rows = data.rows ?? [];

  return (
    <div className="chart-container">
      <div className="data-table-wrap" style={{ maxHeight: 280, overflow: 'auto' }}>
        <table className="data-table">
          <thead>
            <tr>
              {columns.map(col => <th key={col}>{col}</th>)}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={i}>
                {(row as unknown[]).map((val, j) => (
                  <td key={j}>
                    {typeof val === 'number'
                      ? val.toLocaleString('en-IN')
                      : String(val ?? '')}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
