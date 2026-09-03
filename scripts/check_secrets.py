#!/usr/bin/env python
"""`G-SECRETS` · cero hallazgos NUEVOS. Es un AXIOMA y no se negocia.

**ESTE CHECK NO PODÍA FALLAR, y se descubrió el 2026-09-03 probándolo.** Corría
`detect-secrets scan --baseline .secrets.baseline`, y ese comando **REESCRIBE la
línea base en el sitio**: cada hallazgo nuevo entraba solo en el fichero, y acto
seguido el script comparaba contra el fichero ya actualizado y anunciaba «0
hallazgos nuevos». Verificado plantando una clave RSA privada en un fichero
rastreado: salida `ok`, código 0, y la huella de la clave escrita en la línea base.

Todo `make done` verde desde la fase 0 certificaba «cero secretos» con un check
incapaz de dar rojo. Es el peor sitio donde puede estar un fallo: no en una defensa,
sino en la medida que dice que la defensa funciona.

**Cómo se arregla, y la regla vale para cualquier herramienta con línea base:**

1. **El escaneo NUNCA escribe sobre el fichero versionado.** Se escanea contra una
   COPIA en un temporal. Lo que está en git es la referencia y una referencia que la
   propia medida puede modificar no es una referencia.
2. **Se comparan HUELLAS, no cuentas.** Un hallazgo que aparece y otro que desaparece
   dejan el total igual, y el que aparece sería un secreto nuevo.
3. **Un hallazgo nuevo es ROJO.** La línea base NO se regenera para silenciarlo: se
   resuelve el hallazgo, o se añade a mano y se audita, que es un acto deliberado y
   se ve en el diff.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gatelib import ROOT, record, run

BASELINE = ROOT / ".secrets.baseline"


def fingerprints(payload: dict[str, object]) -> set[tuple[str, str, str]]:
    """`(fichero, tipo, huella)` de cada hallazgo. Sin el número de línea.

    La línea se deja fuera a propósito: mover un secreto de sitio no lo convierte en
    otro secreto, y si contara, cualquier edición del fichero daría un falso rojo que
    acabaría con alguien regenerando la línea base — que es justo lo que no puede
    pasar.
    """
    found: set[tuple[str, str, str]] = set()
    for filename, entries in payload.get("results", {}).items():  # type: ignore[union-attr]
        for entry in entries:
            found.add((filename, entry["type"], entry["hashed_secret"]))
    return found


def main() -> int:
    if not BASELINE.exists():
        print("check_secrets: FALLO · no existe .secrets.baseline")
        return 1

    known = json.loads(BASELINE.read_text(encoding="utf-8"))

    # SE ESCANEA CONTRA UNA COPIA. `detect-secrets scan --baseline` reescribe el
    # fichero que se le pasa, así que darle el versionado convertía la medida en su
    # propia referencia. Con la copia, lo que está en git no se puede tocar ni por
    # accidente ni a propósito.
    with tempfile.TemporaryDirectory() as tmp:
        copy = pathlib.Path(tmp) / "baseline.json"
        shutil.copy(BASELINE, copy)
        code, out = run(
            [
                sys.executable,
                "-m",
                "detect_secrets",
                "scan",
                "--baseline",
                str(copy),
                # LA PROPIA LÍNEA BASE SE EXCLUYE, y hace falta decirlo porque antes
                # no hacía falta: `scan --baseline fichero` excluye ese fichero solo.
                # Al escanear contra una COPIA en un temporal, el `.secrets.baseline`
                # versionado pasa a ser un fichero más, y sus veinte `hashed_secret`
                # se detectan como veinte secretos. Serían veinte falsos positivos
                # nuevos en cada pasada, o sea, la presión exacta que acaba con
                # alguien regenerando la línea base.
                "--exclude-files",
                r"^\.secrets\.baseline$",
            ]
        )
        if code:
            print(f"check_secrets: FALLO · el escaneo no terminó:\n{out[-2000:]}")
            return 1
        scanned = json.loads(copy.read_text(encoding="utf-8"))

    before, after = fingerprints(known), fingerprints(scanned)
    new = sorted(after - before)
    gone = sorted(before - after)

    record(
        "secrets.json",
        "G-SECRETS",
        value=len(new),
        detail={
            "baseline_findings": len(before),
            "scanned_findings": len(after),
            # NI UNA HUELLA EN EL INFORME, ni siquiera un prefijo. Escribirlas
            # convertía este artefacto en portador de secretos: la pasada siguiente
            # detectaba sus propios `hashed_secret` como hallazgos nuevos y el check
            # se envenenaba solo. Con doce caracteres seguía pasando —el detector
            # marca cualquier hexadecimal de esa longitud—, así que el artefacto
            # guarda QUÉ fichero y de QUÉ tipo, que es lo que hace falta para
            # auditarlo, y la huella se vuelve a obtener corriendo el check.
            "new": [{"file": f, "type": t} for f, t, _ in new],
            "no_longer_present": [{"file": f, "type": t} for f, t, _ in gone],
        },
        command="python scripts/check_secrets.py",
    )

    if new:
        print(
            f"check_secrets: FALLO · {len(new)} hallazgo(s) NUEVOS · G-SECRETS es un AXIOMA\n"
        )
        for filename, kind, digest in new:
            print(f"  · {filename} · {kind} · {digest[:12]}…")
        print(
            "\n  La línea base NO se regenera para silenciar esto. O se resuelve el\n"
            "  hallazgo, o se añade a mano tras auditarlo, que es un acto deliberado\n"
            "  y se ve en el diff."
        )
        return 1

    detalle = f" · {len(gone)} de la línea base ya no aparecen" if gone else ""
    print(
        f"check_secrets: ok · 0 hallazgos nuevos · {len(before)} en la línea base, "
        f"todos auditados{detalle}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
