# Investigación de fuentes

Antes de escribir código se busca el dato. Este documento enseña cómo se
encontraron las fuentes, cómo se sondeó cada una, con qué criterios se juzgó y
cuáles se descartaron.

## Dónde se busca

Tres lugares dieron fuentes:

1. **El catálogo de datos abiertos** (<https://www.datosabiertos.gob.pe>). Es un
   CKAN: cada conjunto tiene una página y una API JSON.
2. **Servicios ArcGIS REST.** Muchas instituciones publican sus mapas con
   ArcGIS. Detrás del mapa hay una capa que se consulta por HTTP, sin interfaz.
3. **Sitios de noticias.** Los diarios cuentan hechos que ninguna estadística
   oficial cuenta con fecha y lugar.

## Cómo se sondea

Sondear es pedir un poco, mirar la respuesta y decidir. Todavía no se descarga
nada grande. Cada sonda es un comando que se puede repetir.

### Catálogo CKAN

```sh
curl -A "datos-extorsion-peru/0.1" \
  "https://www.datosabiertos.gob.pe/api/3/action/package_show?id=denuncias-policiales-registradas-nivel-nacional-mininter"
```

La respuesta lista los recursos del conjunto (CSV mensuales, el acumulado, el
diccionario, los metadatos) y el campo `license_title`, que aquí trae la URL de
la licencia ODC-BY. El nombre del archivo cambia en cada publicación, por eso
[`src/dep/sidpol.py`](../src/dep/sidpol.py) busca el recurso por el prefijo de
su nombre y no guarda la URL. Esta API devuelve `result` como una lista de un
elemento, no como un objeto, y `resolve_csv_url` acepta las dos formas.

El host rechaza al agente por defecto de `curl` con HTTP 418 (un cortafuegos de
aplicaciones web) y responde 200 a un agente propio. Por eso todo cliente del
proyecto envía un `User-Agent` que empieza por `datos-extorsion-peru/0.1`.

### Capa ArcGIS

Una capa se consulta con la ruta `.../FeatureServer/<n>/query`. Los parámetros
que importan:

| Parámetro                           | Para qué sirve                                        |
| ----------------------------------- | ----------------------------------------------------- |
| `where`                             | Filtra filas. `1=1` pide todas.                       |
| `returnCountOnly=true`              | Devuelve solo cuántas filas hay. Es la primera sonda. |
| `outFields`                         | Elige las columnas. Es la lista blanca de privacidad. |
| `returnGeometry=false`              | No pide coordenadas.                                  |
| `resultOffset`, `resultRecordCount` | Paginan.                                              |
| `orderByFields`                     | Fija el orden, para que las páginas no se repitan.    |
| `f=json`                            | Pide JSON.                                            |

Cada servicio fija un `maxRecordCount`: el tamaño máximo de una página. Para
contar las filas de la capa de distritos del IGN:

```sh
curl "https://www.idep.gob.pe/geoportal/rest/services/DATOS_GEOESPACIALES/L%C3%8DMITES/FeatureServer/5/query?where=1%3D1&returnCountOnly=true&f=json"
```

```text
{"count":1891}
```

Ese 1.891 es el número de distritos que usa el mapa.

### robots.txt

`robots.txt` es el archivo con que un sitio dice qué rutas no quiere que recorra
un programa. Se lee antes de pedir nada más. Los sitios responden de tres
maneras:

- **200 con reglas.** Se obedecen. `datosabiertos.gob.pe` pide además
  `Crawl-delay: 10`.
- **404.** No hay reglas.
- **El host no tiene `robots.txt` y responde otra cosa.** El host de la capa del
  Ministerio del Interior (`services6.arcgis.com`) responde `403` con el texto
  `Invalid URL`. Eso no es una prohibición: el servidor no conoce esa ruta. El
  cliente tiene un modo para este caso (ver el
  [documento de la canalización](pipeline.md)).

Un `429` en `robots.txt` es otra cosa: el sitio pide que se pare. La
canalización se detiene y muestra la URL.

## Criterios

Cada fuente se juzgó con las mismas preguntas:

| Criterio         | Pregunta                                                                              |
| ---------------- | ------------------------------------------------------------------------------------- |
| Lugar y tiempo   | ¿Trae distrito y mes, o algo más fino?                                                |
| Licencia         | ¿Dice qué se puede hacer con el dato? ¿Exige atribución?                              |
| `robots.txt`     | ¿Permite la ruta? ¿Pide una espera?                                                   |
| Datos personales | ¿Hay nombres, documentos, direcciones, fechas de nacimiento o texto libre?            |
| Calidad          | ¿Las coordenadas y los códigos son confiables? ¿Cambia el dato después de publicarse? |

Un dato personal no descarta la fuente, pero obliga a leerla con una lista
blanca y a publicar solo agregados. Las reglas están en el
[documento de privacidad](privacidad-y-etica.md).

## Fuentes aceptadas

| Fuente                              | Qué aporta                                               | Licencia                                                    | Comando                        |
| ----------------------------------- | -------------------------------------------------------- | ----------------------------------------------------------- | ------------------------------ |
| SIDPOL, CSV de denuncias (Mininter) | Denuncias por distrito y mes, 2018-01 a 2026-07          | ODC-BY                                                      | `dep sidpol`                   |
| Capa ArcGIS de SIDPOL (Mininter)    | Denuncias por turno y sector policial, 2025-01 a 2026-07 | La capa no dice; los mismos registros salen como CSV ODC-BY | `dep sidpol-live`              |
| Gobierno Regional del Callao        | Sexo, edad y ocupación de la víctima, una provincia      | ODC-BY                                                      | `dep callao`                   |
| Distrito judicial de Piura          | Sentencias por año, provincia del juzgado y delito       | ODbL                                                        | `dep piura`                    |
| INPE, capa ArcGIS de internos       | Perfil de internos por distrito de domicilio             | Los ocho elementos del portal dicen "none"                  | `dep inpe`                     |
| INEI, proyecciones de población     | Población por distrito y año, para la tasa               | Referencial, se cita al INEI                                | `dep population`               |
| IGN, límites distritales            | Polígonos de los 1.891 distritos                         | Los términos no dan una licencia de reutilización explícita | `dep sidpol`, `dep export-web` |
| Diarios (cuatro)                    | Hechos con fecha y lugar en el texto                     | Cada sitio fija el suyo                                     | `dep news`                     |

## Tres hallazgos que cambiaron el diseño

### La capa del Ministerio del Interior: coordenadas reparadas

La capa `SIDPOL_DELITOS_TOTAL/FeatureServer/1` publica un punto por denuncia
(unos 2,2 millones) y tiene más campos que el CSV: hora, turno y sector
policial. Parecía la mejor fuente. Se contó la calidad de sus coordenadas con
una consulta de estadísticas, sin bajar un solo punto:

```sh
curl "https://services6.arcgis.com/lMIZrqiJkpM748BR/arcgis/rest/services/SIDPOL_DELITOS_TOTAL/FeatureServer/1/query?where=subtipo_hecho%3D%27EXTORSION%27&groupByFieldsForStatistics=ESTADO_COORD&outStatistics=%5B%7B%22statisticType%22%3A%22count%22%2C%22onStatisticField%22%3A%22OBJECTID%22%2C%22outStatisticFieldName%22%3A%22n%22%7D%5D&f=json"
```

El 2026-10-24 devolvió 39.915 filas de extorsión repartidas así:

| `ESTADO_COORD`       | Filas         | Qué pasó                                                                         |
| -------------------- | ------------- | -------------------------------------------------------------------------------- |
| `CERO_AMBAS`         | 24.023 (60 %) | La fuente traía `0,0`. El Ministerio puso el punto interior del sector policial. |
| `INVERTIDA`          | 13.264 (33 %) | Latitud y longitud estaban cambiadas. El Ministerio las reparó.                  |
| `UBIGEO_NO_COINCIDE` | 2.628 (7 %)   | El punto cae en el Perú, pero en otro distrito.                                  |

Ninguna fila de extorsión tiene una coordenada limpia. Un mapa de puntos
mostraría centros de sectores policiales como si fueran hechos. La decisión fue
agregar por distrito, turno y sector, y no pedir coordenadas (`returnGeometry`
en `false`, y los campos `lat_hecho` y `long_hecho` fuera de `outFields`). El
campo `direccion_hecho` es texto libre y puede nombrar un negocio, así que
tampoco se pide.

La capa es el servidor de una aplicación web, no un conjunto publicado. Los
mismos registros salen como CSV con licencia ODC-BY, y ese CSV es la base del
mapa.

### La capa del INPE: nombres expuestos

La capa `MAPEO_2022/FeatureServer/0` del INPE tiene 101.881 filas, una por
interno, y unas 2.700 son de extorsión. Pública y sin clave, expone el nombre
completo, el número de documento, el domicilio y un punto de cada interno. No
tiene campo de tiempo. Los elementos del portal dicen "none" como licencia.

Solo se hicieron consultas de agregados mientras se estudiaba. La canalización
pide siete campos de perfil (delito, situación jurídica, género, edad y los tres
niveles del distrito de domicilio) y publica conteos. Que una institución
exponga esos datos no autoriza a copiarlos.

### El archivo de Piura: un DNI en cada fila

El CSV del distrito judicial de Piura (2.450 sentencias a prisión efectiva, de
2020-01 a 2026-05) tiene documento de identidad, fecha de nacimiento, sexo y
número de expediente en cada fila. Solo 61 filas son de extorsión. No trae fecha
ni lugar del hecho, solo la sede del juzgado, así que registra condenas y no
incidentes. Se lee con tres columnas (delito, provincia del juzgado, fecha de
sentencia) y las demás nunca entran en memoria.

## Fuentes descartadas o aplazadas

| Fuente                    | Qué se midió                                  | Por qué no                                                                                                       |
| ------------------------- | --------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| MPFN, delitos denunciados | Conteos por distrito fiscal y año de registro | El distrito fiscal es la sede de la fiscalía, no el lugar del hecho. No se puede mapear.                         |
| INEI Datacrim             | Capa de puntos por denuncia                   | Todas las consultas `/query` agotaron el tiempo. Sin verificar.                                                  |
| Padrón SUNAT              | Zip de 394 MB                                 | No declara licencia. Un geocodificador acertó el distrito en 9 de 20 direcciones.                                |
| Mapa uMap hecho a mano    | 1.106 puntos con texto libre                  | Requiere permiso de la autora y mezcla fuentes. Otro uMap lista iniciales de víctimas, que son datos personales. |
| GDELT                     | Cuatro archivos de 15 minutos                 | Cero documentos con dominio `.pe`.                                                                               |
| Media Cloud               | Una consulta                                  | Pide inicio de sesión y clave.                                                                                   |
| Perú21                    | Páginas de la etiqueta                        | HTTP 429 en la página 7. Se paró.                                                                                |
| La República              | `robots.txt`                                  | Prohíbe `/*/tag/*` y `/*/pag/`. Se respeta.                                                                      |
| Canal N                   | Etiqueta de extorsión                         | Seis notas en dos años. Muy poco.                                                                                |

Cuatro diarios más (RPP, Infobae, El Comercio y Trome) se midieron y no se
implementaron. Lo medido: Infobae ofrece un RSS con texto completo que cubre
solo tres semanas. RPP no tiene profundidad medida. El Comercio y Trome
etiquetan notas que no son de extorsión.

## Los diarios que sí entraron

`dep news` recorre cuatro medios. Cada uno ofrece una forma distinta de listar
sus notas:

| Medio                | Cómo lista las notas                            |
| -------------------- | ----------------------------------------------- |
| Exitosa              | Mapas del sitio mensuales desde 2018-11         |
| Correo               | Páginas de la etiqueta de extorsión, 19 páginas |
| El Tiempo (Piura)    | API REST de WordPress con búsqueda              |
| Diario Sin Fronteras | API REST de WordPress con búsqueda              |

El código está en [`src/dep/news.py`](../src/dep/news.py). El
[documento de la canalización](pipeline.md) explica la cadena completa de
noticias.

## Qué queda abierto

- Los términos del IGN y de la SUNAT no dan una licencia de reutilización
  explícita. El sitio publica polígonos del IGN simplificados.
- El INPE expone datos personales. Avisarle de la exposición es una decisión
  pendiente.
- La API de Datacrim no se pudo sondear.
