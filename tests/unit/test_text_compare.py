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


def test_routes_are_compared_by_entry_naming_what_changed():
    live = ROUTES.replace(
        "1.1.1.0/24         O_INTRA 10  2           2.2.2.1         GE2/0",
        "1.1.1.0/24         Static  5   0           0.0.0.0         NULL0",
    ).replace("192.168.1.0/24     Direct  0   0           192.168.1.1     Vlan200\r\r\n", "")
    result = compare_text(ROUTES, live, command="display ip routing-table")
    assert result["status"] == "changed"
    assert (result["removed_count"], result["added_count"], result["changed_rows"]) == (1, 0, 1)
    [table] = result["tables"]
    assert table["removed"]["examples"] == [
        "192.168.1.0/24 | Direct | 0 | 0 | 192.168.1.1 | Vlan200"
    ]
    changed = table["changed"]
    assert set(changed["columns"]) == {"Proto", "Pre", "Cost", "NextHop", "Interface"}
    assert changed["columns"]["Proto"]["examples"] == ["O_INTRA→Static"]
    assert changed["examples"][0].startswith("1.1.1.0/24：Proto O_INTRA→Static")
    # The table is not repeated as text lines.
    assert result["diff"] == []


def test_configuration_is_compared_as_text_not_tables():
    config = "#\nsysname R1\n#\ninterface GE1/0\n ip address 10.0.0.1 255.255.255.0\n#\n"
    result = compare_text(config, config.replace("10.0.0.1", "10.0.0.9"),
                          command="display current-configuration")
    assert result["tables"] == [] and result["changed_rows"] == 0
    assert "+ ip address 10.0.0.9 255.255.255.0" in result["diff"]


def test_every_line_is_compared_as_collected():
    # Nothing is masked: an authentication line and a changed key both show.
    before = "#\ninterface GigabitEthernet2/0\n ip address 2.2.2.2 255.255.255.0\n#\n"
    after = before.replace(
        " ip address 2.2.2.2 255.255.255.0\n",
        " ip address 2.2.2.2 255.255.255.0\n"
        " ospf authentication-mode md5 1 cipher EXAMPLE-KEY\n",
    )
    result = compare_text(before, after)
    assert result["status"] == "changed" and result["added_count"] == 1
    assert result["diff"][0].endswith("@@ interface GigabitEthernet2/0")
    assert "+ ospf authentication-mode md5 1 cipher EXAMPLE-KEY" in result["diff"]
    rekeyed = compare_text(after, after.replace("EXAMPLE-KEY", "OTHER-KEY"))
    assert rekeyed["added_count"] == rekeyed["removed_count"] == 1
    assert set(result) == {
        "status", "added_count", "removed_count", "changed_rows", "tables",
        "unchanged_tables", "baseline_lines", "current_lines", "diff", "diff_truncated",
    }


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
