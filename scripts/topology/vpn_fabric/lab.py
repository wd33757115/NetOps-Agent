"""VPN + EVPN/VXLAN fabric lab for the network twin (implementation plan §8 M5), in GNS3.

Two parts, one project, managed over the same out-of-band network as campus-wan:
- DC fabric (AS 65100): SPINE1/SPINE2 (IOL L3, OSPF underlay, BGP EVPN route
  reflectors), LEAF1/LEAF2 (IOL L2 VTEPs) and LEAF3 (H3C VSR VTEP). Tenants RED and BLUE
  both use 192.168.10.0/24 in separate VNIs; BLUE also routes to 192.168.20.0/24 on LEAF3
  through its L3VNI.
- MPLS L3VPN (AS 65200): PE1 (IOL L3) - P (IOL L3) - PE2 (H3C VSR), OSPF + LDP core,
  VPNv4 between the PEs. CUST-A and CUST-B both use 10.1.1.0/24 at PE1; SHARED (PE2)
  exchanges routes with CUST-A only, by route target.
Device configs are in configs/; the password is never in them.

Run from the repository root with the project venv (same commands as campus_wan/lab.py):
    venv\\Scripts\\python.exe scripts\\topology\\vpn_fabric\\lab.py project
    ... type LEAF3 | push LEAF3 | console SPINE1 "show bgp l2vpn evpn summary"
    ... account          (owner only: creates the lab login from NETOPS_LAB_PASSWORD)
    ... traffic          (continuous pings so ARP/MAC/EVPN tables stay populated)
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "campus_wan"))
import lab as base  # noqa: E402  the campus tooling: project file, consoles, push, account

NAME = "netops-vpn-fabric"


def _pc(at, console, address, gateway):
    return {"kind": "vpcs", "at": at, "console": console, "script": f"ip {address} {gateway}"}


# name: kind, position, console port, extras
NODES = {
    "SPINE1": {"kind": "iol3", "at": (-200, -300), "console": 5201},
    "SPINE2": {"kind": "iol3", "at": (200, -300), "console": 5202},
    "LEAF1": {"kind": "iol2", "at": (-350, -100), "console": 5203},
    "LEAF2": {"kind": "iol2", "at": (0, -100), "console": 5204},
    "LEAF3": {
        "kind": "vsr",
        "at": (350, -100),
        "console": 5921,
        "serial": 4201,
        "mac": "0c:c3:00:00:00:00",
    },
    "H-R1": _pc((-420, 60), 5211, "192.168.10.11/24", "192.168.10.1"),
    "H-B2": _pc((0, 60), 5212, "192.168.10.12/24", "192.168.10.1"),
    "H-R3": _pc((280, 60), 5213, "192.168.10.13/24", "192.168.10.1"),
    "H-B3": _pc((440, 60), 5214, "192.168.20.14/24", "192.168.20.1"),
    "PE1": {"kind": "iol3", "at": (-300, 280), "console": 5205},
    "P": {"kind": "iol3", "at": (0, 280), "console": 5206},
    "PE2": {
        "kind": "vsr",
        "at": (300, 280),
        "console": 5922,
        "serial": 4202,
        "mac": "0c:c4:00:00:00:00",
    },
    "A1": _pc((-420, 440), 5215, "10.1.1.10/24", "10.1.1.1"),
    "B1": _pc((-220, 440), 5216, "10.1.1.10/24", "10.1.1.1"),
    "A2": _pc((220, 440), 5217, "10.2.1.10/24", "10.2.1.1"),
    "S2": _pc((420, 440), 5218, "10.99.1.10/24", "10.99.1.1"),
    "MGMT-SW": {"kind": "switch", "at": (-700, 0), "ports": [("access", 1)] * 10},
    "MGMT-CLOUD": {"kind": "cloud", "at": (-700, -200)},
}

LINKS = [
    ("MGMT-CLOUD eth1", "MGMT-SW e0"),
    ("SPINE1 e0/0", "MGMT-SW e1"),
    ("SPINE2 e0/0", "MGMT-SW e2"),
    ("LEAF1 e0/0", "MGMT-SW e3"),
    ("LEAF2 e0/0", "MGMT-SW e4"),
    ("LEAF3 GE1/0", "MGMT-SW e5"),
    ("PE1 e0/0", "MGMT-SW e6"),
    ("P e0/0", "MGMT-SW e7"),
    ("PE2 GE1/0", "MGMT-SW e8"),
    ("SPINE1 e0/1", "LEAF1 e0/1"),
    ("SPINE1 e0/2", "LEAF2 e0/1"),
    ("SPINE1 e0/3", "LEAF3 GE2/0"),
    ("SPINE2 e0/1", "LEAF1 e0/2"),
    ("SPINE2 e0/2", "LEAF2 e0/2"),
    ("SPINE2 e0/3", "LEAF3 GE3/0"),
    ("LEAF1 e1/0", "H-R1 e0"),
    ("LEAF2 e1/0", "H-B2 e0"),
    ("LEAF3 GE4/0", "H-R3 e0"),
    ("LEAF3 GE5/0", "H-B3 e0"),
    ("PE1 e0/1", "P e0/1"),
    ("P e0/2", "PE2 GE2/0"),
    ("PE1 e1/0", "A1 e0"),
    ("PE1 e1/1", "B1 e0"),
    ("PE2 GE3/0", "A2 e0"),
    ("PE2 GE4/0", "S2 e0"),
]

MANAGED = ["SPINE1", "SPINE2", "LEAF1", "LEAF2", "LEAF3", "PE1", "P", "PE2"]
PCS = ["H-R1", "H-B2", "H-R3", "H-B3", "A1", "B1", "A2", "S2"]
# Each host pings across the overlay it belongs to; B1 has no remote site (CUST-B only
# lives at PE1), so it keeps its gateway's ARP entry fresh.
TARGETS = {
    "H-R1": "192.168.10.13",
    "H-R3": "192.168.10.11",
    "H-B2": "192.168.20.14",
    "H-B3": "192.168.10.12",
    "A1": "10.2.1.10",
    "A2": "10.99.1.10",
    "S2": "10.1.1.10",
    "B1": "10.1.1.1",
}

base.HERE, base.NAME, base.NODES, base.LINKS, base.__doc__ = HERE, NAME, NODES, LINKS, __doc__
base.MANAGED, base.UNMANAGED, base.PCS, base.TARGETS = MANAGED, [], PCS, TARGETS
base.PROJECT_DIR = Path.home() / "GNS3" / "projects" / NAME
base.PROJECT_FILE = base.PROJECT_DIR / f"{NAME}.gns3"
# MGMT-SW/MGMT-CLOUD share their names with campus-wan; their GNS3 ids must not.
base._id = lambda *parts: str(uuid.uuid5(base.NS, "/".join((NAME, *parts))))

if __name__ == "__main__":
    base.main()
