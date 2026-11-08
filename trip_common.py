"""Compatibility import; new code should import from albummaker."""

import album_common as _impl

globals().update({name: value for name, value in vars(_impl).items()
                  if not name.startswith("__")})
