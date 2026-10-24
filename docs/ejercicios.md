# Ejercicios

Cinco ejercicios, uno por sesión de clase. Cada uno trae un resultado que
compruebas tú con un comando. Todos usan datos inventados o consultas de conteo:
ninguno requiere cuenta ni clave.

Antes de empezar, instala el proyecto (ver el [README](../README.md)):

```sh
mise install
mise run install
```

| N.º | Tema                                    | Documento                                    | Tiempo |
| --- | --------------------------------------- | -------------------------------------------- | ------ |
| 1   | Sondear una capa ArcGIS                 | [Investigación](investigacion-de-fuentes.md) | 30 min |
| 2   | Correr un comando con datos propios     | [Canalización](pipeline.md)                  | 45 min |
| 3   | Romper una regla de privacidad          | [Privacidad](privacidad-y-etica.md)          | 30 min |
| 4   | Ampliar una tabla de pruebas del Worker | [Despliegue](despliegue-y-operacion.md)      | 30 min |
| 5   | Unir eventos duplicados                 | [Canalización](pipeline.md)                  | 45 min |

## Ejercicio 1: sondea una capa

**Objetivo.** Pedir poco a un servicio y saber qué tiene antes de bajarlo.

**Pasos.**

1. Cuenta las filas de la capa de distritos del IGN con `returnCountOnly=true`
   (comando en el
   [documento de investigación](investigacion-de-fuentes.md#capa-arcgis)).
2. Pide una sola fila: cambia `where` a `UBIGEO='150101'`, agrega
   `outFields=UBIGEO,NOMBDIST,NOMBPROV,NOMBDEP` y `returnGeometry=false`.
   Codifica las comillas y el signo igual en la URL (`%27` y `%3D`).
3. Abre la ficha de la capa con `...FeatureServer/5?f=json` y lee
   `maxRecordCount`.

**Comprueba.**

- El conteo es `{"count":1891}`.
- La fila trae `LIMA` en `NOMBDIST`, `NOMBPROV` y `NOMBDEP`.
- `maxRecordCount` es `1000`, y por eso `dep sidpol` pide los distritos en
  páginas de 1.000.

**Para pensar.** Si el conteo fuera 40.000 y `maxRecordCount` 2.000, ¿cuántas
páginas necesitarías y qué parámetro fija su orden?

## Ejercicio 2: corre `dep sidpol` con tus datos

**Objetivo.** Ver cada paso del comando con una entrada que cabe en la pantalla.

**Pasos.**

1. Crea una carpeta de trabajo fuera del repositorio:
   `mkdir -p "$TMPDIR/ej2" && cd "$TMPDIR/ej2"`.
2. Crea `denuncias.csv` con el mismo encabezado que usa
   [`tests/test_sidpol.py`](../tests/test_sidpol.py) (`HEADER`) y tres filas
   inventadas: una extorsión en `10202` (sin el cero inicial), un robo en
   `10202` y una extorsión en `150101`.
3. Crea `distritos.json` con la forma de `IGN_FIXTURE` en ese mismo test: un
   objeto con la lista `features`, y en cada elemento un objeto `attributes` con
   `UBIGEO`, `NOMBDIST`, `NOMBPROV` y `NOMBDEP`.
4. Desde la raíz del repositorio, corre:

   ```sh
   uv run dep sidpol --csv-file "$TMPDIR/ej2/denuncias.csv" \
     --ign-file "$TMPDIR/ej2/distritos.json" --out-dir "$TMPDIR/ej2/out"
   ```

5. Lee el resultado:

   ```sh
   uv run python -c "
   import duckdb
   print(duckdb.sql(\"SELECT * FROM read_parquet('$TMPDIR/ej2/out/extorsion_distrito_mes.parquet') ORDER BY ubigeo\").fetchall())"
   ```

**Comprueba.** Con las filas de ejemplo de `test_sidpol.py` (extorsión 5 y robo
11 en `10202`, extorsión 3 en `150101`), el resultado tiene dos filas:

```text
[('010202', 'ARAMANGO', 'BAGUA', 'AMAZONAS', 2018, 1, 5), ('150101', 'LIMA', 'LIMA', 'LIMA', 2018, 2, 3)]
```

El UBIGEO quedó con seis dígitos y el robo no aparece. Ahora cambia el CSV a una
sola fila de extorsión de 2019 y corre el comando otra vez: la tabla anterior
desaparece y queda solo la fila nueva. Eso es el reemplazo total.

## Ejercicio 3: rompe la lista blanca

**Objetivo.** Comprobar que una regla de privacidad tiene un test que falla.

Este ejercicio rompe código a propósito. Hazlo en una copia desechable del
repositorio, no en tu trabajo: así borrar la copia deshace todo y no puede
llevarse cambios tuyos.

**Pasos.**

1. Crea la copia con una rama nueva, desde la raíz del repositorio:

   ```sh
   git worktree add "$TMPDIR/ej3" -b ejercicio-3
   cd "$TMPDIR/ej3"
   ```

2. Abre `src/dep/inpe.py` de esa copia (`FIELDS`, en
   [`src/dep/inpe.py`](../src/dep/inpe.py)) y agrega `"NRO_DOCUMENTO_IDENTIDAD"`
   al final de la lista.
3. Corre los tests de la lista blanca:

   ```sh
   uv run pytest tests/test_inpe.py -k whitelist
   ```

4. Borra la copia y la rama. Vuelve primero a la raíz de tu repositorio:

   ```sh
   cd -
   git worktree remove --force "$TMPDIR/ej3"
   git branch -D ejercicio-3
   ```

**Comprueba.** Los tests fallan. Con la lista ampliada fallaron 4 casos
(`test_inpe_writes_both_tables_from_a_whitelist_of_fields`, tres variantes de
`robots`, y
`test_inpe_stops_when_the_server_returns_fields_outside_the_whitelist`) y
quedaron 25 sin ejecutar. Lee el mensaje del último: nombra los campos que el
servidor devolvió fuera de la lista, nunca un valor. Después de borrar la copia,
`git worktree list` ya no la muestra y tu rama sigue como estaba.

**Para pensar.** ¿Qué test lo habría detectado si el campo se agregara a
`sidpol_live.py` en lugar de `inpe.py`?

## Ejercicio 4: agrega un caso al test del Worker

**Objetivo.** Entender una petición `Range` y ampliar una tabla de casos.

**Pasos.**

1. Corre `mise run worker-test` y anota cuántos tests pasan.
2. En [`worker/test/serve.test.ts`](../worker/test/serve.test.ts) busca la tabla
   `cases` de las peticiones `Range`.
3. Agrega, antes del caso "start past the object", uno que pida un solo byte
   (`bytes=0-0`). Copia la forma de los casos vecinos: nombre, rango, estado
   esperado, cuerpo esperado y `contentRange`. Calcula el cuerpo y el total a
   partir del objeto de prueba que define el archivo.
4. Vuelve a correr `mise run worker-test`.

**Comprueba.** El estado esperado es `206` y `contentRange` es `bytes 0-0/20`
(el objeto de prueba mide 20 bytes). El número de tests sube de 14 a 15. Si
cambias el estado a `200`, el test debe fallar y nombrar tu caso. Deshaz o
conserva el cambio con `git diff`.

## Ejercicio 5: une eventos duplicados

**Objetivo.** Ver cómo `dep dedup` decide que dos notas cuentan el mismo hecho.

**Pasos.**

1. Guarda este script en `$TMPDIR/ej5/eventos.py`. Los datos son inventados.

   ```python
   import sys
   from datetime import date

   import duckdb

   salida = sys.argv[1]
   dia = date(2026, 10, 20)
   filas = [
       ("https://ejemplo.pe/a1", dia, dia, "Comas", "negocio", "Banda A", "medium"),
       ("https://ejemplo.pe/a2", dia, dia, "COMAS", "negocio", None, "high"),
       ("https://ejemplo.pe/a3", dia, dia, "Comas", "transporte", None, "low"),
       ("https://ejemplo.pe/a4", dia, None, "Comas", "negocio", None, "low"),
   ]
   con = duckdb.connect()
   con.execute(
       "CREATE TABLE events (url VARCHAR, source VARCHAR, published DATE, "
       "event_date DATE, district VARCHAR, province VARCHAR, department VARCHAR, "
       "victim_type VARCHAR, method VARCHAR, gang VARCHAR, confidence VARCHAR, "
       "model VARCHAR, extracted_at TIMESTAMPTZ, has_event BOOLEAN)"
   )
   for url, pub, ev, dist, victima, banda, conf in filas:
       con.execute(
           "INSERT INTO events VALUES (?, 'ejemplo', ?, ?, ?, 'Lima', 'Lima', ?, "
           "'cobro_de_cupo', ?, ?, 'claude-haiku-5-5', NULL, true)",
           [url, pub, ev, dist, victima, banda, conf],
       )
   con.execute("COPY events TO ? (FORMAT PARQUET)", [salida])
   ```

2. Genera la tabla de eventos y únelos:

   ```sh
   uv run python "$TMPDIR/ej5/eventos.py" "$TMPDIR/ej5/news_events.parquet"
   uv run dep dedup --events "$TMPDIR/ej5/news_events.parquet" \
     --out-dir "$TMPDIR/ej5/out"
   ```

3. Lee `news_events_dedup.parquet` en `$TMPDIR/ej5/out` con DuckDB, como en el
   ejercicio 2.

**Comprueba.** Salen tres eventos, no cuatro:

| Fecha      | Distrito | Víctima    | Banda   | Fuentes | Sin lugar |
| ---------- | -------- | ---------- | ------- | ------- | --------- |
| 2026-10-20 | COMAS    | negocio    | Banda A | 2       | no        |
| 2026-10-20 | Comas    | transporte | vacío   | 1       | no        |
| vacío      | Comas    | negocio    | vacío   | 1       | sí        |

Las notas `a1` y `a2` se unieron: misma fecha, distrito sin importar mayúsculas
y misma víctima, y una banda vacía no contradice a `Banda A`. La nota `a4` no
tiene fecha y queda aparte, marcada como no ubicada.

**Para pensar.** Cambia la banda de `a2` a `Banda B` y vuelve a correr. ¿Se
unen? Revisa la regla en [`src/dep/dedup.py`](../src/dep/dedup.py) antes de
contestar.
