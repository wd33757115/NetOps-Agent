"""Campus + WAN lab for the network twin (roadmap 11.1), built in GNS3.

Two H3C VSR borders (eBGP to an ISP, iBGP via two Cisco core route reflectors),
OSPF areas 0/10, two Cisco IOL L2 distribution switches (VRRP, LACP, rapid-PVST),
two access switches with redundant trunks, and an unmanaged VLAN switch.
Device configs are in configs/; the password is never in them.

Run from the repository root with the project venv:
    venv\\Scripts\\python.exe scripts\\topology\\campus_wan\\lab.py project
    ... screen BR1 | type BR1 | push BR1 | console CORE1 "show ip ospf neighbor"
    ... account          (owner only: creates the lab login from NETOPS_LAB_PASSWORD, .env)
    ... traffic          (continuous pings from every PC so ARP/MAC tables stay populated)
"""

from __future__ import annotations

import argparse
import json
import re
import socket
import struct
import sys
import time
import uuid
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from collect_lab_samples import lab_password  # noqa: E402  one password source for all tools

NAME = "netops-campus-wan"
PROJECT_DIR = Path.home() / "GNS3" / "projects" / NAME
PROJECT_FILE = PROJECT_DIR / f"{NAME}.gns3"
GNS3_VM = "10.10.10.2"
NS = uuid.UUID("6b0f3c52-6d8e-4b8f-9a55-0c2e7a1d4f10")

TEMPLATES = {
    "vsr": "46eff5f3-9a5b-4cc5-854e-590faa566677",
    "iol3": "dc3bbaf1-aa37-4451-b94c-afcc683663a7",
    "iol2": "cb748507-1560-402e-9de2-691d7c2e34e2",
    "vpcs": "19021f99-e36f-394d-b4a1-8aaa902ab9cc",
    "switch": "1966b864-93e7-32d5-965f-001384eec461",
    "cloud": None,
}
SYMBOLS = {
    "vsr": ":/symbols/classic/router.svg",
    "iol3": ":/symbols/router.svg",
    "iol2": ":/symbols/multilayer_switch.svg",
    "vpcs": ":/symbols/vpcs_guest.svg",
    "switch": ":/symbols/ethernet_switch.svg",
    "cloud": ":/symbols/cloud.svg",
}

# name: kind, position, console port, extras
NODES = {
    "ISP": {"kind": "iol3", "at": (0, -430), "console": 5101},
    "INET-PC": {
        "kind": "vpcs",
        "at": (230, -430),
        "console": 5116,
        "script": "ip 198.51.100.10/24 198.51.100.1",
    },
    "BR1": {
        "kind": "vsr",
        "at": (-220, -280),
        "console": 5911,
        "serial": 4101,
        "mac": "0c:b1:00:00:00:00",
    },
    "BR2": {
        "kind": "vsr",
        "at": (220, -280),
        "console": 5912,
        "serial": 4102,
        "mac": "0c:b2:00:00:00:00",
    },
    "CORE1": {"kind": "iol3", "at": (-220, -120), "console": 5102},
    "CORE2": {"kind": "iol3", "at": (220, -120), "console": 5103},
    "DIST1": {"kind": "iol2", "at": (-220, 40), "console": 5104},
    "DIST2": {"kind": "iol2", "at": (220, 40), "console": 5105},
    "ACC1": {"kind": "iol2", "at": (-340, 200), "console": 5106},
    "ACC2": {"kind": "iol2", "at": (340, 200), "console": 5107},
    "PC1": {
        "kind": "vpcs",
        "at": (-460, 350),
        "console": 5111,
        "script": "ip 172.16.10.11/24 172.16.10.1",
    },
    "PC2": {
        "kind": "vpcs",
        "at": (-250, 350),
        "console": 5112,
        "script": "ip 172.16.20.12/24 172.16.20.1",
    },
    "PC3": {
        "kind": "vpcs",
        "at": (220, 350),
        "console": 5113,
        "script": "ip 172.16.10.13/24 172.16.10.1",
    },
    "UNM-SW": {
        "kind": "switch",
        "at": (440, 340),
        "ports": [("dot1q", 1), ("access", 20), ("access", 10)],
    },
    "PC4": {
        "kind": "vpcs",
        "at": (360, 480),
        "console": 5114,
        "script": "ip 172.16.20.14/24 172.16.20.1",
    },
    "PC5": {
        "kind": "vpcs",
        "at": (540, 480),
        "console": 5115,
        "script": "ip 172.16.10.15/24 172.16.10.1",
    },
    "MGMT-SW": {"kind": "switch", "at": (-640, -120), "ports": [("access", 1)] * 10},
    "MGMT-CLOUD": {"kind": "cloud", "at": (-650, -300)},
}

