from alembic import command
from sqlalchemy import create_engine, inspect

from src.platform.kernel.migrations import configuration
from src.platform.kernel.registry import discover


def test_independent_migrations_create_only_owned_tables(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'apps.db'}")
    manifests = discover()
    with engine.begin() as connection:
        for app_id in ("topology", "device-access"):
            manifest = manifests[app_id]
            config, _ = configuration(manifest)
            config.attributes.update(
                connection=connection, version_table="alembic_app_" + app_id.replace("-", "_")
            )
            command.upgrade(config, manifest.data["database"]["head"])
            command.upgrade(config, manifest.data["database"]["head"])
        tables = set(inspect(connection).get_table_names())
        assert all(
            name.startswith(("app_topology_", "app_twin_", "app_device_access_", "alembic_app_"))
            for name in tables
        )
        assert len(inspect(connection).get_foreign_keys("app_topology_links")) == 2
    engine.dispose()


def test_device_access_saved_sessions_keep_one_host_key_per_device(tmp_path):
    from datetime import datetime, timezone

    from sqlalchemy import text

    engine = create_engine(f"sqlite:///{tmp_path / 'apps.db'}")
    manifest = discover()["device-access"]
    when = datetime(2026, 9, 1, tzinfo=timezone.utc)
    with engine.begin() as connection:
        config, _ = configuration(manifest)
        config.attributes.update(connection=connection, version_table="alembic_app_device_access")
        command.upgrade(config, "device_access_0005")
        for domain, host, status, fingerprint in (
            ("lab", "10.0.0.1", "pinned", "SHA256:lab"),
            ("default", "10.0.0.1", "verified", "SHA256:default"),
            ("lab", "10.0.0.2", "pinned", "SHA256:only"),
            ("lab", "10.0.0.3", "revoked", "SHA256:revoked"),
        ):
            connection.execute(
                text(
                    "INSERT INTO app_device_access_host_keys (domain, host, port, key_type, "
                    "public_key, fingerprint_sha256, first_seen, status, revision, "
                    "observed_fingerprint) VALUES (:d, :h, 22, 'ssh-rsa', 'AAAA', :f, :t, :s, 1, "
                    "'SHA256:seen')"
                ),
                {"d": domain, "h": host, "f": fingerprint, "t": when, "s": status},
            )
        command.upgrade(config, manifest.data["database"]["head"])
        tables = set(inspect(connection).get_table_names())
        assert set(manifest.data["database"]["tables"]) <= tables
        assert (
            not {
                "app_device_access_connections",
                "app_device_access_credential_sets",
                "app_device_access_address_grants",
                "app_device_access_host_key_policies",
            }
            & tables
        )
        rows = connection.execute(
            text(
                "SELECT domain, host, status, fingerprint_sha256, observed_fingerprint "
                "FROM app_device_access_host_keys ORDER BY host"
            )
        ).all()
        assert [tuple(row) for row in rows] == [
            ("default", "10.0.0.1", "trusted", "SHA256:default", None),
            ("default", "10.0.0.2", "trusted", "SHA256:only", None),
        ]
    engine.dispose()
