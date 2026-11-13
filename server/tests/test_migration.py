"""The operator helper is dry-run by default and safe around concurrent updates."""

import importlib.util
from pathlib import Path
from unittest.mock import Mock

from botocore.exceptions import ClientError


def test_legacy_retention_migration_is_paginated_conditional_and_dry_run():
    script = (
        Path(__file__).resolve().parents[2] / "scripts/expire_legacy_credentials.py"
    )
    spec = importlib.util.spec_from_file_location("migration", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    table = Mock()
    pages = [
        {
            "Items": [{"pk": "credentials#fixture-one"}],
            "LastEvaluatedKey": {"pk": "cursor"},
        },
        {"Items": [{"pk": "credentials#fixture-two"}]},
    ]
    table.scan.side_effect = pages
    assert module.migrate(table, False) == (2, 0)
    table.update_item.assert_not_called()
    table.scan.side_effect = pages
    table.update_item.side_effect = [
        None,
        ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem"
        ),
    ]
    assert module.migrate(table, True) == (2, 1)
    assert (
        "attribute_not_exists(expires_at)"
        in table.update_item.call_args.kwargs["ConditionExpression"]
    )
