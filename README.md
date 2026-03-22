# Deadlock Lab (common issue seen in legacy monolithic app)

Mistral LLM is used for database deadlock detection, diagnosis, and remediation.
Full stack runs under Docker Compose.

## Current Tech Stack

| Service | Port | Purpose |
|---|---|---|
| PostgreSQL | 5432 | The database (Oracle stand-in) |
| Deadlock Generator | — | Creates deadlocks every 2 min |
| Deadlock Agent | 9090 | Detects, diagnoses, queues for approval |
| Approval Dashboard | 8080 | approve or dismiss fixes |
| Grafana | 3000 | Monitoring dashboard |
| Prometheus | 9091 | Metrics collector |
| PG Exporter | 9187 | PostgreSQL metrics → Prometheus |

## Prerequisites

- Docker Desktop 
- Ollama running locally with a model pulled:
  ```bash
  ollama pull mistral
  ollama server
  ollama list
  ```


#  (Optional) Set up Slack webhook in future




## Architecture

```
┌─────────────────────────────────────────────────────┐
│              Docker Compose (your Mac)               │
│                                                      │
│  ┌────────────┐    ┌────────────┐    ┌───────────┐  │
│  │ PostgreSQL │◄───│ Generator  │    │  Ollama   │  │
│  │   :5432    │    │ (broken    │    │ (on host) │  │
│  │            │    │  app)      │    │  :11434   │  │
│  └─────┬──────┘    └────────────┘    └─────┬─────┘  │
│        │                                   │        │
│        │ reads                    evidence  │        │
│        ▼                                   │        │
│  ┌─────────────────────────────────────────┘        │
│  │          Deadlock Agent :9090                     │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐         │
│  │  │ Detect   │→│ Diagnose │→│  Alert   │         │
│  │  │ (tools)  │ │ (LLM)    │ │ (Slack)  │         │
│  │  └──────────┘ └──────────┘ └──────────┘         │
│  │                    ↓ approval                     │
│  │              ┌──────────┐                         │
│  │              │   Fix    │→ pg_terminate_backend   │
│  │              └──────────┘                         │
│  └──────────────────┬───────────────────────────────│
│        │ metrics     │ metrics                       │
│        ▼             ▼                               │
│  ┌────────────┐ ┌────────────┐                      │
│  │ PG Export  │ │ Prometheus │                      │
│  │   :9187    │→│   :9091    │                      │
│  └────────────┘ └─────┬──────┘                      │
│                       ▼                              │
│                 ┌────────────┐  ┌────────────┐      │
│                 │  Grafana   │  │ Dashboard  │      │
│                 │   :3000    │  │   :8080    │      │
│                 └────────────┘  └────────────┘      │
└─────────────────────────────────────────────────────┘
```

## Teardown

```bash
docker compose down -v
```

## Files

```
postgres-deadlock-agent/
├── docker-compose.yml              # Full stack definition
├── .env.example                    # Slack webhook config
├── config/
│   └── init.sql                    # Database schema + seed data
├── generator/
│   ├── Dockerfile
│   └── app.py                      # Creates real deadlocks
├── agent/
│   ├── Dockerfile
│   └── app.py                      # The product: detect → diagnose → fix
├── dashboard/
│   ├── Dockerfile
│   └── app.py                      # Approval web UI
├── prometheus/
│   └── prometheus.yml              # Scrape config
└── grafana/
    └── provisioning/
        ├── datasources/
        │   └── prometheus.yml      # Auto-connect to Prometheus
        └── dashboards/
            ├── provider.yml
            └── deadlock-agent.json # Pre-built dashboard
```
