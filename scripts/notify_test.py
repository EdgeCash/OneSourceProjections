#!/usr/bin/env python
"""Send a single test push to the configured ntfy topic and report the result.

Used by the "Test notification" workflow to confirm end-to-end delivery from a
phone. Prints whether the topic is configured and the exact HTTP outcome (the
secret value itself stays masked by GitHub), e.g.:
    python scripts/notify_test.py "optional custom message"
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from onesource import notify  # noqa: E402


def main() -> None:
    msg = sys.argv[1] if len(sys.argv) > 1 else (
        "Test push from OneSourceProjections. If you see this, notifications "
        "are wired up correctly.")
    if not notify.configured():
        print("NTFY_TOPIC is NOT set — add it as a repository secret.")
        sys.exit(1)
    ct = notify.confirm_topic()
    print(f"configured=True; confirm_topic suffix resolves (len={len(ct or '')})")
    ok = notify.send(msg, title="✅ Test notification", tags=["white_check_mark"],
                     priority="high")
    print("send returned:", ok)
    sys.exit(0 if ok else 2)


if __name__ == "__main__":
    main()
