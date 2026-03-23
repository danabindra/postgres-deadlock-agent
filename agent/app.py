"""
Deadlock Agent: Detection, Diagnosis, and Remediation

The full observe-reason-act loop applied to database deadlocks.

OBSERVE: Poll PostgreSQL diagnostic views every N seconds.
         Detect deadlocks via pg_locks + pg_stat_activity.
         Collect blocking sessions, SQL statements, lock graph.

REASON:  Send all collected evidence to Ollama (local LLM).
         Receive a plain-English diagnosis with root cause.

ACT:     Send Slack alert with diagnosis.
         Log the event to the deadlock_events table.
         Expose Prometheus metrics for Grafana.
         Wait for approval, then execute pg_terminate_backend().
"""

import os
import time
import json
import logging
import threading
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

import psycopg2
import psycopg2.extras
import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [AGENT] %(message)s",
)
log = logging.getLogger(__name__)

# CONFIGURATION
DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", "5432")),
    "dbname": os.getenv("DB_NAME", "demoapp"),
    "user": os.getenv("DB_USER", "app"),
    "password": os.getenv("DB_PASS", "apppass"),
}
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "mistral")
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "15"))
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")
DASHBOARD_URL = os.getenv("DASHBOARD_URL", "http://localhost:8080")

# PROMETHEUS METRICS (simple counters exposed via HTTP)
metrics = {
    "deadlocks_detected_total": 0,
    "deadlocks_diagnosed_total": 0,
    "deadlocks_fixed_total": 0,
    "deadlocks_pending_approval": 0,
    "scans_total": 0,
    "last_scan_epoch": 0,
    "last_deadlock_epoch": 0,
    "agent_uptime_seconds": 0,
}
agent_start_time = time.time()

# Pending approvals: event_id -> event data
pending_approvals = {}


class MetricsHandler(BaseHTTPRequestHandler):
    """Serves Prometheus metrics at /metrics."""

    def do_GET(self):
        if self.path == "/metrics":
            metrics["agent_uptime_seconds"] = int(time.time() - agent_start_time)
            metrics["deadlocks_pending_approval"] = len(pending_approvals)

            # Format as Prometheus text exposition
            lines = []
            for key, val in metrics.items():
                lines.append(f"deadlock_agent_{key} {val}")
            body = "\n".join(lines) + "\n"

            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(body.encode())

        elif self.path == "/pending":
            # Return pending approvals as JSON (dashboard polls this)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(list(pending_approvals.values())).encode())

        elif self.path.startswith("/approve/"):
            # Approve a pending fix
            event_id = self.path.split("/approve/")[1]
            try:
                event_id = int(event_id)
                if event_id in pending_approvals:
                    event = pending_approvals.pop(event_id)
                    threading.Thread(
                        target=execute_fix,
                        args=(event,),
                    ).start()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    self.wfile.write(json.dumps({"status": "approved", "event_id": event_id}).encode())
                else:
                    self.send_response(404)
                    self.end_headers()
                    self.wfile.write(b'{"error": "not found"}')
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        """Suppress default HTTP logging."""
        pass


def start_metrics_server():
    """Run the metrics/approval HTTP server in a background thread."""
    server = HTTPServer(("0.0.0.0", 9090), MetricsHandler)
    log.info("Metrics server started on :9090")
    server.serve_forever()


# TOOL CHAIN (OBSERVE PHASE)
# Deterministic. Same queries every time. No variation.

def get_db_connection():
    """Create a database connection."""
    return psycopg2.connect(**DB_CONFIG)


def tool_check_deadlocks() -> dict:
    """
    Tool 1: Check for recent deadlock events in PostgreSQL logs.
    Queries pg_stat_activity for sessions in deadlock-related states.
    """
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # Check for blocked sessions (indicators of lock contention)
    cur.execute("""
        SELECT
            pid,
            usename,
            state,
            wait_event_type,
            wait_event,
            query,
            query_start,
            NOW() - query_start AS duration
        FROM pg_stat_activity
        WHERE wait_event_type = 'Lock'
          AND state = 'active'
          AND pid != pg_backend_pid()
        ORDER BY query_start
    """)
    blocked = cur.fetchall()

# Check if any current queries mention deadlock-related waits
    cur.execute("""
        SELECT
            pid,
            wait_event_type,
            wait_event,
            state,
            query
        FROM pg_stat_activity
        WHERE wait_event_type = 'Lock'
           OR query ILIKE '%deadlock%'
        LIMIT 5
    """)

    cur.close()
    conn.close()

    return {
        "tool_name": "check_deadlocks",
        "description": "Scanned pg_stat_activity for blocked sessions and lock waits",
        "blocked_sessions": [dict(r) for r in blocked],
        "blocked_count": len(blocked),
        "success": True,
    }


