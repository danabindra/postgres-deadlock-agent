# Deadlock Demo Lab

AI-powered database deadlock detection, diagnosis, and remediation.
Full stack runs on your MacBook via Docker Compose.

## What's Running

| Service | Port | Purpose |
|---|---|---|
| PostgreSQL | 5432 | The database (Oracle stand-in) |
| Deadlock Generator | — | Creates real deadlocks every 2 min |
| Deadlock Agent | 9090 | Detects, diagnoses, queues for approval |
| Approval Dashboard | 8080 | Human gate: approve or dismiss fixes |
| Grafana | 3000 | Visual monitoring dashboard |
| Prometheus | 9091 | Metrics collection |
| PG Exporter | 9187 | PostgreSQL metrics → Prometheus |

## Prerequisites

- Docker Desktop running on your Mac
- Ollama running locally with a model pulled:
  ```bash
  ollama pull mistral
  ```

## Quick Start

```bash
# 1. Clone/copy the project
cd deadlock-demo

# 2. (Optional) Set up Slack webhook
cp .env.example .env
# Edit .env and paste your Slack webhook URL

# 3. Start everything
docker compose up --build -d

# 4. Wait ~30 seconds for all services to start
docker compose logs -f agent
# Wait until you see "DEADLOCK DIAGNOSTIC AGENT" banner
# Press Ctrl+C to exit log view

# 5. Open your browser tabs:
#    Grafana:   http://localhost:3000  (admin / demo)
#    Dashboard: http://localhost:8080
```

## Demo Script (5 Minutes)

### Minute 1: "Here's the environment"
- Show Grafana at http://localhost:3000
  - Login: admin / demo
  - Navigate to "Deadlock Agent Dashboard"
- Point out: database is running, agent is scanning, zero deadlocks so far

### Minute 2: "Watch a deadlock happen"
- The generator creates one every ~2 minutes automatically
- Or force one immediately:
  ```bash
  docker exec deadlock-demo-generator-1 python -c "
  from app import create_deadlock
  create_deadlock()
  "
  ```
- Grafana: "Deadlocks Detected" counter ticks up

### Minute 3: "The agent caught it"
- Show the agent logs:
  ```bash
  docker compose logs -f agent
  ```
- You'll see the tool chain running: check_deadlocks, get_lock_graph,
  get_session_details, check_deadlock_history
- Then "Sending evidence to LLM for diagnosis..."
- Then the full diagnosis prints

### Minute 4: "I got an alert"
- Show Slack channel (if configured) — alert with diagnosis
- Open http://localhost:8080 — the approval dashboard
- The diagnosis is there with an "Approve Fix" button
- Grafana: "Pending Approval" gauge shows 1

### Minute 5: "One click and it's fixed"
- Click "Approve Fix" on the dashboard
- Agent executes pg_terminate_backend()
- Dashboard shows "Fix applied"
- Grafana: "Fixed" counter ticks up, "Pending" drops to 0
- Done. No SSH. No manual SQL. No context switching.

## For the Customer

"Everything you just saw runs against PostgreSQL because
it's free and we can demo it anywhere. For your Oracle
environment, we swap the database driver and the SQL
dialect. The agent architecture is identical. pg_locks
becomes V$LOCK. pg_terminate_backend becomes ALTER SYSTEM
KILL SESSION. Same pattern, same approval flow, same
Grafana dashboard. That's the contract engagement."

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
deadlock-demo/
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
