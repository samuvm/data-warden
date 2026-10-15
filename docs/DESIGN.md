# Data Warden · especificación de diseño

> Acceso conversacional a un lakehouse con los controles que una empresa exige antes de dejar que un
> modelo se acerque a sus datos: SQL validado antes de ejecutar, límites de coste previos, enmascarado
> de datos personales por rol y registro de auditoría encadenado. Se expone como servidor MCP y como
> API HTTP.

El sistema funciona en local con DuckDB sobre un dataset sintético. El motor en la nube (Athena + Glue)
es una extensión opcional detrás de la misma interfaz de motor.

Documentos relacionados: metas ejecutables en [`GOALS.yaml`](GOALS.yaml), invariantes y régimen de
pruebas en [`RULES.md`](RULES.md), versiones en [`STACK.md`](STACK.md), modelo de amenaza en
[`threat-model.md`](threat-model.md), decisiones en [`adr/`](adr/), contratos propios en [`spec/`](spec/).

---

## 1. Diseño técnico

La premisa: traducir lenguaje natural a SQL es la parte resuelta; lo que impide llevarlo a producción
es lo que va después. El modelo propone y el sistema decide, con reglas que no viven en el prompt.

El sistema se organiza en cinco anillos de control. Cada uno se prueba por separado y ninguno depende
del modelo salvo el segundo.

**Anillo 1 · Contexto.** El catálogo (esquema, tipos, claves, relaciones, linaje de vistas, columnas
sensibles y deprecadas) se genera desde el esquema real y se expone como recurso MCP de solo lectura.
El glosario de negocio firmado se compila junto a él y entra en el prompt de generación. El modelo no
adivina la estructura: la lee.

**Anillo 2 · Generación.** Traducción a SQL con ejemplos del dominio y un bucle de corrección: si el
guard rechaza, el modelo recibe la regla, el motivo y la sugerencia, y reintenta (máximo dos veces). Es
el único anillo probabilístico, y por eso se mide con evaluaciones en lugar de probarse con aserciones.

**Anillo 3 · Validación estática.** El núcleo técnico. La consulta se parsea a árbol con `sqlglot`, se
cualifica contra el catálogo y se somete a catorce reglas (`R001`…`R014`) sobre una allowlist de nodos
y funciones:

- Una sola sentencia y solo lectura: ningún nodo de escritura, DDL, `COPY`, `ATTACH` ni `PRAGMA`.
- Solo tablas del catálogo autorizado, resolviendo alias, CTEs y subconsultas; ningún esquema de sistema.
- Ningún producto cartesiano: todo `JOIN` lleva condición.
- Ninguna función fuera de la allowlist. Un nodo o una función desconocidos se rechazan.
- `LIMIT` obligatorio, inyectado si falta, y acotado por rol.
- Profundidad de anidamiento y tamaño de árbol acotados.
- Política por rol, columna y posición en la consulta (proyección, predicado, agrupación, función).

Se valida el AST y se ejecuta el AST re-serializado, nunca la cadena de entrada (ADR-001, ADR-002).

**Anillo 4 · Coste.** Antes de ejecutar, el coste se estima desde los metadatos de Iceberg (filas y
bytes por columna y partición, con poda), sin leer datos. Por encima del presupuesto blando se pide
confirmación; por encima del duro no se ejecuta, y el rechazo explica cómo acotar la consulta
(ADR-003).

**Anillo 5 · Salida.** Enmascarado por rol mediante reescritura del AST antes de ejecutar (ADR-004),
límite de filas del dominio y registro de auditoría append-only con hash encadenado: quién, qué
modelo, qué pregunta, qué SQL, cuántas filas, cuánto costó y cuándo. Se audita también lo rechazado.

### Interfaz MCP

El MCP es la interfaz, no el sistema. Los cinco anillos viven en el servidor, así que ningún cliente
ni modelo puede saltárselos: las reglas no están de su lado. Un cliente MCP obtiene:

- **Tools:** `run_query`, `describe_table`, `sample_table`, `explain_cost`.
- **Resource:** el catálogo publicado (`warden://catalog`): tablas, columnas, tipos y linaje. Es el
  mismo artefacto contra el que valida el guard, así que lo que no está ahí no se puede consultar.

Exponer el glosario de negocio como recurso propio y las plantillas de análisis frecuentes como
prompts MCP queda fuera de esta versión.

El servidor implementa la spec MCP 2026-07-28, sin sesiones. El rol lo fija quien instala el servidor
(entorno del proceso), nunca quien pregunta: `_meta` y los argumentos de tool son dato, no autoridad.