def tool_get_lock_graph() -> dict:
    """
    Tool 2: Build the lock dependency graph.
    Shows who is blocking whom and on which tables.
    """
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("""
        SELECT
            blocked_locks.pid AS blocked_pid,
            blocked_activity.usename AS blocked_user,
            blocked_activity.query AS blocked_query,
            blocking_locks.pid AS blocking_pid,
            blocking_activity.usename AS blocking_user,
            blocking_activity.query AS blocking_query,
            blocked_locks.locktype,
            blocked_locks.relation::regclass AS locked_table
        FROM pg_catalog.pg_locks blocked_locks
        JOIN pg_catalog.pg_stat_activity blocked_activity
            ON blocked_activity.pid = blocked_locks.pid
        JOIN pg_catalog.pg_locks blocking_locks
            ON blocking_locks.locktype = blocked_locks.locktype
            AND blocking_locks.database IS NOT DISTINCT FROM blocked_locks.database
            AND blocking_locks.relation IS NOT DISTINCT FROM blocked_locks.relation
            AND blocking_locks.page IS NOT DISTINCT FROM blocked_locks.page
            AND blocking_locks.tuple IS NOT DISTINCT FROM blocked_locks.tuple
            AND blocking_locks.virtualxid IS NOT DISTINCT FROM blocked_locks.virtualxid
            AND blocking_locks.transactionid IS NOT DISTINCT FROM blocked_locks.transactionid
            AND blocking_locks.classid IS NOT DISTINCT FROM blocked_locks.classid
            AND blocking_locks.objid IS NOT DISTINCT FROM blocked_locks.objid
            AND blocking_locks.objsubid IS NOT DISTINCT FROM blocked_locks.objsubid
            AND blocking_locks.pid != blocked_locks.pid
        JOIN pg_catalog.pg_stat_activity blocking_activity
            ON blocking_activity.pid = blocking_locks.pid
        WHERE NOT blocked_locks.granted
            AND blocked_locks.pid != pg_backend_pid()
    """)
    lock_graph = cur.fetchall()

    cur.close()
    conn.close()

    return {
        "tool_name": "get_lock_graph",
        "description": "Built lock dependency graph from pg_locks showing who blocks whom",
        "lock_graph": [dict(r) for r in lock_graph],
        "conflict_count": len(lock_graph),
        "success": True,
    }


def tool_get_session_details(pids: list) -> dict:
    """
    Tool 3: Get detailed info about the involved sessions.
    """
    if not pids:
        return {
            "tool_name": "get_session_details",
            "description": "No PIDs to look up",
            "sessions": [],
            "success": True,
        }

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("""
        SELECT
            pid,
            usename,
            application_name,
            client_addr,
            state,
            query,
            query_start,
            xact_start,
            wait_event_type,
            wait_event,
            NOW() - xact_start AS transaction_age
        FROM pg_stat_activity
        WHERE pid = ANY(%s)
    """, (pids,))
    sessions = cur.fetchall()

    cur.close()
    conn.close()

    return {
        "tool_name": "get_session_details",
        "description": f"Retrieved session details for PIDs: {pids}",
        "sessions": [dict(s) for s in sessions],
        "success": True,
    }


def tool_check_deadlock_history() -> dict:
    """
    Tool 4: Check how many deadlocks we've seen before.
    Queries our own tracking table.
    """
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("""
        SELECT
            COUNT(*) as total_deadlocks,
            COUNT(*) FILTER (WHERE detected_at > NOW() - INTERVAL '1 hour') as last_hour,
            COUNT(*) FILTER (WHERE detected_at > NOW() - INTERVAL '24 hours') as last_24h,
            MAX(detected_at) as most_recent
        FROM deadlock_events
    """)
    history = dict(cur.fetchone())

    cur.execute("""
        SELECT table_a, table_b, COUNT(*) as occurrences
        FROM deadlock_events
        WHERE detected_at > NOW() - INTERVAL '24 hours'
        GROUP BY table_a, table_b
        ORDER BY occurrences DESC
        LIMIT 5
    """)
    patterns = [dict(r) for r in cur.fetchall()]

    cur.close()
    conn.close()

    return {
        "tool_name": "check_deadlock_history",
        "description": "Queried deadlock event history for recurring patterns",
        "history": history,
        "patterns": patterns,
        "success": True,
    }


# LLM REASONING (REASON PHASE)

