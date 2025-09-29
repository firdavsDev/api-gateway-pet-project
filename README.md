# API Gateway Project Structure

## What
Demo gateway (FastAPI) + backend + auth + redis + locust load test.
Gateway enforces:
- JWT auth validation
- local token-bucket per-api_key
- global fixed-window rate limiting via Redis
- proxies to backend

## Run
1. Build & start everything:
   docker-compose up --build

2. Visit:
   - Gateway: http://localhost:8080/health
   - Backend: http://localhost:8000/health
   - Auth: http://localhost:8001/health
   - Locust UI: http://localhost:8089

3. In Locust UI:
   - Set number of users (e.g., 200) and spawn rate.
   - Start swarming to hit /api/resource via gateway.
   - Watch RPS, response time, failures (429 limit responses).

## Useful envs in docker-compose:
- GLOBAL_RATE: global Redis per-second limit (demo). Lower to see 429s.
- LOCAL_RATE: local bucket rate per process.

## Notes
This is a demo. For production:
- Use sliding-window or Redis token-bucket with LUA for global limiter.
- Use Envoy/NGINX at edge instead of Python for TLS & performance.
- Add tracing, metrics exporter, healthchecks, sigterm graceful draining.

```bash
api-gateway-project/
├── docker-compose.yml                # Compose to run redis, auth, backend, gateway, locust
├── README.md                         # Run & test notes
├── gateway/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── main.py                       # FastAPI gateway: auth middleware, local token-bucket, Redis global limiter, proxy to backend
├── services/
│   ├── backend/
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   └── app.py                     # simple backend /api/resource + health
│   └── auth/
│       ├── Dockerfile
│       ├── requirements.txt
│       └── auth_service.py            # simple token issuer (JWT) + introspect/health
├── infra/
│   └── (optional) envoy/              # optional Envoy config if you later swap Python gateway for Envoy
│       └── envoy.yaml
└── locust/
    ├── Dockerfile
    ├── requirements.txt
    └── locustfile.py                  # locust scenarios hitting gateway
```




[1]: https://www.envoyproxy.io/docs/envoy/latest/configuration/http/http_filters/local_rate_limit_filter?utm_source=chatgpt.com "Local rate limit — envoy 1.36.0-dev-d51e17 documentation"
[2]: https://redis.io/glossary/rate-limiting/?utm_source=chatgpt.com "Rate Limiting"
[3]: https://engineering.statefarm.com/improving-api-performance-with-http-keepalive-571cc127aca5?utm_source=chatgpt.com "Improving API Performance with HTTP Keepalive"
[4]: https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/?utm_source=chatgpt.com "Pod Lifecycle"

