from prometheus_client import Counter

requests_total = Counter(
    "maxopenballoon_requests_total",
    "Total requests by service",
    ["service", "route"],
)