Una capa HTTP equivalente sirve las mismas cuatro operaciones; un test de contrato exige que ambos
transportes devuelvan el mismo resultset normalizado y que ninguno importe al otro.

### Arquitectura

```
    cliente MCP (Claude Desktop, agente propio, ...)      cliente HTTP
              │  tools / resources                           │
              ▼                                              ▼
    ┌──────────────────────── data-warden ─────────────────────────┐
    │  service/ (cuatro operaciones, agnósticas del transporte)     │
    │                                                               │
    │  catálogo ──► generación SQL ──► sqlglot AST ──► guard        │
    │                    ▲                               │          │
    │                    └──── rechazo accionable ◄── ¿válida? no   │
    │                                                    │ sí       │
    │                                                    ▼          │
    │                                  coste (metadatos Iceberg)    │
    │                                                    ▼          │
    │                                  enmascarado (reescritura AST)│
    │                                                    ▼          │
    │                         AuditedExecutor ──► Engine.execute()  │
    │                                                    ▼          │
    │                         auditoría append-only (hash encadenado)
    └───────────────────────────────────────────────────────────────┘
              │                                    │
       DuckDB + Iceberg (local)          Athena + Glue (extensión)
```

### Componentes

| Componente | Elección | Motivo |
|---|---|---|
| Validación SQL | `sqlglot` | Parsea a AST, cualifica contra esquema y transpila entre dialectos. Es la pieza crítica |
| Motor local | DuckDB + PyIceberg | Lakehouse real en un portátil |
| Motor nube | Athena + Glue Catalog | Mismo SQL, distinto ejecutor, detrás de la interfaz `Engine` |
| Protocolo | SDK oficial de MCP para Python (v2) | stdio para clientes de escritorio |
| Bucle de corrección | Bucle propio; LangGraph como adaptador | El bucle es lo que se mide (ADR-005) |
| Auditoría | SQLite append-only + hash encadenado con JCS (RFC 8785) | Detecta manipulación posterior de registros |
| Datos | CIERZO, dataset 100 % sintético | Pasarela de pagos ficticia de 24 tablas y 294,8 M de filas. Se publican el generador y su semilla, nunca los datos; se reproduce byte a byte sin descargas |

Versiones exactas y su justificación en [`STACK.md`](STACK.md).

---

## 2. Requisitos

### Funcionales

- Servidor MCP instalable en un cliente real con un bloque de configuración documentado.
- Las cuatro tools operativas, con esquemas JSON y descripciones suficientes para elegir la correcta
  sin más contexto.
- Catálogo autogenerado desde el esquema real, nunca escrito a mano, con las columnas sensibles y las
  deprecadas marcadas.
- Bucle de autocorrección: si la validación rechaza, el modelo recibe el motivo y reintenta, como
  máximo dos veces.
- Mismo dominio servido por MCP y por HTTP.

### De calidad

Los umbrales exactos, el comando que los mide y el artefacto donde queda el número están en
[`GOALS.yaml`](GOALS.yaml), sellado por `thresholds.lock`. Aquí se resume qué se mide y por qué.

| Requisito | Meta | Cómo se mide |
|---|---|---|
| Ninguna escritura ni evasión atraviesa el guard | `G-WRITE-BLOCK` (axioma) | Holdout de ataques escrito sin acceso al código del guard, más variantes por mutación de AST. El cuaderno de desarrollo (`G-WRITE-BLOCK-DEV`) es higiene, no evidencia |
| Ninguna fuga de columnas sensibles | `G-PII-LEAK` (axioma) | Tres superficies: proyección (directa, alias, función, `*`), predicado y agregación de grupo único. Se ejecuta por el camino real |
| Ninguna consulta por encima del presupuesto llega al motor | `G-BUDGET-ESCAPE` (axioma) | Contador en `Engine.execute()` más rechazo por reloj sobre el dataset completo |
| El estimador de coste no subestima | `G-COST-CALIB` | p95 de real/estimado sobre 60 consultas calibradas |
| Ninguna invocación sin auditoría | `G-AUDIT-COV` (axioma) | Propiedad: registros == invocaciones, en los cuatro estados (`rejected_by_guard`, `rejected_by_budget`, `executed`, `error`) |
| La cadena de auditoría detecta manipulación | `G-AUDIT-TAMPER` | Mutaciones de byte inyectadas |
| Exactitud de ejecución del SQL generado | `G-EXEC-ACC` | Resultsets normalizados contra SQL de referencia sobre 60 preguntas estratificadas, con intervalo de Wilson |
| Recuperación tras rechazo | `G-RECOVERY` | Corpus fijo de rechazos sembrados, con intervalo de Wilson |
| Elección de tool por descripción | `G-TOOL-CHOICE` | 20 escenarios con modelo fijado y temperatura 0 |

