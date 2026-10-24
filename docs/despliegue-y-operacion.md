# Despliegue y operación

El sitio vive en Cloudflare. Este documento explica las piezas, los pasos para
montar tu propia copia, el flujo de CI y la actualización mensual de datos.

## Las piezas

| Pieza                       | Qué es                                                    | Dónde se define                                 |
| --------------------------- | --------------------------------------------------------- | ----------------------------------------------- |
| Worker `extorsion`          | Código que corre en Cloudflare y responde a cada petición | [`worker/src/index.ts`](../worker/src/index.ts) |
| Archivos estáticos          | El sitio compilado: HTML, JavaScript y CSS                | `web/dist/`, que genera `mise run web-build`    |
| Bucket R2 `extorsion-tiles` | Almacén de objetos con el mapa base y los datos           | Se crea a mano, una vez                         |
| `wrangler.jsonc`            | Une las piezas anteriores                                 | [`wrangler.jsonc`](../wrangler.jsonc)           |

Un Worker con archivos estáticos sirve el sitio compilado sin ejecutar código.
`wrangler.jsonc` marca dos rutas que sí pasan por el Worker
(`run_worker_first`): `/peru.pmtiles` y `/data/*`. Esas rutas se leen del bucket
R2 asociado con el nombre `BUCKET`.

El Worker sirve tres objetos: `peru.pmtiles`, `data/districts.geojson` y
`data/extorsion.json`. Para el mapa base atiende peticiones `Range` y responde
`206`, o `416` si el rango no existe. Envía `ETag` y entiende `If-None-Match`
(`304`) e `If-Match` (`412`). Añade las cabeceras de CORS y responde `204` a
`OPTIONS`. Cualquier método distinto de `GET`, `HEAD` y `OPTIONS` recibe `405`,
y un objeto que no existe recibe `404`. Si el `ETag` cambia mientras se lee un
objeto, responde `503` para no mezclar dos versiones.

Las pruebas están en [`worker/test/serve.test.ts`](../worker/test/serve.test.ts)
y corren en `workerd`, el motor de Cloudflare, contra un bucket simulado:

```sh
mise run worker-test
```

