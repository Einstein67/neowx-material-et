#
# Copyright (c) 2026 PanelOrder Extension for NeoWX Material
#
# Distributed under the terms of the GNU GENERAL PUBLIC LICENSE
#

"""Parses the skin's page layout out of skin.conf so the templates don't have to.

Every page has to answer the same question: which cards and charts appear here,
in what order, and which of them are grouped into a collapsible panel?  The
answer lives in skin.conf, and getting it out takes more work than it looks.
configobj returns a list when a value contains commas and a bare string when it
doesn't.  Sections have to be filtered down to the part of the site being
rendered.  An item named twice has to be resolved rather than drawn twice.

Cheetah gives you nowhere good to put that.  A #def is private to the template
that declares it, and an #include does not export its defs to the file that
included it, so any parsing helper written in a template can only spread by
copy-paste.  That is what this module replaced: the same marker-parsing code
inlined into all eight page templates, kept in step by hand.  A SearchList gives
every page one parser, and a layout bug one place to be fixed.

Layout is entirely in this module's hands.  When parse_sections returns nothing,
the page renders no cards and no charts, which is the intended outcome for a
config that was never migrated off the old *_order settings (reported once by
_report_unmigrated).  The side effect is that a parsing bug here surfaces as a
blank page rather than a traceback, so prefer degrading with a warning over
raising.

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

Registration is not optional.  Without this, the templates call names that were
never added to the search list and the pages fail to generate:

    [CheetahGenerator]
        search_list_extensions = user.panelorder.PanelOrder

Then, in a template:

    #set $segments = $panelSegments('card')
    #set $flat     = $panelItems('chart')

panelSegments carries the grouping and is what you loop over to draw a row or a
panel.  panelItems flattens the same data to bare names, for the places that
only need to know whether an item is present, such as the chart JavaScript.
"""

import logging
import weakref

from weewx.cheetahgenerator import SearchList

log = logging.getLogger(__name__)

VERSION = "2.0.1"

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

# The unmigrated-config error, and the per-section parse warnings below, are
# worth reporting once per config, not once per call.  A set of raw id()
# values would grow without bound across report cycles and, worse, CPython
# can recycle a freed object's address, wrongly suppressing the diagnostic
# for an unrelated config that happens to land at the same address.  Every
# id() stored below is paired with a weakref.finalize() callback that
# discards it the moment the config object it names is actually collected,
# so storage stays bounded to the configs currently alive and a recycled
# id() always starts out "not yet reported".
_legacy_reported = set()


def _remember(seen_set, obj):
    """Record obj (by identity) in seen_set, auto-forgetting it once obj is
    garbage collected."""
    key = id(obj)
    if key not in seen_set:
        seen_set.add(key)
        weakref.finalize(obj, seen_set.discard, key)


def _already_seen(seen_set, obj):
    return id(obj) in seen_set


# Per-section parse warnings (unknown content/collapsed/setting-key values)
# are memoised the same way, but scoped per problem so a config with several
# distinct issues still reports each of them once.  Bucket is
# id(appearance) -> set of (kind, section_id, detail) tuples already logged.
_warned_problems = {}

# parse_sections() itself is memoised per (appearance identity, content,
# enable_panels) so the ~40-60 calls a single template can make for one
# content region (chart JS generation is called from inside nested loops)
# collapse to one real parse per report cycle.  Bucket is
# id(appearance) -> {(content, enable_panels): segments}.
_sections_cache = {}


def _problem_seen(appearance, problem):
    bucket = _warned_problems.get(id(appearance))
    return bucket is not None and problem in bucket


def _mark_problem(appearance, problem):
    appearance_id = id(appearance)
    bucket = _warned_problems.get(appearance_id)
    if bucket is None:
        bucket = set()
        _warned_problems[appearance_id] = bucket
        weakref.finalize(appearance, _warned_problems.pop, appearance_id, None)
    bucket.add(problem)


