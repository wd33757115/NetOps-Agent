"""Generic table reading and entry comparison, on real Comware output from the lab."""

import time

from src.platform.device_access.text_compare import comparable_lines
from src.platform.device_access.text_table import (
    Table,
    compare,
    find,
    layout,
    pair,
    regions,
    summary,
)

ARP = (
    "Type: S-Static   D-Dynamic   O-Openflow   R-Rule   M-Multiport  I-Invalid\n"
    "IP address      MAC address    VLAN/VSI name Interface/Link ID        Aging Type\n"
    "2.2.2.2         0cd2-5bc5-0001 --            GE2/0                    17    D   \n"
    "10.10.1.1       0cd2-5bc5-0000 --            GE1/0                    16    D   \n"
    "10.10.1.254     0015-5d01-0314 --            GE1/0                    19    D\n"
)
OSPF = (
    "OSPF Process 1 with Router ID 3.3.3.3\n"
    "               Neighbor Brief Information\n\n"
    " Area: 0.0.0.0        \n"
    " Router ID       Address         Pri Dead-Time  State             Interface\n"
    " 4.4.4.4         2.2.2.2         1   31         Full/DR           GE2/0\n"
)
ROUTES = (
    "Destinations : 5\tRoutes : 5\n\n"
    "Destination/Mask   Proto   Pre Cost        NextHop         Interface\n"
    "2.2.2.0/24         Direct  0   0           2.2.2.1         GE2/0\n"
    "4.4.4.4/32         O_INTRA 10  1           2.2.2.2         GE2/0\n"
    "10.10.1.0/24       Direct  0   0           10.10.1.2       GE1/0\n"
    "127.0.0.0/8        Direct  0   0           127.0.0.1       InLoop0\n"
    "192.168.1.0/24     O_INTRA 10  2           2.2.2.2         GE2/0\n"
)
BRIEF = (
    "Brief information on interfaces in route mode:\n"
    "Link: ADM - administratively down; Stby - standby\n"
    "Protocol: (s) - spoofing\n"
    "Interface            Link Protocol Primary IP      Description                \n"
    "GE1/0                UP   UP       10.10.1.2       \n"
    "GE2/0                UP   UP       2.2.2.1         \n"
    "GE4/0                DOWN DOWN     --              \n\n"
    "Brief information on interfaces in bridge mode:\n"
    "Link: ADM - administratively down; Stby - standby\n"
    "Speed: (a) - auto\n"
    "Duplex: (a)/A - auto; H - half; F - full\n"
    "Type: A - access; T - trunk; H - hybrid\n"
    "Interface            Link Speed     Duplex Type PVID Description                \n"
    "GE3/0                UP   1G        F      A    100\n"
)
LLDP = (
    "Chassis ID : * -- -- Nearest nontpmr bridge neighbor\n"
    "             # -- -- Nearest customer bridge neighbor\n"
    "             Default -- -- Nearest bridge neighbor\n"
    "Local Interface Chassis ID      Port ID                         System Name     \n"
    "GE1/0           7425-8ae3-ee8e  GigabitEthernet1/0              H3C\n"
    "GE2/0           7425-8ae3-ee8e  GigabitEthernet2/0              H3C\n"
)
MAC_EMPTY = "MAC Address      VLAN ID    State            Port/Nickname            Aging\n"
VLAN = "Total VLANs: 2\n The VLANs include:\n 1(default), 100,\n"


def lines(text):
    return comparable_lines(text, "x")


def columns(text):
    found = lines(text)
    return [layout(t, t)[1] for t in (Table(found, h, e) for h, e in regions(found))]


def only(before, after):
    pairs, _, _ = pair(lines(before), lines(after))
    [(a, b)] = pairs
    return compare(a, b)


