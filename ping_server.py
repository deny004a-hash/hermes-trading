"""Health / ping server — Railway / Render uyumluluk."""
import os
from http.server import HTTPServer, BaseHTTPRequestHandler
PORT = int(os.environ.get("PORT", 10000))
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type","text/plain")
        self.end_headers()
        self.wfile.write(b"Railway-paper-mode OK\n")
    def log_message(self,*a): pass
if __name__=="__main__":
    HTTPServer(("0.0.0.0",PORT),H).serve_forever()
