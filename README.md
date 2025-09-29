# API Gateway Project Structure

Nice — we’ll do this step-by-step. I’ll drop the first step now: requirements + a top-level architecture (who talks to who, and why). When you type **next** I’ll move to the next step (Load Balancers & Traffic Routing). Ready? Here we go.

# Step 1 — Goals & high-level architecture (the map)

**Primary constraint:** handle **100k+ RPS** for APIs (stateless endpoints, JSON over HTTPS).
Secondary goals: low p99 latency, easy horizontal scaling, strong observability, graceful zero-downtime deploys.

## Core design decisions (short version)

* Push as much static content / cacheable responses to a **CDN** so origin RPS drops.
* Put a globally-distributed **Layer-4/Anycast load-balancer** in front for fast routing and TLS handoff.
* Use a cluster of **edge reverse-proxies / API gateway** instances (Envoy recommended) that handle TLS termination, route requests, apply local rate-limits and call out to a global rate-limit service when needed. ([Envoy Proxy][1])
* Keep the gateway **stateless** — business logic runs in backend microservices behind the gateway. State lives in Redis/Postgres, not in the gateway processes.
* For distributed rate-limits use a **local token-bucket** at the proxy plus a **global coordinator** (Redis-backed counters or dedicated rate-limit service) to enforce cross-instance limits. Token-bucket semantics are good for bursts. ([Redis][2])
* Tune TCP/TLS connection reuse (keep-alive / HTTP/2 where appropriate) to avoid handshake overhead at 100k RPS. Keep-alive massively reduces per-request latency and CPU cost. ([State Farm Engineering Blog][3])
* Deploy on orchestrator (Kubernetes) and use **graceful shutdown + connection draining** for zero-downtime rolling updates; consider Envoy/sidecar shutdown helpers to coordinate draining. ([Kubernetes][4])

## Textual “diagram” (call flow)

Client -> (CDN / edge cache) -> Global LB / Anycast -> Edge LB / TLS termination -> **API Gateway (Envoy cluster)** -> Auth service (introspection / token validation) + Rate-limit service (global) -> Internal services (via service mesh / direct) -> Database / Redis / caches

## Responsibilities (who does what)

* **CDN**: cache assets, edge caching of idempotent API responses, absorb bot/bad traffic.
* **Global LB / Anycast**: route users to nearest region, DDoS mitigation (via cloud provider or appliance).
* **Edge LB / TLS**: terminate TLS or forward TCP passthrough if you want mTLS deeper in.
* **API Gateway (Envoy)**: routing, local rate-limits, authentication checks (JWT validation or token introspection cache), request transformation, metrics/logging, quotas. Use local token-bucket to cheaply absorb bursts; call global rate-limit for strict cross-node enforcement. ([Envoy Proxy][1])
* **Rate-limit service**: strong source of truth for global quotas; backed by Redis or a purpose-built service. Use atomic ops/Lua scripts to keep it fast. ([Redis][2])
* **Auth service**: issue/validate JWTs, support OAuth2 flows and introspection for short-lived tokens; cache introspection results at the gateway to avoid blowups.
* **Backend services**: horizontally scaled, stateless, behind service mesh (optional) for finer traffic control.
* **Orchestrator (K8s)**: health checks, readiness probes, rolling updates, SIGTERM handling + connection draining to avoid dropped requests. ([Kubernetes][4])

---

That’s the map. Next I’d deep-dive into **Load Balancers & Traffic Routing** (how to design the global LB layer, Anycast vs Geo DNS, TCP vs TLS termination, keep-alive tuning) — and show exact Envoy/Nginx/K8s knobs you should tweak.

Type **next** when you want me to move on.

[1]: https://www.envoyproxy.io/docs/envoy/latest/configuration/http/http_filters/local_rate_limit_filter?utm_source=chatgpt.com "Local rate limit — envoy 1.36.0-dev-d51e17 documentation"
[2]: https://redis.io/glossary/rate-limiting/?utm_source=chatgpt.com "Rate Limiting"
[3]: https://engineering.statefarm.com/improving-api-performance-with-http-keepalive-571cc127aca5?utm_source=chatgpt.com "Improving API Performance with HTTP Keepalive"
[4]: https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/?utm_source=chatgpt.com "Pod Lifecycle"




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


