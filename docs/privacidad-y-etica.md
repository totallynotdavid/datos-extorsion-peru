# Privacidad y ética

Varias fuentes del proyecto traen datos de personas: nombres, documentos,
domicilios, fechas de nacimiento. El sitio publica conteos. Este documento fija
las reglas que separan una cosa de la otra y dice qué prueba demuestra cada una.

Una regla sin prueba es un deseo. Cada regla de abajo tiene un test que falla si
alguien la rompe.

## Resumen

| Regla                                 | Dónde vive                                            | Qué prueba la sostiene                                                                            |
| ------------------------------------- | ----------------------------------------------------- | ------------------------------------------------------------------------------------------------- |
| Solo agregados                        | Cada comando de fuente con personas escribe conteos   | Los tests de `callao`, `piura` e `inpe` buscan valores centinela en los archivos y en el registro |
| Celdas pequeñas a `otros`             | `MIN_CELL = 5` en `callao.py`, `piura.py` e `inpe.py` | Tablas esperadas fila por fila con celdas de tamaño conocido                                      |
| Sin fragmentos de nombre ni iniciales | Esquema cerrado en `extract.py`                       | El texto del artículo no llega al Parquet                                                         |
| Lista blanca de `outFields`           | `FIELDS` en `inpe.py` y en `sidpol_live.py`           | La consulta enviada no contiene campos personales                                                 |

## Solo agregados

El proyecto nunca guarda una fila por persona. Un comando que lee una fuente con
personas la reduce a conteos antes de escribir nada:

- [`callao.py`](../src/dep/callao.py): víctimas por sexo, banda de edad y mes, y
  por grupo de ocupación y mes.
- [`piura.py`](../src/dep/piura.py): sentencias por año, provincia del juzgado y
  grupo de delito.
- [`inpe.py`](../src/dep/inpe.py): internos por delito, situación jurídica,
  sexo, banda de edad y distrito de domicilio.

Las columnas que identifican se descartan en el momento de leer. El extracto de
Piura se lee con tres columnas: `DELITO`, `PROVINCIA` y `FECHA_SENTENCIA`. El
documento, la fecha de nacimiento y el número de expediente no se cargan en
memoria. El extracto del Callao se lee con seis columnas (`COLUMNS` en
`callao.py`) y deja fuera la fecha de nacimiento, el documento y el texto libre
`DESCRIPCION`.

Si la lectura falla, el mensaje de error no cita la fila. La biblioteca de CSV
cita filas enteras en sus errores, así que `piura.py` captura el error y escribe
solo el tipo de la excepción.

Los datos descargados no se versionan. `data/` y `web/public/data/` están en
[`.gitignore`](../.gitignore).

### Cómo lo prueba un test

Los tests de personas usan **centinelas**: un valor inventado y reconocible que
se escribe en las columnas privadas del archivo de prueba, como `DOCSENTINEL`.
Después se corre el comando y se busca el valor en tres sitios: los bytes de los
Parquet, la salida estándar y el error estándar. Si aparece, el test falla.

- [`tests/test_callao.py`](../tests/test_callao.py): `SECRETS` incluye el
  documento, la ubicación, la descripción, la fecha de nacimiento y las
  coordenadas. El test también compara la lista exacta de columnas de la tabla.
- [`tests/test_piura.py`](../tests/test_piura.py): el mismo método, y un test
  aparte con un CSV roto que comprueba que el error no cita la fila.
- [`tests/test_inpe.py`](../tests/test_inpe.py): el nombre, el documento y el
  domicilio de los internos de prueba no aparecen en ninguna salida.

Los centinelas son texto inventado. Ningún test usa datos reales.

## Celdas pequeñas a `otros`

Un conteo de uno o dos puede señalar a una persona: si solo hay una mujer de 60
años víctima de extorsión en un mes, la tabla la describe. La regla: una celda
con menos de 5 casos no se publica con su distrito.

El proceso, en `callao.py`, `piura.py` e `inpe.py`:

1. Se cuenta cada celda (por ejemplo, año, provincia y grupo de delito).
2. Las celdas con 5 casos o más pasan tal cual.
3. Las celdas con menos de 5 se suman en una fila `otros` del mismo grupo.
4. Si esa fila `otros` sigue por debajo de 5, no se publica.
5. El registro de la corrida cuenta cuántos casos se plegaron y cuántos quedaron
   fuera.

Corrida real de `uv run dep piura`:

```text
sentencias_piura_anio_provincia_delito.parquet: 146 sentences in cells under 5 folded into otros, 53 of them in otros rows still under 5 and not published
```

La regla vale para las tablas con atributos de personas. Los conteos de SIDPOL
por distrito y mes los publica el Estado tal como están y no se pliegan.

### Cómo lo prueba un test