LINKS = [
    ("MGMT-CLOUD eth1", "MGMT-SW e0"),
    ("BR1 GE1/0", "MGMT-SW e1"),
    ("BR2 GE1/0", "MGMT-SW e2"),
    ("CORE1 e0/0", "MGMT-SW e3"),
    ("CORE2 e0/0", "MGMT-SW e4"),
    ("DIST1 e0/0", "MGMT-SW e5"),
    ("DIST2 e0/0", "MGMT-SW e6"),
    ("ACC1 e0/0", "MGMT-SW e7"),
    ("ACC2 e0/0", "MGMT-SW e8"),
    ("ISP e0/1", "BR1 GE2/0"),
    ("ISP e0/2", "BR2 GE2/0"),
    ("ISP e0/3", "INET-PC e0"),
    ("BR1 GE3/0", "CORE1 e0/1"),
    ("BR1 GE4/0", "CORE2 e0/2"),
    ("BR1 GE5/0", "BR2 GE5/0"),
    ("BR2 GE3/0", "CORE2 e0/1"),
    ("BR2 GE4/0", "CORE1 e0/2"),
    ("CORE1 e0/3", "CORE2 e0/3"),
    ("CORE1 e1/0", "DIST1 e0/1"),
    ("CORE1 e1/1", "DIST2 e0/2"),
    ("CORE2 e1/0", "DIST2 e0/1"),
    ("CORE2 e1/1", "DIST1 e0/2"),
    ("DIST1 e1/0", "DIST2 e1/0"),
    ("DIST1 e1/1", "DIST2 e1/1"),
    ("DIST1 e2/0", "ACC1 e0/1"),
    ("DIST1 e2/1", "ACC2 e0/2"),
    ("DIST2 e2/0", "ACC2 e0/1"),
    ("DIST2 e2/1", "ACC1 e0/2"),
    ("ACC1 e1/0", "PC1 e0"),
    ("ACC1 e1/1", "PC2 e0"),
    ("ACC2 e1/0", "PC3 e0"),
    ("ACC2 e1/1", "UNM-SW e0"),
    ("UNM-SW e1", "PC4 e0"),
    ("UNM-SW e2", "PC5 e0"),
]

LABEL_STYLE = "font-family: TypeWriter;font-size: 10.0;font-weight: bold;fill: #000000;"
MANAGED = ["BR1", "BR2", "CORE1", "CORE2", "DIST1", "DIST2", "ACC1", "ACC2"]
UNMANAGED = ["ISP"]  # configured by push, never collected
PCS = ["PC1", "PC2", "PC3", "PC4", "PC5", "INET-PC"]
# traffic: what each PC pings
TARGETS = {**dict.fromkeys(PCS, "198.51.100.10"), "INET-PC": "172.16.10.11"}


# GNS3 project -----------------------------------------------------------------


def _id(*parts):
    return str(uuid.uuid5(NS, "/".join(parts)))


def _port(name, port):
    kind = NODES[name]["kind"]
    if kind == "vsr":
        return int(re.fullmatch(r"GE(\d+)/0", port).group(1)) - 1, 0
    if kind in ("iol2", "iol3"):
        slot, number = re.fullmatch(r"e(\d+)/(\d+)", port).groups()
        return int(slot), int(number)
    if kind == "switch":
        return 0, int(port[1:])
    return 0, 0


