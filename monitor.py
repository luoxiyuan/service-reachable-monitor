#!/usr/bin/env python3
"""Run the monitor without installing the package.

    python monitor.py run -c config/targets.yaml -n config/notify.yaml

Equivalent to ``python -m service_monitor`` once the package is installed.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from service_monitor.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())