La métrica de exactitud es de *ejecución*, no de similitud de texto. Dos consultas escritas de forma
distinta pueden dar el mismo resultado, y eso es lo que importa; comparar SQL como texto engaña. Qué
cuenta como «el mismo resultado» está en [`spec/resultset-equality.md`](spec/resultset-equality.md).

### Fuera de alcance

Escrituras de cualquier tipo, gestión de identidades (los roles se configuran por proceso),
optimización automática de consultas y más de un esquema.

---

## 3. Estructura del repositorio

```
data-warden/
├── src/datawarden/
│   ├── domain/         # tipos congelados y puros
│   ├── catalog/        # introspección de esquema, linaje, estadísticas de Iceberg
│   ├── guard/          # validación sobre AST: allowlist + reglas
│   │   ├── rules/      # una regla = un fichero = su corpus de casos
│   │   └── validator.py
│   ├── cost/           # estimador y presupuesto
│   ├── mask/           # enmascarado por reescritura de AST
│   ├── audit/          # cadena de auditoría; único camino al motor
│   ├── principal/      # rol, política y presupuesto por rol
│   ├── nl2sql/         # generación, bucle de corrección, proveedores de modelo
│   ├── agent/          # adaptador de LangGraph sobre el bucle
│   ├── service/        # las cuatro operaciones, agnósticas del transporte
│   ├── mcp/  http/     # los dos transportes
│   ├── telemetry/      # modelo interno de llamadas a modelo y traductor OTel
│   ├── evalsupport/    # comparación de resultsets, mutación de AST, recuperación
│   └── engines/        # DuckDBEngine (interfaz común para otros motores)
├── prompts/            # prompts versionados con frontmatter
├── attacks/            # cuaderno de ataque de desarrollo
├── evals/              # banco de preguntas, respuestas grabadas, informes
├── datagen/            # generador del dataset y su semilla
├── tests/              # unit · property · contract · integration · adversarial · holdout
└── docs/               # diseño, metas, reglas, stack, amenazas, ADR, contratos
```

Una regla de validación es un fichero con su corpus de casos `accept` y `reject` en
`tests/unit/guard/cases/RNNN.yaml`. Cada evasión nueva se convierte en una regla o en un caso
permanente, de modo que la suite de seguridad crece con cada ataque encontrado.

---

## 4. Estrategia de construcción y pruebas

### Orden de construcción

1. **Motor y catálogo.** Dataset cargado, introspección de esquema y una consulta escrita a mano
   ejecutándose, sin IA.
2. **El validador antes que el generador.** Las barreras se construyen antes de que exista nada que
   las cruce: primero el cuaderno de ataque, después las reglas que lo paran, con TDD estricto. Es el
   mejor sitio del proyecto para aplicarlo porque cada caso es determinista y la especificación es exacta.
3. **Coste, enmascarado y auditoría**, sobre el mismo árbol validado.
4. **Generación NL→SQL** y bucle de corrección.
5. **Transportes MCP y HTTP**, encima de un dominio que ya funciona y está probado.
6. **Evaluación de exactitud** sobre el banco de preguntas de referencia.
7. **Motor en la nube** detrás de la misma interfaz, como extensión.

### Pirámide de pruebas

El régimen exacto por zona del código está en [`RULES.md`](RULES.md) §2.

**Nivel 0 · Estáticos.** `ruff` (incluidas las reglas de bandit), `mypy --strict`, `detect-secrets`, contratos de capas con
`import-linter` y comprobaciones de arquitectura sobre AST (`scripts/check_*.py`). `semgrep` no se usa:
sus reglas de inyección buscan concatenación de cadenas, y aquí el SQL siempre sale de renderizar un
árbol validado (ADR-002).

**Nivel 1 · Unitarios y de propiedades.**

- Cada regla contra su batería de casos que debe aceptar y rechazar, asertando el `rule_id`, la
  posición y el sujeto exactos.
- Evasiones conocidas: comentarios entre tokens, CTEs que ocultan una escritura, `UNION` contra una
  tabla fuera de ámbito, alias que enmascaran una columna sensible, expresiones derivadas
  (`concat(nombre, apellido)` sobre columnas protegidas), funciones del motor que leen ficheros.
