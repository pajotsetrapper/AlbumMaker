# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Pieter Remi Coppens
import socket
import unittest

from albummaker.server import RequestHandler, TripHTTPServer


class PortSharingTests(unittest.TestCase):
    def test_second_server_cannot_share_a_listening_port(self):
        """On Windows a second server used to bind a port another one was using,
        so the browser kept reaching the old server."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as first:
            first.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            first.bind(("127.0.0.1", 0))
            first.listen()
            port = first.getsockname()[1]
            with self.assertRaises(OSError):
                TripHTTPServer(("127.0.0.1", port), RequestHandler, None).server_close()



class ConnectionLimitTests(unittest.TestCase):
    def test_connections_over_the_limit_are_closed_and_slots_come_back(self):
        import threading
        import time

        class Server(TripHTTPServer):
            max_connections = 2

        server = Server(("127.0.0.1", 0), RequestHandler, None)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        port = server.server_address[1]
        idle = [socket.create_connection(("127.0.0.1", port)) for _ in range(2)]   # never send anything
        time.sleep(0.3)
        extra = socket.create_connection(("127.0.0.1", port))
        extra.settimeout(5)
        self.assertEqual(extra.recv(1), b"", "the third connection is closed at once")
        extra.close()
        for s in idle:
            s.close()
        time.sleep(0.3)
        again = socket.create_connection(("127.0.0.1", port))   # slots are free again
        again.settimeout(0.5)
        with self.assertRaises(socket.timeout):
            again.recv(1)                                   # kept open, waiting for a request
        again.close()


class PortArgumentTests(unittest.TestCase):
    def test_only_real_port_numbers_are_accepted(self):
        import contextlib
        import io
        from albummaker.server import build_parser
        parser = build_parser()
        self.assertEqual(parser.parse_args(["-i", ".", "--port", "8080"]).port, 8080)
        for bad in ("0", "70000", "-1", "http"):
            with self.subTest(bad), contextlib.redirect_stderr(io.StringIO()) as err, \
                    self.assertRaises(SystemExit):
                parser.parse_args(["-i", ".", "--port", bad])
            self.assertIn("--port", err.getvalue())


if __name__ == "__main__":
    unittest.main()
