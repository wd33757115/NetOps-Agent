# Campus + WAN lab fixtures

Source: authorized GNS3 lab batch `20261003-010038` from the lab built by
`scripts/topology/campus_wan` (see its README for wiring and addressing). Two H3C VSR1000
borders (Comware 7.1.064 Release 1362P12) and six Cisco IOL 17.15.1 nodes: two core
routers, two distribution and two access switches. Folders are named after the lab
nodes; management addresses are 10.10.1.11–18.

Only the outputs the topology model reads (plus VRRP, link aggregation and spanning tree
for the next facts) are copied; device configurations are not. Each capability has a
cleaned `.txt` and the collector's `.raw.txt`; `manifest.json` records byte counts and
SHA256. Commands a device rejects (MAC and aggregation commands on the core routers) are
recorded as `unsupported`.

Import: `python scripts/topology/import_lab_fixtures.py --lab campus-wan`. The importer
reads the lab password from `NETOPS_LAB_PASSWORD` or a hidden prompt, runs the sensitive
data scanner and direct byte checks, then copies the approved files.

`expected.json` is the lab wiring, not something the CLI proves by itself: what is
checked against the model is listed there, including what cannot be observed.
Replay checks live in `tests/unit/test_topology_campus_wan.py`.

The separately timed [VLAN/trunk batch](vlan-trunk/README.md) adds switchport
observations without replacing this original batch. Replay tests use the newer
VLAN/trunk/switchport captures and retain the original other capabilities.