La corrida terminó con `14 passed`. El
[documento del mapa](mapa.md#por-qué-las-teselas-viven-en-r2) explica por qué
los datos no son archivos estáticos.

### Comprobar el sitio vivo

```sh
curl -sI -r 0-99 https://extorsion.empirical.workers.dev/peru.pmtiles
```

El 2026-10-24 respondió `206`, `accept-ranges: bytes`,
`access-control-allow-origin: *`, `cache-control: public, max-age=86400`, un
`etag` y `content-range: bytes 0-99/211713448`. Los datos llevan `max-age=3600`.

## Montar tu propia copia

Estos pasos crean un despliegue desde cero. No necesitas permiso del proyecto
original. Necesitas una cuenta de Cloudflare y un fork del repositorio en
GitHub.

### 1. Instala las herramientas

```sh
mise install
mise run install
mise run worker-install
```

`wrangler`, la herramienta de línea de comandos de Cloudflare, queda en
`worker/node_modules/.bin/wrangler`.

### 2. Crea el token de API

Un token es una clave con permisos limitados. El CI lo usa para desplegar y para
publicar datos. `wrangler` no puede crearlo.

1. Abre el panel de Cloudflare y entra en **Manage Account > Account API
   Tokens**. También sirve **My Profile > API Tokens** para un token de usuario.
2. Pulsa **Create Token**.
3. Ponle un nombre, por ejemplo `extorsion-deploy`.
4. En la plantilla **Edit Cloudflare Workers** (el rol Workers Editor) pulsa
   **Use template**. En el resumen, comprueba que incluya **Account > Workers
   Scripts** y **Account > Workers R2 Storage** con el nivel **Edit**. Si falta
   alguno, agrégalo.
5. En **Account Resources** elige tu cuenta.
6. Fija una fecha de vencimiento. Pulsa **Continue to summary** y luego **Create
   Token**.
7. Copia el valor. Cloudflare lo muestra una sola vez.

El token que despliega el sitio real tiene el rol Workers Editor, Workers
Scripts Write y Workers R2 Storage Write. Ese token desplegó un Worker que ya
existía. Crear el Worker por primera vez en un fork nuevo necesita otra cosa:
una sesión de tu cuenta (paso 7) o un token con el permiso Workers Admin.
Después de esa primera creación, basta el token estrecho. Ver
[el error del primer despliegue](#el-error-del-primer-despliegue).

### 3. Busca el id de tu cuenta

En el panel, abre **Workers & Pages**. El id de la cuenta aparece en **Account
Details**. No es un secreto, pero tampoco se publica en el repositorio.

### 4. Guarda el token y el id en GitHub

En tu fork, el flujo lee un secreto y una variable:

| Nombre                  | Tipo     | Valor               |
| ----------------------- | -------- | ------------------- |
| `CLOUDFLARE_API_TOKEN`  | Secreto  | El token del paso 2 |
| `CLOUDFLARE_ACCOUNT_ID` | Variable | El id del paso 3    |

Con la CLI de GitHub:

```sh
gh secret set CLOUDFLARE_API_TOKEN
gh variable set CLOUDFLARE_ACCOUNT_ID --body "<tu id de cuenta>"
```

`gh secret set` pregunta el valor, así que el token no queda en el historial de
la terminal. Sin la CLI, usa **Settings > Secrets and variables > Actions** en
GitHub.

### 5. Crea el bucket

Exporta las mismas dos variables en tu terminal y crea el bucket con el nombre
que espera `wrangler.jsonc`:

```sh
export CLOUDFLARE_API_TOKEN=...
export CLOUDFLARE_ACCOUNT_ID=...
worker/node_modules/.bin/wrangler r2 bucket create extorsion-tiles
```

Escribe tu token donde dice `...`. No lo guardes en un archivo del repositorio.
Si cambias el nombre del bucket, cámbialo también en `wrangler.jsonc` y en las
tareas `data-publish` y `tiles-publish` de [`mise.toml`](../mise.toml).

### 6. Genera y sube los datos y el mapa base

```sh
uv run dep sidpol
uv run dep population
uv run dep export-web
mise run web-tiles
mise run data-publish
mise run tiles-publish
```

Las dos tareas `*-publish` suben con `wrangler r2 object put ... --remote`.
`tiles-publish` sube 212 MB y tarda según tu conexión. Esto se hace una vez: el
CI nunca sube el mapa base.

### 7. Despliega por primera vez

La primera vez crea el Worker, y eso no lo hace el token estrecho del paso 2.
Entra con tu cuenta de Cloudflare. Antes, saca el token de tu terminal para que
`wrangler` no lo use:

```sh
unset CLOUDFLARE_API_TOKEN
worker/node_modules/.bin/wrangler login
mise run deploy
```

`wrangler login` abre el navegador para que autorices el acceso. La tarea
`deploy` compila el sitio (`web-build`) y ejecuta `wrangler deploy`. La salida
muestra la URL pública del Worker, de la forma
`https://extorsion.<tu-subdominio>.workers.dev`. Ábrela y comprueba que el mapa
carga.

A partir de aquí el Worker existe y el CI lo actualiza con el token estrecho:
cada cambio al sitio que llega a `main` se despliega solo.

## El flujo de CI

[`.github/workflows/ci.yml`](../.github/workflows/ci.yml) corre en cada
solicitud de cambios y en cada empuje a `main`. Tiene cinco trabajos:

| Trabajo   | Qué hace                                                    |
| --------- | ----------------------------------------------------------- |
| `check`   | `mise run check`: formato, tipos y los tests de Python      |
| `web`     | `mise run web-test`: los tests de Playwright                |
| `worker`  | `mise run worker-test`: tipos y tests del Worker            |
| `changes` | Mira qué archivos cambiaron, con `dorny/paths-filter`       |
| `deploy`  | `mise run deploy`, solo en `main` y solo si el sitio cambió |

El sitio cambió si se tocó `web/**`, `worker/**`, `wrangler.jsonc`, `mise.toml`
o el propio `ci.yml`. Un cambio solo en Python no despliega, porque el sitio lee
sus datos del bucket. Un disparo manual (`workflow_dispatch`) despliega siempre.

`deploy` espera a `check`, `web`, `worker` y `changes`. Antes de desplegar
comprueba que el secreto exista y falla con un mensaje claro si falta. El grupo
de concurrencia `deploy` impide que dos despliegues corran a la vez, y un empuje
nuevo no cancela uno en curso.

Las acciones de terceros están fijadas por el hash del commit, no por la
etiqueta. [`.github/dependabot.yml`](../.github/dependabot.yml) abre cada semana
solicitudes de actualización para npm, GitHub Actions y uv, con una espera de
siete días antes de proponer una versión nueva.

## La actualización mensual

SIDPOL publica un archivo nuevo cada mes.
[`.github/workflows/refresh.yml`](../.github/workflows/refresh.yml) lo recoge el
día 5 de cada mes a las 06:17 UTC (`17 6 5 * *`), o cuando alguien lo lanza a
mano. Corre en este orden:

```text
mise run install -> dep sidpol -> dep population -> dep export-web -> mise run data-publish
```

Reemplaza `districts.geojson` y `extorsion.json` en el bucket. No despliega el
Worker: el sitio lee los datos del bucket con `max-age=3600`, así que la página
nueva se ve en menos de una hora. Las demás fuentes (`sidpol-live`, `callao`,
`piura`, `inpe` y las noticias) no forman parte de la actualización y no están
en el mapa.

Para lanzarla a mano:

```sh
gh workflow run refresh.yml
```

## El error del primer despliegue

El primer despliegue falló con este mensaje:

```text
No access to the specified resource
```

El token tenía solo el rol **Workers Editor**. La subida de los archivos
estáticos falló con ese error. El token que funcionó añadió **Workers Scripts
Write** y **Workers R2 Storage Write**, y con él se desplegó un Worker que ya
existía. La actualización mensual se probó después desde un runner de GitHub sin
bloqueo.

El mensaje no nombra el permiso que falta. Nadie quitó permisos uno a uno, así
que el documento solo afirma el conjunto que funcionó.

Lecciones para tu token:

- Revisa en el resumen del paso 2 que el token tenga esos tres permisos antes de
  crearlo.
- Un mensaje de acceso denegado no dice qué permiso falta. Compara los permisos
  del token con lo que hace el comando: `wrangler deploy` sube el Worker y los
  archivos estáticos, `wrangler r2 object put` escribe en R2.
- Crea el token con vencimiento y anota la fecha. Un token vencido falla igual
  que uno sin permiso.
- Nunca pegues el token en un chat, en un issue ni en un archivo del
  repositorio. Si se expone, bórralo en el panel y crea otro.
