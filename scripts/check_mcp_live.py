#!/usr/bin/env python
"""El servidor MCP contestando DE VERDAD, por stdio y con un cliente real.

**Esto existe porque `G-MCP-CONFORM` decía 11/11 mientras `run_query` reventaba en
todas las llamadas.** El check de conformidad valida la forma de lo que el servidor
PUBLICA —`resultType`, `ttlMs`, el `oneOf` del `outputSchema`, el orden de
`tools/list`— y todo eso estaba bien. Lo que no comprobaba nadie es que el servidor
sepa contestar, y no sabía: el SDK ejecuta las tools síncronas en un hilo de trabajo,
y tanto SQLite como DuckDB se negaban a que se usara desde otro hilo su conexión.

Es la tercera vez esta semana que aparece el mismo error de método, así que conviene
escribirlo con todas las letras: **no basta con medir un anillo; hay que medirlo por
el camino que se ejecuta.** Antes fue `G-PII-LEAK` midiendo `screen_and_mask()` en
vez del ejecutor, y `G-SECRETS` comparándose contra un fichero que la propia medida
reescribía.

Levanta el servidor como lo levanta Claude Desktop —un proceso hijo hablando JSON-RPC
por la entrada y la salida estándar— y le exige cinco cosas que un usuario nota el
primer minuto.
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
from gatelib import ROOT, record

#: La pimienta del gate. No es un secreto: es una constante de medida, la misma que
#: usa `pii_suite.py`, y está en la línea base auditada de `detect-secrets`.
GATE_PEPPER = "pimienta-del-gate-solo-para-medir-g-pii-leak"
DATABASE = ROOT / "datagen" / "out" / "cierzo-dev.duckdb"


def uv_path() -> str:
    """La ruta ABSOLUTA de `uv`, que es lo que el README exige poner en el cliente.

    Claude Desktop no hereda el `PATH` de la terminal, así que `"uv"` a secas no se
    encuentra. Aquí se resuelve para que el check use exactamente la misma forma.
    """
    import shutil

    found = shutil.which("uv")
    if found is None:
        message = (
            "no se encuentra `uv` en el PATH. Es el comando que el README pone en la "
            "configuración del cliente MCP, así que sin él no se puede comprobar lo "
            "que el usuario va a ejecutar de verdad."
        )
        raise RuntimeError(message)
    return found


async def exercise() -> list[tuple[str, bool, str]]:
    from mcp import Client
    from mcp.client.stdio import StdioServerParameters

    env = dict(os.environ)
    env["DATAWARDEN_MASK_PEPPER"] = GATE_PEPPER
    env["WARDEN_ROLE"] = "analyst"
    # **EL MISMO COMANDO QUE DICE EL README, Y DESDE OTRO DIRECTORIO.**
    #
    # Antes esto lanzaba `sys.executable -m datawarden.cli` con `cwd=ROOT`, o sea,
    # un comando que el README no menciona desde un directorio que el cliente no
    # garantiza. Daba 8/8 mientras la instalación real moría con
    # `error: Failed to spawn: warden` — porque **Claude Desktop ignora el `cwd`**
    # de la configuración, y `uv run warden` fuera del proyecto no encuentra nada.
    #
    # Se lanza desde el directorio raíz del sistema a propósito: si el servidor
    # vuelve a depender del directorio de trabajo, esto se cae aquí y no en el
    # portátil de quien lo instala.
    params = StdioServerParameters(
        command=uv_path(),
        args=["run", "--directory", str(ROOT), "warden", "mcp", "serve"],
        cwd="/",
        env=env,
    )
    checks: list[tuple[str, bool, str]] = []

    async with Client(params) as client:
        checks.append(
            (
                "protocolo-2026-07-28",
                str(client.protocol_version) == "2026-07-28",
                str(client.protocol_version),
            )
        )

        tools = [t.name for t in (await client.list_tools()).tools]
        from datawarden.mcp.server import TOOL_ORDER

        checks.append(("tools-list-en-orden", tuple(tools) == TOOL_ORDER, str(tools)))

        uris = [str(r.uri) for r in (await client.list_resources()).resources]
        checks.append(("recurso-catalogo", "warden://catalog" in uris, str(uris)))

        # 1 · una consulta normal DEVUELVE FILAS. Es lo que fallaba.
        out = await client.call_tool(
            "run_query",
            {
                "question_sql": (
                    "SELECT country_code, count(*) AS n FROM dim_customer "
                    "GROUP BY country_code ORDER BY n DESC LIMIT 3"
                )
            },
        )
        body = out.structured_content or {}
        rows = (body.get("result") or {}).get("rows") or []
        checks.append(("run_query-devuelve-filas", len(rows) == 3, str(rows)[:80]))

        # 2 · una columna `mask` sale ENMASCARADA, y el resultado lo dice.
        out = await client.call_tool(
            "run_query", {"question_sql": "SELECT first_name FROM dim_customer LIMIT 3"}
        )
        body = (out.structured_content or {}).get("result") or {}
        valores = [v for fila in body.get("rows", []) for v in fila if v is not None]
        masked = body.get("columns_masked") or []
        checks.append(
            (
                "columna-mask-sale-enmascarada",
                bool(valores) and all(v == "***" for v in valores) and bool(masked),
                f"valores={valores} masked={masked}",
            )
        )

        # 3 · EL RECHAZO llega como respuesta, con su regla y su alternativa. No es
        #     un error del protocolo: es la respuesta más valiosa del sistema.
        out = await client.call_tool(
            "run_query",
            {
                "question_sql": (
                    "SELECT customer_sk FROM dim_customer "
                    "WHERE birth_date < '1990-01-01' LIMIT 10"
                )
            },
        )
        body = out.structured_content or {}
        rej = body.get("rejected") or {}
        checks.append(
            (
                "rechazo-accionable-con-alternativa",
                body.get("outcome") == "rejected"
                and rej.get("rule_id") == "R008"
                and bool(rej.get("suggestion"))
                and rej.get("alternative") == "dim_customer.age_band",
                f"{rej.get('rule_id')} -> {rej.get('alternative')}",
            )
        )
        checks.append(
            (
                "rechazo-no-es-error-de-protocolo",
                not bool(out.is_error),
                f"is_error={out.is_error}",
            )
        )

        # 4 · una escritura se para y NO se ofrece reintentar.
        out = await client.call_tool("run_query", {"question_sql": "DELETE FROM dim_customer"})
        rej = (out.structured_content or {}).get("rejected") or {}
        checks.append(
            (
                "escritura-parada-y-no-reintentable",
                rej.get("rule_id") == "R010" and rej.get("retryable") is False,
                f"{rej.get('rule_id')} retryable={rej.get('retryable')}",
            )
        )

    return checks


def main() -> int:
    if not DATABASE.exists():
        print(
            f"check_mcp_live: FALLO · no existe {DATABASE.relative_to(ROOT)}.\n"
            "  El servidor se prueba CONTESTANDO, no razonando sobre su configuración.\n"
            "  Genera el dataset con `make dataset PROFILE=dev`."
        )
        return 1

    checks = asyncio.run(exercise())
    passed = sum(1 for _, ok, _ in checks if ok)

    record(
        "mcp-conformance.json",
        "G-MCP-LIVE",
        value=float(len(checks) - passed),
        detail={
            "total": len(checks),
            "passed": passed,
            "checks": [{"name": n, "ok": ok, "detail": d} for n, ok, d in checks],
            "como_se_mide": (
                "se levanta `warden mcp serve` como proceso hijo y se le habla "
                "JSON-RPC por stdio con el cliente del SDK, igual que hace Claude "
                "Desktop. No se inspecciona ninguna estructura en proceso."
            ),
        },
        command="python scripts/check_mcp_live.py",
    )

    print(f"check_mcp_live: {passed}/{len(checks)} · servidor contestando por stdio\n")
    for name, ok, detail in checks:
        print(f"  {'ok  ' if ok else 'FALLO'} {name:36} {detail}")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
