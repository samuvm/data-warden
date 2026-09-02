"""CLI `warden`.

Los subcomandos aparecen cuando existe la pieza que ejecutan. Un CLI que promete
`warden query` antes de que haya guard es peor que uno que no promete nada: quien
lo prueba concluye que el proyecto no funciona, y tiene razón.

Hoy: `catalog build` y `catalog show` (fase 0), y `audit verify | reconcile | anchor`
(fase 5).
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections.abc import Sequence

DEFAULT_DATABASE = pathlib.Path("datagen/out/cierzo-dev.duckdb")
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


def _abrir_almacen(path: str) -> object:
    """Abre el almacén de auditoría, o explica por qué no puede.

    **El CLI no importa `datawarden.engines` en ninguna parte**, y no es casualidad:
    el contrato de import-linter «Al motor solo se llega por la auditoría» lo
    prohíbe. Estos tres subcomandos LEEN la cadena; el día que exista `warden query`,
    su motor lo construirá una factoría de `audit/`, no este fichero.
    """
    from datawarden.audit.store import AuditStore

    return AuditStore(path)


def _cmd_audit_verify(args: argparse.Namespace) -> int:
    """¿Está intacta la cadena? Y si no, dónde se rompió."""
    ruta = pathlib.Path(args.database)
    if not ruta.exists():
        print(f"warden audit verify: no existe {ruta}. No hay cadena que verificar.")
        return 1
    store = _abrir_almacen(str(ruta))
    ok, problema = store.verify()  # type: ignore[attr-defined]
    total = store.count()  # type: ignore[attr-defined]
    if not ok:
        print(f"warden audit verify: CADENA ROTA · {total} registros\n  {problema}")
        return 1
    print(f"warden audit verify: ok · {total} registros, cadena intacta")
    return 0


def _cmd_audit_reconcile(args: argparse.Namespace) -> int:
    """Cuadra la cadena consigo misma y publica el reparto por estado.

    `--strict` convierte cualquier anomalía en código de salida distinto de cero.
    Es el modo que nombra el comando de `G-AUDIT-COV`, y existe porque un
    reconciliador que solo imprime avisos no bloquea nada.
    """
    from datawarden.domain.types import Status

    ruta = pathlib.Path(args.database)
    if not ruta.exists():
        print(f"warden audit reconcile: no existe {ruta}.")
        return 1 if args.strict else 0

    store = _abrir_almacen(str(ruta))
    ok, problema = store.verify()  # type: ignore[attr-defined]
    conteo = store.count_by_status()  # type: ignore[attr-defined]
    total = store.count()  # type: ignore[attr-defined]
    suma = sum(conteo.values())

    print(f"warden audit reconcile · {total} registros")
    for estado in Status:
        print(f"  {estado.value:22s} {conteo[estado]:6d}")

    problemas: list[str] = []
    if not ok and problema is not None:
        problemas.append(f"cadena rota: {problema}")
    if suma != total:
        problemas.append(
            f"los estados suman {suma} y hay {total} registros: hay filas con un "
            "estado fuera del contrato"
        )
    if problemas:
        print("\n  ANOMALÍAS:")
        for p in problemas:
            print(f"    · {p}")
        return 1
    print("  cadena intacta y los cuatro estados cuadran")
    return 0


def _cmd_audit_anchor(args: argparse.Namespace) -> int:
    """Emite la PUNTA de la cadena para publicarla donde el atacante no manda.

    Es la única defensa contra el límite honesto del hash encadenado: **quien tiene
    escritura sobre el almacén puede recalcular la cadena entera**. A partir de un
    anclaje publicado, reescribir el pasado exige además falsificar el anclaje.
    Entre dos anclajes la ventana sigue abierta, y eso está escrito en
    `docs/threat-model.md` en vez de disimulado.

    Este comando NO publica: emite. Dónde se publica —un repositorio, un servicio de
    sellado de tiempo, un correo— es una decisión del despliegue.
    """
    ruta = pathlib.Path(args.database)
    if not ruta.exists():
        print(f"warden audit anchor: no existe {ruta}.")
        return 1
    store = _abrir_almacen(str(ruta))
    entradas = store.rows_as_entries()  # type: ignore[attr-defined]
    if not entradas:
        print("warden audit anchor: la cadena está vacía, no hay punta que anclar.")
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
                    "Publicar esto donde quien escribe el almacén no pueda tocarlo. "
                    "Un anclaje no impide reescribir la cadena: impide hacerlo sin "
                    "que se note."
                ),
            },
            indent=2,
            ensure_ascii=False,
        )
    )
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

    audit = sub.add_parser("audit", help="la cadena de auditoría (anillo 5)")
    audit_sub = audit.add_subparsers(dest="subcommand", required=True)

    verify = audit_sub.add_parser("verify", help="¿está intacta la cadena?")
    verify.add_argument("--database", default=str(DEFAULT_AUDIT_DB))
    verify.set_defaults(func=_cmd_audit_verify)

    reconcile = audit_sub.add_parser("reconcile", help="cuadra la cadena y los estados")
    reconcile.add_argument("--database", default=str(DEFAULT_AUDIT_DB))
    reconcile.add_argument(
        "--strict",
        action="store_true",
        help="cualquier anomalía sale con código distinto de cero",
    )
    reconcile.set_defaults(func=_cmd_audit_reconcile)

    anchor = audit_sub.add_parser("anchor", help="emite la punta de la cadena")
    anchor.add_argument("--database", default=str(DEFAULT_AUDIT_DB))
    anchor.set_defaults(func=_cmd_audit_anchor)

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
