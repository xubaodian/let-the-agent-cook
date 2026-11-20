import argparse
import json
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer


class RegistrationHandler(SimpleHTTPRequestHandler):
    def do_POST(self) -> None:
        if self.path != "/api/register":
            self.send_error(404)
            return
        try:
            size = int(self.headers.get("Content-Length", 0))
            if not 1 <= size <= 4096:
                raise ValueError
            email = json.loads(self.rfile.read(size))["email"]
            if not isinstance(email, str) or "@" not in email:
                raise ValueError
        except (ValueError, KeyError, TypeError):
            self.reply(400, False, "Enter a valid email.")
            return
        if email.casefold() == "capacity@example.test":
            self.reply(200, False, "The demo session is full.")
        else:
            self.reply(200, True, "Registration confirmed.")

    def reply(self, code: int, registered: bool, message: str) -> None:
        data = json.dumps({"registered": registered, "message": message}).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Serve the registration fixture and simulated API.")
    parser.add_argument("--port", type=int, default=8075)
    parser.add_argument("--directory", default=".")
    args = parser.parse_args()
    handler = partial(RegistrationHandler, directory=args.directory)
    with ThreadingHTTPServer(("127.0.0.1", args.port), handler) as server:
        server.serve_forever()
