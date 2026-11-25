#!/usr/bin/env python3
"""Local browser interface for the DSA Huffman compressor."""

from __future__ import annotations

import json
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote

from huffman_compressor import compress_file, decompress_file, default_output_path


HOST = "127.0.0.1"
PORT = 8000
PAGE = Path(__file__).with_name("index.html")


class HuffmanRequestHandler(BaseHTTPRequestHandler):
    server_version = "HuffmanDSA/1.0"

    def do_GET(self) -> None:
        if self.path not in ("/", "/index.html"):
            self.send_error(404, "Page not found")
            return
        try:
            page = PAGE.read_bytes()
        except OSError:
            self.send_error(500, "Could not read index.html")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(page)))
        self.end_headers()
        self.wfile.write(page)

    def do_POST(self) -> None:
        if self.path not in ("/api/compress", "/api/decompress"):
            self._send_json(404, {"error": "Unknown operation."})
            return

        try:
            length = int(self.headers.get("Content-Length", "-1"))
            if length < 0:
                raise ValueError("Choose a file before starting.")
            source_bytes = self.rfile.read(length)
            if len(source_bytes) != length:
                raise ValueError("The upload ended before the whole file arrived.")

            uploaded_name = unquote(self.headers.get("X-File-Name", "upload.txt"))
            # Keep only a filename; uploaded paths must never choose server paths.
            safe_name = Path(uploaded_name.replace("\\", "/")).name or "upload.txt"

            with tempfile.TemporaryDirectory(prefix="huffman-web-") as temp_name:
                folder = Path(temp_name)
                source = folder / safe_name
                source.write_bytes(source_bytes)

                if self.path == "/api/compress":
                    result = folder / (safe_name + ".huf")
                    input_size, output_size, method = compress_file(source, result)
                    operation = "compressed"
                else:
                    result = default_output_path(source)
                    _, output_size = decompress_file(source, result)
                    input_size = len(source_bytes)
                    method = "decompressed"
                    operation = "decompressed"

                result_bytes = result.read_bytes()
                self._send_file(
                    result_bytes,
                    result.name,
                    input_size,
                    output_size,
                    operation,
                    method,
                )
        except (OSError, ValueError) as error:
            self._send_json(400, {"error": str(error)})

    def _send_file(
        self,
        data: bytes,
        filename: str,
        input_size: int,
        output_size: int,
        operation: str,
        method: str,
    ) -> None:
        encoded_name = quote(filename, safe="")
        fallback_name = filename.encode("ascii", "replace").decode("ascii").replace('"', "_")
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header(
            "Content-Disposition",
            f'attachment; filename="{fallback_name}"; filename*=UTF-8\'\'{encoded_name}',
        )
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Input-Bytes", str(input_size))
        self.send_header("X-Output-Bytes", str(output_size))
        self.send_header("X-Operation", operation)
        self.send_header("X-Method", method)
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, status: int, body: dict[str, str]) -> None:
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {format % args}")


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), HuffmanRequestHandler)
    print(f"Huffman Compressor is running at http://{HOST}:{PORT}")
    print("Press Ctrl+C to stop the server.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Huffman Compressor.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
