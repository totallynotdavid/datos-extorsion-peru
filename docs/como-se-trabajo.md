# Cómo se hizo el trabajo

Este proyecto se construyó con agentes de IA que escribían el código y con
personas que decidían. Este documento cuenta el método, para que lo copies si
trabajas igual. Es corto a propósito: lo que importa del proyecto está en los
otros documentos.

## Primero se investiga, después se construye

Antes de escribir código, agentes "exploradores" sondearon las fuentes y
escribieron un informe cada uno. Un explorador no cambia código ni guarda
cambios en el repositorio: solo mide y reporta. Cada afirmación del informe
lleva una marca: _vista_ (el agente la comprobó) o _reportada_ (una página lo
dice). El [documento de investigación](investigacion-de-fuentes.md) resume esos
informes.

Con los informes en la mano, una persona decidió la tecnología por mérito: qué
herramienta resuelve el problema con menos piezas. Ahí salieron DuckDB y Parquet
en vez de una base de datos, httpx en vez de Selenium, y MapLibre con PMTiles en
vez de un servicio de mapas con clave.

## El encargo

Cada tarea de código empieza con un encargo escrito. Tiene cuatro partes:

| Parte        | Qué dice                                                                 |
| ------------ | ------------------------------------------------------------------------ |
| Resultado    | Qué existe al terminar, en una o dos frases y con un comando concreto    |
| Fuente       | Qué fuentes usar y lo que ya se midió de ellas, con la fecha             |
| Reglas       | Lo que no se negocia: privacidad, `robots.txt`, qué carpetas no se tocan |
| Comprobación | Qué comando o evidencia demuestra que se cumplió                         |

Un buen encargo evita que el agente adivine. Un encargo malo dice "haz un mapa".
Uno bueno dice qué entrada lee, qué archivo escribe y cómo se prueba.

## El agente trabaja aislado

Cada tarea corre en su propia copia del repositorio (un _worktree_ de git), en
una rama aparte. El agente edita, corre las pruebas y deja los cambios sin
guardar. No hace commit ni sube nada. Escribe un informe con la evidencia:
salida de comandos, cifras, y los defectos que encontró y no pudo arreglar.

## Dos revisores independientes

Un cambio no entra porque el agente diga que está bien. Dos revisores
independientes, cada uno con su propio modelo, leen el diff y el informe. Cada
revisor reporta hallazgos con una gravedad. Un hallazgo grave bloquea la
entrega.

Los revisores también fallan. El 2026-10-21, dos revisores aprobaron el cambio
que agregaba la lectura de noticias, y una ronda posterior encontró un error en
[`src/dep/http.py`](../src/dep/http.py): un `429` al pedir `robots.txt` se
trataba como "sin restricciones", y el programa seguía haciendo peticiones a un
sitio que acababa de pedirle que parara. Hoy ese caso está cubierto por tests
que el [documento de la canalización](pipeline.md#el-cliente-http-educado)
nombra.

La lección: una revisión es una probabilidad, no una garantía. Cuando un error
se escapa, se agrega un test que lo detecta, no una línea más en una lista de
cuidados.

## Entrega

Las personas deciden la política: qué hosting usar, si se avisa al INPE de sus
datos expuestos, qué hacer con los términos de cada diario. El agente no decide
eso. Lo reporta como pregunta.

Cuando las revisiones pasan, el trabajo se entrega con commits pequeños que
explican _por qué_, y con una solicitud de cambios que CI valida (ver el
[documento de despliegue](despliegue-y-operacion.md#el-flujo-de-ci)). Cada
commit lo escribe quien coordina el trabajo a partir de un plan que entrega el
agente.

## Qué copiar de este método

- Mide antes de construir, y deja la medición escrita.
- Escribe en el encargo cómo se comprueba el resultado.
- Pon las reglas de privacidad en tests, no en recordatorios.
- Las decisiones de política las toma una persona.
