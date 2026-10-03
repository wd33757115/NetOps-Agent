"""Collect read-only CLI samples from lab devices for topology parser fixtures.

Only fixed, read-only ``display``/``show`` commands are sent (Comware or Cisco IOS,
chosen by the SSH banner). The password is read from the NETOPS_LAB_PASSWORD
environment variable, else from that line of the owner's repository .env, else prompted
interactively; it is never written to disk, logs, or output files.

Output: .runtime/lab-samples/<timestamp>/<host>/<command>.txt (normalized),
<command>.raw.txt (exact bytes received) + meta.json
Host keys: trust-on-first-use, pinned in .runtime/lab-samples/known_hosts
(a changed key aborts the connection).

Usage (Windows, project venv):
    venv\\Scripts\\python.exe scripts\\topology\\collect_lab_samples.py 10.10.1.1 --username admin
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parents[2]
OUT_ROOT = ROOT / ".runtime" / "lab-samples"

# H3C Comware 7, read-only. Order matters: paging off first.
COMMANDS = [
    ("paging", "screen-length disable"),
    ("version", "display version"),
    ("inventory", "display device manuinfo"),
    ("lldp_local", "display lldp local-information"),
    ("lldp_verbose", "display lldp neighbor-information verbose"),
    ("lldp_list", "display lldp neighbor-information list"),
    ("interfaces", "display interface brief"),
    ("ip_interfaces", "display ip interface brief"),
    ("arp", "display arp"),
    ("mac", "display mac-address"),
    ("vlan", "display vlan brief"),
    ("trunk", "display port trunk"),
    ("lag", "display link-aggregation verbose"),
    ("routes", "display ip routing-table"),
    ("vrfs", "display ip vpn-instance"),
    ("irf", "display irf"),
    ("vrrp", "display vrrp verbose"),
    ("ospf", "display ospf peer"),
    ("ospf_interfaces", "display ospf interface"),
    ("ospf_process", "display ospf"),
    ("bgp", "display bgp peer ipv4"),
    # VPN / EVPN / VXLAN (plan §8 M5)
    ("bgp_vpnv4", "display bgp peer vpnv4"),
    ("bgp_evpn", "display bgp peer l2vpn evpn"),
    ("bgp_vpnv4_routes", "display bgp routing-table vpnv4"),
    ("bgp_evpn_routes", "display bgp l2vpn evpn"),
    ("ldp", "display mpls ldp peer"),
    ("mpls_lsp", "display mpls lsp"),
    ("vxlan_tunnels", "display vxlan tunnel"),
    ("vsi", "display l2vpn vsi verbose"),
    ("evpn_instances", "display evpn instance verbose"),
    ("evpn_imet", "display evpn auto-discovery imet"),
    ("tunnel_interfaces", "display interface tunnel"),
    ("evpn_mac", "display evpn route mac"),
    ("config", "display current-configuration"),
]

# Cisco IOS / IOS-XE (chosen when the SSH banner says Cisco), read-only.
CISCO_COMMANDS = [
    ("paging", "terminal length 0"),
    ("width", "terminal width 511"),
    ("version", "show version"),
    ("inventory", "show inventory"),
    ("lldp_verbose", "show lldp neighbors detail"),
    ("lldp_list", "show lldp neighbors"),
    ("interfaces", "show interfaces"),
    ("interfaces_status", "show interfaces status"),
    ("interfaces_description", "show interfaces description"),
    ("ip_interfaces", "show ip interface brief"),
    ("ip_interface", "show ip interface"),
    ("arp", "show ip arp"),
    ("arp_mgmt", "show ip arp vrf MGMT"),
    ("mac", "show mac address-table"),
    ("vlan", "show vlan brief"),
    ("trunk", "show interfaces trunk"),
    ("switchport", "show interfaces switchport"),
    ("lag", "show etherchannel summary"),
    ("stp", "show spanning-tree"),
    ("routes", "show ip route"),
    ("routes_mgmt", "show ip route vrf MGMT"),
    ("vrfs", "show vrf"),
    ("vrrp", "show vrrp"),
    ("ospf", "show ip ospf neighbor"),
    ("ospf_interfaces", "show ip ospf interface brief"),
    ("ospf_process", "show ip ospf"),
    ("bgp", "show ip bgp summary"),
    # VPN / EVPN / VXLAN (plan §8 M5)
    ("vrf_detail", "show vrf detail"),
    ("bgp_vpnv4", "show bgp vpnv4 unicast all summary"),
    ("bgp_evpn", "show bgp l2vpn evpn summary"),
    ("bgp_vpnv4_routes", "show bgp vpnv4 unicast all"),
    ("bgp_evpn_routes", "show bgp l2vpn evpn"),
    ("ldp", "show mpls ldp neighbor"),
    ("mpls_forwarding", "show mpls forwarding-table"),
    ("nve_peers", "show nve peers"),
    ("nve_vni", "show nve vni"),
    ("nve_interface", "show nve interface nve1 detail"),
    ("evpn_evi", "show l2vpn evpn evi detail"),
    ("evpn_mac", "show l2vpn evpn mac"),
    ("config", "show running-config"),
]
SETUP = ("paging", "width")


def lab_password(prompt: str) -> str:
    """NETOPS_LAB_PASSWORD: environment, then the owner's repository .env, then a prompt.

    Every lab tool reads the same source, so device accounts and samplers cannot drift.
    """
    if os.environ.get("NETOPS_LAB_PASSWORD"):
        return os.environ["NETOPS_LAB_PASSWORD"]
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            if key.strip() == "NETOPS_LAB_PASSWORD" and value.strip():
                return value.strip().strip("\"'")
    return getpass.getpass(prompt)


def vrf_commands(names, cisco: bool) -> list[tuple[str, str]]:
    """Per-VRF tables for the VRF/vpn-instance names given with --vrf (read-only)."""
    commands = []
    for name in names:
        if cisco:
            commands += [
                (f"routes_vrf_{name}", f"show ip route vrf {name}"),
                (f"arp_vrf_{name}", f"show ip arp vrf {name}"),
            ]
        else:
            commands += [
                (f"vrf_{name}", f"display ip vpn-instance instance-name {name}"),
                (f"routes_vrf_{name}", f"display ip routing-table vpn-instance {name}"),
                (f"arp_vrf_{name}", f"display arp vpn-instance {name}"),
            ]
    return commands


# Refuse anything that is not a pure display/show command (defence in depth).
READ_ONLY = re.compile(
    r"^((?:display|show)\s+[^;|\r\n]+|screen-length disable|terminal (?:length 0|width 511))$"
)
PROMPT = re.compile(r"(?m)^(?:[<\[][^\r\n<>\[\]]{1,64}[>\]]|[\w.\-]{1,64}[#>])\s*$")
MORE = re.compile(r"-{2,}\s*More\s*-{2,}")
# Paging prompt plus the cursor-back/space padding the device uses to erase it.
MORE_ERASE = re.compile(r"[ \t]*-{2,}\s*More\s*-{2,}(?:\x1b\[\d*D|[ \x08])*")
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


class PromptTimeout(TimeoutError):  # noqa: N818 - existing collector helper API
    def __init__(self, message: str, received: str):
        super().__init__(message)
        self.received = received


# Interactive questions we must never answer automatically (they change device state).
QUESTION = re.compile(
    r"(?i)(\[Y/N\]|\(Y/N\)|change (?:your |the )?password|new password|password.*expired)"
)


def read_until_prompt(channel, timeout: float, *, wake: bool = False) -> str:
    """Read until a Comware prompt. With wake=True, press Enter when the device is idle."""
    buffer = ""
    deadline = time.monotonic() + timeout
    last_data = time.monotonic()
    wakes = 0
    while time.monotonic() < deadline:
        if channel.recv_ready():
            chunk = channel.recv(65535).decode("utf-8", errors="replace")
            buffer += chunk
            last_data = time.monotonic()
            if MORE.search(chunk):
                channel.send(" ")
                continue
            plain = ANSI.sub("", buffer)
            lines = [line for line in plain.replace("\r", "\n").split("\n") if line.strip()]
            if lines and PROMPT.search(lines[-1].strip()):
                return buffer
            if wake and lines and QUESTION.search("\n".join(lines[-3:])):
                raise PromptTimeout(
                    "device is asking an interactive question; not answered automatically", buffer
                )
        elif channel.closed:
            raise ConnectionError("channel closed by device")
        else:
            plain_tail = ANSI.sub("", buffer).rstrip()
            # Never press Enter at an input prompt (e.g. "password:" or "Continue?").
            at_input = plain_tail.endswith((":", "?", "]"))
            if wake and not at_input and wakes < 3 and time.monotonic() - last_data > 2:
                channel.send("\r")
                wakes += 1
                last_data = time.monotonic()
            time.sleep(0.05)
    raise PromptTimeout(f"prompt not seen within {timeout}s", buffer)


def normalize_newlines(text: str) -> str:
    # Order matters: Comware emits CR CR LF / LF CR sequences.
    for seq in ("\r\r\n", "\r\n", "\n\r"):
        text = text.replace(seq, "\n")
    return text.replace("\r", "\n")


def clean(raw: str, command: str) -> str:
    text = normalize_newlines(ANSI.sub("", MORE_ERASE.sub("", raw)))
    lines = text.split("\n")
    if lines and command in lines[0]:
        lines = lines[1:]  # echoed command
    if lines and PROMPT.match(lines[-1].strip() or "x"):
        lines = lines[:-1]  # trailing prompt
    return "\n".join(lines).strip("\n") + "\n"


def collect(host: str, username: str, password: str, out_dir: Path, port: int, vrfs=()) -> dict:
    known_hosts = OUT_ROOT / "known_hosts"
    known_hosts.parent.mkdir(parents=True, exist_ok=True)
    known_hosts.touch(exist_ok=True)
    client = paramiko.SSHClient()
    client.load_host_keys(str(known_hosts))
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())  # TOFU; mismatch still raises
    meta = {"host": host, "port": port, "username": username, "commands": []}
    started = time.monotonic()
    try:
        client.connect(
            host,
            port=port,
            username=username,
            password=password,
            timeout=10,
            auth_timeout=10,
            banner_timeout=15,
            look_for_keys=False,
            allow_agent=False,
        )
        client.save_host_keys(str(known_hosts))
        key = client.get_transport().get_remote_server_key()
        meta["host_key"] = {"type": key.get_name(), "fingerprint_sha256": key.fingerprint}
        meta["server_version"] = client.get_transport().remote_version
        channel = client.invoke_shell(width=512, height=1000)
        banner = read_until_prompt(channel, 30, wake=True)
        meta["prompt"] = ANSI.sub("", banner).strip().splitlines()[-1].strip()
        host_dir = out_dir / host
        host_dir.mkdir(parents=True, exist_ok=True)
        cisco = "Cisco" in meta["server_version"]
        commands = (CISCO_COMMANDS if cisco else COMMANDS) + vrf_commands(vrfs, cisco)
        for command_id, command in commands:
            if not READ_ONLY.match(command):
                raise ValueError(f"refusing non read-only command: {command}")
            entry = {"id": command_id, "command": command}
            t0 = time.monotonic()
            channel.send(command + "\n")
            try:
                raw = read_until_prompt(channel, 120 if command_id == "routes" else 60)
                text = clean(raw, command)
                entry.update(status="ok", lines=text.count("\n"))
                rejected = re.search(
                    r"(?im)(unrecognized command|wrong parameter|incomplete command|% ?invalid)",
                    text,
                )
                if rejected:
                    entry["status"] = "rejected_by_device"
                if command_id not in SETUP:
                    # newline="" keeps Windows from turning \n into \r\n.
                    with open(
                        host_dir / f"{command_id}.txt", "w", encoding="utf-8", newline=""
                    ) as fh:
                        fh.write(text)
                    # Exact bytes received, including echo, paging and prompt,
                    # are preserved for parser fixtures.
                    with open(
                        host_dir / f"{command_id}.raw.txt", "w", encoding="utf-8", newline=""
                    ) as fh:
                        fh.write(raw)
            except (TimeoutError, ConnectionError) as exc:
                entry.update(status="failed", error=str(exc))
                if isinstance(exc, PromptTimeout):
                    entry["received_tail"] = ANSI.sub("", exc.received)[-800:]
            entry["seconds"] = round(time.monotonic() - t0, 2)
            entry["finished_at"] = datetime.now(timezone.utc).isoformat()
            meta["commands"].append(entry)
            print(f"  {host} {command:<45} {entry['status']}")
        channel.close()
        meta["status"] = "ok"
    except paramiko.BadHostKeyException:
        meta["status"] = "host_key_changed"
        meta["error"] = (
            "host key differs from pinned key; verify the device, "
            "then remove its line from known_hosts"
        )
    except PromptTimeout as exc:
        meta["status"] = "shell_not_ready"
        meta["error"] = str(exc)
        # What the device actually printed after login (no secrets are ever sent to it here).
        meta["received_after_login"] = ANSI.sub("", exc.received)[-1500:]
    except paramiko.AuthenticationException:
        meta["status"] = "auth_failed"
        meta["error"] = "authentication failed (not retried)"
    except Exception as exc:  # noqa: BLE001 - report any transport error in meta
        meta["status"] = "connection_failed"
        meta["error"] = f"{type(exc).__name__}: {exc}".replace(password, "[redacted]")
    finally:
        client.close()
    meta["seconds"] = round(time.monotonic() - started, 2)
    return meta


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("hosts", nargs="+")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--port", type=int, default=22)
    parser.add_argument("--vrf", action="append", default=[], help="also sample this VRF")
    args = parser.parse_args()
    password = lab_password("SSH password: ")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = OUT_ROOT / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for host in args.hosts:
        print(f"[{host}] connecting ...")
        meta = collect(host, args.username, password, out_dir, args.port, args.vrf)
        print(f"[{host}] {meta['status']} {meta.get('error', '')}")
        results.append(meta)
    (out_dir / "meta.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\nSamples written to {out_dir}")
    return 0 if all(r["status"] == "ok" for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
