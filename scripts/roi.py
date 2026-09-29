"""Print the ROI simulation. This does not read invoices or measured savings."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    sys.path.insert(0, str(ROOT / "api"))
    from roi import calculate_roi, load_assumptions

    assumptions = load_assumptions(ROOT / "config" / "roi_assumptions.json")
    result = calculate_roi(assumptions, completed_requests=0)
    print(result["label"])
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
