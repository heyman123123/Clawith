"""Import agency-agents-zh Markdown roles into the runtime role cache.

Usage:
    python scripts/import_agency_agents_zh.py /path/to/agency-agents-zh
    python scripts/import_agency_agents_zh.py --download

The source project is MIT licensed.  This importer deliberately keeps every
role's original Markdown as ``soul.md`` so a team-created agent receives the
role's complete operating instructions, not merely a shortened description.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Direct ``python scripts/...`` execution puts ``scripts/`` on sys.path, not
# the backend package root. Keep the helper usable both in a container and by
# an operator running it from a checkout.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.agency_role_sync import (
    agency_role_template_root,
    ensure_agency_role_templates,
    import_agency_roles,
)


def main() -> int:
    if len(sys.argv) == 2 and sys.argv[1] in {"--help", "-h"}:
        print("usage: import_agency_agents_zh.py <agency-agents-zh-source> | --download")
        return 0
    if len(sys.argv) == 2 and sys.argv[1] == "--download":
        import asyncio

        asyncio.run(ensure_agency_role_templates())
        print(f"Downloaded agency-agents-zh roles into {agency_role_template_root()}")
        return 0
    if len(sys.argv) != 2:
        raise SystemExit("usage: import_agency_agents_zh.py <agency-agents-zh-source> | --download")
    imported = import_agency_roles(Path(sys.argv[1]), agency_role_template_root())
    print(f"Imported {imported} agency-agents-zh roles into {agency_role_template_root()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
