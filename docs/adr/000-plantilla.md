# ADR-000 · Plantilla

> Copiar a `docs/adr/NNN-titulo-en-kebab-case.md` con el siguiente número libre. **Un ADR existente no se
> edita jamás**: si la decisión cambia, se escribe otro que lo supersede y se actualiza solo la línea de
> estado del viejo. Tope: ~40 líneas. Si necesitas más, es que hay dos decisiones y son dos ADR.
>
> Un ADR se escribe cuando la decisión es **no obvia y estructural**. Lo reversible y ordinario va a
> `docs/JOURNAL.md`. Escribir un ADR para "elegí `pathlib` en vez de `os.path`" devalúa los que importan.

---

## Contexto

Qué problema real apareció, cuándo, y qué restricciones lo acotan (medidas, no supuestas: horas, latencia,
coste, licencia, versión de una dependencia). Si hay un número, va aquí con su comando y su artefacto.

## Opciones consideradas

| Opción | Pros | Contras | Coste |
|---|---|---|---|
| **A ·** | | | |
| **B ·** | | | |
| **C · no hacer nada** | | | |

La fila de "no hacer nada" no es de relleno: si no aparece, casi siempre es que no se consideró.

## Decisión

Una frase que empieza por el verbo. Qué se elige y **qué se renuncia explícitamente al elegirlo**.

## Consecuencias

- Qué se vuelve más fácil.
- Qué se vuelve más difícil, y qué queda expresamente sin cubrir.
- Qué invariante, meta o contrato queda afectado (`I-NN`, `G-XXXX`, fichero de `docs/spec/` si es propio del
  proyecto, o de `docs/CONTRACTS/` si es una copia transversal — y entonces el cambio se hace en `_comun/`).
- Qué señal indicaría que esta decisión fue equivocada, y qué habría que medir para saberlo.

## Estado

`PROPUESTO` | `ACEPTADO` | `SUPERSEDIDO por ADR-NNN` · fecha · fase.

---

## ADR pendientes de escribir en este proyecto

Salen de la investigación previa y de las correcciones sobre `PROJECT.md`. El número definitivo lo asigna
quien lo escriba, por orden de aparición; esta lista es el inventario, no la numeración.

| Tema | Fase | Por qué merece ADR |
|---|---|---|
| Validación por AST y no por lista negra de palabras | 2 | Es la tesis técnica del proyecto. Incluye por qué `sqlparse` no sirve: solo tokeniza |
| Allowlist en vez de denylist de funciones | 2 | `PROJECT.md` dice "sin funciones peligrosas", que es denylist; una denylist se queda corta el día que DuckDB añade una extensión |
| Ejecutar el AST re-serializado y nunca la cadena de entrada | 2 | Elimina la clase entera de ataques por diferencia de parser. Es la invariante I-02 |
| Hypothesis construye árboles + mutación de AST, no una gramática SQL | 2 | Un generador gramatical de SQL válido es un subproyecto de 1-2 semanas; hay que dejar escrito por qué no se hizo |
| dev / holdout / mutación en vez de un cuaderno de 40 casos | 2 | El 100 % sobre el conjunto que guio las reglas es tautológico. Decisión que cambia la métrica insignia |
| `semgrep` fuera, `check_no_raw_sql.py` dentro | 2 | Se retira una herramienta que `PROJECT.md` §4 listaba; hay que justificar que sus reglas son un no-op aquí |
| Estimador de coste desde metadatos Iceberg, no desde `EXPLAIN` | 3 | `EXPLAIN` no da bytes escaneados y `EXPLAIN ANALYZE` ejecuta. Ventaja lateral: el mismo estimador vale para los dos motores |
| Enmascarado por reescritura de AST, no por post-proceso del resultado | 4 | Post-procesar por nombre de columna se rompe con el primer alias |
| Canonicalización JCS (RFC 8785) y límite honesto del hash encadenado | 5 | Sin canonicalización el hash depende del orden de claves. Y quien escribe en el almacén puede recalcular la cadena: hay que declararlo |
| Bucle propio puro en vez de LangGraph en el dominio | 6 | `PROJECT.md` fija LangGraph. Dos reintentos no justifican un framework en el dominio; el adaptador delgado demuestra el conocimiento sin acoplar |
| El rol bajo un protocolo sin sesiones | 7 | La spec 2026-07-28 eliminó las sesiones: sin este ADR, el anillo 5 es ficción |
| MCP: SDK oficial v2, sin FastMCP externo, stdio + Streamable HTTP | 7 | Ruptura de generación; HTTP+SSE está deprecado |
| Modelo OTel interno propio + capa de traducción | 8 | Nada de `gen_ai.*` es estable y ya hubo renombrados. Es lo que convierte una ruptura de spec en un cambio de un fichero |
| 60 preguntas estratificadas en vez de 100, con Wilson publicado | 8 | Recorta 7-13 h humanas y cambia cómo se publica el número |
| Iceberg spec v2 y no v3; DuckLake descartado | 0 y 9 | Athena no soporta la v3: con v3 el criterio de aceptación nº 5 falla por el formato, no por la abstracción |
| Python 3.12 porque MWAA no ofrece más | 0 | Existen 3.13 y 3.14. Decir "porque lo pide la oferta" es transcripción; decir "porque MWAA no ofrece más" es ingeniería |
| Terraform propio mínimo; no se importan módulos del proyecto 05 | 9 | La dependencia que declaraba el mapa está invertida en el tiempo |
