import json

from src.platform.device_access.governance import redact
from src.platform.device_access.text_compare import MAX_DIFF_LINES, compare_text

ROUTES = (
    "Destinations : 3        Routes : 3\r\r\n\r\r\n"
    "Destination/Mask   Proto   Pre Cost        NextHop         Interface\r\r\n"
    "1.1.1.0/24         O_INTRA 10  2           2.2.2.1         GE2/0\r\r\n"
    "2.2.2.0/24         Direct  0   0           2.2.2.2         GE2/0\r\r\n"
    "192.168.1.0/24     Direct  0   0           192.168.1.1     Vlan200\r\r\n"
)


def test_same_output_through_different_transports_is_unchanged():
    live = "\x00<H3C>display ip routing-table\r\n" + ROUTES.replace("\r\r\n", "\n") + "<H3C>"
    result = compare_text(ROUTES, live, command="display ip routing-table")
    assert result["status"] == "unchanged" and result["diff"] == []
    assert result["baseline_lines"] == result["current_lines"] == 6


def test_a_stripped_baseline_matches_live_output_with_leading_indentation():
    # Inspection stores the output stripped; the live read keeps the first indent.
    stored = "OSPF Process 1 with Router ID 4.4.4.4\n Area: 0.0.0.0"
    live = "\r\n\t OSPF Process 1 with Router ID 4.4.4.4\r\n Area: 0.0.0.0\r\n<H3C>"
    assert compare_text(stored, live)["status"] == "unchanged"


def test_removed_and_changed_routes_are_named_line_by_line():
    live = ROUTES.replace(
        "1.1.1.0/24         O_INTRA 10  2           2.2.2.1         GE2/0",
        "1.1.1.0/24         Static  5   0           0.0.0.0         NULL0",
    ).replace("192.168.1.0/24     Direct  0   0           192.168.1.1     Vlan200\r\r\n", "")
    result = compare_text(ROUTES, live)
    assert result["status"] == "changed"
    assert result["removed_count"] == 2 and result["added_count"] == 1
    assert "+1.1.1.0/24         Static  5   0           0.0.0.0         NULL0" in result["diff"]
    assert "-192.168.1.0/24     Direct  0   0           192.168.1.1     Vlan200" in result["diff"]


def test_every_line_is_compared_including_redacted_credential_lines():
    # Both sides carry the platform SSH redaction before they reach the comparison.
    before = "#\ninterface GigabitEthernet2/0\n ip address 2.2.2.2 255.255.255.0\n#\n"
    after = before.replace(
        " ip address 2.2.2.2 255.255.255.0\n",
        " ip address 2.2.2.2 255.255.255.0\n"
        " ospf authentication-mode md5 1 cipher EXAMPLE-SECRET\n",
    )
    result = compare_text(redact(before), redact(after))
    assert result["status"] == "changed" and result["added_count"] == 1
    assert result["diff"][0].endswith("@@ interface GigabitEthernet2/0")
    assert "+[redacted credential line]" in result["diff"]
    assert "EXAMPLE-SECRET" not in json.dumps(result)
    assert set(result) == {
        "status", "added_count", "removed_count", "baseline_lines", "current_lines",
        "diff", "diff_truncated",
    }


def test_the_same_redacted_line_on_both_sides_is_unchanged():
    text = "#\ninterface GigabitEthernet3/0\n ospf authentication-mode md5 1 cipher X\n mtu 1500\n#"
    result = compare_text(redact(text), redact(text))
    assert result["status"] == "unchanged" and result["baseline_lines"] == 5


def test_an_indented_change_names_its_section_like_git():
    before = (
        "#\ninterface GigabitEthernet1/0\n port link-mode route\n ip address 10.10.1.1 24\n#\n"
        "interface GigabitEthernet2/0\n port link-mode route\n ip address 2.2.2.2 24\n#\n"
    )
    after = before.replace(
        " port link-mode route\n ip address 2.2.2.2",
        " port link-mode route\n shutdown\n ip address 2.2.2.2",
    )
    diff = compare_text(before, after)["diff"]
    assert diff[0].endswith("@@ interface GigabitEthernet2/0")
    assert "+ shutdown" in diff
    routes = compare_text("a 1\nb 2\n", "a 1\nb 3\n")["diff"]
    assert routes[0].endswith("@@")  # unindented output needs no section


def test_large_diff_is_bounded_and_says_so():
    before = "\n".join(f"route {n}" for n in range(1000))
    result = compare_text(before, "")
    assert result["status"] == "changed" and result["diff_truncated"]
    assert len(result["diff"]) == MAX_DIFF_LINES and result["removed_count"] == 1000
