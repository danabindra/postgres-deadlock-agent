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
        .event-card.fixed { border-left-color: #22c55e; opacity: 0.6; }
        .event-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
        .event-id { font-size: 18px; font-weight: 700; color: #f8fafc; }
        .event-time { color: #94a3b8; font-size: 13px; }

        .summary-banner {
            background: #1e3a5f; border: 1px solid #2563eb; border-radius: 8px;
            padding: 12px 16px; margin-bottom: 16px;
            font-size: 14px; font-weight: 600; color: #93c5fd;
        }

        .section { margin-bottom: 14px; }
        .section-label {
            font-size: 10px; font-weight: 700; text-transform: uppercase;
            letter-spacing: 0.08em; color: #64748b; margin-bottom: 6px;
        }
        .section-body {
            background: #0f172a; padding: 10px 14px; border-radius: 6px;
            font-size: 13px; line-height: 1.6; color: #cbd5e1; white-space: pre-wrap;
        }

        .fix-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 14px; }
        .fix-cell {
            background: #0f172a; padding: 10px 14px; border-radius: 6px;
        }
        .fix-cell-label {
            font-size: 10px; font-weight: 700; text-transform: uppercase;
            letter-spacing: 0.08em; margin-bottom: 4px;
        }
        .fix-cell-label.immediate { color: #f59e0b; }
        .fix-cell-label.structural { color: #818cf8; }
        .fix-cell-body { font-size: 13px; line-height: 1.5; color: #cbd5e1; white-space: pre-wrap; }
        .query-rewrite {
            background: #0f172a; padding: 10px 14px; border-radius: 6px;
            font-family: monospace; font-size: 12px; color: #86efac;
            white-space: pre-wrap; margin-bottom: 14px;
        }

        .confidence-badge {
            display: inline-block; padding: 2px 10px; border-radius: 999px;
            font-size: 11px; font-weight: 700; margin-bottom: 14px;
        }
        .confidence-HIGH { background: #14532d; color: #4ade80; }
        .confidence-MEDIUM { background: #451a03; color: #fb923c; }
        .confidence-LOW { background: #450a0a; color: #f87171; }

        .pid-info { color: #fbbf24; font-size: 13px; margin-bottom: 16px; }
        .btn-row { display: flex; gap: 12px; margin-top: 4px; }
        .btn {
            padding: 10px 24px; border: none; border-radius: 8px;
            font-size: 14px; font-weight: 600; cursor: pointer;
        }
        .btn-approve { background: #059669; color: white; }
        .btn-approve:hover { background: #047857; }
        .btn-dismiss { background: #374151; color: #94a3b8; }
        .btn-dismiss:hover { background: #4b5563; }
        .empty { text-align: center; padding: 60px; color: #64748b; font-size: 16px; }
        .pulse { animation: pulse 2s infinite; }
        @keyframes pulse { 0%,100% { opacity:1; } 50% { opacity:0.5; } }

        details summary {
            cursor: pointer; font-size: 12px; color: #64748b;
            margin-bottom: 6px; user-select: none;
        }
        details summary:hover { color: #94a3b8; }
    </style>
</head>
<body>
    <h1>Deadlock Agent Dashboard</h1>
    <p class="subtitle">Approval gate for database remediation</p>

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
        const AGENT_BASE = 'http://localhost:9090';

        function tag(xml, name) {
            const m = xml.match(new RegExp('<' + name + '[^>]*>([\\\\s\\\\S]*?)<\\/' + name + '>', 'i'));
            return m ? m[1].trim() : null;
        }

        function innerTag(xml, name) {
            const m = xml.match(new RegExp('<' + name + '[^>]*>([\\\\s\\\\S]*?)<\\/' + name + '>', 'i'));
            return m ? m[1].trim() : '';
        }

        function confidenceLevel(text) {
            if (!text) return 'LOW';
            const t = text.toUpperCase();
            if (t.startsWith('HIGH')) return 'HIGH';
            if (t.startsWith('MEDIUM')) return 'MEDIUM';
            return 'LOW';
        }

        function renderDiagnosis(diagnosis, blockingPid) {
            const summary = tag(diagnosis, 'deadlock_summary');

            if (!summary) {
                return `
                    <div class="section">
                        <div class="section-label">Diagnosis</div>
                        <div class="section-body">${diagnosis}</div>
                    </div>
                    <div class="pid-info">Blocking PID: ${blockingPid || 'N/A'}</div>`;
            }

            const fixXml      = tag(diagnosis, 'fix') || '';
            const immediate   = innerTag(fixXml, 'immediate');
            const structural  = innerTag(fixXml, 'structural');
            const queryRw     = innerTag(fixXml, 'query_rewrite');
            const rootCause   = tag(diagnosis, 'root_cause') || '';
            const cycle       = tag(diagnosis, 'cycle') || '';
            const blastRadius = tag(diagnosis, 'blast_radius') || '';
            const victim      = tag(diagnosis, 'victim') || '';
            const confidence  = tag(diagnosis, 'confidence') || 'LOW';
            const confLevel   = confidenceLevel(confidence);

            return `
                <div class="summary-banner">${summary}</div>

                <span class="confidence-badge confidence-${confLevel}">Confidence: ${confLevel}</span>

                <div class="fix-grid">
                    <div class="fix-cell">
                        <div class="fix-cell-label immediate">Immediate Action</div>
                        <div class="fix-cell-body">${immediate || 'N/A'}</div>
                    </div>
                    <div class="fix-cell">
                        <div class="fix-cell-label structural">Structural Fix</div>
                        <div class="fix-cell-body">${structural || 'N/A'}</div>
                    </div>
                </div>

                ${queryRw && queryRw !== 'N/A' ? `
                <div class="section">
                    <div class="section-label">Query Rewrite</div>
                    <div class="query-rewrite">${queryRw}</div>
                </div>` : ''}

                <div class="pid-info">Blocking PID: ${blockingPid || 'N/A'} &nbsp;|&nbsp; ${victim}</div>

                <details>
                    <summary>Root cause / cycle / blast radius</summary>
                    ${rootCause ? `<div class="section"><div class="section-label">Root Cause</div><div class="section-body">${rootCause}</div></div>` : ''}
                    ${cycle ? `<div class="section"><div class="section-label">Wait Cycle</div><div class="section-body">${cycle}</div></div>` : ''}
                    ${blastRadius ? `<div class="section"><div class="section-label">Blast Radius</div><div class="section-body">${blastRadius}</div></div>` : ''}
                </details>`;
        }

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
                        ${renderDiagnosis(e.diagnosis, e.blocking_pid)}
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
            document.getElementById('event-' + eventId).style.display = 'none';
        }

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
