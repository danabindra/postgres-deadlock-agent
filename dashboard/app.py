"""
Approval Dashboard: Approval Gate

A simple web page that shows pending deadlock events and
lets the operator approve or dismiss fixes.


"""

import os
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler

logging.basicConfig(level=logging.INFO, format="%(asctime)s [DASHBOARD] %(message)s")
log = logging.getLogger(__name__)

AGENT_URL = "http://agent:9090"

HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Deadlock Agent - Approval Dashboard</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', system-ui, sans-serif;
            background: #0f172a;
            color: #e2e8f0;
            padding: 24px;
        }
        h1 { color: #f8fafc; margin-bottom: 8px; font-size: 24px; }
        .subtitle { color: #94a3b8; margin-bottom: 24px; font-size: 14px; }
        .status-bar {
            display: flex; gap: 24px; margin-bottom: 24px;
            padding: 16px; background: #1e293b; border-radius: 12px;
        }
        .status-item { text-align: center; }
        .status-value { font-size: 28px; font-weight: 700; color: #38bdf8; }
        .status-label { font-size: 11px; color: #94a3b8; text-transform: uppercase; }
        .event-card {
            background: #1e293b; border-radius: 12px; padding: 20px;
            margin-bottom: 16px; border-left: 4px solid #ef4444;
        }
        .event-card.fixed {
            border-left-color: #22c55e; opacity: 0.6;
        }
        .event-header { display: flex; justify-content: space-between; margin-bottom: 12px; }
        .event-id { font-size: 18px; font-weight: 700; color: #f8fafc; }
        .event-time { color: #94a3b8; font-size: 13px; }
        .diagnosis {
            background: #0f172a; padding: 12px; border-radius: 8px;
            font-size: 13px; line-height: 1.6; margin-bottom: 16px;
            color: #cbd5e1; white-space: pre-wrap;
        }
        .pid-info { color: #fbbf24; font-size: 13px; margin-bottom: 16px; }
        .btn-row { display: flex; gap: 12px; }
        .btn {
            padding: 10px 24px; border: none; border-radius: 8px;
            font-size: 14px; font-weight: 600; cursor: pointer;
        }
        .btn-approve { background: #059669; color: white; }
        .btn-approve:hover { background: #047857; }
        .btn-dismiss { background: #374151; color: #94a3b8; }
        .btn-dismiss:hover { background: #4b5563; }
        .empty {
            text-align: center; padding: 60px; color: #64748b; font-size: 16px;
        }
        .pulse { animation: pulse 2s infinite; }
        @keyframes pulse { 0%,100% { opacity:1; } 50% { opacity:0.5; } }
    </style>
</head>
<body>
    <h1>Deadlock Agent Dashboard</h1>
    <p class="subtitle">Approval  for database remediation</p>

    <div class="status-bar">
        <div class="status-item">
            <div class="status-value" id="pending-count">-</div>
            <div class="status-label">Pending Approval</div>
        </div>
        <div class="status-item">
            <div class="status-value" id="scan-status" class="pulse">●</div>
            <div class="status-label">Agent Status</div>
        </div>
    </div>

    <div id="events-container">
        <div class="empty">Waiting for deadlock events...</div>
    </div>

    <script>
        // Agent's metrics/pending endpoint (proxied via dashboard or direct)
        const AGENT_BASE = 'http://localhost:9090';

        async function fetchPending() {
            try {
                const res = await fetch(AGENT_BASE + '/pending');
                const events = await res.json();
                document.getElementById('pending-count').textContent = events.length;
                document.getElementById('scan-status').textContent = '● Online';
                document.getElementById('scan-status').style.color = '#22c55e';

                const container = document.getElementById('events-container');
                if (events.length === 0) {
                    container.innerHTML = '<div class="empty">No pending deadlocks. Agent is scanning...</div>';
                    return;
                }

                container.innerHTML = events.map(e => `
                    <div class="event-card" id="event-${e.event_id}">
                        <div class="event-header">
                            <span class="event-id">Event #${e.event_id}</span>
                            <span class="event-time">${e.detected_at}</span>
                        </div>
                        <div class="diagnosis">${e.diagnosis}</div>
                        <div class="pid-info">Blocking PID: ${e.blocking_pid || 'N/A'}</div>
                        <div class="btn-row">
                            <button class="btn btn-approve" onclick="approve(${e.event_id})">
                                Approve Fix 
                            </button>
                            <button class="btn btn-dismiss" onclick="dismiss(${e.event_id})">
                                Dismiss
                            </button>
                        </div>
                    </div>
                `).join('');

            } catch (err) {
                document.getElementById('scan-status').textContent = '● Offline';
                document.getElementById('scan-status').style.color = '#ef4444';
            }
        }

        async function approve(eventId) {
            const btn = event.target;
            btn.textContent = 'Executing fix...';
            btn.disabled = true;

            try {
                const res = await fetch(AGENT_BASE + '/approve/' + eventId);
                const data = await res.json();
                const card = document.getElementById('event-' + eventId);
                card.classList.add('fixed');
                card.querySelector('.btn-row').innerHTML =
                    '<span style="color:#22c55e;font-weight:700;">✓ Fix applied</span>';
            } catch (err) {
                btn.textContent = 'Error - retry';
                btn.disabled = false;
            }
        }

        function dismiss(eventId) {
            const card = document.getElementById('event-' + eventId);
            card.style.display = 'none';
        }

        // Poll every 3 seconds
        setInterval(fetchPending, 3000);
        fetchPending();
    </script>
</body>
</html>"""


class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(HTML_PAGE.encode())

    def log_message(self, format, *args):
        pass


def main():
    server = HTTPServer(("0.0.0.0", 8080), DashboardHandler)
    log.info("Dashboard running at http://localhost:8080")
    server.serve_forever()


if __name__ == "__main__":
    main()
