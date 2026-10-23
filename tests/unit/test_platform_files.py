"""Netdisk file references as the platform uses them."""

from src.platform import files


def test_a_reference_names_a_netdisk_file_and_nothing_else():
    assert files.reference("file-0123456789abcdef") == "netdisk:file-0123456789abcdef"
    assert files.file_id_of("netdisk:file-0123456789abcdef") == "file-0123456789abcdef"
    for value in (None, "", "netdisk:", "http://x/netdisk:file-1", "file-0123456789abcdef"):
        assert files.file_id_of(value) is None


def test_a_name_already_in_the_folder_gets_a_number(monkeypatch):
    taken = {"R1.cfg", "R1 (2).cfg"}
    monkeypatch.setattr("src.storage.file_service._duplicate_file_name",
                        lambda session, folder_id, name, exclude_id=None: name in taken)
    # The netdisk keys objects by folder and name: a second upload must not overwrite the first.
    assert files._free_name(None, "folder-1", "R1.cfg") == "R1 (3).cfg"
    assert files._free_name(None, "folder-1", "申请表.xlsx") == "申请表.xlsx"
