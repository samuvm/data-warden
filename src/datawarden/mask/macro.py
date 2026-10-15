"""Engine-side masking function, so the pepper does not travel with each query.

`hash_estable` is computed in the engine (a consequence of masking by AST rewrite
rather than post-processing), so the engine needs the key. With the pepper inlined in
every query it ended up in the engine logs and in the `sql` field of the audit
record; the latter matters because `docs/threat-model.md §4.1` assumes any
same-user process can read that store.

With the macro, the tree says `warden_hash(...)` and the pepper no longer appears in
the audit record. It still appears in the engine logs inside `CREATE TEMP MACRO`, but
once per connection instead of once per query. This is a large reduction, not an
elimination.

The full fix is a stable pseudonym rather than a keyed hash, following the existing
`dim_card.card_token` (denied) / `card_sk` pattern: generating
`device_fingerprint_sk` and `ip_address_sk` at build time keeps distinct counts and
removes the pepper for these columns entirely. It is deferred because regenerating a
derived column invalidates the dataset `MANIFEST`.

`TEMP` because the engine connection is read-only and must stay so. A temporary macro
lives in the session schema and does not touch the file. Verified against the `dev`
DuckDB: it installs, runs, and yields exactly the same result as the inline-pepper
version.
"""

from __future__ import annotations

from typing import Final

from datawarden.mask.config import MaskConfig

#: Function name, part of the contract between `mask/` and the engine: the rewriter
#: emits it and the adapter installs it. Changing one side only yields a runtime
#: `Function not found`.
MACRO_NAME: Final = "warden_hash"

#: Published hash length in hex characters. Defined here, not in `rewrite.py`,
#: because truncation happens inside the macro; two disagreeing numbers would give
#: the same value two hashes depending on the path.
HASH_HEX: Final = 12


def macro_ddl(config: MaskConfig, *, dialect: str = "duckdb") -> str:
    """The `CREATE TEMP MACRO` statement to run when opening the connection.

    The pepper is embedded in this string. It is a key installation, not a user
    query, so it must never be written to the audit log or to an application log.
    """
    if dialect != "duckdb":
        message = (
            f"no masking macro for dialect {dialect!r}. Athena must declare its own "
            "via an equivalent UDF, or report `G-ENGINE-PARITY` as NOT VERIFIED for "
            "`hash_estable` columns. Emitting SQL for an untested dialect is not an "
            "option."
        )
        raise ValueError(message)
    escaped = config.pepper.replace("'", "''")
    return (
        f"CREATE OR REPLACE TEMP MACRO {MACRO_NAME}(v) AS "
        f"substring(sha256(v || '{escaped}'), 1, {HASH_HEX})"
    )
