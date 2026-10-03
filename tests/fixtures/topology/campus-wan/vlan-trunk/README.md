# VLAN and switchport lab observations

Authorized read-only SSH batch `20261003-014006`, captured on 2026-10-03 from
the same eight campus lab devices as the parent fixtures. ACC1/ACC2 were sampled
after the owner confirmed their changed host keys. No device configuration was
changed. The manifest records the individual capture times, commands, transport
status, byte counts, SHA-256 and zero literal/sensitive-configuration matches.

There are 24 command observations: Comware interface brief, VLAN brief and trunk
on BR1/BR2; IOS VLAN brief, interfaces trunk and interfaces switchport on the six
Cisco nodes. CORE1/CORE2 reject these switching commands (`unsupported`). BR1/BR2
return genuinely empty trunk outputs; nonempty Comware trunk syntax is still
unverified and must fail visibly until a real sample is available.

The IOS trunk sections distinguish allowed, active and STP-forwarding VLANs.
Allowed VLANs are `10,20`; STP subsets do not narrow the RFC 8944 outer tag range.
The native VLAN is 1 and outside that allowed range. The switchport outputs retain
access VLAN/PVID, administrative and operational mode, and administrative native
tagging. An administrative setting alone does not prove operational native
tagging. Distribution physical members report `trunk (member of bundle Po1)`.

`outer-tag` describes tagged traffic under RFC 8944, so access VLANs/PVID are kept
as device observations and are not written into that topology leaf. This batch
does not constitute an IEEE 802.1Q device YANG export, a STP model or a fault test.

Import using `scripts/topology/import_lab_fixtures.py --lab campus-vlan`. The
importer verifies secrets and hashes and refuses to replace different fixtures.
Replays are covered by `test_topology_vlan_parsers.py`,
`test_topology_vlan_model.py` and `test_topology_campus_wan.py`.
