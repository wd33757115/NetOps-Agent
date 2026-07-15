from src.core.parser.sanitization import (
    SANITIZATION_VERSION,
    runtime_pattern_for_redacted_value,
    sanitize_parser_sample,
)


def test_parser_sample_sanitization_redacts_sensitive_values_deterministically():
    raw = """Username: admin
Password: hunter2
SNMP community public-read
Serial Number: CN12345678
Management IP: 10.20.30.40
Base MAC: aa:bb:cc:dd:ee:ff
Contact: noc@example.com
Interface Gi1/0/1 is up
"""

    first = sanitize_parser_sample(raw)
    second = sanitize_parser_sample(raw)

    assert first == second
    assert first.version == SANITIZATION_VERSION
    assert "hunter2" not in first.text
    assert "public-read" not in first.text
    assert "CN12345678" not in first.text
    assert "10.20.30.40" not in first.text
    assert "aa:bb:cc:dd:ee:ff" not in first.text
    assert "noc@example.com" not in first.text
    assert "Interface Gi1/0/1 is up" in first.text
    assert first.redaction_summary == {
        "credential": 2,
        "username": 1,
        "serial_number": 1,
        "mac_address": 1,
        "ipv4_address": 1,
        "email": 1,
    }


def test_parser_sample_sanitization_is_idempotent_for_redacted_serial_labels():
    raw = "MANU SERIAL NUMBER   : CN12345678\n"

    first = sanitize_parser_sample(raw)
    second = sanitize_parser_sample(first.text)

    assert first.text == "MANU SERIAL NUMBER   : <SERIAL>\n"
    assert second.text == first.text
    assert second.sha256 == first.sha256


def test_parser_sample_sanitization_handles_structured_serial_and_quartet_mac():
    raw = """DEVICE_SERIAL_NUMBER : ABC123
MANU SERIAL NUMBER   : PSU456
MAC_ADDRESS          : 90F7-B2B1-0510
"""

    sanitized = sanitize_parser_sample(raw)

    assert "ABC123" not in sanitized.text
    assert "PSU456" not in sanitized.text
    assert "90F7-B2B1-0510" not in sanitized.text
    assert "DEVICE_SERIAL_NUMBER : <SERIAL>" in sanitized.text
    assert "MANU SERIAL NUMBER   : <SERIAL>" in sanitized.text
    assert "MAC_ADDRESS          : 0200-0000-0001" in sanitized.text
    assert runtime_pattern_for_redacted_value("<SERIAL>") == r"[A-Za-z0-9._/-]+"
