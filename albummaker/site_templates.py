# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Pieter Remi Coppens
"""The site templates: the look and page layout of a generated trip site.

Every template shows the same content (days, photos, maps, the full-screen
viewer, in-page editing); they differ in how it is laid out and styled. A
template is a folder of page layouts (albummaker/templates/<folder>/), an
optional stylesheet and an optional script that are added to the shared ones
(albummaker/static/). Pick one with --template NAME.

To add a template: put its pages in albummaker/templates/<name>/ (index.html,
day.html, library.html), its CSS and JS in albummaker/static/, and list it in
TEMPLATES below. See "Templates" in the README.

This module has no imports from the rest of the package, so the command line,
the desktop app and the renderer can all use it.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class TemplateInfo:
    name: str                  # what --template takes, lower case
    label: str                 # shown in the desktop app
    description: str           # one line: what it looks like
    folder: str = ""           # sub-folder of albummaker/templates with index/day/library.html ("": the top folder)
    css: tuple = ()            # stylesheets in albummaker/static, added after style.css
    js: tuple = ()             # scripts in albummaker/static, added after script.js
    previews: int = 0          # photos shown on each day's card on the homepage (0: just the cover)


DEFAULT_TEMPLATE = "classic"

TEMPLATES = {
    "classic": TemplateInfo(
        name="classic",
        label="Classic",
        description="Dark navy pages with parchment text panels, a map and a card per day.",
    ),
    "journal": TemplateInfo(
        name="journal",
        label="Journal",
        description="A light travel-journal look: a big cover photo, trip statistics, a timeline of "
                    "steps next to a map that follows your scrolling, and photo collages.",
        folder="journal",
        css=("journal.css",),
        js=("journal.js",),
        previews=5,
    ),
    "scrapbook": TemplateInfo(
        name="scrapbook",
        label="Scrapbook",
        description="A warm paper scrapbook: taped polaroid photos, rubber-stamp statistics, "
                    "a push-pin map and lined notebook pages for each day's story.",
        folder="scrapbook",
        css=("scrapbook.css",),
        js=("scrapbook.js",),
        previews=4,
    ),
}


def template_names() -> list:
    return list(TEMPLATES)


def template_info(name: Optional[str]) -> TemplateInfo:
    """The template called `name` (None: the default). Raises ValueError listing the choices."""
    key = (name or DEFAULT_TEMPLATE).strip().lower()
    if key not in TEMPLATES:
        raise ValueError(f"unknown template {name!r}; choose one of: {', '.join(TEMPLATES)}")
    return TEMPLATES[key]


def template_arg(value: str) -> str:
    """argparse type for --template: the template's name, normalised."""
    try:
        return template_info(value).name
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def add_template_arg(parser) -> None:
    parser.add_argument("--template", type=template_arg, default=DEFAULT_TEMPLATE, metavar="NAME",
                        help="The look of the site: " + "; ".join(f"{t.name} ({t.description})" for t in TEMPLATES.values())
                             + f" Default: {DEFAULT_TEMPLATE}.")
