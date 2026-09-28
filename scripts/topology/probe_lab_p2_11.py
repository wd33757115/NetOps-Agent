"""P2-11 read-only live probe for model evidence gaps.

Two rounds, 90 s apart, reusing collect_lab_samples safeguards (display-only
allowlist, pinned host keys, no answers to interactive questions). The password
comes from NETOPS_LAB_PASSWORD or an interactive prompt and is never written.
Output: .runtime/lab-samples/<timestamp>-p2-11-{a,b}/.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import time
from datetime import datetime

import collect_lab_samples as lab

COMMANDS = [
    ("paging", "screen-length disable"),
    ("clock", "display clock"),
    ("version", "display version"),
    ("lldp_verbose", "display lldp neighbor-information verbose"),
    ("if_ge1", "display interface GigabitEthernet1/0"),
    ("if_ge3", "display interface GigabitEthernet3/0"),
    ("if_vlan100", "display interface Vlan-interface100"),
    ("if_vlan200", "display interface Vlan-interface200"),
    ("stp", "display stp"),
    ("stp_brief", "display stp brief"),
    ("clock_end", "display clock"),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("hosts", nargs="+")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--interval", type=int, default=90)
    args = parser.parse_args()
    password = os.environ.get("NETOPS_LAB_PASSWORD") or getpass.getpass("SSH password: ")
    lab.COMMANDS = COMMANDS
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    ok = True
    for index, round_name in enumerate(("a", "b")):
        if index:
            time.sleep(args.interval)
        out = lab.OUT_ROOT / f"{stamp}-p2-11-{round_name}"
        out.mkdir(parents=True, exist_ok=True)
        metas = [lab.collect(host, args.username, password, out, 22) for host in args.hosts]
        (out / "meta.json").write_text(json.dumps(metas, ensure_ascii=False, indent=2), "utf-8")
        ok = ok and all(m["status"] == "ok" for m in metas)
        print(json.dumps({"round": round_name, "dir": str(out)}))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
