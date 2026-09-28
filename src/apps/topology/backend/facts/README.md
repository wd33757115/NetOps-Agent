# Shared domain facts

`FactRepository` is a server-only API. The gateway/worker supplies the authenticated
`CurrentUser`; model arguments never supply the actor or role. Every read and write
rechecks the actor's database `device.collect` grant against the device's management
addresses. An authorized viewer can read, while submitting requires admin/operator.
Sharing facts does not share credentials or grant network access.

Devices and fact sets are shared within a management domain. `create_device` ensures
a weak candidate at `(domain, seed_address)` under a unique constraint, including
concurrent discoveries. It does not equate different addresses or hostnames with
one physical device. P3-2 owns strong-identity merging and must clear the weak seed
key when promoting identity; the column is nullable for that transition and for
non-managed entities. Existing private discovery runs/snapshots are not exposed.

Use `save_evidence(domain, uid, outputs)` before `append(..., FactInput(...))`.
The immutable evidence envelope binds domain and device ID, and its content hash is
verified before facts are accepted. The fact set and typed rows commit together.
`read_set` replays a historical batch. `current` selects the latest **complete** set
by observation time, received time, then ID; empty complete sets replace old data.
The latest attempt is also returned so a newer failed/partial/unsupported collection
does not silently make old facts appear fresh. Both candidates use one SQL statement.

Interface/IP/ARP/MAC/LLDP/route/VLAN/member rows have typed columns. Version and other
capabilities without a dedicated relation use `summary`; their row count is zero.
Production CLI parsing and normalization are later cards. Facts retain parser,
profile, evidence, observation time and submitting actor. A referenced discovery run
must belong to that actor and domain. Refresh/offline facts may omit `run_id`.

The package migration `topology_0002` creates only the 11 `app_twin_*` tables. It does
not rewrite historical snapshots, run the live collector, deploy, or migrate patrol.
