"""Global intelligence: world incidents from open public sources.

One shared event model (``model.py``) fed by keyless, commercially reusable
upstreams (``parsers.py``: USGS, GDACS, NASA EONET, NOAA NHC, GDELT), kept in
one store with stable ids (``store.py``) and refreshed on demand by
``service.py``. The World screen, the globe (God's Eye View or the built-in
one) and the agent tools (``mcp_tools.py``) all read the same events, so an id
means the same incident everywhere.

Every event says what kind of evidence it rests on (``Evidence``): an
instrument measurement, an agency alert, or a machine-coded media report. A
media report is never presented as a confirmed event.
"""
