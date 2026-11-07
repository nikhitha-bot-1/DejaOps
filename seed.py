R, B, S = "Restart service", "Roll back last deploy", "Scale up replicas"
RC, CB, ML = "Renew certificate", "Enable circuit breaker", "Raise memory limit"

FAMILIES = {
    "pool":    {"name": "DB connection pool exhaustion", "kw": ["connection pool", "pool exhausted", "too many connections"], "fixes": [R, B, S]},
    "memleak": {"name": "Memory leak after deploy",      "kw": ["oomkilled", "out of memory", "heap"],                       "fixes": [R, B, ML]},
    "tls":     {"name": "Expired TLS certificate",       "kw": ["certificate", "tls handshake", "x509"],                     "fixes": [R, RC, B]},
    "timeout": {"name": "Upstream timeout cascade",      "kw": ["504", "upstream timeout", "gateway timeout"],               "fixes": [R, CB, S]},
}

# (days_ago, service, family, alert, [(fix, worked), ...])
SEED = [
    (41, "payments-api", "pool", "connection pool exhausted (50/50), p99 latency 8.2s", [(R, False), (B, True)]),
    (33, "orders-svc", "pool", "too many connections on orders-db, 5xx at 14%", [(R, False), (S, False), (B, True)]),
    (27, "checkout", "memleak", "OOMKilled pod restarts x6, heap growing 40MB/min", [(R, False), (B, True)]),
    (22, "auth-gateway", "tls", "x509 certificate has expired, TLS handshake failures", [(R, False), (RC, True)]),
    (18, "search-api", "timeout", "504 upstream timeout from ranking-svc, queue depth 900", [(R, False), (CB, True)]),
    (12, "payments-api", "pool", "connection pool exhausted after v2.14 deploy", [(B, True)]),
    (9, "inventory", "memleak", "out of memory, container OOMKilled again", [(B, True)]),
    (5, "edge-proxy", "timeout", "gateway timeout 504 spike to billing", [(CB, True)]),
]
