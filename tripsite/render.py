"""The pages: Jinja templates, CSS and JavaScript (real files in
tripsite/templates and tripsite/static), and the data that fills them. Both
scripts render the same pages; a SiteUrls subclass decides where links point."""

from __future__ import annotations

import hashlib
from typing import Optional

from jinja2 import Environment, FunctionLoader, StrictUndefined  # noqa: F401
from markupsafe import Markup  # noqa: F401

from .content import _text, day_text, photo_entry, trip_title
from .model import DAY_COLORS, UNKNOWN_DAY_COLOR, DayGroup, MediaItem
from .offline import DEFAULT_TILE_URL
from .scan import format_long_date
from .util import read_resource, script_json

CSS = read_resource("static", "style.css")
WORLD_LAND_JSON = read_resource("static", "world_land.json").strip()
JS = read_resource("static", "script.js").replace("__WORLD_LAND__", WORLD_LAND_JSON)
# changes whenever the CSS or JS does: the live server puts it in the asset
# URLs, so browsers can cache them for good and still never see a stale copy
ASSET_VERSION = hashlib.sha1((CSS + "\0" + JS).encode("utf-8")).hexdigest()[:12]


def _load_template(name: str):
    return read_resource("templates", name)


_JINJA = Environment(loader=FunctionLoader(_load_template), autoescape=True)
INDEX_TEMPLATE = _JINJA.get_template("index.html")
DAY_TEMPLATE = _JINJA.get_template("day.html")


class SiteUrls:
    """Where the pages link to. Each script subclasses this: generate_site
    links to files by relative path, trip_server to its own routes.
    `from_index` is True for links on the homepage rather than a day page,
    which matters for relative paths."""
    footer = ""     # the line at the bottom of every page
    edit_hint = ""  # completes "add one for <date> in trip_content.json..."
    tile_url = DEFAULT_TILE_URL   # what the maps ask for; {z}/{x}/{y} are filled in by Leaflet

    def vendor_url(self, from_index: bool) -> Optional[str]:
        """Folder (ending in /) holding local copies of Leaflet and the fonts,
        or None to use the copies on the internet."""
        return None

    def hi_thumb(self, item: MediaItem, from_index: bool) -> Optional[str]:
        """The larger thumbnail the page may swap in where a screen can use
        more pixels (L/XL sizes, high-resolution displays), or None when
        there is no larger version."""
        return None

    def home(self, from_index: bool) -> str:
        raise NotImplementedError

    def assets_prefix(self, from_index: bool) -> str:
        raise NotImplementedError

    def day(self, date_key: str, from_index: bool) -> str:
        raise NotImplementedError

    def thumb(self, item: MediaItem, from_index: bool) -> str:
        raise NotImplementedError

    def display(self, item: MediaItem) -> str:
        raise NotImplementedError

    def download(self, item: MediaItem) -> str:
        raise NotImplementedError

    def live(self, item: MediaItem) -> str:
        """URL of item.live_clip (only called when there is one)."""
        raise NotImplementedError

    def asset(self, name: str, from_index: bool) -> str:
        """URL of style.css / script.js."""
        return f"{self.assets_prefix(from_index)}assets/{name}"



def pick_cover(d: DayGroup, content: dict) -> MediaItem:
    """The day's cover: the one chosen in trip_content.json, else the first
    photo (not a video frame), else the first item."""
    entry = content.get("days", {}).get(d.date_key)
    want = entry.get("cover") if isinstance(entry, dict) else None
    if isinstance(want, str):
        for it in d.items:
            if want in it.content_keys:
                return it
    for it in d.items:
        if it.media_type == "image":
            return it
    return d.items[0]


def _day_ranks(days: list) -> dict:
    """1-based rank among *real* days only, so "Day 3 of 5" always means
    "day 3 of 5 actual trip days": the unknown-date bucket (if any)
    doesn't consume a number and isn't counted in that denominator."""
    ranks = {}
    for d in days:
        if not d.is_unknown:
            ranks[d.date_key] = len(ranks) + 1
    return ranks


def _day_title(d, content: dict, ranks: dict) -> tuple:
    """(title, description) for a day, with the default title filled in."""
    title, description = day_text(content, d.date_key)
    if d.is_unknown and not description:
        description = ("Photos and videos with no usable capture date, or with one that looked "
                       "wrong (the Details panel of each says which). They are kept together "
                       "here rather than being given a day they weren't taken on.")
    return title or ("Unsorted" if d.is_unknown else f"Day {ranks[d.date_key]}"), description


