"""One searchable, deduplicated catalog of every tool Jarvis and Hermes can reach.

Jarvis and Hermes Agent each keep their own lists: Jarvis's ``mcp.json``,
skills, Marketplace apps and connected CLIs; Hermes's ``config.yaml`` MCP
servers, skills, plugins and toolsets. The same upstream often appears in both
(Windows-MCP is ``windows-mcp`` in Jarvis and ``windows`` in Hermes). This
package folds them into ONE record per canonical upstream, each carrying the
runtime bindings it has, and states computed honestly from those bindings:

* ``available``  the item is known and could be used or installed;
* ``installed``  configured in at least one runtime;
* ``enabled``    switched on in at least one runtime;
* ``connected``  a runtime reports a live connection (Jarvis: a running client);
* ``visible``    the ACTIVE brain can see its tools this turn;
* ``verified``   a real tool call succeeded, read only from the verification
  file (:mod:`.verifications`), never inferred from the flags above.

Unknown parts are ``None``; nothing is guessed ``True``. No secret (env value,
header value, key, full URL or command line) ever leaves this package.
"""

from __future__ import annotations

from .build import build_catalog, filter_items
from .verifications import load_verifications, verifications_path

__all__ = ["build_catalog", "filter_items", "load_verifications", "verifications_path"]
