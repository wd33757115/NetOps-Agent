# H3C VSR1000 lab fixtures

Source: authorized GNS3 lab batch `20260928-163159`, Comware 7.1.064 Release
1362P12. `vsr-1` is 10.10.1.1; `vsr-2` is 10.10.1.2. These are lab addresses,
serials and MACs, preserved to test identity and cross-device correlation.

Each device has 14 capabilities, each with a cleaned `.txt` and a `.raw.txt`.
The latter preserves the bytes of the collector's UTF-8 file, including command
echo, prompts and CR CR LF. The historical collector decoded incoming chunks
with replacement before saving, so these files are not general lossless SSH
wire captures. This batch's files contain ASCII only. Git must not normalize
their line endings; `manifest.json` records every byte count and SHA256.

Import command: `python scripts/topology/import_lab_fixtures.py`. The importer
asks for the lab password without echo, runs the existing sensitive-data file
scanner and additional direct byte checks (including files containing NUL),
then copies only approved capability files. No password or scanner search term
is included in this directory. Initial login banners were not captured.

`expected.json` distinguishes CLI-observable facts from lab wiring supplied in
the roadmap. LLDP alone cannot prove a direct cable, Hub, or unmanaged switch.
Inventory and IRF contain unsupported-command output, while LAG and VRFs are
valid empty results. A transport-level `ok` in the source metadata does not
mean the capability succeeded.

E1–E17 replay checks live in `tests/unit/test_topology_lab_fixtures.py`.
E12's conditional wake behavior uses a synthetic channel; E18's initial NUL
prompt regression lives in `test_device_access_ssh.py`. Neither is presented
as a captured login banner from this batch. Business parsers are later cards.