def test_real_tables_are_read_without_a_parser_per_command():
    assert columns(ARP) == [
        ["IP address", "MAC address", "VLAN/VSI name", "Interface/Link ID", "Aging", "Type"]
    ]
    # Neither the legend above nor the prose before the area is taken for a header.
    assert columns(OSPF) == [["Router ID", "Address", "Pri", "Dead-Time", "State", "Interface"]]
    assert columns(ROUTES) == [
        ["Destination/Mask", "Proto", "Pre", "Cost", "NextHop", "Interface"]
    ]
    route_mode, bridge_mode = columns(BRIEF)
    # A column nobody filled ("Description") still has its own name.
    assert route_mode == ["Interface", "Link", "Protocol", "Primary IP", "Description"]
    assert bridge_mode[:5] == ["Interface", "Link", "Speed", "Duplex", "Type"]
    assert columns(LLDP) == [["Local Interface", "Chassis ID", "Port ID", "System Name"]]
    assert columns(VLAN) == [] and columns(MAC_EMPTY) == []


def test_timers_show_as_one_numeric_column_and_lost_entries_are_named():
    after = ARP.replace("17    D", "3     D").replace("16    D", "9     D").replace(
        "10.10.1.254     0015-5d01-0314 --            GE1/0                    19    D\n", ""
    )
    table = only(ARP, after)
    assert table["removed"]["examples"] == ["10.10.1.254 | 0015-5d01-0314 | -- | GE1/0 | 19 | D"]
    assert table["changed"]["columns"] == {
        "Aging": {"rows": 2, "examples": ["17→3", "16→9"], "numeric": True}
    }
    assert summary(table) == (
        "表 IP address 等 6 列，3→2 条：消失 1；内容变化 2（Aging 2 行（数值））"
    )


def test_state_changes_name_the_entry_and_columns():
    table = only(OSPF, OSPF.replace("31         Full/DR", "38         Full/BDR"))
    assert table["changed"]["examples"] == ["4.4.4.4：Dead-Time 31→38，State Full/DR→Full/BDR"]
    assert "numeric" not in table["changed"]["columns"]["State"]
    after = BRIEF.replace("GE2/0                UP   UP  ", "GE2/0                DOWN DOWN")
    (route_mode, b1), (bridge_mode, b2) = pair(lines(BRIEF), lines(after))[0]
    assert compare(route_mode, b1)["changed"]["examples"] == [
        "GE2/0：Link UP→DOWN，Protocol UP→DOWN"
    ]
    assert compare(bridge_mode, b2) is None


def test_lost_routes_are_summarised_by_what_they_share():
    after = ROUTES.replace(
        "4.4.4.4/32         O_INTRA 10  1           2.2.2.2         GE2/0\n", ""
    ).replace("192.168.1.0/24     O_INTRA 10  2           2.2.2.2         GE2/0\n", "")
    table = only(ROUTES, after)
    # Words before numbers: the protocol, next hop and interface explain the loss.
    assert table["removed"]["mostly"] == [
        "Proto=O_INTRA（2/2）", "NextHop=2.2.2.2（2/2）", "Interface=GE2/0（2/2）"
    ]


def test_equal_cost_routes_are_keyed_by_prefix_and_next_hop():
    second = (
        "192.168.1.0/24     O_INTRA 10  2           2.2.2.2         GE2/0\n"
        "                   O_INTRA 10  2           2.2.3.2         GE4/0\n"
    )
    before = ROUTES.replace("192.168.1.0/24     O_INTRA 10  2           2.2.2.2         GE2/0\n",
                            second)
    after = before.replace("2.2.3.2         GE4/0", "2.2.3.9         GE4/0")
    table = only(before, after)
    assert table["key"] == ["Destination/Mask", "NextHop"]
    assert table["removed"]["examples"] == ["192.168.1.0/24 | O_INTRA | 10 | 2 | 2.2.3.2 | GE4/0"]
    assert table["added"]["examples"] == ["192.168.1.0/24 | O_INTRA | 10 | 2 | 2.2.3.9 | GE4/0"]


