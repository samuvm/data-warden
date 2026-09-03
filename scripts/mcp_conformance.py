#!/usr/bin/env python
"""`G-MCP-CONFORM` · los 11 puntos de la spec MCP 2026-07-28. **== 11, no >= 11.**

La spec de julio de 2026 es una **ruptura de generación**, no una revisión: se fueron
las sesiones (`Mcp-Session-Id`), el handshake `initialize`, `ping` y la resumibilidad
SSE; `sampling`, `roots` y `logging` quedaron deprecados; `FastMCP` pasó a
`MCPServer`. El código de ejemplo de 2025 no compila.

**Los once puntos no se inventan aquí:** salen de `docs/STACK.md §7`, que es donde la
investigación previa los dejó escritos, y de `docs/PLAN.md` fase 7. Este script los
comprueba sobre los artefactos REALES —el contrato firmado y lo que el servidor
publica— y no sobre una lista de deseos.

**Se comprueban las AUSENCIAS igual que las presencias**, y eso es la mitad del
valor: un servidor que arrastre `initialize` de la generación anterior «funciona»
contra un cliente viejo y falla contra uno nuevo, que es la peor forma de fallar.
"""

from __future__ import annotations

import pathlib
import sys

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
from datawarden.mcp import server as warden_mcp
from gatelib import ROOT, record

CONTRACT = ROOT / "docs" / "spec" / "tools.yaml"
SERVER_SRC = ROOT / "src" / "datawarden" / "mcp" / "server.py"

#: Lo que la generación ANTERIOR tenía y esta no. Si aparece en el servidor, es que
#: alguien copió un ejemplo de 2025.
GONE = (
    "Mcp-Session-Id",
    "mcp_session_id",
    "def initialize",
    '"initialize"',
    '"ping"',
    "sse_app",
    "run_sse_async",
)


def main() -> int:
    contract = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    specs = warden_mcp.tool_specs(contract)
    discover = warden_mcp.discover_payload(specs, "0.7.0")
    source = SERVER_SRC.read_text(encoding="utf-8")

    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append((name, bool(ok), detail))

    # 1 · sin sesiones, sin initialize, sin ping, sin SSE resumible
    arrastres = [marker for marker in GONE if marker in source]
    check(
        "sin-sesiones-ni-handshake",
        not arrastres,
        f"arrastres de la generación anterior: {arrastres}" if arrastres else "ninguno",
    )

    # 2 · `server/discover` obligatorio
    check(
        "server-discover",
        bool(discover.get("supportedVersions")),
        f"supportedVersions={discover.get('supportedVersions')}",
    )

    # 3 · la versión que se declara es la de la spec
    check(
        "version-de-la-spec",
        discover.get("supportedVersions") == ["2026-07-28"],
        str(discover.get("supportedVersions")),
    )

    # 4 · `resultType` obligatorio
    check(
        "result-type",
        discover.get("resultType") == "complete",
        f"resultType={discover.get('resultType')}",
    )

    # 5 · `ttlMs` obligatorio en los list
    check("ttl-ms", isinstance(discover.get("ttlMs"), int), f"ttlMs={discover.get('ttlMs')}")

    # 6 · `cacheScope` obligatorio en los list
    check(
        "cache-scope",
        discover.get("cacheScope") in {"public", "private"},
        f"cacheScope={discover.get('cacheScope')}",
    )

    # 7 · `tools/list` DETERMINISTA. Se compara con el orden del contrato, y se
    #     recalcula dos veces: un orden que sale de un diccionario puede coincidir
    #     por casualidad una vez.
    publicado = [tool["name"] for tool in discover["tools"]]
    otra_vez = [
        tool["name"]
        for tool in warden_mcp.discover_payload(warden_mcp.tool_specs(contract), "x")["tools"]
    ]
    check(
        "tools-list-determinista",
        publicado == list(contract["orden"]) == otra_vez,
        f"{publicado} vs contrato {contract['orden']}",
    )

    # 8 · `inputSchema` en JSON Schema 2020-12
    dialecto = warden_mcp.SCHEMA_DIALECT
    check(
        "input-schema-2020-12",
        all(t["inputSchema"].get("$schema") == dialecto for t in discover["tools"]),
        dialecto,
    )

    # 9 · `outputSchema` con `oneOf` entre filas y rechazo. **Un rechazo NO es un
    #     error del protocolo:** modelarlo como error haría que el cliente lo
    #     reintentara a ciegas en vez de leer el motivo.
    con_oneof = [
        t["name"] for t in discover["tools"] if len(t["outputSchema"].get("oneOf", [])) == 2
    ]
    check(
        "output-schema-oneof-rows-o-rechazo",
        len(con_oneof) == len(discover["tools"]),
        f"{len(con_oneof)}/{len(discover['tools'])} herramientas",
    )

    # 10 · las cuatro herramientas del contrato, publicadas y descritas
    sin_descripcion = [t["name"] for t in discover["tools"] if len(t["description"]) < 80]
    check(
        "cuatro-herramientas-descritas",
        len(discover["tools"]) == 4 and not sin_descripcion,
        f"{len(discover['tools'])} herramientas, sin descripción útil: {sin_descripcion}",
    )

    # 11 · MRTR en lugar de sampling/elicitation. Se comprueba que el SDK instalado
    #      trae `InputRequiredResult` y `request_state`, que es el patrón nuevo, y
    #      que el servidor NO usa los deprecados.
    import mcp.types as mcp_types
    from mcp.server import request_state

    deprecados = [d for d in ("createMessage", "sampling/", "elicitation/") if d in source]
    check(
        "mrtr-en-lugar-de-sampling",
        hasattr(mcp_types, "InputRequiredResult")
        and hasattr(request_state, "RequestStateBoundary")
        and not deprecados,
        f"deprecados en el servidor: {deprecados}" if deprecados else "InputRequiredResult ok",
    )

    passed = sum(1 for _, ok, _ in checks if ok)
    record(
        "mcp-conformance.json",
        "G-MCP-CONFORM",
        value=float(passed),
        detail={
            "total": len(checks),
            "checks": [{"name": n, "ok": ok, "detail": d} for n, ok, d in checks],
            "spec": "2026-07-28",
        },
        command="make mcp-conformance",
    )

    print(f"mcp_conformance: {passed}/{len(checks)} puntos de la spec 2026-07-28\n")
    for name, ok, detail in checks:
        print(f"  {'ok  ' if ok else 'FALLO'} {name:38} {detail}")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
