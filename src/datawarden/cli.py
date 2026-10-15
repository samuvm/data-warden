"""The `warden` CLI.

Subcommands: `catalog build | show`, `audit verify | reconcile | anchor` and
`mcp serve`.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections.abc import Sequence

#: Warehouse served by default: `full`, for consistency across rings.
#: `catalog/generated/statistics.json` declares `profile: full`, so the budget ring
#: prices 66.6 M rows; serving `dev` made the engine run against 683 K. Observed with
#: Claude Desktop as client: it returned "ES = 23,908" while the evaluation bank,
#: frozen on `full`, says 2,375,936.
#:
#: For fast development use `--database datagen/out/cierzo-dev.duckdb`.
DEFAULT_DATABASE = pathlib.Path("datagen/out/cierzo-full.duckdb")
DEFAULT_AUDIT_DB = pathlib.Path("var/audit.sqlite3")


def _cmd_catalog_build(args: argparse.Namespace) -> int:
    from datawarden.catalog import SCHEMA_PATH
    from datawarden.catalog.build import generate, write

    schema = generate(pathlib.Path(args.database))
    rendered = write(schema)
    published = schema.published()
    hidden = sum(len(t.columns) for t in schema.tables) - sum(
        len(t.columns) for t in published.tables
    )
    print(
        f"catalog generated at {SCHEMA_PATH.relative_to(pathlib.Path.cwd())}\n"
        f"  · {len(schema.tables)} tables and views\n"
        f"  · {sum(len(t.columns) for t in schema.tables)} columns\n"
        f"  · {hidden} excluded from the published catalog (docs/spec/policy.yaml)\n"
        f"  · {len(rendered)} bytes"
    )
    return 0


def _cmd_catalog_show(args: argparse.Namespace) -> int:
    from datawarden.catalog import SCHEMA_PATH, load_generated

    if not SCHEMA_PATH.exists():
        print(
            "no generated catalog. The catalog is never hand-written (I-07): "
            "run `warden catalog build`.",
            file=sys.stderr,
        )
        return 1
    schema = load_generated(SCHEMA_PATH)
    if args.table:
        table = schema.table(args.table)
        if table is None:
            print(f"table {args.table!r} does not exist", file=sys.stderr)
            return 1
        print(json.dumps(table.to_dict(), indent=2, ensure_ascii=False))
        return 0
    for t in schema.tables:
        marca = "view" if t.kind == "view" else "table"
        print(f"{t.name:36s} {marca:6s} {len(t.columns):3d} columns")
    return 0


def _abrir_almacen(path: str) -> object:
    """Open the audit store.

    The CLI never imports `datawarden.engines`: the import-linter contract allows
    reaching the engine only through the audit layer. These subcommands only read
    the chain.
    """
    from datawarden.audit.store import AuditStore

    return AuditStore(path)


def _cmd_audit_verify(args: argparse.Namespace) -> int:
    """Check whether the chain is intact and, if not, where it breaks."""
    ruta = pathlib.Path(args.database)
    if not ruta.exists():
        print(f"warden audit verify: {ruta} does not exist. No chain to verify.")
        return 1
    store = _abrir_almacen(str(ruta))
    ok, problema = store.verify()  # type: ignore[attr-defined]
    total = store.count()  # type: ignore[attr-defined]
    if not ok:
        print(f"warden audit verify: BROKEN CHAIN · {total} records\n  {problema}")
        return 1
    print(f"warden audit verify: ok · {total} records, chain intact")
    return 0


def _cmd_audit_reconcile(args: argparse.Namespace) -> int:
    """Reconcile the chain with itself and report the count per status.

    `--strict` turns any anomaly into a non-zero exit code; it is the mode used by
    the `G-AUDIT-COV` command, since a reconciler that only prints warnings blocks
    nothing.
    """
    from datawarden.domain.types import Status

    ruta = pathlib.Path(args.database)
    if not ruta.exists():
        print(f"warden audit reconcile: {ruta} does not exist.")
        return 1 if args.strict else 0

    store = _abrir_almacen(str(ruta))
    ok, problema = store.verify()  # type: ignore[attr-defined]
    conteo = store.count_by_status()  # type: ignore[attr-defined]
    total = store.count()  # type: ignore[attr-defined]
    suma = sum(conteo.values())

    print(f"warden audit reconcile · {total} records")
    for estado in Status:
        print(f"  {estado.value:22s} {conteo[estado]:6d}")

    problemas: list[str] = []
    if not ok and problema is not None:
        problemas.append(f"broken chain: {problema}")
    if suma != total:
        problemas.append(
            f"statuses add up to {suma} but there are {total} records: some rows "
            "have a status outside the contract"
        )
    if problemas:
        print("\n  ANOMALIES:")
        for p in problemas:
            print(f"    · {p}")
        return 1
    print("  chain intact and the four statuses reconcile")
    return 0


def _cmd_audit_anchor(args: argparse.Namespace) -> int:
    """Emit the chain head so it can be published where an attacker has no control.

    A hash chain has a known limit: anyone with write access to the store can
    recompute the whole chain. Once an anchor is published, rewriting history also
    requires forging the anchor. Between anchors the window remains open, as
    documented in `docs/threat-model.md`.

    This command emits; where to publish (a repository, a timestamping service, an
    email) is a deployment decision.
    """
    ruta = pathlib.Path(args.database)
    if not ruta.exists():
        print(f"warden audit anchor: {ruta} does not exist.")
        return 1
    store = _abrir_almacen(str(ruta))
    entradas = store.rows_as_entries()  # type: ignore[attr-defined]
    if not entradas:
        print("warden audit anchor: the chain is empty, nothing to anchor.")
        return 1
    punta = entradas[-1]
    print(
        json.dumps(
            {
                "seq": punta.seq,
                "chain_hash": punta.chain_hash,
                "recorded_at": punta.record.recorded_at,
                "records": len(entradas),
                "nota": (
                    "Publish this where whoever writes the store cannot modify it. "
                    "An anchor does not prevent rewriting the chain; it makes "
                    "rewriting detectable."
                ),
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


def _cmd_mcp_serve(args: argparse.Namespace) -> int:
    """Start the MCP server over stdio. Launched by the client, not by hand.

    Configured in the MCP client (Claude Desktop, Cursor), which spawns and stops it.
    Run in a terminal it waits silently, listening for JSON-RPC on stdin.

    Startup failures are reported on `stderr`: over stdio, `stdout` is the protocol
    channel and an error message there would corrupt the stream.
    """
    from datawarden.audit.factory import MissingDatasetError
    from datawarden.mcp.runtime import serve_stdio

    try:
        serve_stdio(
            database=pathlib.Path(args.database),
            audit_db=pathlib.Path(args.audit_db),
        )
    except MissingDatasetError as falta:
        print(f"warden mcp serve: {falta}", file=sys.stderr)
        return 1
    except ValueError as mal:
        # Typically the missing masking pepper. The server refuses to start without
        # it: masking with a well-known key only looks like protection.
        print(f"warden mcp serve: {mal}", file=sys.stderr)
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="warden", description=__doc__)
    parser.add_argument("-V", "--version", action="store_true", help="print version and exit")
    sub = parser.add_subparsers(dest="command")

    catalog = sub.add_parser("catalog", help="the generated catalog (ring 1)")
    catalog_sub = catalog.add_subparsers(dest="subcommand", required=True)

    build = catalog_sub.add_parser("build", help="regenerate catalog/generated/schema.json")
    build.add_argument("--database", default=str(DEFAULT_DATABASE))
    build.set_defaults(func=_cmd_catalog_build)

    show = catalog_sub.add_parser("show", help="show the generated catalog")
    show.add_argument("--table", default=None)
    show.set_defaults(func=_cmd_catalog_show)

    audit = sub.add_parser("audit", help="the audit chain (ring 5)")
    audit_sub = audit.add_subparsers(dest="subcommand", required=True)

    verify = audit_sub.add_parser("verify", help="check that the chain is intact")
    verify.add_argument("--database", default=str(DEFAULT_AUDIT_DB))
    verify.set_defaults(func=_cmd_audit_verify)

    reconcile = audit_sub.add_parser("reconcile", help="reconcile the chain and statuses")
    reconcile.add_argument("--database", default=str(DEFAULT_AUDIT_DB))
    reconcile.add_argument(
        "--strict",
        action="store_true",
        help="exit non-zero on any anomaly",
    )
    reconcile.set_defaults(func=_cmd_audit_reconcile)

    anchor = audit_sub.add_parser("anchor", help="emit the chain head")
    anchor.add_argument("--database", default=str(DEFAULT_AUDIT_DB))
    anchor.set_defaults(func=_cmd_audit_anchor)

    mcp = sub.add_parser("mcp", help="the MCP server")
    mcp_sub = mcp.add_subparsers(dest="subcommand", required=True)

    serve = mcp_sub.add_parser("serve", help="serve MCP over stdio; launched by the client")
    serve.add_argument(
        "--database",
        default=str(DEFAULT_DATABASE),
        help="the DuckDB warehouse, generated with `make dataset PROFILE=dev`",
    )
    serve.add_argument(
        "--audit-db",
        default=str(DEFAULT_AUDIT_DB),
        help="where the audit chain is written",
    )
    serve.set_defaults(func=_cmd_mcp_serve)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Returns the process exit code."""
    parser = build_parser()
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    if args.version:
        from datawarden import __version__

        print(__version__)
        return 0
    if args.command is None:
        parser.print_help(sys.stderr)
        return 2
    exit_code: int = args.func(args)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