- Hypothesis construye árboles con `st.builds()` sobre nodos de `sqlglot` y muta AST de un corpus
  semilla, y comprueba invariantes como «si el validador acepta, el árbol no contiene ningún nodo de
  escritura» y «`validate()` no propaga nunca una excepción».
- Enmascarado: la columna sensible no aparece en la salida ni directa, ni por alias, ni dentro de una
  función, ni en un `GROUP BY` que la exponga por agregación de grupo único.
- Cadena de auditoría: modificar cualquier byte de un registro anterior rompe la verificación.

**Nivel 2 · Integración.** DuckDB real con el dataset.

- Consultas conocidas con resultado esperado.
- Presupuesto: una consulta cara se rechaza antes de ejecutarse, verificado por reloj.
- El mismo caso contra cada implementación de `Engine` produce el mismo resultset normalizado.

**Nivel 3 · Contrato.** Los esquemas de las tools MCP son un contrato con un consumidor que no se
controla. Se prueba que:

- Los esquemas JSON de las tools son válidos y estables (snapshot) y `tools/list` es determinista.
- El servidor cumple los puntos de conformidad de la spec 2026-07-28 (`G-MCP-CONFORM`).
- Las descripciones bastan: un modelo que solo ve las descripciones elige la tool correcta en 20
  escenarios. Si se confunde, la descripción está mal escrita.
- Un error devuelto es accionable: contiene la regla, el motivo y una sugerencia, y no revela valores
  de celda.
- MCP y HTTP devuelven lo mismo.

**Nivel 4 · Evaluación.**

- Exactitud de ejecución sobre el banco de referencia, comparando resultsets normalizados, no texto.
- Tasa de recuperación tras rechazo.
- Respuestas del modelo grabadas por petición, de modo que `make eval` es determinista y no necesita
  ningún modelo.

**Nivel 5 · Adversarial.**

- Cuaderno de ataque de desarrollo, holdout y mutación de AST. Ningún ataque puede pasar: aquí no hay
  umbral estadístico.
- Inyección de prompt: el modelo nunca ve filas del almacén, y lo que escriba vuelve a pasar por el
  guard (ver [`threat-model.md`](threat-model.md) §4.3.1).
- Confusión de autoridad: instrucciones o un rol dentro de `_meta` o de un argumento de tool no alteran
  el principal efectivo.

### Flujo de trabajo

- Trunk-based, ramas cortas, Conventional Commits.
- Toda regla del guard tiene su familia de ataque en el cuaderno y toda familia su regla (I-14,
  `scripts/check_attack_coverage.py`). Una regla nueva llega con sus casos.
- Gate rápido en menos de 30 s (estáticos, unitarios, propiedades en perfil `dev`); el gate completo y
  `make done` añaden integración, holdout, mutación, secretos y evaluaciones desde caché.
- Las decisiones estructurales no obvias quedan en [`adr/`](adr/); el modelo de amenaza, qué se
  protege, contra quién y qué queda fuera, en [`threat-model.md`](threat-model.md).

---

## 5. Criterios de aceptación

1. Se instala en un cliente MCP real siguiendo el README, sin ayuda.
2. El cuaderno de ataque, el holdout y la mutación de AST corren en el gate y están en verde, con los
   casos visibles.
3. La exactitud de ejecución está publicada con su intervalo y su metodología.
4. Existe un log de auditoría de ejemplo, con la verificación de integridad pasando.
5. El mismo caso funciona en DuckDB y en Athena sin cambiar código de dominio.

El criterio nº 5 depende de una cuenta AWS y se publica como no verificado mientras no se haya
ejecutado contra Athena. Para que sea alcanzable, las tablas se escriben en Iceberg spec v2, que
Athena soporta, y el estimador de coste trabaja sobre los mismos metadatos que leería Athena.

## 6. Riesgos

| Riesgo | Mitigación |
|---|---|
| Alcance sin límite | Solo lectura y un esquema, fijados desde el principio |
| El validador se queda corto | Allowlist: lo no declarado se rechaza. El cuaderno de ataque crece con cada evasión, el holdout mide lo que el desarrollo no vio, y el modelo de amenaza declara lo que no se cubre |
| Athena genera coste inesperado | Presupuesto y alarma antes de la primera consulta; modo local por defecto |
| El protocolo eclipsa el resto | El README deja claro desde la primera línea que el valor está en los anillos, no en el protocolo |
