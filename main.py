"""
WCHS-IST Cloud Service Entrypoint (Azure App Service Linux)
- Starts HTTP health/API server on PORT (for Azure probes and Mini App).
- Starts 24/7 Telegram Bot loop in background worker thread.
"""

import json
import os
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
import bot

PORT = int(os.environ.get("PORT", 8080))

class CloudHealthHandler(BaseHTTPRequestHandler):
    def _send_json(self, status_code, data):
        try:
            body = json.dumps(data, default=str, ensure_ascii=False).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            print(f"[!] HTTP send error: {e}", flush=True)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path in ["/", "/health", "/status"]:
            now_ist = bot.get_now_ist()
            resp = {
                "status": "online",
                "version": "2026-09-14-gate-flow-v2",
                "service": "WCHS-IST Transfer Radar (Azure Cloud)",
                "istTime": now_ist.strftime("%Y-%m-%d %H:%M:%S"),
                "botUser": "@wchs_bot",
                "cachedFlights": len(bot.cached_flights.get("arrivals", [])),
                "userGate": bot.CONFIG.get("user_gate", "F3")
            }
            self._send_json(200, resp)
            return

        elif path.startswith("/api/radar"):
            now_ist, arrs, deps, src = bot.fetch_iga_direct_flights()
            self._send_json(200, {
                "time": now_ist.strftime("%H:%M"),
                "source": src,
                "arrivalsCount": len(arrs),
                "departuresCount": len(deps),
                "arrivals": arrs[:20],
                "departures": deps[:20]
            })
            return

        elif path.startswith("/api/gate/"):
            flight_num = path.split("/api/gate/")[1].strip().upper().replace(" ", "")
            now_ist, arrs, deps, src = bot.fetch_iga_direct_flights()
            target = None
            for f in arrs + deps:
                if f.get("flight_no", "").replace(" ", "").upper() == flight_num:
                    target = f
                    break
            if target:
                self._send_json(200, {"success": True, "flight": target})
            else:
                self._send_json(404, {"success": False, "error": f"Flight {flight_num} not found"})
            return

        else:
            self._send_json(200, {
                "service": "WCHS Radar Cloud API",
                "endpoints": ["/health", "/api/radar", "/api/gate/{flightNumber}"]
            })

    def log_message(self, format, *args):
        # Silence standard HTTP access logging to prevent noisy stdout
        pass

def run_http_server():
    server = HTTPServer(("0.0.0.0", PORT), CloudHealthHandler)
    print(f"[*] Azure HTTP Probe Server listening on port {PORT}...", flush=True)
    server.serve_forever()

if __name__ == "__main__":
    print("[*] Starting WCHS-IST Unified Cloud Daemon on Azure App Service...", flush=True)
    
    # 1. Start Telegram Bot polling in daemon thread
    bot_thread = threading.Thread(target=bot.run_bot_loop, daemon=True, name="TelegramBotWorker")
    bot_thread.start()
    print("[*] Telegram Bot background thread started.", flush=True)

    # 2. Start HTTP server on main thread to satisfy Azure App Service ping
    run_http_server()