def _properties(name, spec):
    kind = spec["kind"]
    if kind == "vsr":
        return {
            "adapter_type": "virtio-net-pci",
            "adapters": 5,
            "bios_image": "",
            "boot_priority": "c",
            "cdrom_image": "",
            "cpu_throttling": 0,
            "cpus": 2,
            "create_config_disk": False,
            "hda_disk_image": "VSR1000_HPE-CMW710-E0519L03-X64.qco",
            "hda_disk_interface": "virtio",
            "hdb_disk_image": "",
            "hdb_disk_interface": "none",
            "hdc_disk_image": "",
            "hdc_disk_interface": "none",
            "hdd_disk_image": "",
            "hdd_disk_interface": "none",
            "initrd": "",
            "kernel_command_line": "",
            "kernel_image": "",
            "legacy_networking": False,
            "linked_clone": True,
            "mac_address": spec["mac"],
            "on_close": "power_off",
            "platform": "x86_64",
            "process_priority": "normal",
            # The VNC screen is the console line; the serial port is the aux line used by push.
            "options": f"-cpu qemu64 -serial telnet:0.0.0.0:{spec['serial']},server,nowait",
            "qemu_path": "/usr/bin/qemu-system-x86_64",
            "ram": 2048,
            "replicate_network_connection_state": True,
            "tpm": False,
            "uefi": False,
            "usage": "",
        }
    if kind in ("iol2", "iol3"):
        return {
            "path": "x86_64_crb_linux_l2-adventerprisek9-ms.iol"
            if kind == "iol2"
            else "x86_64_crb_linux-adventerprisek9-ms.iol",
            "ethernet_adapters": 4 if kind == "iol2" else 2,
            "serial_adapters": 0,
            "ram": 512,
            "nvram": 512,
            "l1_keepalives": False,
            "use_default_iou_values": False,
            "application_id": 1,  # GNS3 allocates the real one when the project opens
        }
    # No startup_config_content / startup_script: GNS3 keeps them in the project file,
    # rejects every later node update because of them and re-applies them on each open.
    if kind == "vpcs":
        return {}
    if kind == "switch":
        mapping = []
        for number, (mode, vlan) in enumerate(spec["ports"]):
            port = {"name": f"Ethernet{number}", "port_number": number, "type": mode, "vlan": vlan}
            if mode == "dot1q":
                port["ethertype"] = "0x8100"
            mapping.append(port)
        return {"ports_mapping": mapping}
    return {  # cloud bridged to the GNS3 VM's eth1 (Hyper-V switch GNS3-MGMT, host 10.10.1.254)
        "interfaces": [
            {"name": n, "special": n != "eth1" and n != "eth0", "type": "ethernet"}
            for n in ("docker0", "eth0", "eth1", "lo", "virbr0")
        ],
        "ports_mapping": [
            {"interface": "eth1", "name": "eth1", "port_number": 0, "type": "ethernet"}
        ],
        "remote_console_host": "",
        "remote_console_http_path": "/",
        "remote_console_port": 23,
        "remote_console_type": "none",
    }


def _node(name, spec):
    kind = spec["kind"]
    node_type = {"vsr": "qemu", "iol2": "iou", "iol3": "iou"}.get(
        kind, kind if kind != "switch" else "ethernet_switch"
    )
    console_type = {"vsr": "vnc", "iol2": "telnet", "iol3": "telnet", "vpcs": "telnet"}.get(
        kind, "none"
    )
    return {
        "compute_id": "vm",
        "console": spec.get("console"),
        "console_auto_start": False,
        "console_type": console_type,
        "custom_adapters": [],
        "first_port_name": None,
        "height": 45,
        "width": 66,
        "label": {
            "rotation": 0,
            "style": LABEL_STYLE,
            "text": name,
            "x": 0,
            "y": -25,
        },
        "locked": False,
        "name": name,
        "node_id": _id("node", name),
        "node_type": node_type,
        "port_name_format": "GE{port1}/0" if kind == "vsr" else "Ethernet{0}",
        "port_segment_size": 0,
        "properties": _properties(name, spec),
        "symbol": SYMBOLS[kind],
        "template_id": TEMPLATES[kind],
        "x": spec["at"][0],
        "y": spec["at"][1],
        "z": 1,
    }


def _link(a, b):
    ends = []
    for end in (a, b):
        name, port = end.split()
        adapter, number = _port(name, port)
        ends.append(
            {"adapter_number": adapter, "node_id": _id("node", name), "port_number": number}
        )
    return {
        "filters": {},
        "link_id": _id("link", a, b),
        "link_style": {},
        "nodes": ends,
        "suspend": False,
    }


