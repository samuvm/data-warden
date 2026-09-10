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
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
from gatelib import ROOT, record

#: La pimienta del gate. No es un secreto: es una constante de medida, la misma que
#: usa `pii_suite.py`, y está en la línea base auditada de `detect-secrets`.
GATE_PEPPER = "pimienta-del-gate-solo-para-medir-g-pii-leak"

#: Una consulta MEDIDA en 377 MB para `analyst`, entre su presupuesto blando (300 MB) y
#: el duro (600 MB). Es la única franja donde MRTR tiene algo que preguntar: por debajo
#: se ejecuta y por encima se rechaza. Si los presupuestos se recalibran, esto deja de
#: caer en la franja y el check lo dice — que es lo que se quiere.
SOFT_BUDGET_SQL = (
    "SELECT p.amount_eur_minor, p.risk_score, m.trade_name "
    "FROM fact_payment_attempt AS p JOIN dim_merchant AS m ON p.merchant_sk = m.merchant_sk"
)
DATABASE = ROOT / "datagen" / "out" / "cierzo-full.duckdb"


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

    pedido: list[str] = []

    def responde(decision: str) -> Any:
        """Un cliente que contesta a MRTR. Es lo único que prueba que se pregunta."""

        async def callback(_ctx: object, _params: object) -> Any:
            from mcp.types import ElicitResult

            pedido.append(decision)
            if decision == "decline":
                return ElicitResult(action="decline")
            return ElicitResult(action="accept", content={"proceed": True})

        return callback

    async with Client(params, elicitation_callback=responde("decline")) as client:
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

        # 0 · SE PUEDE DESCUBRIR EL ALMACÉN SIN SABER NADA. Es lo que fallaba en
        #     Q-009: el catálogo solo estaba como recurso MCP, el cliente no tenía
        #     lectura de recursos, y el modelo acabó adivinando `customers`,
        #     `clientes`, `pagos` hasta rendirse. Los recursos son OPCIONALES para un
        #     cliente; las herramientas no.
        out = await client.call_tool("describe_table", {})
        body = (out.structured_content or {}).get("result") or {}
        tablas = [fila[0] for fila in body.get("rows", [])]
        checks.append(
            (
                "descubrir-tablas-sin-saber-nada",
                len(tablas) > 20 and "dim_customer" in tablas,
                f"{len(tablas)} tablas · {tablas[:3]}…",
            )
        )

        # 0.bis · Y EL RECHAZO DE UNA TABLA INVENTADA NOMBRA ALGO QUE SE PUEDE HACER.
        #     Decía «read the catalog resource», que en ese cliente era imposible. Un
        #     mensaje accionable que nombra una acción irrealizable no es accionable,
        #     y este proyecto se sostiene sobre esa promesa.
        out = await client.call_tool("describe_table", {"table": "clientes"})
        rej = (out.structured_content or {}).get("rejected") or {}
        sugerencia = str(rej.get("suggestion", ""))
        checks.append(
            (
                "rechazo-sugiere-algo-ejecutable",
                rej.get("rule_id") == "R004" and "describe_table" in sugerencia,
                sugerencia[:60],
            )
        )

        # 0.ter · MRTR · el presupuesto `soft` PREGUNTA antes de gastar.
        #     La spec 2026-07-28 retiró sampling y elicitation y puso el patrón de
        #     petición de entrada en su sitio. Antes, `soft` ejecutaba con un aviso que
        #     nadie leía: un umbral blando que no pregunta no es blando, es decorativo.
        #     Aquí el cliente DECLINA, así que la consulta no debe ejecutarse.
        out = await client.call_tool("run_query", {"question_sql": SOFT_BUDGET_SQL})
        body = out.structured_content or {}
        rej = body.get("rejected") or {}
        checks.append(
            (
                "mrtr-pregunta-antes-de-gastar",
                bool(pedido)
                and body.get("outcome") == "rejected"
                and rej.get("code") == "not_confirmed",
                f"preguntado={bool(pedido)} · {body.get('outcome')}/{rej.get('code')}",
            )
        )

        # 0.quater · y el camino del SÍ: confirmada, la consulta cara SÍ se ejecuta.
        #     Sin esto, «no se ejecuta» podría estar pasando por cualquier motivo y el
        #     check no sabría distinguir una confirmación que funciona de una tubería
        #     rota que nunca ejecuta nada.
        async with Client(params, elicitation_callback=responde("accept")) as otro:
            out = await otro.call_tool("run_query", {"question_sql": SOFT_BUDGET_SQL})
            body = out.structured_content or {}
            filas = (body.get("result") or {}).get("rows", [])
            checks.append(
                (
                    "mrtr-confirmada-si-se-ejecuta",
                    body.get("outcome") == "rows" and bool(filas),
                    f"{body.get('outcome')} · {len(filas)} filas",
                )
            )

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