def reason_over_evidence(tool_results: list) -> str:
    """Send all tool output to Ollama for diagnosis."""
    evidence = []
    for r in tool_results:
        evidence.append(
            f"## {r['tool_name']}\n"
            f"{r['description']}\n"
            f"```json\n{json.dumps(r, indent=2, default=str)}\n```"
        )

    prompt = f"""You are a database diagnostic agent analyzing a PostgreSQL deadlock.
The following evidence was collected from pg_stat_activity and pg_locks.
Respond using ONLY the XML structure below. Do not include any text outside the tags.

# Evidence

{chr(10).join(evidence)}

# Required Response Format

<deadlock_summary>
One sentence: which transactions deadlocked and on which resources.
</deadlock_summary>

<transactions>
For each session involved provide a txn block:
  <txn id="[pid]">
    <operation>SELECT/UPDATE/DELETE/INSERT and the target table</operation>
    <locks_held>Lock type and resource currently held</locks_held>
    <locks_waiting>Lock type and resource it is blocked on</locks_waiting>
    <query>The SQL statement if available</query>
    <session_info>PID, user, application name</session_info>
  </txn>
</transactions>

<cycle>
The circular wait chain using actual PIDs and table names from the evidence.
</cycle>

<root_cause>
Why this deadlock formed. Name the specific pattern: lock ordering violation,
escalation conflict, index contention, foreign key cascade, or application anti-pattern.
Reference specific PIDs and tables.
</root_cause>

<victim>
Which session was chosen as the deadlock victim and the recommended session to terminate.
</victim>

<fix>
  <immediate>What to do right now to unblock the situation.</immediate>
  <structural>Code or schema changes that eliminate this class of deadlock.</structural>
  <query_rewrite>Corrected SQL or transaction ordering if applicable.</query_rewrite>
</fix>

<blast_radius>
What else this deadlock affects: blocked sessions, downstream timeouts, connection pool pressure.
</blast_radius>

<confidence>
HIGH, MEDIUM, or LOW. If not HIGH, list what additional information would raise it.
</confidence>"""

    try:
        response = requests.post(
            OLLAMA_URL,
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.2, "num_predict": 1024},
            },
            timeout=120,
        )
        if response.status_code == 200:
            return response.json().get("response", "No response from model")
        return f"Ollama error: {response.status_code}"
    except requests.ConnectionError:
        return "Could not connect to Ollama. Is it running?"
    except Exception as e:
        return f"LLM error: {e}"


# ALERTING (Slack)

def send_slack_alert(event_id: int, diagnosis: str, lock_data: dict):
    """Post a rich alert to Slack with the diagnosis."""
    if not SLACK_WEBHOOK_URL:
        log.info("No Slack webhook configured, skipping alert")
        return

    blocked_count = lock_data.get("conflict_count", 0)
    approval_url = f"http://localhost:9090/approve/{event_id}"

    payload = {
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "🔴 Deadlock Detected", "emoji": True},
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Blocked Sessions:* {blocked_count}\n*Event ID:* {event_id}\n*Time:* {datetime.now().strftime('%H:%M:%S')}",
                },
            },
            {"type": "divider"},
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*AI Diagnosis:*\n{diagnosis[:500]}",
                },
            },
            {"type": "divider"},
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Approve fix:* `GET {approval_url}`\n*Dashboard:* http://localhost:8080",
                },
            },
        ],
    }

    try:
        requests.post(SLACK_WEBHOOK_URL, json=payload, timeout=10)
        log.info("Slack alert sent")
    except Exception as e:
        log.error(f"Slack alert failed: {e}")


# FIX EXECUTION (ACT PHASE - only after approval)

def execute_fix(event: dict):
    """
    Terminate the blocking session. Only runs after
    human approval via the dashboard or Slack.
    """
    blocking_pid = event.get("blocking_pid")
    event_id = event.get("event_id")

    if not blocking_pid:
        log.warning(f"Event {event_id}: no blocking PID to terminate")
        return

    log.info(f"Event {event_id}: EXECUTING FIX - terminating PID {blocking_pid}")

    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT pg_terminate_backend(%s)", (blocking_pid,))
        result = cur.fetchone()
        conn.commit()

        if result and result[0]:
            log.info(f"Event {event_id}: PID {blocking_pid} terminated successfully")
            # Update the event record
            cur.execute(
                "UPDATE deadlock_events SET status='fixed', fixed_at=NOW() WHERE event_id=%s",
                (event_id,),
            )
            conn.commit()
            metrics["deadlocks_fixed_total"] += 1
        else:
            log.warning(f"Event {event_id}: PID {blocking_pid} already gone")

        cur.close()
        conn.close()

    except Exception as e:
        log.error(f"Event {event_id}: fix failed - {e}")