Los tests construyen un CSV con celdas de tamaños conocidos y comprueban la
tabla de salida fila por fila. En
[`tests/test_callao.py`](../tests/test_callao.py), seis casos de un grupo
quedan, dos casos de abril se pliegan y no se publican, y el registro dice
cuántos fueron. En [`tests/test_inpe.py`](../tests/test_inpe.py), los distritos
plegados no aparecen en la columna `distrito`.

## Sin fragmentos de nombre ni iniciales

Las noticias nombran a víctimas, sospechosos y policías. El proyecto no guarda
ninguno de esos nombres, ni completos, ni a medias, ni como iniciales. Un mapa
con "J. P." en un distrito sigue señalando a alguien.

[`extract.py`](../src/dep/extract.py) manda cada artículo a un modelo de
lenguaje y le pide un objeto con campos cerrados:

- `victim_type`, `method` y `confidence` solo aceptan valores de una lista fija.
  Un valor fuera de la lista detiene la corrida con la URL del artículo.
- El esquema de la herramienta no admite propiedades adicionales, y el código
  construye la fila solo con los campos conocidos.
- La instrucción al modelo dice: "Do not return article text, quotes, or the
  names of victims, suspects, police, or other people. The only name that may be
  returned is a criminal organisation in gang."
- El texto del artículo se corta a 8.000 caracteres antes de enviarlo.

La tabla `news_events.parquet` guarda la URL, la fecha, el lugar, el tipo de
víctima, el método, la organización criminal (`gang`) y la confianza. No guarda
el texto. La tabla de candidatos de `dep news` tampoco guarda texto ni
titulares; el HTML crudo queda en `data/raw/news/`, que está fuera del
repositorio.

### Cómo lo prueba un test

[`tests/test_extract.py`](../tests/test_extract.py) simula la API del modelo con
un servidor local. El artículo de prueba contiene
`UNIQUE_ARTICLE_TEXT_PERSON_NAME_AND_QUOTE`, y la respuesta simulada trae un
campo extra `article_text`. El test comprueba que ninguna de las dos cosas
aparece en los bytes del Parquet.

### El límite de esta regla

`district`, `province`, `department` y `gang` son texto libre. Ningún test
comprueba que el modelo no escriba un nombre de persona en ellos. La regla se
apoya ahí en la instrucción al modelo. Antes de publicar la tabla de noticias en
el mapa hay que revisar esos campos.

## Lista blanca de `outFields`

Una capa ArcGIS devuelve todas las columnas si se le deja. La lista blanca
invierte eso: la consulta nombra las columnas que quiere y el código se detiene
si el servidor responde con otra.

- [`inpe.py`](../src/dep/inpe.py) define `FIELDS` con siete campos de perfil.
  Cada consulta envía `outFields` con esa lista y `returnGeometry=false`. Si el
  servidor devuelve un campo fuera de la lista, `_check_fields` detiene la
  corrida y nombra el campo, nunca su valor.
- [`sidpol_live.py`](../src/dep/sidpol_live.py) define `FIELDS` con siete
  campos: distrito, fecha y hora, turno, modalidad, código y nombre de la
  comisaría, y el estado de la coordenada. No pide `direccion_hecho`,
  `cuadra_hecho`, `tipo_via_hecho`, `lat_hecho` ni `long_hecho`, y envía
  `returnGeometry=false`.

### Cómo lo prueban los tests

- [`tests/test_inpe.py`](../tests/test_inpe.py): un servidor falso registra cada
  consulta. El test comprueba que `outFields` no es `*`, que no contiene ninguno
  de los campos de `PERSONAL` (nombre, documento, domicilio, `X`, `Y`) y que
  `returnGeometry` es `false`. Otro test hace que el servidor ignore la lista y
  devuelva todo: la corrida debe fallar, nombrar el campo y no escribir nada.
- [`tests/test_sidpol_live.py`](../tests/test_sidpol_live.py): el servidor falso
  guarda cada consulta, y el test comprueba que `outFields` no contiene
  dirección, cuadra, tipo de vía ni coordenadas.

## Qué sale de la máquina

- **El texto de los artículos** viaja a la API del modelo cuando corre
  `dep extract`. Ese texto puede nombrar personas. Es lo único que el proyecto
  envía a un tercero, y requiere `ANTHROPIC_API_KEY`.
- **El mapa** no muestra puntos. `export-web` solo escribe los polígonos de los
  distritos y un conteo por distrito y mes.
- **Los tests** usan valores inventados, nunca filas de los archivos reales.

## Qué hacer al agregar una fuente

1. Lee los campos de la fuente. Marca los que identifican a una persona.
2. Pide solo las columnas que necesitas. Descarta el resto al leer.
3. Agrega la tabla de salida con una lista de columnas exacta.
4. Escribe un test con centinelas en las columnas privadas. Busca los centinelas
   en la salida, el registro y el error.
5. Aplica `MIN_CELL` a toda tabla que mezcle atributos de personas.
