# Contrato · atributos OpenTelemetry GenAI

> Fija una versión de las convenciones semánticas GenAI y la lista de atributos que emite
> Data Warden. Sin versión fijada ni lista cerrada, cualquier nombre inventado rompe en
> silencio a quien consuma las trazas, y el argumento "estándar abierto, cualquier backend
> lo consume" deja de ser cierto.
>
> **Versión del contrato: 1**

---

## 1. El punto de partida: nada de `gen_ai.*` es estable

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
No es opcional: si no, los paneles se vacían al cruzar una frontera de versión.

---

## 2. Modelo interno propio, con traducción

**El proyecto mantiene su propio modelo de datos interno, desacoplado del esquema externo, y
una capa de traducción al emitir.**

Parece más trabajo y es lo contrario: es lo que permite que una ruptura de la spec sea un
cambio en un fichero en vez de una migración del almacén de trazas y una reescritura de los
paneles. Es la diferencia entre adoptar un estándar y atarse a él mientras es inestable.

```
dominio → LlmCall (modelo interno, estable, propio)
              │
              └─→ traductor → span OTel con los atributos de la versión pineada
```

El modelo interno **nunca** usa nombres `gen_ai.*`. El traductor es el único módulo que los
conoce, y tiene sus propios tests de contrato.

---

## 3. Versión pineada

Se fija en `otel-semconv.lock`, en la raíz del repositorio:

```
repo:    open-telemetry/semantic-conventions-genai
version: v1.42.0
sha:     <sha del tag, verificado al fijar>
```

**Subir esa versión es un cambio consciente**, con entrada en el CHANGELOG y revisión del
traductor. Nunca es automático.

---

## 4. Atributos obligatorios

Lista literal. Un span que no los lleve todos es un span roto, y el test de contrato lo rechaza.

### Span de invocación de modelo

| Atributo | Tipo | Ejemplo | Obligatorio |
|---|---|---|---|
| `gen_ai.operation.name` | string | `chat`, `embeddings` | sí |
| `gen_ai.provider.name` | string | `ollama`, `aws.bedrock` | sí |
| `gen_ai.request.model` | string | `gemma4:26b-mlx` | sí |
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
| `app.stream.client_disconnected` | bool | Los tokens de una respuesta abandonada por el cliente ya se han pagado y se contabilizan igual |
| `app.ttft_ms` | double | Latencia hasta el primer token |

### Métricas

| Métrica | Tipo | Unidad |
|---|---|---|
| `gen_ai.client.token.usage` | histogram | `{token}` |
| `gen_ai.client.operation.duration` | histogram | `s` |
| `app.spans.dropped` | counter | `{span}` |

`app.spans.dropped` es de primera clase. "Cero trazas perdidas" y "la cola se llena y descarta
sin bloquear" son objetivos en tensión; se resuelven declarando el caudal sostenido bajo el cual
se garantiza el 0 % y exponiendo el contador. Un descarte silencioso es peor que un descarte
medido.

---

## 5. Instrumentación

- **OpenLLMetry** (Traceloop) o SDK OTel puro. Salida OTel estándar, conectable a cualquier
  backend.
- **No OpenInference** (Arize): usa convenciones propias, no `gen_ai.*`, y saca las trazas del
  estándar que este contrato fija.

## 6. La regla que no se negocia

**Un fallo de observabilidad nunca tumba la aplicación.** Si el almacén de trazas no responde,
la aplicación sigue sirviendo peticiones y descarta spans contando cuántos. Una capa de
observabilidad que puede tirar la aplicación es peor que no tenerla.

Se prueba **rompiendo el sumidero a mitad del test**, no razonándolo.
