"""Create the OpsPilot Notion database under one parent page.

The integration must already be connected to that parent page.
This script does not search or edit any other page.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

NOTION_VERSION = "2022-06-28"
ROOT = Path(__file__).resolve().parents[1]


def load_env_file() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def main() -> int:
    load_env_file()
    token = os.environ.get("NOTION_API_KEY", "")
    parent = os.environ.get("NOTION_PARENT_PAGE_ID", "")
    if os.environ.get("NOTION_DATABASE_ID"):
        print("NOTION_DATABASE_ID is already set. Refusing to create another database.")
        return 1
    if not token or not parent:
        print("Set NOTION_API_KEY and NOTION_PARENT_PAGE_ID in .env first.")
        return 1
    payload = {
        "parent": {"type": "page_id", "page_id": parent},
        "title": [{"type": "text", "text": {"content": "AI Automation Lab"}}],
        "properties": {
            "Request": {"title": {}},
            "Status": {
                "select": {
                    "options": [
                        _option(name)
                        for name in (
                            "Pending",
                            "Processing",
                            "Waiting Approval",
                            "Approved",
                            "Rejected",
                            "Completed",
                            "Failed",
                        )
                    ]
                }
            },
            "AI Analysis": {"rich_text": {}},
            "Proposed Action": {"rich_text": {}},
            "Human Approval": {
                "select": {
                    "options": [
                        _option("Pending"),
                        _option("Approved"),
                        _option("Rejected"),
                    ]
                }
            },
            "Created At": {"date": {}},
            "Processed At": {"date": {}},
            "Request ID": {"rich_text": {}},
            "Requester": {"rich_text": {}},
            "Error": {"rich_text": {}},
        },
    }
    request = urllib.request.Request(
        "https://api.notion.com/v1/databases",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        print(exc.read().decode("utf-8"))
        return 1
    database_id = body["id"]
    print("Created AI Automation Lab.")
    print(f"NOTION_DATABASE_ID={database_id}")
    print("Copy that id into .env. Do not commit .env.")
    return 0


def _option(name: str) -> dict:
    return {"name": name}


if __name__ == "__main__":
    sys.exit(main())
