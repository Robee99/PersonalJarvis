"""Run the brain map indexer without the app: ``python -m jarvis.memory.brain_map``.

    python -m jarvis.memory.brain_map reindex [--data-dir DIR] [--vault DIR]
    python -m jarvis.memory.brain_map projects [--data-dir DIR]
    python -m jarvis.memory.brain_map report PROJECT [--data-dir DIR]

Deterministic rules only; it reads the stores read-only and writes nothing but
the brain map tables in ``<data-dir>/jarvis.db``. With the app running, prefer
``jarvis api brain-map reindex`` (the same pass, through the REST route).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m jarvis.memory.brain_map")
    parser.add_argument("command", choices=("reindex", "projects", "report"))
    parser.add_argument("project", nargs="?", default="")
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--vault", type=Path, default=None)
    args = parser.parse_args(argv)

    from jarvis.core import config as cfg

    from . import query
    from .service import BrainMapService

    data_dir = args.data_dir or Path(cfg.DATA_DIR)
    service = BrainMapService(data_dir=data_dir, vault_root=args.vault)
    try:
        if args.command == "reindex":
            print(json.dumps(service.reindex(trigger="cli").to_dict(), indent=2))
        elif args.command == "projects":
            for p in query.list_projects(service.store):
                print(f"{p['status']:>6}  {p['priority_score']:6.1f}  {p['title']}  ({p['id']})")
        else:
            project_id = query.resolve_project(service.store, args.project)
            report = query.make_report(service.store, project_id) if project_id else None
            if report is None:
                print("No such project.", file=sys.stderr)
                return 1
            print(report, end="")
    finally:
        service.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
