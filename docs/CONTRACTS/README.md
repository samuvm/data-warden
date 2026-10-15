# Contratos congelados

Tres contratos que fijan formatos de intercambio con herramientas externas al código de
Data Warden. No se editan como un documento más: su sha256 está en `SHA256SUMS`, y
`scripts/check_contracts.py` (meta `G-CONTRACTS-FROZEN`) falla si un fichero no coincide con
su manifiesto o si aparece en el directorio un fichero que el manifiesto no declara.

| Contrato | Qué fija |
|---|---|
| `goals.schema.json` | La forma de `docs/GOALS.yaml`: umbrales estructurados, unidades explícitas (ratio frente a porcentaje), comando y artefacto de cada meta. Es lo que permite que un único `scripts/goals_check.py` lea todas las metas |
| `eval-report.schema.json` | El formato de los informes de `evals/reports/`. Una puerta de calidad o un panel externo solo puede comparar informes que comparten esquema; `scripts/check_eval_reports.py` los valida contra él |
| `otel-genai.md` | La versión pineada de las convenciones semánticas OpenTelemetry GenAI, los atributos obligatorios de cada span y las extensiones propias bajo `app.*` |

## Cambiar un contrato congelado

Es un cambio consciente, no un ajuste para que el código pase:

1. Se edita el contrato y, si cambia su forma, se sube su versión.
2. Se regenera el manifiesto: `uv run python scripts/check_contracts.py --write-manifest`.
3. Se anota en el CHANGELOG.

El diff de `SHA256SUMS` deja el cambio a la vista en la revisión.

## Congelados frente a propios

Los contratos propios del dominio (la política de columnas, el registro de auditoría, el
formato de rechazo, la igualdad de resultsets) viven en `docs/spec/`, se firman y se
compilan; ahí se escribe con normalidad. Confundir los dos directorios haría inejecutable
cualquier cambio cuyo entregable sea un esquema propio.
