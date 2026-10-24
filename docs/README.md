# Documentación

Estos documentos enseñan cómo se construyó este sitio, desde la pregunta hasta
la página desplegada. Están escritos para quien sabe leer Python y TypeScript y
nunca ha armado un proyecto de datos. Léelos en este orden.

1. [La pregunta y sus límites](pregunta-y-limites.md): qué pregunta el proyecto,
   qué dicen y qué no dicen los datos, y por qué el mapa muestra denuncias y no
   delitos.
2. [Investigación de fuentes](investigacion-de-fuentes.md): cómo se buscaron,
   sondearon y juzgaron las fuentes, y cuáles se descartaron.
3. [Privacidad y ética](privacidad-y-etica.md): las reglas que impiden que un
   dato de persona llegue al mapa, y el test que prueba cada una.
4. [La canalización de datos](pipeline.md): un comando por fuente, DuckDB y
   Parquet, y un comando recorrido de punta a punta.
5. [El mapa](mapa.md): MapLibre, PMTiles, los dos archivos de datos y por qué
   las teselas viven en R2.
6. [Despliegue y operación](despliegue-y-operacion.md): el Worker de Cloudflare,
   el bucket, el CI, la actualización mensual y cómo montar tu propia copia.
7. [Cómo se hizo el trabajo](como-se-trabajo.md): encargos, agentes, revisiones
   y entrega.
8. [Ejercicios](ejercicios.md): cinco ejercicios de una sesión, cada uno con una
   comprobación que corres tú.

Para correr los comandos hace falta [mise](https://mise.jdx.dev). El
[README](../README.md) del repositorio explica la instalación.
