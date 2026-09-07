"""Las cuatro operaciones del sistema, **por debajo de todo transporte**.

Existe porque el contrato de capas lo exigió: `http/app.py` importaba
`mcp/server.py` y `lint-imports` lo marcó en rojo. Y tenía razón — `mcp` y `http` son
hermanos en la misma capa, así que uno no puede depender del otro. Si el transporte
HTTP necesitara al transporte MCP para existir, se destruiría exactamente lo que
`http/` viene a demostrar: **que el dominio no depende del transporte.**

Lo que vive aquí es lo que ninguno de los dos transportes debería poseer: qué hace
`run_query`, qué devuelve `describe_table` y qué forma tiene una respuesta. Lo que se
queda en `mcp/` es el protocolo —`tools/list`, `server/discover`, `resultType`, los
esquemas JSON— y en `http/`, las rutas.

**La prueba de que el reparto es correcto:** los dos transportes dan byte a byte la
misma respuesta a la misma pregunta, y ninguno importa al otro.
"""
