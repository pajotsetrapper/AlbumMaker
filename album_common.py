"""
album_common.py: compatibility shim.

The shared code now lives in the albummaker package (see albummaker/__init__.py
for what is where; the CSS, JavaScript and page templates are real files in
albummaker/static and albummaker/templates). This module re-exports the names it
used to define, so existing imports keep working. New code should import from
albummaker.* directly. Note that assigning to a name here (e.g. in a test)
does not change the albummaker module that actually uses it.
"""

from albummaker.cli import *        # noqa: F401,F403
from albummaker.cli import _parse_ymd  # noqa: F401
from albummaker.content import *    # noqa: F401,F403
from albummaker.content import _day_entry, _text  # noqa: F401
from albummaker.imaging import *    # noqa: F401,F403
from albummaker.metadata import *   # noqa: F401,F403
from albummaker.metadata import _gps_from_exif, _parse_iso_datetime, _to_deg  # noqa: F401
from albummaker.model import *      # noqa: F401,F403
from albummaker.model import route_distance_km as _route_distance_km  # noqa: F401
from albummaker.offline import *    # noqa: F401,F403
from albummaker.offline import (_LEAFLET_FILES, _NPM_REGISTRY, _VENDOR_FONT_SUBSETS,  # noqa: F401
                              _VENDOR_FONT_WEIGHTS, _VENDOR_PACKAGES, _download_package, _font_faces)
from albummaker.render import *     # noqa: F401,F403
from albummaker.render import _JINJA, _day_ranks, _day_title  # noqa: F401
from albummaker.render import WORLD_LAND_JSON as _WORLD_LAND_JSON  # noqa: F401
from albummaker.scan import *       # noqa: F401,F403
from albummaker.util import *       # noqa: F401,F403