def project(_args):
    topology = {
        "auto_close": True,
        "auto_open": False,
        "auto_start": False,
        "drawing_grid_size": 25,
        "grid_size": 75,
        "name": NAME,
        "project_id": _id("project", NAME),
        "revision": 9,
        "scene_height": 1100,
        "scene_width": 1600,
        "show_grid": False,
        "show_interface_labels": True,
        "show_layers": False,
        "snap_to_grid": False,
        "supplier": None,
        "type": "topology",
        "variables": None,
        "version": "2.2.58.1",
        "zoom": 100,
        "topology": {
            "computes": [],
            "drawings": [],
            "links": [_link(a, b) for a, b in LINKS],
            "nodes": [_node(n, s) for n, s in NODES.items()],
        },
    }
    if PROJECT_FILE.exists():
        sys.exit(
            f"{PROJECT_FILE} exists; GNS3 owns it once opened. Delete the project in GNS3 first."
        )
    PROJECT_DIR.mkdir(parents=True, exist_ok=True)
    PROJECT_FILE.write_text(json.dumps(topology, indent=4, sort_keys=True), encoding="utf-8")
    print(f"wrote {PROJECT_FILE}: {len(NODES)} nodes, {len(LINKS)} links")


def _consoles():
    """Console ports as GNS3 assigned them (it rewrites the file when the project opens)."""
    nodes = json.loads(PROJECT_FILE.read_text(encoding="utf-8"))["topology"]["nodes"]
    return {n["name"]: n.get("console") for n in nodes}


# consoles ---------------------------------------------------------------------

IAC, DONT, DO, WONT, WILL, SB, SE = 255, 254, 253, 252, 251, 250, 240


class Console:
    """Raw telnet: a GNS3 console port or a QEMU serial port."""

    def __init__(self, port, host=GNS3_VM):
        self.sock = socket.create_connection((host, port), timeout=10)
        self.text = ""

    def _strip(self, data):
        out, i = bytearray(), 0
        while i < len(data):
            if data[i] != IAC or i + 1 >= len(data):
                out.append(data[i])
                i += 1
                continue
            cmd = data[i + 1]
            if cmd in (DO, DONT, WILL, WONT):
                i += 3  # unanswered: replying makes GNS3's console renegotiate forever
            elif cmd == SB:
                end = data.find(bytes([IAC, SE]), i)
                i = len(data) if end < 0 else end + 2
            else:
                if cmd == IAC:
                    out.append(IAC)
                i += 2
        return out.decode("utf-8", "replace")

    def read(self, wait=0.5):
        self.sock.settimeout(wait)
        chunks = []
        try:
            while True:
                data = self.sock.recv(65536)
                if not data:
                    raise ConnectionError("console closed")
                chunks.append(self._strip(data))
                self.sock.settimeout(0.3)
        except TimeoutError:
            pass
        text = "".join(chunks)
        self.text += text
        return text

    def expect(self, pattern, timeout=30):
        deadline, seen = time.time() + timeout, ""
        while time.time() < deadline:
            seen += self.read(0.5)
            if re.search(pattern, seen):
                return seen
        raise TimeoutError(f"no {pattern!r} after {timeout}s; last output: {seen[-300:]!r}")

    def send(self, line, end="\r"):
        self.sock.sendall((line + end).encode())


# VPCS starts its prompt line with "\r".
PROMPT = r"(?m)^\r?[\w.\-]+(?:\([\w\-]+\))?[>#]\s*$|[<\[][\w./\-]+[>\]]\s*$"
REJECTED = re.compile(r"% *(Unrecognized|Incomplete|Wrong|Too many|Invalid|Ambiguous)|\^", re.I)


def _ready(console):
    """Wake a console and get to an exec prompt, answering only the boot questions."""
    for _ in range(12):
        console.send("")
        seen = console.read(2)
        if re.search(r"initial configuration dialog\? \[yes/no\]", seen):
            console.send("no")
            continue
        if re.search(PROMPT, seen):
            if re.search(r"(?m)^[\w.\-]+>\s*$", seen):
                console.send("enable")  # IOS user exec; a VSR "<BR1>" is already its top view
                console.read(1)
            return
    raise TimeoutError(f"console not ready: {console.text[-300:]!r}")


def run(console, lines, quiet=False):
    rejected = []
    for line in lines:
        console.send(line)
        out = console.expect(PROMPT + r"|\[Y/N\]|\[yes/no\]|\[confirm\]", timeout=120)
        if "Overwrite the previous NVRAM configuration?[confirm]" in out:
            # IOL's first save after GNS3 wrote its NVRAM; unanswered, nothing is saved.
            console.send("")
            out += console.expect(PROMPT, timeout=60)
        elif re.search(r"\[Y/N\]|\[yes/no\]|\[confirm\]", out):
            # Never replace existing keys or files.
            console.send("no" if "[yes/no]" in out else "n" if "[confirm]" in out else "N")
            out += console.expect(PROMPT, timeout=60)
        if REJECTED.search(out):
            rejected.append((line, out.strip()))
        if not quiet:
            print(out, end="")
    return rejected


