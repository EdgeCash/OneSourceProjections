"""Print BettingPros market IDs for your account so you can verify/update
BP_MARKET_IDS in onesource/config.py.

Usage:
    python scripts/discover_markets.py [SPORT]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from onesource.clients import bettingpros  # noqa: E402

if __name__ == "__main__":
    sport = sys.argv[1] if len(sys.argv) > 1 else "MLB"
    for m in bettingpros.markets(sport):
        print(m)
