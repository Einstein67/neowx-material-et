#
# Copyright (c) 2026 PanelOrder Extension for NeoWX Material
#
# Distributed under the terms of the GNU GENERAL PUBLIC LICENSE
#

"""Section-based layout parsing for the skin.

Layout lives in [Extras][[Appearance]][[[sections]]].  Each section names its
items and, optionally, how to present them:

    [[[[soil]]]]
        items = soilMoist1, soilMoist2
        title = Soil Moisture
        collapsed = true
        content = card

  items      ordered list of cards/charts.  Required.
  title      omit for no panel - the items render loose in the surrounding row
             and 'collapsed' is ignored.
  collapsed  true  -> panel, starts collapsed
             false -> panel, starts expanded (the default)
             none  -> panel chrome, always open, no toggle
  content    card (default), chart, embedded, telemetry, telemetry_chart

Sections render in declaration order within each content value.  Items are
de-duplicated, first occurrence winning, separately per content value.

To use, add to the [CheetahGenerator] section of skin.conf:

    search_list_extensions = user.panelorder.PanelOrder

Then, in a template:

    #set $segments = $panelSegments('card')
    #set $flat     = $panelItems('chart')
"""

import logging

from weewx.cheetahgenerator import SearchList

log = logging.getLogger(__name__)

VERSION = "2.0.0"

# Segment types.  'type' is None for a section with no title, whose items
# render loose rather than inside a panel.
EXPANDED = "expanded"
COLLAPSED = "collapsed"
STATIC = "static"

CARD = "card"
CONTENTS = (CARD, "chart", "embedded", "telemetry", "telemetry_chart")

# Keys a section may carry that are settings rather than typos.
SETTING_KEYS = ("items", "title", "collapsed", "content")

# Order settings from 1.68.x.  Only used to recognise an unmigrated config.
LEGACY_KEYS = (
    "values_order",
    "charts_order",
    "embedded_order",
    "telemetry_order",
    "telemetry_chart_order",
)

TRUE_WORDS = ("true", "yes", "1")
FALSE_WORDS = ("false", "no", "0")

# The unmigrated-config error is worth saying once per config, not once per
# region per page.
_legacy_reported = set()


def _as_list(value):
    """Normalise a configobj value to a list of non-empty strings.

    configobj yields a list when the value contained commas and a bare string
    when it did not; an unset value comes through as None or "".
    """
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        parts = list(value)
    else:
        parts = str(value).split(",")
    return [str(p).strip() for p in parts if str(p).strip()]


def _appearance(skin_dict):
    return skin_dict.get("Extras", {}).get("Appearance", {})


def _report_unmigrated(appearance):
    """Complain once per config if this looks like an unmigrated 1.68.x setup."""
    key = id(appearance)
    if key in _legacy_reported:
        return
    stale = [k for k in LEGACY_KEYS if appearance.get(k) is not None]
    if stale:
        _legacy_reported.add(key)
        log.error(
            "panelorder: no [[[sections]]] found, but %s still present. "
            "These settings were replaced by [[[sections]]] and are no longer "
            "read, so no cards or charts will render. See the [[Appearance]] "
            "comments in skin.conf for the new syntax.",
            ", ".join(stale),
        )


def _panel_type(section, section_id):
    """Map a section's 'collapsed' setting to a segment type."""
    raw = section.get("collapsed")
    if raw is None:
        return EXPANDED
    word = str(raw).strip().lower()
    if word in TRUE_WORDS:
        return COLLAPSED
    if word in FALSE_WORDS:
        return EXPANDED
    if word == "none":
        return STATIC
    log.warning(
        "panelorder: section '%s' has collapsed = %s, which is not "
        "true, false or none; treating it as false.",
        section_id,
        raw,
    )
    return EXPANDED


def _warn_unknown_keys(section, section_id):
    for key in getattr(section, "scalars", list(section.keys())):
        if key not in SETTING_KEYS:
            log.warning(
                "panelorder: section '%s' has unknown setting '%s'; ignoring "
                "it. Items belong in 'items'.",
                section_id,
                key,
            )


def enable_panels_setting(skin_dict):
    """Read [[Appearance]] enablePanels, defaulting to true."""
    raw = _appearance(skin_dict).get("enablePanels", "true")
    return str(raw).strip().lower() not in FALSE_WORDS


def parse_sections(skin_dict, content=CARD, enable_panels=True):
    """Return the layout segments for one content region.

    With enable_panels false the grouping is discarded but every item is kept,
    so turning panels off degrades to a flat row rather than losing cards.
    """
    appearance = _appearance(skin_dict)
    sections = appearance.get("sections")
    if not sections:
        _report_unmigrated(appearance)
        return []

    wanted = str(content).strip().lower()
    segments = []
    seen = set()

    for section_id in getattr(sections, "sections", list(sections.keys())):
        section = sections[section_id]

        section_content = str(section.get("content", CARD)).strip().lower()
        if section_content not in CONTENTS:
            log.warning(
                "panelorder: section '%s' has content = %s, which is not one "
                "of %s; skipping the section.",
                section_id,
                section.get("content"),
                ", ".join(CONTENTS),
            )
            continue
        if section_content != wanted:
            continue

        _warn_unknown_keys(section, section_id)

        items = []
        for item in _as_list(section.get("items")):
            if item not in seen:
                seen.add(item)
                items.append(item)
        if not items:
            continue

        title = str(section.get("title", "")).strip()
        if enable_panels and title:
            segments.append(
                {
                    "type": _panel_type(section, section_id),
                    "title": title,
                    "items": items,
                }
            )
        else:
            segments.append({"type": None, "title": "", "items": items})

    return segments


def order_items(skin_dict, content=CARD):
    """Flat, de-duplicated item names for one content region.

    For loops that need the items themselves rather than the layout, such as
    the chart JavaScript generation.
    """
    return [
        item
        for segment in parse_sections(skin_dict, content, True)
        for item in segment["items"]
    ]


class PanelOrder(SearchList):
    """Exposes the two helpers above to Cheetah."""

    def __init__(self, generator):
        SearchList.__init__(self, generator)
        log.info("PanelOrder version %s", VERSION)

    def get_extension_list(self, timespan, db_lookup):
        skin_dict = self.generator.skin_dict

        def panel_segments(content=CARD, enable_panels=None):
            if enable_panels is None:
                enable_panels = enable_panels_setting(skin_dict)
            return parse_sections(skin_dict, content, enable_panels)

        def panel_items(content=CARD):
            return order_items(skin_dict, content)

        return [{"panelSegments": panel_segments, "panelItems": panel_items}]
