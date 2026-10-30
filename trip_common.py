"""
trip_common.py: compatibility shim.

The shared code now lives in the tripsite package (see tripsite/__init__.py
for what is where; the CSS, JavaScript and page templates are real files in
tripsite/static and tripsite/templates). This module re-exports the names it
used to define, so existing imports keep working. New code should import from
tripsite.* directly. Note that assigning to a name here (e.g. in a test)
does not change the tripsite module that actually uses it.
"""

from tripsite.cli import *        # noqa: F401,F403
from tripsite.cli import _parse_ymd  # noqa: F401
from tripsite.content import *    # noqa: F401,F403
from tripsite.content import _day_entry, _text  # noqa: F401
from tripsite.imaging import *    # noqa: F401,F403
from tripsite.metadata import *   # noqa: F401,F403
from tripsite.metadata import _gps_from_exif, _parse_iso_datetime, _to_deg  # noqa: F401
from tripsite.model import *      # noqa: F401,F403
from tripsite.model import route_distance_km as _route_distance_km  # noqa: F401
from tripsite.offline import *    # noqa: F401,F403
from tripsite.offline import (_LEAFLET_FILES, _NPM_REGISTRY, _VENDOR_FONT_SUBSETS,  # noqa: F401
                              _VENDOR_FONT_WEIGHTS, _VENDOR_PACKAGES, _download_package, _font_faces)
from tripsite.render import *     # noqa: F401,F403
from tripsite.render import _JINJA, _day_ranks, _day_title  # noqa: F401
from tripsite.render import WORLD_LAND_JSON as _WORLD_LAND_JSON  # noqa: F401
from tripsite.scan import *       # noqa: F401,F403
from tripsite.util import *       # noqa: F401,F403
