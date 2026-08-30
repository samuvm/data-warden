# Contrato · atributos OpenTelemetry GenAI

> Afecta a **01**, **02**, **03** y **04**. Se copia a los cuatro. No se importa.
>
> Existe porque el mapa de conjunto declara OpenTelemetry como uno de los cinco contratos que
> unen el ecosistema, y ninguno de los documentos fija una versión ni una lista de atributos.
> Si el agente del 01 inventa nombres, la integración con el 02 no funciona y el argumento
> "estándar abierto, cualquier backend lo consume" se cae solo.
>
> **Versión del contrato: 1**

---

## 1. El hecho incómodo que hay que asumir primero

**Ningún atributo `gen_ai.*` es estable a agosto de 2026.** Todo el namespace está en estado
*Development*. Las convenciones se movieron a un repo propio
(`open-telemetry/semantic-conventions-genai`) y se eliminaron del repo principal en v1.43.0.

Rupturas que **ya han ocurrido** y que hay que absorber:

| Antes | Ahora | Desde |
|---|---|---|
| `gen_ai.system` | `gen_ai.provider.name` | v1.37.0 |
| `gen_ai.usage.prompt_tokens` | `gen_ai.usage.input_tokens` | v1.27.0 |
| `gen_ai.usage.completion_tokens` | `gen_ai.usage.output_tokens` | v1.27.0 |

Toda consulta analítica sobre trazas históricas debe hacer `coalesce()` de ambas generaciones.
No es opcional: si no, los paneles del 02 se vacían al cruzar una frontera de versión.

---

## 2. La decisión de gobierno: modelo interno propio, con traducción

**Cada proyecto mantiene su propio modelo de datos interno, desacoplado del esquema externo, y
una capa de traducción al emitir.**

Parece más trabajo y es lo contrario: es lo que permite que una ruptura de la spec sea un
cambio en un fichero en vez de una migración de esquema en ClickHouse y una reescritura de
todos los paneles. Es además un ADR de mucho valor — demuestra que entiendes la diferencia
entre adoptar un estándar y atarte a él mientras es inestable.

```
dominio → LlmCall (modelo interno, estable, tuyo)
              │
              └─→ traductor → span OTel con los atributos de la versión pineada
```

El modelo interno **nunca** usa nombres `gen_ai.*`. El traductor es el único módulo que los
conoce, y tiene sus propios tests de contrato.

---

## 3. Versión pineada

Se fija en `otel-semconv.lock`, en la raíz de cada proyecto que emita trazas:

```
repo:    open-telemetry/semantic-conventions-genai
version: v1.42.0
sha:     <sha del tag, verificado al fijar>
```

**Subir esa versión es un cambio consciente**, con entrada en el CHANGELOG de cada repo
afectado y revisión del traductor. Nunca es automático.

---

## 4. Atributos obligatorios

Lista literal. Un span que no los lleve todos es un span roto, y el test de contrato lo rechaza.

### Span de invocación de modelo

| Atributo | Tipo | Ejemplo | Obligatorio |
|---|---|---|---|
| `gen_ai.operation.name` | string | `chat`, `embeddings` | sí |
| `gen_ai.provider.name` | string | `ollama`, `aws.bedrock` | sí |
| `gen_ai.request.model` | string | `qwen3.5:9b-mlx` | sí |
| `gen_ai.response.model` | string | modelo que respondió de verdad | sí |
| `gen_ai.usage.input_tokens` | int | | sí |
| `gen_ai.usage.output_tokens` | int | | sí |
| `gen_ai.request.temperature` | double | | si aplica |
| `gen_ai.request.max_tokens` | int | | si aplica |
| `gen_ai.response.finish_reasons` | string[] | `["stop"]` | sí |
| `error.type` | string | | si hay error |
| `server.address` | string | | sí |

### Extensiones propias — namespace `app.*`, nunca `gen_ai.*`

Inventar nombres dentro de `gen_ai.*` es el error que hace inútil la integración con cualquier
herramienta de terceros. Lo propio va en su propio espacio:

| Atributo | Tipo | Para qué |
|---|---|---|
| `app.cost.eur` | double | Coste calculado con la tabla de precios vigente |
| `app.cost.pricing_version` | string | Fecha de vigencia de la tabla usada. Sin esto un informe histórico no es reproducible |
| `app.prompt.id` | string | Id del prompt de `prompts/` |
| `app.prompt.version` | int | Versión del prompt |
| `app.prompt.sha256` | string | Hash del fichero |
| `app.index.version` | string | `index_version` activa (01 y 04) |
| `app.stream.client_disconnected` | bool | **Clave en el 02**: esos tokens ya se han pagado y deben contabilizarse igual |
| `app.ttft_ms` | double | Latencia hasta el primer token |

### Métricas

| Métrica | Tipo | Unidad |
|---|---|---|
| `gen_ai.client.token.usage` | histogram | `{token}` |
| `gen_ai.client.operation.duration` | histogram | `s` |
| `app.spans.dropped` | counter | `{span}` |

`app.spans.dropped` es de primera clase y va en el panel. El documento del 02 declara a la vez
"pérdida de trazas 0 %" y "la cola se llena y descarta sin bloquear": son objetivos en tensión.
Se resuelven declarando el caudal sostenido bajo el cual se garantiza el 0 %, y exponiendo el
contador. Un descarte silencioso es peor que un descarte medido.

---

## 5. Instrumentación

- **OpenLLMetry** (Traceloop) o SDK OTel puro. Salida OTel estándar, conectable a cualquier
  backend.
- **No OpenInference** (Arize): usa convenciones propias, no `gen_ai.*`, y te saca del estándar
  que el 02 dice defender. Elegirlo contradice la tesis del proyecto.

## 6. La regla que no se negocia

**Un fallo de observabilidad nunca tumba la aplicación.** Si el almacén de trazas no responde,
el proxy sigue sirviendo peticiones y descarta spans contando cuántos. Una capa de
observabilidad que puede tirar la aplicación es peor que no tenerla.

Se prueba **tumbando el contenedor a mitad del test**, no razonándolo.

## 7. Sobre el proyecto 04

El mapa afirma que el 04 "emite trazas OTel directamente". **El documento del 04 no menciona
OpenTelemetry ni una sola vez**; su capa de observabilidad es MLflow, métricas y alertas.

Decisión: **el 04 emite OTel en su fase de observabilidad, que está marcada como ampliación.**
Coste real 6-10 h. Mientras no lo haga, el mapa lo declara como trabajo futuro y no como hecho.
No se finge una coherencia que no existe: eso se nota.
