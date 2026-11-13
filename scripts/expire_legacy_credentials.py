"""Give legacy credentials a 30-day expiry before rollout; dry-run by default.

Run with the server's environment and an operator role allowed Scan/UpdateItem.
Never prints user IDs, tokens, or ciphertext. Does not alter already-migrated rows.
"""

import argparse
import time

import boto3
from botocore.exceptions import BotoCoreError, ClientError


def migrate(table, apply: bool) -> tuple[int, int]:
    candidates = updated = 0
    cursor = None
    expires_at = int(time.time()) + 30 * 24 * 60 * 60
    while True:
        options = {
            "FilterExpression": "begins_with(pk, :prefix) AND attribute_not_exists(expires_at)",
            "ExpressionAttributeValues": {":prefix": "credentials#"},
            "ProjectionExpression": "pk",
        }
        if cursor:
            options["ExclusiveStartKey"] = cursor
        page = table.scan(**options)
        for item in page.get("Items", []):
            candidates += 1
            if apply:
                try:
                    table.update_item(
                        Key={"pk": item["pk"]},
                        UpdateExpression="SET expires_at = :expiry",
                        ConditionExpression="attribute_exists(pk) AND attribute_not_exists(expires_at)",
                        ExpressionAttributeValues={":expiry": expires_at},
                    )
                    updated += 1
                except ClientError as exc:
                    if (
                        exc.response["Error"]["Code"]
                        != "ConditionalCheckFailedException"
                    ):
                        raise RuntimeError(
                            "Migration failed; check operator permissions and connectivity."
                        ) from None
        cursor = page.get("LastEvaluatedKey")
        if not cursor:
            return candidates, updated


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--table", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        counts = migrate(boto3.resource("dynamodb").Table(args.table), args.apply)
    except (BotoCoreError, ClientError, RuntimeError):
        raise SystemExit(
            "Migration failed. Check operator permissions/connectivity; no credential details were printed."
        ) from None
    print(
        f"Legacy credential rows found: {counts[0]}; updated: {counts[1]}."
        + ("" if args.apply else " Dry run only.")
    )