def push(args):
    """Configure nodes over their consoles from configs/ and save; report rejected lines."""
    consoles = _consoles()
    for name in [*MANAGED, *UNMANAGED, *PCS] if args.node == "all" else [args.node]:
        spec = NODES[name]
        if spec["kind"] == "vpcs":
            console = Console(consoles[name])
            console.send("")
            console.read(1)
            run(console, [f"set pcname {name}", spec["script"], "save"], quiet=True)
            print(f"{name}: {spec['script']}")
            continue
        if spec["kind"] == "vsr":
            console = Console(spec["serial"])
            text = (HERE / "configs" / f"{name}.txt").read_text(encoding="utf-8")
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            commands = ["system-view", *lines, "return", "save force"]
        else:
            console = Console(consoles[name])
            text = (HERE / "configs" / f"{name}.cfg").read_text(encoding="utf-8")
            lines = [line for line in text.splitlines() if line.strip() not in ("", "!", "end")]
            keys = ["crypto key generate rsa general-keys modulus 2048"] if name in MANAGED else []
            commands = [
                "terminal length 0",
                "configure terminal",
                *lines,
                *keys,
                "end",
                "write memory",
            ]
        _ready(console)
        rejected = run(console, commands, quiet=True)
        for line, out in rejected:
            print(f"REJECTED {line!r}\n{out}\n")
        print(f"{name}: {len(lines)} lines sent, {len(rejected)} rejected")


def console_cmd(args):
    # A VSR console is its VNC screen; commands go over its serial (aux) line.
    port = NODES[args.node].get("serial") or _consoles()[args.node]
    console = Console(port)
    if NODES[args.node]["kind"] == "vpcs":
        console.send("")
        console.read(1)
        for command in args.commands:
            console.send(command)
            deadline = time.time() + args.wait  # VPCS prints ping replies a second apart
            while time.time() < deadline:
                print(console.read(0.5), end="", flush=True)
        return
    _ready(console)
    run(console, args.commands)


def account(args):
    """Owner-run: the lab login on every managed node. The password is never printed."""
    password = lab_password("lab password: ")
    if "?" in password:
        sys.exit("the password contains ?, which IOS and Comware consoles read as help")
    consoles = _consoles()
    for name in MANAGED:
        spec = NODES[name]
        if spec["kind"] == "vsr":
            console = Console(spec["serial"])
            lines = [
                "system-view",
                f"local-user {args.username} class manage",
                f"password simple {password}",
                "service-type ssh",
                "authorization-attribute user-role network-admin",
                "quit",
                "return",
                "save force",
            ]
        else:
            console = Console(consoles[name])
            lines = [
                "configure terminal",
                f"username {args.username} privilege 15 secret 0 {password}",
                "end",
                "write memory",
            ]
        _ready(console)
        rejected = run(console, lines, quiet=True)
        # Only the first word of a rejected line: the account line carries the password.
        words = ", ".join(line.split()[0] for line, _ in rejected)
        print(f"{name}: {'rejected ' + words if rejected else 'ok'}")


def traffic(args):
    """Continuous pings from every PC (or Ctrl+C them with --stop)."""
    consoles = _consoles()
    for name in PCS:
        console = Console(consoles[name])
        if args.stop:
            console.sock.sendall(b"\x03")
        else:
            console.send("")
            console.read(1)
            console.send(f"ping {TARGETS[name]} -t")
        print(f"{name}: {'stopped' if args.stop else 'pinging'}")


# VNC: the VSR console line (aux starts as network-operator) ---------------------


