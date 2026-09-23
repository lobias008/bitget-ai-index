"""Local paper-trading workflow for the Bitget AI Index platform.

Safety contract (mirrors AGENTS.md):
  - PUBLIC, read-only market data only. No auth headers, no private Bitget
    APIs, no account data, no orders - real or simulated-as-real.
  - The existing strategy (src/main.py) is reused UNMODIFIED through a local
    getagent harness; trading rules and risk controls are never re-implemented
    or altered here.
  - All outputs are written under the gitignored output/paper/ directory and
    are always labeled PAPER / SIMULATED.
  - Nothing in this package can place a live order: there is no order code.
"""
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))