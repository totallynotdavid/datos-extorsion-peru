# El mapa

El mapa es una página estática: HTML, TypeScript y dos archivos de datos. No hay
servidor de aplicación. El navegador descarga los datos, calcula la tasa y pinta
los distritos.

El código está en [`web/`](../web/). El punto de entrada es
[`web/src/main.ts`](../web/src/main.ts).

## Verlo en tu máquina

```sh
mise run web-install
uv run dep export-web
mise run web-tiles
mise run web-dev
```

`export-web` necesita las tablas de `sidpol` y `population` en `data/out/`.
`web-tiles` baja el mapa base (212 MB) a `web/public/peru.pmtiles`. `web-dev`
abre Vite en <http://localhost:5173/>.

Ninguno de esos archivos se versiona: `web/public/data/` y
`web/public/peru.pmtiles` están en [`.gitignore`](../.gitignore).

Sin `export-web`, la página usa unos datos de ejemplo de cinco distritos en
[`web/fixtures/`](../web/fixtures/). `fetchFirst` en `main.ts` prueba primero
`data/…` y, si la respuesta no es válida, `fixtures/…`. Eso permite abrir la
página y correr sus tests sin descargar nada.

## Las piezas

| Pieza                                                       | Para qué                                       | Dónde                     |
| ----------------------------------------------------------- | ---------------------------------------------- | ------------------------- |
| [MapLibre GL JS](https://maplibre.org/maplibre-gl-js/docs/) | Dibuja el mapa en el navegador con WebGL       | `web/src/main.ts`         |
| Un archivo PMTiles                                          | El mapa base: tierra, agua, calles y fronteras | `web/public/peru.pmtiles` |
| `districts.geojson`                                         | Los polígonos de los 1.891 distritos           | `web/public/data/`        |
| `extorsion.json`                                            | Las denuncias por mes y la población           | `web/public/data/`        |
| Vite                                                        | Compila TypeScript y sirve en desarrollo       | `web/vite.config.ts`      |

El proyecto usa MapLibre directamente, sin React. Se descartó mapcn porque solo
funciona con React y su mapa base de ejemplo depende de los términos de CARTO.
Se descartó deck.gl porque rinde su ventaja con más de un millón de puntos y
aquí hay 1.891 polígonos.

### PMTiles: un mapa base en un solo archivo

Un mapa base se compone de teselas: recortes cuadrados del mapa a cada nivel de
zoom. Lo habitual es pedirlas a un servidor de teselas, con su cuenta y sus
límites. [PMTiles](https://docs.protomaps.com/pmtiles/) guarda todas las teselas
en un solo archivo. El navegador no descarga el archivo entero: pide con `Range`
solo los bytes de la tesela que necesita.

El archivo sale del mapa mundial de Protomaps, recortado a Perú:

```sh
mise run web-tiles
```

La tarea `web-tiles` de [`mise.toml`](../mise.toml) corre `pmtiles extract`
sobre una versión fija del mapa mundial, con el rectángulo de Perú y hasta el
zoom 13. En la corrida del 2026-10-24 bajó 222 MB en 23 segundos y dejó un
archivo de 212 MB. Las teselas son datos de OpenStreetMap, con licencia ODbL; la
página exige atribuir a "OpenStreetMap" y no pide clave.

`main.ts` registra el protocolo `pmtiles://` en MapLibre y define el estilo en
el propio código: fondo, tierra, agua, calles y fronteras, sin texto. Sin texto
no hay tipografías que alojar. Los distritos se dibujan encima como una capa
GeoJSON.

## El contrato de datos

El navegador y la canalización se hablan por dos archivos. `dep export-web`
([`src/dep/export_web.py`](../src/dep/export_web.py)) los escribe.

**`districts.geojson`** es una colección de polígonos. Cada uno tiene las
propiedades `ubigeo`, `nombre`, `provincia` y `departamento`. Los polígonos se
simplifican hasta que el archivo comprimido con gzip pesa 3 MB o menos
(`GZIP_LIMIT`). El archivo de la corrida pesó 7,5 MB sin comprimir y 2,0 MB con
gzip. El IGN deja vacía la geometría de dos distritos (`040101` y `040126`) y
[`src/dep/ign_geometry_patch.geojson`](../src/dep/ign_geometry_patch.geojson)
las completa con los límites del mismo IGN publicados por OCHA.

**`extorsion.json`** tiene tres claves:

| Clave        | Contenido                                                              |
| ------------ | ---------------------------------------------------------------------- |
| `months`     | La lista de meses, de `2018-01` a `2026-07`: 103 entradas              |
| `rows`       | Para cada UBIGEO, la lista de denuncias en el mismo orden que `months` |
| `population` | Para cada UBIGEO, la población por año                                 |

`rows` solo tiene los 1.004 distritos con alguna denuncia. El mapa trata un
distrito sin fila como cero. `population` cubre los 1.891 distritos.

`export-web` falla si un distrito del IGN falta en la salida. Si el INEI trae un
distrito que el IGN no tiene (`160405`), solo avisa y lo omite.

## Qué calcula el navegador

[`web/src/main.ts`](../web/src/main.ts) hace todo el cálculo:

- **Periodo.** Dos deslizadores eligen el mes inicial y el final. Por defecto,
  los últimos 12 meses.
- **Tasa.** Denuncias del periodo divididas por los años-persona del periodo,
  por 100.000. Los años-persona son la suma de la población de cada año dividida
  entre 12, una vez por cada mes del periodo en ese año.
- **Distritos pequeños.** Un distrito con menos de 1.000 habitantes (o sin
  población) se pinta de gris en la vista de tasa.
- **Leyenda.** Una clase para el cero y cuatro clases de cuantiles de los
  valores mayores que cero, redondeados a enteros. La leyenda se recalcula cada
  vez que cambia el periodo.
- **Ventana emergente.** Al hacer clic en un distrito muestra su nombre, las
  denuncias, la tasa, la población, el periodo y una línea con la serie mensual.
- **Línea de fuente.** Cita SIDPOL, datosabiertos.gob.pe, la población del INEI,
  la licencia ODC-By y el último mes de los datos.

## Por qué las teselas viven en R2

Un sitio estático normal sirve todo desde sus archivos. El mapa base no cabe
ahí:

- Un Worker de Cloudflare con archivos estáticos acepta como máximo 25 MiB por
  archivo, y `peru.pmtiles` pesa 212 MB.
- PMTiles necesita peticiones `Range`: el navegador pide fragmentos del archivo,
  no el archivo.
- El archivo no se versiona: es un binario de 212 MB que se regenera con
  `web-tiles`.

La solución es guardar el archivo en R2, el almacén de objetos de Cloudflare, y
que un Worker lo sirva con `Range`. R2 no cobra la salida de datos y su nivel
gratuito incluye 10 GB de almacenamiento. El archivo está fuera de la carpeta de
archivos estáticos: [`web/public/.assetsignore`](../web/public/.assetsignore) lo
excluye al subir.

El Worker también sirve `districts.geojson` y `extorsion.json` desde R2. Así la
actualización mensual cambia los datos sin volver a desplegar el sitio. El
[documento de despliegue](despliegue-y-operacion.md) explica el Worker, el
bucket y los comandos.

## Pruebas

[`web/tests/map.spec.ts`](../web/tests/map.spec.ts) usa Playwright con Chrome y
los datos de ejemplo. Compila el sitio, lo abre en un navegador real y comprueba
cuatro cosas: la vista por defecto muestra la tasa y el deslizador la recalcula,
la leyenda separa el cero, el conmutador pasa a denuncias y un clic en un
distrito muestra conteo, tasa y población.

```sh
mise run web-test
```

La primera vez hay que instalar Chrome para Playwright:
`npx playwright install chrome`. La corrida terminó con `4 passed`.
