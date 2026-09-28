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
            name.startswith(("app_topology_", "app_device_access_", "alembic_app_"))
            for name in tables
        )
        assert len(inspect(connection).get_foreign_keys("app_topology_links")) == 2
    engine.dispose()
