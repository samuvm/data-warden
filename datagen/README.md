# CIERZO · generador de dataset sintético

> **Esto no es el proyecto.** Es el generador de datos sobre el que el proyecto se
> construirá. Vive en `datagen/`, fuera de `src/`, y **no toca `pyproject.toml`**:
> `Q-002` (firma de versiones) y `P-001` (cambio de `PROJECT.md` para admitir
> dataset 100 % sintético) siguen `PENDIENTE` en `docs/PARA-SAMUEL.md`. Sus
> dependencias están pinadas aparte en `datagen/requirements.txt` y se ejecutan con
> `uv run --with-requirements`, que no escribe nada en el entorno del proyecto.

CIERZO es una pasarela de pagos europea que no existe: 12.400 comercios, 9,2 M de
clientes y 68 M de intentos de autorización en dos años (2024-09-01 → 2026-08-31).
La empresa es inventada y las personas son sintéticas, pero el vocabulario es el
real de la industria — MCC, códigos ISO 8583, exenciones SCA de PSD2, liquidación
por lotes, contracargos por etapas — porque de ahí viene la credibilidad.

## Cómo se ejecuta

```bash
./datagen/run.sh dev     # ~15 s   · 380 comercios,   92 k clientes,  0,7 M intentos
./datagen/run.sh demo    # ~3 min  · 3.100 comercios, 920 k clientes, 6,6 M intentos
./datagen/run.sh full    # ~25 min · 12.400 comercios, 9,2 M clientes, 66,5 M intentos
```

Cinco pasos, y el orden importa: se generan los hechos de autorización, se cataloga,
se **derivan** en SQL las tablas de movimiento de dinero (para que cuadren por
construcción, no por suerte), se recataloga y se verifica, y se emite el script de
catálogo portable que usa el contenedor.

Explorarlo:

```bash
docker compose -f datagen/docker/compose.yaml build
PROFILE=demo docker compose -f datagen/docker/compose.yaml run --rm sql
cierzo> .read sql/01-explora.sql
```

### Iceberg

El mismo dataset está además materializado como **tablas Apache Iceberg, spec v2**:

```bash
duckdb -c ".read datagen/out/full/iceberg/duckdb-views.sql" \
       -c "SELECT count(*) FROM fact_payment_attempt;"
```

**Qué añade sobre el Parquet suelto.** Hasta aquí una tabla era «lo que devuelva
este `glob`», y cualquiera que dejase un fichero de más la cambiaba sin querer.
Iceberg pone encima un **manifiesto**: la lista exacta de ficheros que forman la
tabla en cada instante. De ahí salen la evolución de esquema, el viaje en el
tiempo, las instantáneas atómicas y una poda de particiones fiable — y ninguna de
las cuatro es posible sobre un `glob`.

**No copia un solo byte:** `add_files` registra el Parquet que ya existe. El
catálogo del perfil completo pesa **1,4 MB para 7,46 GB de datos**, y contar los
66,6 M de filas tarda 0,02 s porque lee el manifiesto en vez de escanear.

**Spec v2, no v3, y no es un detalle.** `docs/STACK.md` lo dice: Athena no soporta
v3, y el criterio de aceptación nº 5 del proyecto es que el mismo caso funcione en
DuckDB **y** en Athena. Escribirlo en v3 haría fallar ese criterio por el formato
en vez de por la abstracción de motor, que es justo lo que se quería demostrar. El
script lo comprueba y aborta si no sale v2.

### El contenedor

El contenedor es **solo lectura y sin base de datos en disco**: reconstruye el
catálogo en memoria desde el Parquet montado cada vez que arranca, así que nunca
puede estar describiendo una versión anterior de los datos.

## Determinismo

Misma semilla + mismo perfil ⇒ Parquet idéntico byte a byte. Nada lee el reloj, el
sistema de ficheros ni la red. Las tablas derivadas usan `hash(clave)` en vez de un
generador aleatorio, así que tampoco dependen del orden de las filas.

Se publica **el generador y su semilla**, nunca los datos.

## Escala: se estratifica por entidad, jamás por filas

Los tres perfiles conservan los 730 días y la forma estadística completa. Muestrear
filas destruiría justo lo que da valor: las series por comercio, los duplicados de
ingesta (solo se detectan por pares) y las anomalías plantadas.