def _cache_get(appearance, key):
    bucket = _sections_cache.get(id(appearance))
    return None if bucket is None else bucket.get(key)


def _cache_set(appearance, key, value):
    appearance_id = id(appearance)
    bucket = _sections_cache.get(appearance_id)
    if bucket is None:
        bucket = {}
        _sections_cache[appearance_id] = bucket
        weakref.finalize(appearance, _sections_cache.pop, appearance_id, None)
    bucket[key] = value


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
    if _already_seen(_legacy_reported, appearance):
        return
    stale = [k for k in LEGACY_KEYS if appearance.get(k) is not None]
    if stale:
        _remember(_legacy_reported, appearance)
        log.error(
            "panelorder: no [[[sections]]] found, but %s still present. "
            "These settings were replaced by [[[sections]]] and are no longer "
            "read, so no cards or charts will render. See the [[Appearance]] "
            "comments in skin.conf for the new syntax.",
            ", ".join(stale),
        )


def _panel_type(appearance, section, section_id):
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
    problem = ("collapsed", section_id, str(raw))
    if not _problem_seen(appearance, problem):
        _mark_problem(appearance, problem)
        log.warning(
            "panelorder: section '%s' has collapsed = %s, which is not "
            "true, false or none; treating it as false.",
            section_id,
            raw,
        )
    return EXPANDED


def _warn_unknown_keys(appearance, section, section_id):
    for key in getattr(section, "scalars", list(section.keys())):
        if key not in SETTING_KEYS:
            problem = ("key", section_id, key)
            if _problem_seen(appearance, problem):
                continue
            _mark_problem(appearance, problem)
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

    Results are memoised per (appearance identity, content, enable_panels),
    since a single template can call this dozens of times for the same
    content region (chart JS generation runs inside nested per-item loops).
    That also bounds the unknown-content/-collapsed/-key warnings below to
    once per distinct problem per config, instead of once per call.
    """
    appearance = _appearance(skin_dict)
    sections = appearance.get("sections")
    if not sections:
        _report_unmigrated(appearance)
        return []

    wanted = str(content).strip().lower()
    enable_panels = bool(enable_panels)
    cache_key = (wanted, enable_panels)
    cached = _cache_get(appearance, cache_key)
    if cached is not None:
        return cached

    segments = []
    seen = set()

    for section_id in getattr(sections, "sections", list(sections.keys())):
        section = sections[section_id]

        raw_content = section.get("content", CARD)
        section_content = str(raw_content).strip().lower()
        if section_content != wanted:
            # A section whose content matches wanted is, by construction,
            # already a recognised value (wanted is always one of CONTENTS),
            # so the validity check only needs to run for the sections we're
            # about to skip anyway - that's also the only place an unknown
            # value can be caught, since it can never equal a valid wanted.
            if section_content not in CONTENTS:
                problem = ("content", section_id, str(raw_content))
                if not _problem_seen(appearance, problem):
                    _mark_problem(appearance, problem)
                    log.warning(
                        "panelorder: section '%s' has content = %s, which is "
                        "not one of %s; skipping the section.",
                        section_id,
                        raw_content,
                        ", ".join(CONTENTS),
                    )
            continue

        _warn_unknown_keys(appearance, section, section_id)

        items = []
        for item in _as_list(section.get("items")):
            if item not in seen:
                seen.add(item)
                items.append(item)
        if not items:
            continue

        raw_title = section.get("title", "")
        if isinstance(raw_title, (list, tuple)):
            # configobj returns a list when a value contains a comma, e.g. an
            # unquoted "title = Wind, Rain"; join it back into the string the
            # user meant rather than rendering the Python repr.
            raw_title = ", ".join(str(p) for p in raw_title)
        title = str(raw_title).strip()
        if enable_panels and title:
            segments.append(
                {
                    "type": _panel_type(appearance, section, section_id),
                    "title": title,
                    "items": items,
                }
            )
        else:
            segments.append({"type": None, "title": "", "items": items})

    _cache_set(appearance, cache_key, segments)
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