def build_index_context(days: list, content: dict, urls: SiteUrls) -> dict:
    """Template variables for INDEX_TEMPLATE."""
    ranks = _day_ranks(days)
    summaries = []
    for d in days:
        title, desc = _day_title(d, content, ranks)
        rank = ranks.get(d.date_key)
        cover = pick_cover(d, content) if d.items else None
        summaries.append({
            "href": urls.day(d.date_key, from_index=True),
            "date_display": "Date unknown" if d.is_unknown else format_long_date(d.date_obj),
            "title": title,
            "excerpt": (desc[:110] + "\u2026") if len(desc) > 110 else desc,
            "cover": urls.thumb(cover, from_index=True) if cover else None,
            "cover_hi": urls.hi_thumb(cover, from_index=True) if cover else None,
            "photo_count": d.photo_count,
            "video_count": d.video_count,
            "color": UNKNOWN_DAY_COLOR if d.is_unknown else DAY_COLORS[(rank - 1) % len(DAY_COLORS)],
            "route": d.route,
            "label": ("Unsorted" if d.is_unknown else f"Day {rank}") + f" \u2014 {d.date_key}",
            "is_unknown": d.is_unknown,
            "day_number": rank,
        })
    return dict(
        page_title=trip_title(content),
        home_href=urls.home(from_index=True),
        assets_prefix=urls.assets_prefix(from_index=True),
        css_href=urls.asset("style.css", from_index=True),
        js_href=urls.asset("script.js", from_index=True),
        vendor=urls.vendor_url(from_index=True),
        tile_url_json=script_json(urls.tile_url),
        footer=urls.footer,
        title=trip_title(content),
        subtitle=_text(content.get("subtitle")),
        day_count=len(ranks),
        photo_count=sum(d.photo_count for d in days),
        video_count=sum(d.video_count for d in days),
        distance_km=round(sum(d.distance_km for d in days), 1),
        days=summaries,
        days_json=script_json(summaries),
    )


def build_day_context(days: list, i: int, content: dict, urls: SiteUrls,
                      editable: bool = False) -> dict:
    """Template variables for DAY_TEMPLATE, for days[i]."""
    ranks = _day_ranks(days)
    d = days[i]
    title, desc = _day_title(d, content, ranks)

    items_ctx, slides, route_thumbs, route_slide_indices = [], [], [], []
    for idx, it in enumerate(d.items):
        pe = photo_entry(content, it)
        item_ctx = {"thumb_url": urls.thumb(it, from_index=False), "media_type": it.media_type,
                    "live": it.live_clip is not None, "caption": pe["caption"],
                    "favorite": pe["favorite"], "hidden": pe["hidden"],
                    "hi_url": urls.hi_thumb(it, from_index=False)}
        if not d.is_unknown:
            # only show a time badge when it's a real capture time: a
            # file-modified fallback time isn't worth presenting as fact
            item_ctx["time_display"] = it.dt.strftime("%H:%M")
        items_ctx.append(item_ctx)

        slide = {
            "type": it.media_type,
            "display": urls.display(it),
            "time": None if d.is_unknown else it.dt.isoformat(),
            "lat": it.lat,
            "lon": it.lon,
            "detail": it.detail,
            "downloadUrl": urls.download(it),
        }
        slide["key"] = it.key   # what #p=... in a link points at
        slide["caption"] = pe["caption"]
        slide["favorite"] = pe["favorite"]
        slide["hidden"] = pe["hidden"]
        if editable:
            slide["rel"] = it.rel   # what edits are saved against; visitors never get it
        if it.media_type == "video":
            slide["poster"] = urls.thumb(it, from_index=False)
        if it.live_clip is not None:
            slide["live"] = urls.live(it)
        slides.append(slide)
        if it.has_gps:
            route_thumbs.append(urls.thumb(it, from_index=False))
            # the day map's route only includes items with GPS, so its
            # index doesn't line up with the full slides list: this records
            # which slide each map point corresponds to, so clicking a
            # marker can open the right photo
            route_slide_indices.append(idx)

    prev_day = days[i - 1] if i > 0 else None
    next_day = days[i + 1] if i < len(days) - 1 else None
    prev_href = urls.day(prev_day.date_key, from_index=False) if prev_day else None
    next_href = urls.day(next_day.date_key, from_index=False) if next_day else None

    return dict(
        page_title=f"{title} \u2014 {trip_title(content)}",
        home_href=urls.home(from_index=False),
        home_href_json=script_json(urls.home(from_index=False)),
        assets_prefix=urls.assets_prefix(from_index=False),
        css_href=urls.asset("style.css", from_index=False),
        js_href=urls.asset("script.js", from_index=False),
        vendor=urls.vendor_url(from_index=False),
        tile_url_json=script_json(urls.tile_url),
        footer=urls.footer,
        edit_hint=urls.edit_hint,
        trip_title=trip_title(content),
        day_number=ranks.get(d.date_key),
        day_total=len(ranks),
        is_unknown=d.is_unknown,
        day_title=title,
        date_display="Date unknown" if d.is_unknown else format_long_date(d.date_obj),
        date_key=d.date_key,
        date_key_json=script_json(d.date_key),
        editable=editable,
        raw_title=day_text(content, d.date_key)[0],
        raw_description=day_text(content, d.date_key)[1],
        description=[p.strip() for p in desc.split("\n\n") if p.strip()],
        route=d.route,
        route_json=script_json(d.route),
        route_thumbs_json=script_json(route_thumbs),
        route_slide_indices_json=script_json(route_slide_indices),
        items=items_ctx,
        slides_json=script_json(slides),
        prev_href=prev_href,
        prev_href_json=script_json(prev_href),
        prev_label=prev_day.date_key if prev_day else "",
        next_href=next_href,
        next_href_json=script_json(next_href),
        next_label=next_day.date_key if next_day else "",
    )
