"""Idempotent initial business data, invoked only by platform bootstrap."""


def initialize():
    from src.core.check_items.catalog import CheckItemCatalogRepository
    from src.core.check_items.identity import EntityIdentityPolicyRepository
    from src.core.check_items.text_comparison import TextComparisonProfileRepository

    CheckItemCatalogRepository().bootstrap(actor="platform-bootstrap")
    EntityIdentityPolicyRepository().bootstrap(actor="platform-bootstrap")
    TextComparisonProfileRepository().bootstrap(actor="platform-bootstrap")