def test_a_table_that_vanishes_header_and_all_reads_as_entries_gone():
    from src.platform.device_access.text_compare import compare_text

    # No neighbours left: Comware prints neither the area nor the header.
    after = "OSPF Process 1 with Router ID 3.3.3.3\n               Neighbor Brief Information\n"
    result = compare_text(OSPF, after, command="display ospf peer")
    [table] = result["tables"]
    assert table["rows"] == [1, 0] and table["removed"]["count"] == 1
    assert table["removed"]["examples"] == ["4.4.4.4 | 2.2.2.2 | 1 | 31 | Full/DR | GE2/0"]
    # The area line is text; the neighbour is not repeated as text.
    assert "- Area: 0.0.0.0" in result["diff"]
    assert not any("4.4.4.4" in line for line in result["diff"])


def test_an_empty_table_pairs_with_its_header_on_the_other_side():
    learned = MAC_EMPTY + (
        "0050-7966-6802   100        Learned          GE3/0                    Y\n"
    )
    table = only(MAC_EMPTY, learned)
    assert table["rows"] == [0, 1] and table["added"]["count"] == 1
    gone = only(learned, MAC_EMPTY)
    assert gone["removed"]["examples"] == ["0050-7966-6802 | 100 | Learned | GE3/0 | Y"]


def test_a_hundred_thousand_routes_compare_quickly_into_one_line():
    header = "Destination/Mask   Proto   Pre Cost        NextHop         Interface\n"

    def route(n, hop, port):
        prefix = f"10.{n // 65536}.{n // 256 % 256}.{n % 256}/32"
        return f"{prefix:<19}O_INTRA 10  2           {hop:<16}{port}\n"

    before = header + "".join(
        route(n, "3.3.3.1" if n % 10 == 0 else "2.2.2.1", "GE4/0" if n % 10 == 0 else "GE2/0")
        for n in range(100_000)
    )
    after = "".join(line + "\n" for line in before.splitlines() if "3.3.3.1" not in line)
    started = time.perf_counter()
    table = only(before, after)
    assert time.perf_counter() - started < 10
    assert table["rows"] == [100_000, 90_000] and table["removed"]["count"] == 10_000
    assert table["removed"]["mostly"][:2] == [
        "NextHop=3.3.3.1（10000/10000）", "Interface=GE4/0（10000/10000）"
    ]
    assert len(table["removed"]["examples"]) == 5


def test_lookup_lines_up_a_filtered_read_with_the_full_baseline():
    filtered = (
        "Summary count : 1\n\n"
        "Destination/Mask   Proto   Pre Cost        NextHop         Interface\n"
        "192.168.1.0/24     O_INTRA 10  20          2.2.2.2         GE2/0\n"
    )
    found = find(lines(ROUTES), lines(filtered), "192.168.1")
    [table] = found["tables"]
    assert table["entries"] == [{
        "baseline": "192.168.1.0/24 | O_INTRA | 10 | 2 | 2.2.2.2 | GE2/0",
        "current": "192.168.1.0/24 | O_INTRA | 10 | 20 | 2.2.2.2 | GE2/0",
        "status": "changed",
        "changed": ["Cost"],
    }]
    baseline_only = find(lines(ROUTES), [], "4.4.4.4")["tables"][0]["entries"]
    assert baseline_only[0]["status"] == "only_baseline"


def test_lookup_returns_configuration_sections_side_by_side():
    config = (
        "#\ninterface GigabitEthernet2/0\n ip address 2.2.2.1 255.255.255.0\n"
        "#\nospf 1\n area 0.0.0.0\n#\n"
    )
    changed = config.replace(" ip address 2.2.2.1 255.255.255.0\n",
                             " ip address 2.2.2.1 255.255.255.0\n ospf network-type p2p\n")
    found = find(lines(config), lines(changed), "GigabitEthernet2/0", tables=False)
    assert found["sections"] == [{
        "section": "interface GigabitEthernet2/0",
        "baseline": ["interface GigabitEthernet2/0", " ip address 2.2.2.1 255.255.255.0"],
        "current": [
            "interface GigabitEthernet2/0", " ip address 2.2.2.1 255.255.255.0",
            " ospf network-type p2p",
        ],
    }]
    same = find(lines(config), lines(changed), "area", tables=False)["sections"]
    assert same == [{"section": "ospf 1", "same": True, "lines": ["ospf 1", " area 0.0.0.0"]}]
