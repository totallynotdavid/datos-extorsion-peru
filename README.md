# datos-extorsion-peru

Mapa de denuncias por extorsión en el Perú, por distrito y mes, con una
canalización de datos abiertos que lo alimenta. El mapa muestra denuncias
policiales, no delitos: muchas extorsiones no se denuncian. Publica solo
agregados, nunca datos de personas.

Sitio: <https://extorsion.empirical.workers.dev>

## Uso

Instala [mise](https://mise.jdx.dev) y ejecuta:

```sh
mise install
mise run install
uv run dep sidpol
```

El último comando descarga el CSV de denuncias y escribe
`data/out/extorsion_distrito_mes.parquet`. `uv run dep --help` lista las demás
fuentes.

## Documentación

[docs/](docs/README.md) enseña, en orden, cómo se construyó el sitio: la
pregunta, las fuentes, la privacidad, la canalización, el mapa, el despliegue y
cinco ejercicios.