Hay un **suelo declarado**: por debajo de ~300 comercios el «top 1 %» es un solo
comercio, y fijarlo en el 45 % del tráfico fuerza al top 10 % por encima del 87 %.
Las dos concentraciones publicadas no pueden cumplirse a la vez, y el solucionador
**falla en vez de errar el objetivo en silencio**.

## La regla: ningún número se escribe, todos se miden

Se declara la **concentración que se quiere publicar** y se resuelve el exponente
numéricamente. Declarar un exponente y afirmar un porcentaje después es cómo cinco
de seis diseños candidatos publicaron leyes de potencia que no producían sus
propios números.

| Forma | Objetivo | Cómo se consigue |
|---|---|---|
| Top 1 % de comercios | 45 % del **vector de pesos** | Zipf-Mandelbrot, `(exponente, q)` resueltos para **ambos** objetivos a la vez |
| Top 10 % de comercios | 80 % del **vector de pesos** | ídem — una ley de un solo parámetro no puede con los dos |
| Concentración **realizada** | se **mide**, no se fija | ~22 % / 62 % del tráfico real: la afinidad, la fidelidad y el sesgo doméstico mueven el tráfico DESPUÉS de sortear los pesos. Se publica la medida, con un suelo comprobado por el gate |
| Pagos por cliente | mezcla Gamma-Poisson | `1 + BinomialNegativa`, nunca una ley de potencias por rango |
| Clientes que nunca pagan | 4,3 % | declarado; es la trampa del `INNER JOIN` donde iba un `LEFT JOIN` |
| Pirámide de edad | 8/21/22/20/16/13 % | declarada por tramo; ninguna gamma reproduce la cola de mayores |
| Cuota de volumen por sector | 20 valores declarados | asignación voraz sobre rangos de comercio, y medida después |

Todo lo medido queda en `MANIFEST.json` junto al objetivo, con su desviación.

## El esquema: 24 tablas, 278 columnas, **tres granos distintos**

Los tres granos son deliberados, porque confundirlos es el error más caro que se
comete contra un almacén de pagos:

- **`fact_payment_attempt`** — una fila por **INTENTO DE AUTORIZACIÓN**. Un pago que
  falla dos veces y acierta a la tercera son tres filas con el mismo
  `payment_intent_id`. `count(*)` sobre esta tabla **no** es el número de ventas.
- **`fact_order_line`** — una fila por **LÍNEA DE CESTA**. Sumar importes uniendo con
  la tabla de intentos multiplica cada cesta por su número de intentos.
- **liquidación / pagos** — una fila por **LOTE**. Es donde el dinero se movió de
  verdad y el único grano donde «cuánto nos han pagado» tiene una sola respuesta.

```
ref_country ─ ref_city ─ dim_ip_block ──┐
ref_mcc ─ ref_currency ─ ref_fx_rate_daily ─ ref_decline_reason ─ dim_date
                                        │
dim_corporate_group ──(recursiva)──┐    │
                                   ▼    ▼
                             dim_merchant (SCD2) ── dim_merchant_site
                                   │                      │
dim_customer ── dim_card           │                      │
     │  │                          ▼                      ▼
     │  └── bridge_customer_device ──► FACT_PAYMENT_ATTEMPT ◄── dim_device
     │                                    │       │
     │                                    │       └──► fact_order_line ── dim_product
     │                                    ▼
     │                     fact_settlement_batch ──► fact_payout
     └──────────────────►  fact_refund · fact_dispute

dim_employee (recursiva) ── bridge_merchant_account_manager (con peso y vigencia)
```

## Las trampas plantadas, y por qué cada una está ahí

Ninguna es ruido por el ruido. Cada una es un error de negocio real con una
respuesta correcta.

