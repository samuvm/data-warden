"""CLI `warden`.

Los subcomandos aparecen cuando existe la pieza que ejecutan. Un CLI que promete
`warden query` antes de que haya guard es peor que uno que no promete nada: quien
lo prueba concluye que el proyecto no funciona, y tiene razón.

Hoy: `catalog build` y `catalog show`, que son la fase 0.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections.abc import Sequence

DEFAULT_DATABASE = pathlib.Path("datagen/out/cierzo-dev.duckdb")


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
        f"catálogo generado en {SCHEMA_PATH.relative_to(pathlib.Path.cwd())}\n"
        f"  · {len(schema.tables)} tablas y vistas\n"
        f"  · {sum(len(t.columns) for t in schema.tables)} columnas\n"
        f"  · {hidden} excluidas del catálogo publicado (C-3 de la firma de Q-003)\n"
        f"  · {len(rendered)} bytes"
    )
    return 0


def _cmd_catalog_show(args: argparse.Namespace) -> int:
    from datawarden.catalog import SCHEMA_PATH, load_generated

    if not SCHEMA_PATH.exists():
        print(
            "no hay catálogo generado. El catálogo NO se escribe a mano (I-07): "
            "ejecuta `warden catalog build`.",
            file=sys.stderr,
        )
        return 1
    schema = load_generated(SCHEMA_PATH)
    if args.table:
        table = schema.table(args.table)
        if table is None:
            print(f"no existe la tabla {args.table!r}", file=sys.stderr)
            return 1
        print(json.dumps(table.to_dict(), indent=2, ensure_ascii=False))
        return 0
    for t in schema.tables:
        marca = "vista" if t.kind == "view" else "tabla"
        print(f"{t.name:36s} {marca:6s} {len(t.columns):3d} columnas")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="warden", description=__doc__)
    parser.add_argument("-V", "--version", action="store_true", help="versión y salir")
    sub = parser.add_subparsers(dest="command")

    catalog = sub.add_parser("catalog", help="el catálogo generado (anillo 1)")
    catalog_sub = catalog.add_subparsers(dest="subcommand", required=True)

    build = catalog_sub.add_parser("build", help="regenera catalog/generated/schema.json")
    build.add_argument("--database", default=str(DEFAULT_DATABASE))
    build.set_defaults(func=_cmd_catalog_build)

    show = catalog_sub.add_parser("show", help="lo que hay en el catálogo generado")
    show.add_argument("--table", default=None)
    show.set_defaults(func=_cmd_catalog_show)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Punto de entrada. Devuelve el código de salida del proceso."""
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