def _vnc(port):
    sock = socket.create_connection((GNS3_VM, port), timeout=10)

    def recv(n):
        data = b""
        while len(data) < n:
            chunk = sock.recv(n - len(data))
            if not chunk:
                raise ConnectionError("vnc closed")
            data += chunk
        return data

    recv(12)
    sock.sendall(b"RFB 003.008\n")
    types = recv(recv(1)[0])
    if 1 not in types:
        raise ConnectionError("VNC needs a password")
    sock.sendall(b"\x01")
    if struct.unpack(">I", recv(4))[0]:
        raise ConnectionError("VNC refused")
    sock.sendall(b"\x01")
    width, height = struct.unpack(">HH", recv(4))
    recv(16)
    recv(struct.unpack(">I", recv(4))[0])
    # 32 bpp true colour, little endian: bytes are B, G, R, X.
    sock.sendall(struct.pack(">BxxxBBBBHHHBBBxxx", 0, 32, 24, 0, 1, 255, 255, 255, 16, 8, 0))
    sock.sendall(struct.pack(">BxHi", 2, 1, 0))
    return sock, recv, width, height


def screen(args):
    sock, recv, width, height = _vnc(_consoles()[args.node])
    sock.sendall(struct.pack(">BBHHHH", 3, 0, 0, 0, width, height))
    frame = bytearray(width * height * 4)
    while True:
        kind = recv(1)[0]
        if kind == 0:
            recv(1)
            for _ in range(struct.unpack(">H", recv(2))[0]):
                x, y, w, h, encoding = struct.unpack(">HHHHi", recv(12))
                if encoding != 0:
                    continue
                data = recv(w * h * 4)
                for row in range(h):
                    start = ((y + row) * width + x) * 4
                    frame[start : start + w * 4] = data[row * w * 4 : (row + 1) * w * 4]
            break
        if kind == 2:
            continue
        if kind == 3:
            recv(3)
            recv(struct.unpack(">I", recv(4))[0])
    rgb = bytearray(width * height * 3)
    rgb[0::3], rgb[1::3], rgb[2::3] = frame[2::4], frame[1::4], frame[0::4]
    rows = b"".join(
        b"\x00" + bytes(rgb[r * width * 3 : (r + 1) * width * 3]) for r in range(height)
    )

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    png = b"\x89PNG\r\n\x1a\n" + chunk(
        b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    )
    png += chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")
    Path(args.out).write_bytes(png)
    print(f"{args.out} {width}x{height}")


KEYSYM = {"\r": 0xFF0D, "\n": 0xFF0D}
SHIFTED = '~!@#$%^&*()_+{}|:"<>?'


def type_text(args):
    """Type lines on the VSR console line; default grants the aux line network-admin."""
    sock, _recv, _w, _h = _vnc(_consoles()[args.node])
    lines = args.lines or [
        "",
        "system-view",
        "line aux 0",
        "authentication-mode none",  # a fresh VSR's aux line refuses the login otherwise
        "user-role network-admin",
        "quit",
        "return",
    ]

    def key(sym, shift=False):
        if shift:
            sock.sendall(struct.pack(">BBxxI", 4, 1, 0xFFE1))
        sock.sendall(struct.pack(">BBxxI", 4, 1, sym) + struct.pack(">BBxxI", 4, 0, sym))
        if shift:
            sock.sendall(struct.pack(">BBxxI", 4, 0, 0xFFE1))
        time.sleep(0.04)

    for line in lines:
        if line == "^C":
            sock.sendall(struct.pack(">BBxxI", 4, 1, 0xFFE3))
            key(ord("c"))
            sock.sendall(struct.pack(">BBxxI", 4, 0, 0xFFE3))
        else:
            for char in line:
                key(KEYSYM.get(char, ord(char)), char.isupper() or char in SHIFTED)
            key(0xFF0D)
        time.sleep(1.5)
    print(f"{args.node}: typed {len(lines)} lines")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("project").set_defaults(func=project)
    p = sub.add_parser("screen")
    p.add_argument("node")
    p.add_argument("--out", default="vnc.png")
    p.set_defaults(func=screen)
    p = sub.add_parser("type")
    p.add_argument("node")
    p.add_argument("lines", nargs="*")
    p.set_defaults(func=type_text)
    p = sub.add_parser("push")
    p.add_argument("node", choices=["all", *MANAGED, *UNMANAGED, *PCS])
    p.set_defaults(func=push)
    p = sub.add_parser("console")
    p.add_argument("node")
    p.add_argument("commands", nargs="+")
    p.add_argument("--wait", type=float, default=3)
    p.set_defaults(func=console_cmd)
    p = sub.add_parser("account")
    p.add_argument("--username", default="admin")
    p.set_defaults(func=account)
    p = sub.add_parser("traffic")
    p.add_argument("--stop", action="store_true")
    p.set_defaults(func=traffic)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
