# VPN + EVPN/VXLAN fabric lab (vpn-fabric)

This is the lab for [implementation plan §8 M5](../../../docs/network-digital-twin-implementation.md), covering VPN, EVPN, VXLAN and DC fabric, approved by the owner on 2026-10-03. It is a separate GNS3 project, `netops-vpn-fabric`. campus-wan is left untouched; the GNS3 GUI keeps only one project open, so opening this one closes campus-wan.

It uses the same out-of-band management network as campus-wan (MGMT-SW → cloud → host 10.10.1.254). The Cisco management ports are in VRF MGMT, and management ports neither send nor receive LLDP.

```text
                 SPINE1 (IOL L3)          SPINE2 (IOL L3)        OSPF underlay, BGP EVPN route reflectors, AS 65100
                 /     |     \           /     |     \
        LEAF1 (IOL L2)  LEAF2 (IOL L2)  LEAF3 (H3C VSR)           VTEPs; NVE / tunnels sourced from Loopback0
           |               |             |        |
         H-R1            H-B2          H-R3     H-B3
     RED 192.168.10.11  BLUE .10.12   RED .10.13  BLUE 192.168.20.14

     A1 ─┐                                    ┌─ A2   CUST-A 10.2.1.0/24
         PE1 (IOL L3) ─── P (IOL L3) ─── PE2 (H3C VSR)              OSPF + LDP core, VPNv4 iBGP, AS 65200
     B1 ─┘                                    └─ S2   SHARED 10.99.1.0/24
  CUST-A and CUST-B are both 10.1.1.0/24
```

## Overlays

| Overlay | Members | Identifiers | Behavior (pinged during bring-up) |
|---|---|---|---|
| RED L2VNI | LEAF1 VLAN 10, LEAF3 VSI RED | EVI 10, VNI 10010, RT 65100:10010 | H-R1 ↔ H-R3 bridged across vendors |
| BLUE L2VNI | LEAF2 VLAN 20 (gateway 192.168.10.1) | EVI 20, VNI 10020, RT 65100:10020 | — |
| BLUE L2VNI | LEAF3 VSI BLUE-20 (gateway 192.168.20.1) | VNI 10021, RT 65100:10021 | — |
| BLUE L3VNI | LEAF2 VRF BLUE (VLAN 920), LEAF3 vpn-instance BLUE (Vsi-interface920) | VNI 50020, RT 65100:50020 | H-B2 → H-B3 routed by symmetric IRB (TTL 63) |
| Tenant isolation | RED and BLUE both use 192.168.10.0/24 | — | H-R1 → H-B2 unreachable |
| CUST-A | PE1 Ethernet1/0, PE2 GE3/0 | RD 65200:1 / 65200:11, export 65200:1, import 65200:1 and 65200:99 | A1 ↔ A2 |
| CUST-B | PE1 Ethernet1/1 | RD 65200:2, RT 65200:2 | B1 → A2 and B1 → S2 unreachable (same 10.1.1.0/24 as CUST-A) |
| SHARED | PE2 GE4/0 | RD 65200:99, export 65200:99, import 65200:99 and 65200:1 | A1 ↔ S2 (route leaking by RT) |

## Addressing

| Device | Management | Loopback0 | Links |
|---|---|---|---|
| SPINE1 | 10.10.1.21 (e0/0) | 10.255.10.1 | e0/1 10.10.11.1/30 (LEAF1), e0/2 10.10.12.1/30 (LEAF2), e0/3 10.10.13.1/30 (LEAF3) |
| SPINE2 | 10.10.1.22 | 10.255.10.2 | e0/1 10.10.21.1/30, e0/2 10.10.22.1/30, e0/3 10.10.23.1/30 |
| LEAF1 | 10.10.1.23 | 10.255.10.11 | e0/1 10.10.11.2, e0/2 10.10.21.2; e1/0 H-R1 (VLAN 10) |
| LEAF2 | 10.10.1.24 | 10.255.10.12 | e0/1 10.10.12.2, e0/2 10.10.22.2; e1/0 H-B2 (VLAN 20) |
| LEAF3 | 10.10.1.25 (GE1/0) | 10.255.10.13 | GE2/0 10.10.13.2, GE3/0 10.10.23.2; GE4/0 H-R3 (VSI RED), GE5/0 H-B3 (VSI BLUE-20) |
| PE1 | 10.10.1.26 | 10.255.20.1 | e0/1 10.20.12.1/30 (P); e1/0 A1 (CUST-A), e1/1 B1 (CUST-B) |
| P | 10.10.1.27 | 10.255.20.2 | e0/1 10.20.12.2, e0/2 10.20.23.1/30 (PE2) |
| PE2 | 10.10.1.28 (GE1/0) | 10.255.20.3 | GE2/0 10.20.23.2; GE3/0 A2 (CUST-A), GE4/0 S2 (SHARED) |

## Build

The steps are the same as campus-wan (`../campus_wan/README.md`), using `vpn_fabric/lab.py`:

1. `lab.py project` writes `~/GNS3/projects/netops-vpn-fabric/`. Open it in GNS3 and start all nodes.
2. Run `lab.py type LEAF3` / `type PE2` only after the VSR prints "Line con0 is available" (lines typed during automatic configuration are lost). This lets the aux line log in as network-admin. Then run `lab.py push all`.
3. The owner runs `lab.py account`, which creates the lab login on the 8 managed devices.
4. Run `lab.py traffic`.

## After a restart: re-bind LEAF2's L3VNI

When the IOL images boot from saved config, `vlan configuration 920 / member vni 50020` is
applied before `interface nve1` exists, so LEAF2's L3VNI is not bound to VRF BLUE. Symptoms:
LEAF2 receives LEAF3's type-5 route for 192.168.20.0/24 (RT 65100:50020, Router MAC, VNI
50020) but does not import it into BLUE, `show nve peers` is empty, and LEAF3 has no route
to 192.168.10.0/24 or tunnel to LEAF2. BGP clears and a reload do not help. Re-applying the
membership (no net config change) does:

```text
lab.py console LEAF2 "configure terminal" "interface nve1" "no member vni 50020 vrf BLUE" "member vni 50020 vrf BLUE" "exit" "vlan configuration 920" "no member vni 50020" "member vni 50020" "end"
```

Then check `show ip route vrf BLUE` on LEAF2 (B routes via Vlan920) and
`display ip routing-table vpn-instance BLUE` on LEAF3. Start `lab.py traffic` before
sampling: host ARP entries age out within hours.