| Trampa | Cuánto | Qué castiga |
|---|---|---|
| Grano de intento vs de pago | 1,11 intentos/pago | Contar ingresos contando filas: **+27 %** |
| Líneas × intentos | 2,24 líneas/cesta | Multiplicar la cesta por los reintentos |
| SCD2 con clave natural | 1,3 versiones/comercio | Unir por `merchant_id` en vez de `merchant_sk`: **+60 % de filas** |
| Sin cotización FX en fin de semana | — | `ON fecha = fecha` pierde **el 24 %** de los pagos no-euro en silencio |
| Clientes sin ningún pago | 4,3 % | `INNER JOIN` donde iba `LEFT JOIN` |
| Cliente invitado (`customer_sk = -1`) | 6,1 % | El miembro desconocido de la dimensión |
| Duplicados de ingesta at-least-once | 0,35 % | Misma tupla de negocio, `ingestion_id` distinto |
| Llegada tardía | 1,7 % | `ingested_at` cae 1-6 días después del evento |
| Tráfico de pruebas en producción | 1,2 % | `is_test` sin filtrar |
| Columna obsoleta que discrepa | 0,4 % | `amount_cents` frente a `amount_minor` |
| Fechas de nacimiento imposibles | 0,21 % | Centinela `1900-01-01` y fechas futuras |
| Puente con `allocation_pct` | 1,3 gestores/comercio | Sumar sin ponderar cuenta el dinero dos y tres veces |
| Segundo apellido nulo | ~68 % de países | NULL que significa «este sistema de nombres no lo tiene», no «falta el dato» |
| **`support_note` con PII dentro** | 8,1 % de clientes | Texto libre con el nombre, el teléfono, el NIF o los últimos cuatro dígitos **dentro de la frase**. Es la columna que ninguna política por nombre de columna protege, y donde la PII se escapa de verdad |
| Solicitud de supresión RGPD | 0,34 % | Auditoría inmutable frente a derecho de supresión: la contradicción que un revisor externo señala en el primer minuto |

## Los ganchos que hacen que dé juego

- **Perfilado real.** Los rasgos del cliente (edad, renta, sensibilidad al precio,
  afinidad de categoría) **conducen** la generación: qué compra, cuánto gasta y desde
  dónde se conecta. Por eso `18-24 → Gaming` sale con lift 2,5 y `65+ → Donaciones`
  con 2,56, y no es una tautología: la etiqueta de segmento no se usa para generar,
  se deriva de lo mismo que sí se usa.
- **Flujo de dinero entre grupos.** Un comercio opera en un país y su matriz última
  cobra en otro. Responderlo exige **CTE recursiva** — y una `WITH RECURSIVE` sin cota
  es una denegación de servicio escrita en SQL, que es justo lo que un guard de
  validación tiene que rechazar por forma y no por palabra clave.
- **Dispositivos e IP.** Una familia y un anillo de fraude son idénticos en el puente.
  Se separan en tres ejes a la vez: cuánta gente, si además comparten **red**, y qué
  compran. 4-6 personas → riesgo 222 y 7 % de categorías de riesgo. 11-14 personas
  desde 2 redes → riesgo 470 y **46 % en apuestas y cripto**.
- **La unión geográfica es un RANGE JOIN** (`ip BETWEEN inicio AND fin`), la operación
  más cara del catálogo, y por tanto la prueba honesta de cualquier estimador que
  diga cuántos bytes va a escanear antes de ejecutar.

## Lo que sale del perfil completo, medido

**Estas cifras no se escriben: se generan.** `./datagen/run.sh full` produce
`datagen/MEASURED-full.md` desde el manifiesto y la base de datos, y este README lo
incluye. Una cifra que `datagen/report.py` no sepa producir no entra aquí.

La regla nació de un fallo concreto: la tabla anterior se titulaba «medido» y su fila
de concentración de comercios publicaba el **objetivo del solucionador** (45 % / 80 %)
cuando el tráfico real daba 30,9 % / 71,6 %. Era exactamente el pecado que
`config.py` dice que este generador existe para evitar, y un revisor lo encontró en
la documentación en vez de en los datos.

👉 **[`MEASURED-full.md`](MEASURED-full.md)** · también
[`MEASURED-demo.md`](MEASURED-demo.md) y [`MEASURED-dev.md`](MEASURED-dev.md).

## Verificación

`build_duckdb.py` ejecuta **51 comprobaciones bloqueantes** en cada build. Todas
tienen que salir en verde.

