"""Prometheus metrics collector and OpenTelemetry tracer helpers."""

import threading
from collections import defaultdict

from opentelemetry import trace

_DURATION_BUCKETS: tuple[float, ...] = (0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)


def _escape_label_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


class MetricsCollector:
    """Thread-safe in-process Prometheus exposition metrics store."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._http_requests: dict[tuple[str, str, str], int] = defaultdict(int)
        self._http_duration_buckets: dict[tuple[str, str, str], int] = defaultdict(int)
        self._http_duration_sum: dict[tuple[str, str], float] = defaultdict(float)
        self._http_duration_count: dict[tuple[str, str], int] = defaultdict(int)
        self._quality_runs: dict[str, int] = defaultdict(int)
        self._quality_run_duration_sum = 0.0
        self._quality_run_duration_count = 0

    def record_http_request(
        self, method: str, path: str, status_code: int, duration_seconds: float
    ) -> None:
        """Record an HTTP request count and latency histogram observation."""
        status_str = str(status_code)
        dur = max(0.0, float(duration_seconds))
        with self._lock:
            self._http_requests[(method, path, status_str)] += 1
            for bound in _DURATION_BUCKETS:
                if dur <= bound:
                    self._http_duration_buckets[(method, path, str(bound))] += 1
            self._http_duration_buckets[(method, path, "+Inf")] += 1
            self._http_duration_sum[(method, path)] += dur
            self._http_duration_count[(method, path)] += 1

    def record_quality_run(self, status: str, duration_seconds: float) -> None:
        """Record a completed/failed quality assessment run and its duration."""
        dur = max(0.0, float(duration_seconds))
        with self._lock:
            self._quality_runs[status] += 1
            self._quality_run_duration_sum += dur
            self._quality_run_duration_count += 1

    def reset(self) -> None:
        """Reset all counters and histograms (primarily for unit tests)."""
        with self._lock:
            self._http_requests.clear()
            self._http_duration_buckets.clear()
            self._http_duration_sum.clear()
            self._http_duration_count.clear()
            self._quality_runs.clear()
            self._quality_run_duration_sum = 0.0
            self._quality_run_duration_count = 0

    def render(self) -> str:
        """Render all metrics in Prometheus text exposition format 0.0.4."""
        lines: list[str] = [
            "# HELP qualitext_http_requests_total Total HTTP requests processed by QualiText API.",
            "# TYPE qualitext_http_requests_total counter",
        ]
        with self._lock:
            for (method, path, status_str), count in sorted(self._http_requests.items()):
                lines.append(
                    "qualitext_http_requests_total"
                    f'{{method="{_escape_label_value(method)}",'
                    f'path="{_escape_label_value(path)}",'
                    f'status="{_escape_label_value(status_str)}"}} {count}'
                )

            lines.extend(
                [
                    (
                        "# HELP qualitext_http_request_duration_seconds "
                        "HTTP request duration in seconds."
                    ),
                    "# TYPE qualitext_http_request_duration_seconds histogram",
                ]
            )
            for method, path in sorted(self._http_duration_count.keys()):
                m_esc = _escape_label_value(method)
                p_esc = _escape_label_value(path)
                for bound in _DURATION_BUCKETS:
                    le = str(bound)
                    b_count = self._http_duration_buckets.get((method, path, le), 0)
                    lines.append(
                        "qualitext_http_request_duration_seconds_bucket"
                        f'{{method="{m_esc}",path="{p_esc}",le="{le}"}} {b_count}'
                    )
                inf_count = self._http_duration_buckets.get((method, path, "+Inf"), 0)
                lines.append(
                    "qualitext_http_request_duration_seconds_bucket"
                    f'{{method="{m_esc}",path="{p_esc}",le="+Inf"}} {inf_count}'
                )
                lines.append(
                    "qualitext_http_request_duration_seconds_sum"
                    f'{{method="{m_esc}",path="{p_esc}"}} '
                    f"{self._http_duration_sum[(method, path)]:.6f}"
                )
                lines.append(
                    "qualitext_http_request_duration_seconds_count"
                    f'{{method="{m_esc}",path="{p_esc}"}} '
                    f"{self._http_duration_count[(method, path)]}"
                )

            lines.extend(
                [
                    "# HELP qualitext_quality_runs_total Total quality assessment runs by status.",
                    "# TYPE qualitext_quality_runs_total counter",
                ]
            )
            for run_status, count in sorted(self._quality_runs.items()):
                lines.append(
                    f'qualitext_quality_runs_total{{status="{_escape_label_value(run_status)}"}} '
                    f"{count}"
                )

            lines.extend(
                [
                    (
                        "# HELP qualitext_quality_run_duration_seconds "
                        "Total quality run execution duration."
                    ),
                    "# TYPE qualitext_quality_run_duration_seconds summary",
                    (
                        "qualitext_quality_run_duration_seconds_sum "
                        f"{self._quality_run_duration_sum:.6f}"
                    ),
                    (
                        "qualitext_quality_run_duration_seconds_count "
                        f"{self._quality_run_duration_count}"
                    ),
                ]
            )
        return "\n".join(lines) + "\n"


_collector = MetricsCollector()


def record_http_request(method: str, path: str, status_code: int, duration_seconds: float) -> None:
    """Record an HTTP observation in the global Prometheus metrics collector."""
    _collector.record_http_request(method, path, status_code, duration_seconds)


def record_quality_run(status: str, duration_seconds: float) -> None:
    """Record a quality run completion/failure observation."""
    _collector.record_quality_run(status, duration_seconds)


def render_prometheus_metrics() -> str:
    """Return Prometheus exposition payload for `/metrics`."""
    return _collector.render()


def reset_metrics() -> None:
    """Reset the global metrics collector."""
    _collector.reset()


def get_tracer(name: str = "qualitext") -> trace.Tracer:
    """Return an OpenTelemetry tracer instance."""
    return trace.get_tracer(name)
