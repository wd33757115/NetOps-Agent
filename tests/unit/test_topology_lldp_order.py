"""Generic LLDP blocks: the local port may precede or follow the neighbour identity."""

import pytest

from src.apps.topology.backend.collectors.tiers import generic_lldp

# NX-OS "show lldp neighbors detail" prints Chassis id and Port id before Local Port id.
NXOS_CHASSIS_FIRST = """Capability codes:
Chassis id: 0011.2233.4455
Port id: Ethernet1/2
Local Port id: Eth1/1
System Name: edge-b
Management Address: 10.0.0.2
Chassis id: 0011.2233.4466
Port id: Ethernet1/3
Local Port id: Eth1/2
System Name: edge-c
Total entries displayed: 2
"""
IOS_LOCAL_FIRST = """Local Intf: Gi0/1
Chassis id: 0011.2233.4455
Port id: Gi0/2
System Name: edge-b
Local Intf: Gi0/2
Chassis id: 0011.2233.4466
Port id: Gi0/3
System Name: edge-c
"""
# Comware header spacing varies between releases: "port 17[GE1/0]" and "port 1 [GE1/0/1]".
COMWARE_SPACED = """LLDP neighbor-information of port 1 [GigabitEthernet1/0/1]:
LLDP agent nearest-bridge:
 Chassis type : MAC address
 Chassis ID : 0011-2233-4455
 Port ID type : Interface name
 Port ID : GigabitEthernet1/0/2
 Management address : 10.0.0.2
"""


@pytest.mark.parametrize(
    "text,family,expected",
    [
        (
            NXOS_CHASSIS_FIRST,
            "nxos",
            [("Ethernet1/1", "Ethernet1/2", ["10.0.0.2"]), ("Ethernet1/2", "Ethernet1/3", [])],
        ),
        (
            IOS_LOCAL_FIRST,
            "ios",
            [("GigabitEthernet0/1", "Gi0/2", []), ("GigabitEthernet0/2", "Gi0/3", [])],
        ),
        (
            COMWARE_SPACED,
            "comware",
            [("GigabitEthernet1/0/1", "GigabitEthernet1/0/2", ["10.0.0.2"])],
        ),
    ],
)
def test_local_port_before_or_after_neighbour(text, family, expected):
    result = generic_lldp.parse(text, family)
    assert result.rejected == 0
    assert [
        (r["local_if"], r["remote_port_id"], r["remote_mgmt_addresses"]) for r in result.records
    ] == expected


def test_a_block_without_its_own_local_port_is_rejected_not_inherited():
    # Without a header, a neighbour must never borrow the previous neighbour's local port.
    text = NXOS_CHASSIS_FIRST.replace("Local Port id: Eth1/2\n", "")
    result = generic_lldp.parse(text, "nxos")
    assert [r["local_if"] for r in result.records] == ["Ethernet1/1"]
    assert result.rejected == 1
