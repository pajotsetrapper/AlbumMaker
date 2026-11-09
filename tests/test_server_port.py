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


if __name__ == "__main__":
    unittest.main()
