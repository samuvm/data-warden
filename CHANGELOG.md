# Changelog

Formato según [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/).
Versionado semántico a partir de la primera fase cerrada.

**Cómo se escribe aquí.** Una entrada **por fase cerrada**, y solo cuando `make done MILESTONE=N` ha salido en
verde: el punto 11 de la Definition of Done comprueba que la fase tiene su entrada. La entrada incluye
**los números medidos con su comando**, no adjetivos: cada meta activada en esa fase aparece con su id, su
valor y su artefacto. Un cambio que no cierra fase no se anota aquí, se anota en `docs/JOURNAL.md`.
Es append-only: las entradas cerradas no se reescriben.

Plantilla:

```markdown
## [0.N.0] · fase N · Nombre de la fase — AAAA-MM-DD

### Añadido
- …

### Cambiado
- …

### Números medidos
| Meta | Valor | Umbral | Comando | Artefacto |
|---|---|---|---|---|
| G-XXXX | | | | |

### Decisiones
- ADR-NNN · …

### Pendiente declarado
- …
```

---

## [No publicado]

Proyecto sin arrancar. Ninguna fase cerrada todavía. Ver `.claude/state/STATE.md` y `docs/PLAN.md`.
