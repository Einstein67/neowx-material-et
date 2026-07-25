#
# Copyright (c) 2026 PanelOrder Extension for NeoWX Material
#
# Distributed under the terms of the GNU GENERAL PUBLIC LICENSE
#

"""Panel-aware parsing of the skin's *_order settings.

Used by values_order, charts_order, embedded_order, telemetry_order and
telemetry_chart_order.  Syntax:

    {Title: a, b, c}      collapsible panel, initially EXPANDED
    {{Title: a, b, c}}    collapsible panel, initially COLLAPSED
    {a, b, c}             panel with no title
    //                    blank full-width row break (see below)
    anything else         a plain item, rendered outside any panel

Everything outside a {...} group renders loose, so a panel can be followed by
ungrouped items - the groups are self-delimiting.

"//" is not panel syntax: it is a full-width spacer that ends the current row so
the next card starts on a new line.  It may appear inside or outside a group and,
unlike real items, may be repeated.

configobj splits unquoted values on commas, so a group reaches us either as a
single element (when the user quoted it) or spread over several elements (when
they did not).  Both forms are accepted:

    "{Wind: a, b}"  ->  ['{Wind: a, b}']
     {Wind: a, b}   ->  ['{Wind: a', 'b}']

To use, add to the [CheetahGenerator] section of skin.conf:

    search_list_extensions = user.panelorder.PanelOrder

Then, in a template:

    #set $segments = $panelSegments($Extras.Appearance.values_order, $enablePanels)
    #set $flat     = $panelItems($Extras.Appearance.charts_order)
"""

import logging

from weewx.cheetahgenerator import SearchList

log = logging.getLogger(__name__)

VERSION = "1.0.0"

# Segment types.  A segment is a dict: {'type': ..., 'title': str, 'items': list}
# 'type' is None for loose items that are not wrapped in a panel.
EXPANDED = "expanded"
COLLAPSED = "collapsed"

SPACER = "//"


def _elements(order):
    """Normalise a skin.conf setting to a list of non-empty strings.

    configobj yields a list when the value contained commas and a bare string
    when it did not; an unset value comes through as None or "".
    """
    if order is None:
        return []
    if isinstance(order, (list, tuple)):
        parts = list(order)
    else:
        parts = str(order).split(",")
    return [str(p).strip() for p in parts if str(p).strip()]


def parse_order(order, enable_panels=True):
    """Return a list of segments describing how to lay out `order`.

    With enable_panels false the grouping is discarded but the items are kept,
    so turning panels off degrades to a flat row rather than losing cards.
    """
    segments = []
    current = {"type": None, "title": "", "items": []}
    seen = set()
    in_group = False

    def add_items(segment, text):
        # A quoted group arrives whole, so its body may still hold commas.
        for token in str(text).split(","):
            token = token.strip()
            if not token:
                continue
            if token.lower() == SPACER:
                # Spacers are deliberately exempt from de-duplication.
                segment["items"].append(SPACER)
            elif token not in seen:
                seen.add(token)
                segment["items"].append(token)

    for element in _elements(order):
        if not in_group and element.startswith("{"):
            collapsed = element.startswith("{{")
            body = element.lstrip("{").rstrip()
            closed_here = body.endswith("}")
            if closed_here:
                body = body.rstrip("}").rstrip()
            title, _, rest = body.partition(":") if ":" in body else ("", "", body)

            if enable_panels:
                segments.append(current)
                current = {
                    "type": COLLAPSED if collapsed else EXPANDED,
                    "title": title.strip(),
                    "items": [],
                }
            add_items(current, rest)

            if closed_here:
                if enable_panels:
                    segments.append(current)
                    current = {"type": None, "title": "", "items": []}
            else:
                in_group = True
            continue

        if in_group:
            body = element.rstrip()
            closed_here = body.endswith("}")
            if closed_here:
                body = body.rstrip("}").rstrip()
            add_items(current, body)
            if closed_here:
                in_group = False
                if enable_panels:
                    segments.append(current)
                    current = {"type": None, "title": "", "items": []}
            continue

        add_items(current, element)

    if in_group:
        log.info(
            "panelorder: unterminated '{' group in order setting; "
            "treating the remaining items as part of it"
        )

    segments.append(current)
    return segments


def order_items(order, enable_panels=True):
    """Flat list of real item names - no titles, no spacers, de-duplicated.

    For loops that need the items themselves rather than the layout, such as the
    chart JavaScript generation.
    """
    return [
        item
        for segment in parse_order(order, enable_panels)
        for item in segment["items"]
        if item.lower() != SPACER
    ]


class PanelOrder(SearchList):
    """Exposes the two helpers above to Cheetah."""

    def __init__(self, generator):
        SearchList.__init__(self, generator)
        log.info("PanelOrder version %s", VERSION)

    def get_extension_list(self, timespan, db_lookup):
        return [{"panelSegments": parse_order, "panelItems": order_items}]
