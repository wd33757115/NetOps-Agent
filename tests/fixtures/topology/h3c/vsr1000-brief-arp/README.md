# Comware brief ARP tables (historical captures)

`display arp` from the VSR1000 lab, batch `20260928-163159` (vsr-1 is 10.10.1.1, vsr-2 is
10.10.1.2), byte-for-byte as that batch's manifest recorded them (sha256 checked when they
were kept here). The lab batch in `../vsr1000-7.1.064` was re-sampled on 2026-10-05 with
`display arp verbose`, which rule governance now reads for Comware (each entry names its
VPN instance, VSI and aging unit).

These brief tables stay as real samples of the brief layout: the generic ARP parser still
serves families that print it (e.g. Huawei VRP), and the fact-storage acceptance test
replays their rows.
