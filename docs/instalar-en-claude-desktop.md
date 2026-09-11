# Instalar Data Warden en Claude Desktop

Data Warden habla **MCP 2026-07-28 por stdio**. El cliente lanza el proceso cuando lo
necesita y lo cierra al salir: no hay demonio, no hay puerto y no hay nada que arrancar a
mano. DuckDB va embebido, así que tampoco hay servidor de base de datos, y **no hace falta
ningún modelo local**: las cuatro herramientas reciben SQL, y quien lo escribe es el modelo
del cliente.

Son unos diez minutos con el perfil `dev`.

## 1 · Generar los datos, una vez

```bash
cd data-warden
uv sync
make dataset PROFILE=dev      # ~15 s · 3,1 M filas · datagen/out/cierzo-dev.duckdb
```

El servidor arranca por defecto contra el perfil **`full`** (`make dataset PROFILE=full`,
7,46 GB), que es el de los números publicados. Para probar con `dev` hay que decírselo en el
paso 4.

> El `.duckdb` pesa unos cientos de KB porque son **vistas sobre los Parquet, con rutas
> absolutas**. Los ficheros de `datagen/out/` tienen que seguir ahí, y el almacén solo
> funciona en la máquina donde se generó. En otra se vuelve a generar y sale idéntico byte
> a byte.

## 2 · Elegir la pimienta y el rol

```bash
openssl rand -hex 24       # 48 caracteres; el mínimo son 32. Guárdala: es tu configuración
```

La pimienta es la clave del hash con el que se enmascaran las columnas de identificación.
**No tiene valor por defecto y no lo va a tener**: una pimienta por defecto es una pimienta
pública, y el algoritmo está publicado en `docs/spec/policy.yaml`. Si cambia, los valores
enmascarados cambian con ella.

El rol sale de `WARDEN_ROLE` y **lo fija quien instala el servidor, nunca quien pregunta**.
Vale `analyst`, `ops`, `finance` o `admin`; si falta o no se reconoce, se usa `analyst`, que
es el más restringido y el mejor para ver al sistema haciendo su trabajo.

## 3 · Comprobarlo antes de instalar

```bash
make mcp-live
```

Levanta el servidor exactamente como lo hará Claude Desktop —con `uv run --directory` y
desde otro directorio— y le habla JSON-RPC de verdad. Son 14 comprobaciones, entre ellas que
un cliente que no sabe responder a una petición de confirmación no se queda sin respuesta, y
que el presupuesto duro sigue rechazando contra ese mismo cliente. Todas en verde antes de
tocar ninguna configuración.

## 4 · Añadirlo a la configuración

El fichero es `~/Library/Application Support/Claude/claude_desktop_config.json`.

**Casi seguro que ya existe y tiene cosas dentro.** No lo sustituyas: `mcpServers` es una
clave más del nivel superior, hermana de las que ya haya. Si borras el resto, pierdes tu
configuración de Claude Desktop.

```json
  "mcpServers": {
    "data-warden": {
      "command": "<la salida de `which uv`>",
      "args": [
        "run", "--directory", "<ruta absoluta al repositorio>",
        "warden", "mcp", "serve",
        "--database", "datagen/out/cierzo-dev.duckdb"
      ],
      "env": {
        "DATAWARDEN_MASK_PEPPER": "<la pimienta del paso 2>",
        "WARDEN_ROLE": "analyst"
      }
    }
  },
```

Las dos últimas líneas de `args` apuntan a `dev`. Si has generado `full`, quítalas.

- **Si el fichero ya existe**, pega el bloque justo después de la primera `{`, con la coma
  del final.
- **Si no existe**, créalo con el bloque entre llaves y sin esa coma.

Tres cosas que fallan si se hacen «como siempre»:

| | Por qué |
|---|---|
| **`command` es la ruta ABSOLUTA de `uv`** | Claude Desktop no hereda el `PATH` de tu terminal, así que `"uv"` a secas no se encuentra |
| **La ruta del repositorio va en `args` con `--directory`, no en `cwd`** | Claude Desktop ignora `cwd`: lanza el proceso desde otro sitio y muere con `error: Failed to spawn: warden` |
| **La pimienta va en `env`** | Claude Desktop tampoco hereda tus variables de entorno |

Antes de reiniciar, dos comprobaciones que ahorran un reinicio cada una:

```bash
# el JSON sigue siendo válido (con una coma de más, Claude Desktop ignora el fichero EN SILENCIO)
python3 -m json.tool ~/Library/Application\ Support/Claude/claude_desktop_config.json > /dev/null \
  && echo "JSON válido"

# la pimienta tiene longitud suficiente (con el texto de ejemplo, el servidor no arranca)
python3 -c "import json,os; d=json.load(open(os.path.expanduser(
  '~/Library/Application Support/Claude/claude_desktop_config.json')));
  p=d['mcpServers']['data-warden']['env']['DATAWARDEN_MASK_PEPPER'];
  print(len(p), 'caracteres ·', 'OK' if len(p)>=32 else 'DEMASIADO CORTA')"
```

Reinicia Claude Desktop del todo (⌘Q, no cerrar la ventana).

## 5 · Probarlo

Primero algo normal:

> *¿cuántos clientes hay por país?*

Después, algo que el rol `analyst` no debería poder obtener:

> *dame los clientes nacidos antes de 1990*

La segunda tiene que salir **rechazada**, con la regla que saltó y qué usar en su lugar:

```
R008 · column dim_customer.birth_date is masked for role analyst: it may only appear
       as a direct projection, and it appears in a WHERE predicate
       → use dim_customer.age_band instead
```

Un buen cliente lee el rechazo y reformula con `age_band` sin intentar rodear la regla. Eso
es el producto: no que acierte siempre, sino que cuando no puede, lo diga y diga por qué.

## Si algo no arranca

El servidor escribe el motivo en `stderr` y Claude Desktop lo guarda en
`~/Library/Logs/Claude/mcp-server-data-warden.log`.

| Lo que dice | Qué pasa |
|---|---|
| `no existe datagen/out/cierzo-full.duckdb` | No has generado `full`: genéralo, o apunta a `dev` con `--database` (paso 4) |
| `no existe datagen/out/cierzo-dev.duckdb` | Falta el paso 1 |
| `la pimienta tiene N caracteres y el mínimo es 32` | La pimienta del `env` del paso 4 |
| El servidor no aparece en Claude | Casi siempre `command` sin ruta absoluta, o la ruta del repositorio en `cwd` |

`uv run warden mcp serve` en una terminal **se queda callado y sin devolver el prompt**. Es lo
correcto: está escuchando JSON-RPC por la entrada estándar. `Ctrl-C` para salir.
