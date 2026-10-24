# La canalización de datos

La canalización convierte cada fuente en una tabla pequeña y limpia. Vive en
[`src/dep/`](../src/dep/) y se opera con un solo comando, `dep`.

## La forma

Cada fuente tiene su propio subcomando. Cada subcomando hace lo mismo:

```text
fuente en internet -> descarga -> reducción con DuckDB -> un archivo Parquet
```

El archivo cae en `data/out/`. Casi todos los subcomandos parten de la fuente.
Cuatro leen la salida de otro: `export-web`, `extract`, `dedup` y la comparación
opcional de `sidpol-live` con la tabla de `sidpol`.

Las herramientas, con su porqué:

| Herramienta                                    | Qué hace aquí                                                                                                                       |
| ---------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| Python 3.13 y [uv](https://docs.astral.sh/uv/) | Corren el código y fijan las dependencias en `uv.lock`.                                                                             |
| [DuckDB](https://duckdb.org)                   | Lee el CSV, filtra, agrupa, une y escribe Parquet con SQL, en un proceso, sin servidor. Lee archivos grandes sin cargarlos enteros. |
| [Parquet](https://parquet.apache.org)          | Un archivo por tabla, con tipos y columnas. Se abre con DuckDB, pandas o cualquier lector.                                          |
| [httpx](https://www.python-httpx.org)          | Cliente HTTP.                                                                                                                       |
| python-calamine                                | Lee el libro de Excel de población.                                                                                                 |
| anthropic                                      | Cliente del modelo que extrae eventos de las noticias.                                                                              |

Todo corre en una máquina. No hay base de datos que mantener.

## Un comando por fuente

`uv run dep --help` lista los diez subcomandos. Esta tabla agrega lo que cada
uno lee y escribe.

| Comando       | Fuente                                            | Tabla en `data/out/`                                                              | En la actualización mensual |
| ------------- | ------------------------------------------------- | --------------------------------------------------------------------------------- | --------------------------- |
| `sidpol`      | CSV de denuncias del Mininter y distritos del IGN | `extorsion_distrito_mes.parquet`                                                  | Sí                          |
| `population`  | Libro de proyecciones del INEI                    | `poblacion_distrito_anio.parquet`                                                 | Sí                          |
| `export-web`  | Las dos tablas anteriores y los polígonos del IGN | `web/public/data/districts.geojson` y `extorsion.json`                            | Sí                          |
| `sidpol-live` | Capa ArcGIS del Mininter                          | `extorsion_live_distrito_mes_turno.parquet`, `extorsion_live_sector_mes.parquet`  | No                          |
| `callao`      | CSV del Gobierno Regional del Callao              | `victimas_sexo_edad_mes.parquet`, `victimas_ocupacion_mes.parquet`                | No                          |
| `piura`       | CSV del distrito judicial de Piura                | `sentencias_piura_anio_provincia_delito.parquet`                                  | No                          |
| `inpe`        | Capa ArcGIS del INPE                              | `inpe_internos_perfil_distrito.parquet`, `inpe_extorsion_perfil_distrito.parquet` | No                          |
| `news`        | Cuatro diarios                                    | `news_candidates.parquet`                                                         | No                          |
| `extract`     | `news_candidates.parquet` y el HTML crudo         | `news_events.parquet`                                                             | No                          |
| `dedup`       | `news_events.parquet`                             | `news_events_dedup.parquet`                                                       | No                          |

El mapa usa solo `sidpol` y `population`, a través de `export-web`. Las demás
tablas son análisis aparte y todavía no están en el sitio.

La actualización mensual está en el
[documento de despliegue](despliegue-y-operacion.md).

## El cliente HTTP educado

Toda petición pasa por `RobotsCheckedClient`, en
[`src/dep/http.py`](../src/dep/http.py). Hace tres cosas:

- **Lee `robots.txt` una vez por host** y se niega a pedir una ruta que el sitio
  prohíbe.
- **Espera `delay` segundos entre dos peticiones al mismo host.** Los comandos
  `sidpol-live`, `callao`, `piura` e `inpe` aceptan `--delay` (1,5 por defecto)
  y `news` espera 1,5 segundos. `sidpol`, `population` y `export-web` no esperan
  entre peticiones.
- **Se detiene cuando el sitio pide parar.** Un `429` en `robots.txt` lanza un
  error con la URL. Otros estados de error en `robots.txt` dependen del modo:
  por defecto un `401` o `403` prohíbe todo y los demás lo permiten todo; con
  `open_on_robots_error=True`, cualquier error significa "sin reglas".
  `sidpol-live` e `inpe` usan ese modo porque el host de ArcGIS responde `403` y
  `Invalid URL` a `/robots.txt`.

Cada comando se presenta con un `User-Agent` propio, que `news` completa con la
URL del repositorio para que un administrador pueda ubicar al responsable.

El caso del `429` en `robots.txt` lo cubren los casos `robots-429` de
[`tests/test_inpe.py`](../tests/test_inpe.py) y
[`tests/test_sidpol_live.py`](../tests/test_sidpol_live.py), y el test
`test_rate_limited_robots_txt_stops_the_source` de
[`tests/test_news.py`](../tests/test_news.py).

## Reemplazo total en cada corrida

Cada corrida descarga la fuente completa y reemplaza la tabla anterior. No hay
actualizaciones parciales ni migraciones. Dos motivos:

- **La fuente cambia lo ya publicado.** SIDPOL corrige sus cifras después de
  publicarlas (ver el [documento de la pregunta](pregunta-y-limites.md)). Sumar
  filas nuevas a las viejas dejaría números desactualizados.
- **Es más simple.** Una tabla es siempre el resultado de una corrida. Para
  cambiar el esquema se borra y se vuelve a correr.

`news` es la excepción medida: reemplaza solo el rango de meses y las fuentes
que se pidieron, y conserva el resto. `extract` agrega artículos nuevos y se
salta los ya procesados, salvo con `--force`, para no pagar dos veces a la API.

## Recorrido de `dep sidpol`

Es el comando más corto y alimenta el mapa.

### Ejecútalo

```sh
uv run dep sidpol --out-dir "$TMPDIR/sidpol"
```

```text
downloading https://www.datosabiertos.gob.pe/sites/default/files/DATASET_Denuncias_Policiales_Ene%202018%20a%20Julio%202026.csv
/home/.../sidpol/extorsion_distrito_mes.parquet
```

Tarda unos 9 segundos. Sin `--out-dir`, escribe en `data/out/`. Los parámetros
`--csv-file` y `--ign-file` leen archivos locales, y `--csv-url` e `--ign-url`
cambian las fuentes. Los tests y el [ejercicio 2](ejercicios.md) usan esos
parámetros.

### Paso a paso

El código está en [`src/dep/sidpol.py`](../src/dep/sidpol.py).

1. **`cli.py` recibe el subcomando.** [`src/dep/cli.py`](../src/dep/cli.py)
   registra los argumentos y llama a `sidpol.run`.
2. **`run` abre el cliente educado.** Crea el `httpx.Client` con el `User-Agent`
   y lo envuelve en `RobotsCheckedClient`.
3. **`resolve_csv_url` pregunta al catálogo.** Llama a `package_show` del CKAN
   con el identificador del conjunto y elige el recurso CSV cuyo nombre empieza
   por `DataSet Denuncias Policiales - Enero 2018 a`. El nombre completo cambia
   cada mes, el prefijo no.
4. **`download_csv` baja el archivo a un directorio temporal.** Lo escribe por
   trozos, sin cargarlo en memoria. El directorio se borra al terminar. El CSV
   de 27 MB nunca se guarda en el repositorio.
5. **`fetch_districts` pide el catálogo de distritos al IGN.** Pagina la capa
   ArcGIS de 1.000 en 1.000 filas, ordenada por `OBJECTID`, y pide solo
   `UBIGEO`, `NOMBDIST`, `NOMBPROV` y `NOMBDEP`, sin geometría.
6. **`build_table` reduce con SQL en DuckDB.**
   - Lee el CSV con todas las columnas como texto (`all_varchar`).
   - Rellena el UBIGEO a seis dígitos con `lpad`.
   - Se queda con las filas cuya modalidad es `Extorsión`.
   - Suma `cantidad` por UBIGEO, año y mes.
   - Une con los distritos del IGN para traer el nombre. Si un UBIGEO no está en
     el IGN, imprime un aviso y deja los nombres vacíos.
   - Escribe el resultado con `COPY ... (FORMAT PARQUET)`, ordenado.

### Mira el resultado

El archivo tiene siete columnas:

```text
ubigeo VARCHAR, distrito VARCHAR, provincia VARCHAR, departamento VARCHAR,
anio INTEGER, mes INTEGER, denuncias INTEGER
```

Para contar sin mostrar filas:

```sh
uv run python -c "
import duckdb
print(duckdb.sql(\"SELECT count(*), count(DISTINCT ubigeo), sum(denuncias) FROM read_parquet('$TMPDIR/sidpol/extorsion_distrito_mes.parquet')\").fetchall())"
```

El 2026-10-24 imprimió `[(19527, 1004, 113210)]`: 19.527 filas, 1.004 distritos
con al menos una denuncia y 113.210 denuncias de 2018-01 a 2026-07. Los otros
distritos del país (el IGN lista 1.891) no aparecen en el CSV con extorsión.

### De la tabla al mapa

`dep export-web` toma esta tabla, la población y los polígonos del IGN, y
escribe los dos archivos que el navegador lee. Su contrato se describe en el
[documento del mapa](mapa.md).

## La cadena de noticias

Tres comandos, en orden:

1. `dep news --from 2026-09 --to 2026-09` recorre los diarios y escribe
   `news_candidates.parquet`: una fila por nota con URL, medio, fecha, región y
   los términos que coincidieron. Un filtro de expresiones regulares (`TERMS`)
   descarta las notas que no hablan de extorsión. El HTML crudo queda en
   `data/raw/news/`. Un `429` o `403` detiene ese medio.
2. `dep extract` lee los candidatos y el HTML y pide a un modelo de lenguaje un
   objeto estructurado por evento. Necesita `ANTHROPIC_API_KEY`. Procesa 10
   artículos por corrida (`--limit`). El esquema cerrado está descrito en el
   [documento de privacidad](privacidad-y-etica.md).
3. `dep dedup` junta los eventos que cuentan el mismo hecho. Es SQL puro en
   DuckDB: dos eventos son el mismo si coinciden la fecha, el distrito sin
   acentos ni mayúsculas y el tipo de víctima, y la organización es igual o
   falta en uno. Un evento sin fecha o sin distrito no se une con nadie y queda
   marcado `unplaced`.

## Pruebas

Las pruebas viven en [`tests/`](../tests/). Las de cada fuente levantan un
servidor HTTP local que hace de fuente y corren `python -m dep <comando>` como
un proceso aparte, con el servidor como URL. Así prueban el comando completo,
con su cliente, su lectura y su escritura, sin tocar la fuente real.

`mise run check` corre el formato, los tipos (mypy en modo estricto) y los
tests:

```sh
mise run check
```
