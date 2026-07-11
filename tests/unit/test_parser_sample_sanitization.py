from src.core.parser.sanitization import (
    SANITIZATION_VERSION,
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
