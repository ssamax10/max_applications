import time

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

requests_total = Counter(
    'maxopenballoon_requests_total',
    'Total HTTP requests by service and route',
    ['service', 'route', 'method', 'status_code'],
)

request_latency_seconds = Histogram(
    'maxopenballoon_request_duration_seconds',
    'HTTP request latency in seconds',
    ['service', 'route', 'method'],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5),
)


class RequestTimer:
    def __init__(self) -> None:
        self._start = time.perf_counter()

    def elapsed_seconds(self) -> float:
        return time.perf_counter() - self._start


def metrics_payload() -> bytes:
    return generate_latest()


def metrics_content_type() -> str:
    return CONTENT_TYPE_LATEST
