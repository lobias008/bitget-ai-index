"""AI-assisted review layer for the local paper-trading workflow.

Safety contract (mirrors AGENTS.md):

  * The AI is a VETO-ONLY reviewer. It can confirm, reject or watch a signal
    that the deterministic strategy (src/main.py) already produced. It can
    never create a signal, widen a risk limit, size a position or bypass a
    gate. An accepted review still has to pass every deterministic control in
    paper/simulator.py before anything is simulated.
  * Every failure mode fails CLOSED: invalid model output, a provider error,
    an exhausted call budget or missing market data all result in the signal
    NOT being simulated. Nothing is invented to keep a run going.
  * No order plumbing exists in this package. The only network code lives in
    providers.py and may POST only to an allowlisted AI inference endpoint.
    Bitget access stays read-only public market data via paper/market_data.py.
  * All artifacts land under the gitignored output/ai/ directory and are
    labeled PAPER / SIMULATED. A run using the offline fixture provider or the
    synthetic market-data source is additionally labeled SYNTHETIC.
"""
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
