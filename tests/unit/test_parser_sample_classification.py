from src.core.parser.output_classifier import classify_parser_generation_sample


def test_parser_sample_classification_recognizes_generic_valid_empty_output():
    result = classify_parser_generation_sample("M-LAG is not configured.\n")

    assert result["status"] == "valid_empty"
    assert result["recommended_role"] == "negative"


def test_parser_sample_classification_keeps_structured_output_as_generation_candidate():
    result = classify_parser_generation_sample("Interface Link\nGE1/0/1 UP\n")

    assert result["status"] == "data"
    assert result["recommended_role"] == "generation"


def test_parser_sample_classification_does_not_treat_warranty_prose_as_valid_empty():
    result = classify_parser_generation_sample(
        "Cisco Nexus Operating System (NX-OS) Software\n"
        "This software is provided as is, and there is no warranty, express or implied.\n"
        "NXOS: version 9.3(8)\n"
    )

    assert result["status"] == "data"
    assert result["recommended_role"] == "generation"


def test_parser_sample_classification_requires_data_for_header_only_table():
    result = classify_parser_generation_sample(
        'IP Interface Status for VRF "default"(1)\n'
        "Interface            IP Address      Interface Status\n"
    )

    assert result["status"] == "header_only_unverified"
    assert result["recommended_role"] == "unassigned"
