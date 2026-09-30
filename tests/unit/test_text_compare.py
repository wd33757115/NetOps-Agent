import json

from src.platform.device_access.governance import redact
from src.platform.device_access.text_compare import (
    MAX_DIFF_LINES,
    comparable_lines,
    compare_text,
    redact_config,
)

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


def test_redaction_preserves_lines_and_hides_authentication_private_key_values():
    text = (
        "#\ninterface GigabitEthernet3/0\n ospf authentication-mode md5 1 cipher SECRET-A\n"
        " password cipher SECRET-B\n -----BEGIN PRIVATE KEY-----\n SECRET-C\n"
        " -----END PRIVATE KEY-----\n mtu 1500\n#"
    )
    safe = redact_config(text)
    assert "SECRET-" not in safe
    assert len(safe.splitlines()) == len(text.splitlines())
    lines, hidden = comparable_lines(text)
    assert hidden == 5 and lines[-2:] == [" mtu 1500", "#"]


def test_raw_baseline_and_pre_audit_redacted_live_do_not_invent_sensitive_changes():
    text = (
        "#\ninterface GigabitEthernet3/0\n"
        " ospf authentication-mode md5 1 cipher EXAMPLE-SECRET\n mtu 1500\n#"
    )
    result = compare_text(text, redact(text))
    assert result["status"] == "unchanged" and not result["diff"]
    # Hidden lines are counted, never called equal.
    assert result["hidden_lines"] == {"baseline": 1, "current": 1}
    assert result["quality_gaps"] == ["sensitive_lines_not_compared"]
    assert "EXAMPLE-SECRET" not in json.dumps(result)


def test_large_diff_is_bounded_and_says_so():
    before = "\n".join(f"route {n}" for n in range(1000))
    result = compare_text(before, "")
    assert result["status"] == "changed" and result["diff_truncated"]
    assert len(result["diff"]) == MAX_DIFF_LINES and result["removed_count"] == 1000
