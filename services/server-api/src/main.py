from http.server import BaseHTTPRequestHandler, HTTPServer
import json

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        response = {"status": "ok", "message": "API stub is running"}
        self.wfile.write(json.dumps(response).encode("utf-8"))

    def log_message(self, format, *args):
        # Подавляем лишние логи в консоль для чистоты, но можно и оставить
        pass

def main():
    server_address = ("", 8000)
    httpd = HTTPServer(server_address, HealthCheckHandler)
    print("API stub server started on port 8000...")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    print("API stub server stopped.")

if __name__ == "__main__":
    main()
