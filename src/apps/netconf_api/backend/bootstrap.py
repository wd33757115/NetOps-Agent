"""Initial data: the shipped seed definitions, published as system versions."""

from . import service


def initialize():
    with service.sessions() as session:
        added = service.import_seeds(session)
    print(f"[OK] netconf-api seed definitions: {', '.join(added) or 'unchanged'}", flush=True)