# MAIN SCAN LOOP

def log_deadlock_event(diagnosis: str, lock_data: dict) -> int:
    """Record the deadlock in our tracking table, return event_id."""
    conn = get_db_connection()
    cur = conn.cursor()

    # Extract PIDs and tables from the lock graph
    session_a = None
    session_b = None
    table_a = None
    table_b = None

    if lock_data.get("lock_graph"):
        first = lock_data["lock_graph"][0]
        session_a = first.get("blocked_pid")
        session_b = first.get("blocking_pid")
        table_a = str(first.get("locked_table", ""))
        table_b = table_a  # simplified

    cur.execute(
        """INSERT INTO deadlock_events (session_a, session_b, table_a, table_b, diagnosis, status)
           VALUES (%s, %s, %s, %s, %s, 'diagnosed') RETURNING event_id""",
        (session_a, session_b, table_a, table_b, diagnosis),
    )
    event_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()

    return event_id

# Track the last known deadlock count
last_deadlock_count = 0

def scan():
    """
    One scan cycle. Check if new deadlocks have occurred
    since our last scan using pg_stat_database counters.
    """
    global last_deadlock_count
    metrics["scans_total"] += 1
    metrics["last_scan_epoch"] = int(time.time())

    # Check PostgreSQL's deadlock counter
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT deadlocks FROM pg_stat_database
        WHERE datname = %s
    """, (DB_CONFIG["dbname"],))
    row = cur.fetchone()
    current_count = row[0] if row else 0
    cur.close()
    conn.close()

    if last_deadlock_count == 0:
        # First scan, just record the baseline
        last_deadlock_count = current_count
        log.info(f"Baseline deadlock count: {current_count}")
        return

    if current_count <= last_deadlock_count:
        return  # No new deadlocks since last scan

    new_deadlocks = current_count - last_deadlock_count
    last_deadlock_count = current_count

    log.info(f"NEW DEADLOCKS DETECTED: {new_deadlocks} (total: {current_count})")
    metrics["deadlocks_detected_total"] += new_deadlocks
    metrics["last_deadlock_epoch"] = int(time.time())

    # Now run the full diagnostic chain
    # Tool 1: Check for any currently blocked sessions
    deadlock_result = tool_check_deadlocks()

    # Tool 2: Build the lock graph
    lock_result = tool_get_lock_graph()

    # Tool 3: Get session details for involved PIDs
    pids = set()
    for entry in lock_result.get("lock_graph", []):
        pids.add(entry.get("blocked_pid"))
        pids.add(entry.get("blocking_pid"))
    session_result = tool_get_session_details(list(pids))

    # Tool 4: Check deadlock history
    history_result = tool_check_deadlock_history()

    # REASON: Send all evidence to the LLM
    all_results = [deadlock_result, lock_result, session_result, history_result]
    log.info("Sending evidence to LLM for diagnosis...")
    diagnosis = reason_over_evidence(all_results)
    metrics["deadlocks_diagnosed_total"] += 1

    log.info(f"DIAGNOSIS:\n{diagnosis}\n")

    # Log the event
    event_id = log_deadlock_event(diagnosis, lock_result)

    # Determine blocking PID for the fix
    blocking_pid = None
    if lock_result.get("lock_graph"):
        blocking_pid = lock_result["lock_graph"][0].get("blocking_pid")

    # Queue for approval
    event_data = {
        "event_id": event_id,
        "blocking_pid": blocking_pid,
        "diagnosis": diagnosis,
        "lock_data": lock_result,
        "detected_at": datetime.now().isoformat(),
    }
    pending_approvals[event_id] = event_data
    log.info(f"Event {event_id} queued for approval (PID {blocking_pid})")

    # Send Slack alert
    send_slack_alert(event_id, diagnosis, lock_result)


def main():
    """Main agent loop."""
    log.info("=" * 55)
    log.info("  DEADLOCK DIAGNOSTIC AGENT")
    log.info(f"  Database: {DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['dbname']}")
    log.info(f"  LLM: {OLLAMA_MODEL} via {OLLAMA_URL}")
    log.info(f"  Scan interval: {SCAN_INTERVAL}s")
    log.info(f"  Slack: {'configured' if SLACK_WEBHOOK_URL else 'not configured'}")
    log.info("=" * 55)

    # Start the metrics/approval HTTP server
    threading.Thread(target=start_metrics_server, daemon=True).start()

    # Wait for database to be ready
    time.sleep(5)

    while True:
        try:
            scan()
        except Exception as e:
            log.error(f"Scan error: {e}")

        time.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    main()
