#!/usr/bin/env python3
"""Compatibility launcher; use album_server.py for new installations."""

import albummaker.server as _impl

globals().update({name: value for name, value in vars(_impl).items()
                  if not name.startswith("__")})

if __name__ == "__main__":
    _impl.main()
