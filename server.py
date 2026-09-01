import json
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from iga_client import IGAClient

client = IGAClient()

class IGAHandler(BaseHTTPRequestHandler):
    def _set_headers(self, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if path.startswith("/api/gate/"):
            flight_num = path.split("/api/gate/")[1].strip()
            flight = client.get_flight_gate(flight_num)
            if flight:
                self._set_headers(200)
                resp = {
                    "success": True,
                    "flightNumber": flight.flight_number,
                    "gate": flight.gate,
                    "hasGate": flight.has_gate,
                    "counter": flight.counter,
                    "status": flight.status,
                    "airline": flight.airline_name,
                    "destination": flight.to_city,
                    "scheduledTime": flight.scheduled_datetime,
                    "data": flight.to_dict()
                }
                self.wfile.write(json.dumps(resp, ensure_ascii=False).encode("utf-8"))
            else:
                self._set_headers(404)
                self.wfile.write(json.dumps({"success": False, "error": f"Flight '{flight_num}' not found"}, ensure_ascii=False).encode("utf-8"))
            return

        elif path == "/api/search":
            q = query.get("q", [""])[0]
            nature = int(query.get("nature", ["1"])[0])
            limit = int(query.get("limit", ["20"])[0])
            flights = client.search(q, nature=nature, limit=limit)
            self._set_headers(200)
            resp = {
                "success": True,
                "count": len(flights),
                "flights": [f.to_dict() for f in flights]
            }
            self.wfile.write(json.dumps(resp, ensure_ascii=False).encode("utf-8"))
            return

        elif path == "/api/departures":
            limit = int(query.get("limit", ["50"])[0])
            flights = client.fetch_flights(nature=1, page_size=limit)
            self._set_headers(200)
            self.wfile.write(json.dumps({"success": True, "count": len(flights), "flights": [f.to_dict() for f in flights]}, ensure_ascii=False).encode("utf-8"))
            return

        else:
            self._set_headers(200)
            self.wfile.write(json.dumps({
                "name": "IGA Istanbul Airport Flight & Gate API",
                "endpoints": [
                    "GET /api/gate/{flightNumber} - Get gate info for a specific flight",
                    "GET /api/search?q={searchTerm}&nature=1 - Search flights",
                    "GET /api/departures?limit=50 - Live departures board"
                ]
            }, ensure_ascii=False).encode("utf-8"))

def run_server(port=8080):
    server = HTTPServer(("0.0.0.0", port), IGAHandler)
    print(f"[*] IGA Flight API Server listening on http://localhost:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[!] Server shutting down.")

if __name__ == "__main__":
    run_server()
