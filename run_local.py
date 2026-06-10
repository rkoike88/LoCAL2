"""Dev convenience shim — delegates to src/local/run.py.

Use ``python run_local.py`` during development (no install needed).
Use ``local2`` after ``pip install -e .`` or ``pip install local2``.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from local.run import main  # noqa: E402

if __name__ == "__main__":
    main()
