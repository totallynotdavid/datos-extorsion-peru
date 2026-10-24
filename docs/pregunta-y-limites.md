# La pregunta y sus límites

Un proyecto de datos empieza con una pregunta que los datos puedan contestar.
Este documento fija la pregunta, lo que las cifras dicen y lo que no dicen.

## La pregunta

¿En qué distritos del Perú se denuncian más extorsiones, y cómo cambia eso mes a
mes?

La pregunta pide dos cosas: un lugar (el distrito) y un tiempo (el mes). Cada
fuente se juzgó con esas dos medidas. Una fuente sin lugar o sin tiempo no sirve
para el mapa, aunque tenga buenos datos.

## Qué mide la fuente principal

La fuente principal es SIDPOL, el sistema de denuncias de la Policía Nacional.
El Ministerio del Interior publica un CSV con una fila por distrito, mes y
modalidad. Cada fila trae un conteo de denuncias. El proyecto toma las filas con
modalidad `Extorsión`.

`uv run dep sidpol` lo descarga. En la corrida del 2026-10-24 produjo 19.527
filas, de 2018-01 a 2026-07, con 113.210 denuncias en total. El
[documento de la canalización](pipeline.md) recorre el comando completo.

Un distrito se identifica con su UBIGEO, el código de seis dígitos del INEI.
`150101` es Lima, distrito de Lima. El CSV lo trae como número, y el cero
inicial se pierde en cerca del 38 % de las filas (`10202` en lugar de `010202`).
La canalización lo rellena a seis dígitos.

## Denuncias, no delitos

El mapa muestra denuncias. Una denuncia existe solo si alguien la presentó y la
policía la registró. Eso separa tres cosas que el lector tiende a mezclar:

| Cosa      | Qué es                              | ¿La tenemos?                                   |
| --------- | ----------------------------------- | ---------------------------------------------- |
| Delito    | Lo que ocurrió                      | No                                             |
| Denuncia  | Lo que alguien reportó a la policía | Sí, por distrito y mes                         |
| Sentencia | Lo que un juez condenó              | Solo un extracto de Piura, sin lugar del hecho |

Por eso el sitio dice "denuncias" en el título de la página, en el botón de
medida, en el total y en la leyenda. Cuatro consecuencias:

- **Una subida no prueba más extorsión.** Puede ser que más víctimas denuncien,
  que la policía registre mejor o que cambie la forma de contar. La serie anual
  pasa de 4.539 denuncias en 2021 a 16.066 en 2022. Los datos no explican por
  qué, y este proyecto no lo afirma.
- **Un distrito sin denuncias no está libre de extorsión.** El conteo cubre solo
  lo que alguien denunció.
- **Los números cambian después de publicarse.** El informe mensual en PDF de
  enero de 2026, con corte al 31 de enero, decía 1.193 extorsiones. El CSV
  publicado en agosto dice 1.642 para el mismo mes. El propio SIDPOL avisa que
  la información "está sujeta a variación por ser datos dinámicos". Por eso cada
  corrida descarga todo el archivo y reemplaza las filas viejas.
- **El lugar es el del hecho.** El CSV usa el distrito donde ocurrió el hecho
  (`UBIGEO_HECHO`), no donde vive la víctima ni donde se presentó la denuncia.

## Por qué la tasa y no el conteo

Un distrito grande tiene más denuncias porque tiene más gente. Comparar conteos
colorea de oscuro los distritos populosos. El mapa muestra por defecto la
**tasa**: denuncias por cada 100.000 habitantes, en el periodo elegido. Un
interruptor cambia a conteos.

La población sale de las proyecciones del INEI (`uv run dep population`), que el
INEI califica como "información de carácter referencial". El mapa pinta de gris
los distritos con menos de 1.000 habitantes, porque con tan poca población una
sola denuncia mueve la tasa demasiado. La fórmula y los cortes de la leyenda
están en el [documento del mapa](mapa.md).

## Lo que el mapa no responde

- **Quién extorsiona y por qué.** Las filas no traen autores ni móviles.
- **Qué tipo de víctima.** El CSV nacional ya viene agregado y no tiene datos de
  la víctima. Solo el extracto del Callao trae sexo, edad y ocupación, y cubre
  una provincia.
- **Dónde ocurrió, dentro del distrito.** La capa de puntos del Ministerio del
  Interior existe, pero sus coordenadas no sirven para ubicar un hecho. El
  [documento de investigación](investigacion-de-fuentes.md) explica por qué.
- **Qué pasó después de la denuncia.** Las condenas del extracto de Piura no
  traen fecha ni lugar del hecho, así que no se pueden unir a una denuncia.

## Qué sigue

La limitación más importante se vuelve una regla de diseño: el sitio publica
agregados y dice qué cuenta. El [documento de privacidad](privacidad-y-etica.md)
fija las reglas para que ningún dato de una persona llegue al mapa.
