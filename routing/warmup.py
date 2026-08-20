"""Build the process-wide lookup structures before the first request arrives.

The station grid index and the 121k-key gazetteer are immutable, shared and take ~190 ms
to construct. Built lazily, that cost lands inside whichever request happens to be first
-- which in a latency demo is precisely the request being timed. Building them at WSGI
startup instead moves the cost to boot, where nobody is holding a stopwatch.

This lives outside AppConfig.ready() on purpose: Django warns against touching the
database during app initialisation, and it is right to.
"""

import logging

log = logging.getLogger(__name__)


def warm() -> bool:
    try:
        from routing.corridor import get_index
        from routing.geocode import _tables

        _tables()
        n = len(get_index())
    except Exception as exc:
        # A fresh checkout has no stations yet. /api/v1/health/ says so plainly, and the
        # first request will build whatever it can lazily.
        log.warning("warm-up skipped: %s", exc)
        return False
    log.info("warm-up complete: %s stations indexed", f"{n:,}")
    return True