**Doce bugs reales del generador salieron de aquí**, no de mirar los datos:
`ingestion_id` reiniciándose cada día (99,6 % de claves duplicadas); reintentos con
marca de tiempo anterior a su predecesor; un bloque IP de casa asignado al azar que
ponía al 15 % de los pagos en el extranjero; un hueco de cobertura en la SCD2; un
muestreador que inventaba un bloqueo del motor de riesgo para pagos que el motor
había permitido; 98.769 pagos hechos con una tarjeta que aún no existía; duplicados
de ingesta que rompían dos invariantes que no los contemplaban; y tablas de dinero
que liquidaban dos veces el mismo pago por leer la tabla cruda; tráfico de pruebas
que entraba en el libro mayor; devoluciones y disputas fechadas después del último
día del almacén; menores comprando alcohol y apuestas; y una cohorte de tarjetas
caducadas declarada al 2,2 % que se entregaba al 0,98 %.

## La auditoría adversarial

El dataset pasó por seis revisores independientes con lentes distintas —pagos,
estadística, modelado, fraude, privacidad y las necesidades del propio proyecto—
consultando DuckDB de verdad, con cada hallazgo verificado después por un segundo
agente cuyo trabajo era refutarlo. **22 hallazgos, 20 confirmados como «arreglar
ya».** Los diez principales se comprobaron a mano antes de tocar nada: acertaron en
los diez.

Lo que cambió, y el número medido antes y después:

| | antes | después |
|---|---:|---:|
| `fx_rate` dentro de la libra esterlina (doble indexado) | 0,77 – 385,88 | 0,777 – 0,845 |
| Tipo aplicado frente al publicado | hasta 29 % de error | **desviación 0,0** |
| Ticket mediano en Hungría (moneda local ignorada) | 0,19 € | 59,63 € = 22.019 HUF |
| Tráfico transfronterizo | 86,7 % | **27,1 %** |
| Interchange sobre el tope del Reglamento (UE) 2015/751 | 55,5 % | **0 %** |
| Cola del motor de riesgo | 1 bloqueo en 6,6 M de filas | 0,3 % bloqueo · 3,0 % revisión |
| Duplicados de ingesta (declarados 0,35 %) | **0 %, nunca implementados** | 0,353 % |
| Dispositivos compartidos | 51,5 %, colisión aleatoria | 6,5 %, deliberado |
| Pagos anteriores al alta del cliente | 19,3 % | **0** |
| ¿`risk_score` predice el contracargo? | no, y dentro del MCC 7995 se invertía | sí: 0,39 % → 0,73 % → 1,46 % |
| Deriva de aprobación (tarjetas nunca reemitidas) | −7 puntos en dos años | plana, ±0,4 puntos |

Tres cambios fueron estructurales y no parches: **los puntos de venta tienen país
propio** (un comercio de un solo país deja sin dónde comprar al cliente de un
mercado pequeño, y ese era el origen del transfronterizo); **la asignación de pagos
se calcula antes de crear los clientes** (la fecha de alta tiene que preceder al
primer pago, y eso solo se sabe con la asignación hecha); y **la línea de pedido se
valora en la moneda de la transacción antes de sumar el importe**, no al revés.

## Estructura

```
datagen/
├── requirements.txt        pines aislados del proyecto
├── run.sh                  build de extremo a extremo de un perfil
├── generate.py             orquestador: dimensiones + bucle de 730 días
├── build_duckdb.py         catálogo, vistas derivadas y las 29 comprobaciones
├── build_derived.py        liquidación, pagos, devoluciones y disputas, en SQL
├── cierzo/
│   ├── config.py           perfiles, semilla, objetivos declarados
│   ├── shape.py            solucionadores de forma; nadie declara un exponente
│   ├── pools.py            MCC, BIN, ISO 8583, taxonomía, ASN, geografía
│   ├── refs.py             calendario y tablas de referencia
│   ├── dims_org.py         grafo societario, comercios SCD2, empleados
│   ├── dims_people.py      clientes con rasgos, tarjetas
│   ├── dims_digital.py     dispositivos, bloques IP, catálogo de producto
│   ├── facts.py            modelos de aprobación, riesgo y reintento
│   └── build_facts.py      construcción de un día
├── sql/01-explora.sql      cuaderno de exploración
└── docker/                 shell SQL de solo lectura
```
