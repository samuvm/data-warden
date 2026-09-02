---
id: nl2sql-retry
version: "1"
modelo_destino: qwen3.5:9b-mlx
escrito_el: 2026-09-02
---

El intento anterior **fue rechazado por el guard**. Este es el motivo, y no es una
opinión: es la regla que lo paró.

- **Consulta anterior:** `{previous_sql}`
- **Regla:** {rule_id} · {code}
- **Qué pasó:** {message}
- **Qué hacer:** {suggestion}

Corrige **eso concreto**. No reescribas la consulta entera desde cero si el resto era
correcto, y no intentes rodear la regla: está comprobada y volverá a pararte.
