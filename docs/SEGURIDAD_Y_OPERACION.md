# Seguridad y operacion

## Limites de servicio

Las consultas de score se ejecutan con capacidad limitada por proceso:

- `APP_SCORE_QUERY_MAX_CONCURRENCY`: calculos Polars simultaneos (por defecto,
  2).
- `APP_SCORE_QUERY_QUEUE_TIMEOUT_SECONDS`: espera maxima para obtener capacidad
  (por defecto, 1 segundo). Se responde `429 SCORE_QUERY_CAPACITY_REACHED` al
  agotarse.
- `APP_SCORE_QUERY_TIMEOUT_SECONDS`: espera maxima de una respuesta (por
  defecto, 180 segundos). Se responde `504 SCORE_QUERY_TIMEOUT`; el calculo no
  se cancela a mitad de lectura y sigue ocupando su capacidad hasta cerrar.

`GET /api/health` indica que el proceso responde. `GET /api/ready` solo es 200
si todos los recursos requeridos existen. `GET /api/metrics` expone metricas
Prometheus de solicitudes, latencia, cache y saturacion de score.

Compose ejecuta el backend como usuario no privilegiado, monta los datos en
solo lectura y aplica raiz de sistema de archivos de solo lectura, limites de
CPU/memoria y rotacion de logs. Los valores de `APP_WEB_CONCURRENCY`,
`BACKEND_*_LIMIT` y `FRONTEND_*_LIMIT` se acuerdan tras la medicion de
rendimiento; no se incrementan a ciegas porque cada worker mantiene su propia
cache y compite por memoria con Polars. El valor por defecto es `1`: la cache
de score y el semaforo `APP_SCORE_QUERY_MAX_CONCURRENCY` son por worker, de
modo que `N` workers ocupan `N` caches y admiten `N x` consultas pesadas
simultaneas.

Las metricas de `/api/metrics` son correctas con cualquier numero de workers.
Con `APP_WEB_CONCURRENCY > 1` el entrypoint activa el modo multiproceso de
`prometheus_client`: cada worker escribe sus contadores en ficheros mmap de
`PROMETHEUS_MULTIPROC_DIR` (por defecto `/tmp/prometheus-multiproc`, en el
`tmpfs` del contenedor) y el scrape, lo atienda el worker que lo atienda,
devuelve la suma. El directorio se vacia en cada arranque. Al apagarse, cada
worker retira su aportacion a `calidad_score_queries_active`; si un worker
muere abruptamente su ultimo valor permanece hasta el siguiente reinicio del
contenedor. Las metricas `calidad_gold_*` se leen del estado en disco y no
dependen del numero de workers.

El contenedor arranca mediante `backend/entrypoint.sh`, que hace `exec` de
uvicorn: no hay shell intermedia y `SIGTERM` llega directamente al servidor,
por lo que `stop_grace_period` se aplica al apagado ordenado real.

## Identidad corporativa

En desarrollo se utiliza `APP_AUTH_MODE=disabled`. En produccion debe usarse
`APP_AUTH_MODE=trusted_proxy` y no debe exponerse el puerto de la aplicacion a
clientes no confiables. El proxy corporativo es responsable de autenticar al
usuario, eliminar cualquier cabecera de identidad enviada por el cliente y
emitir:

- `X-Forwarded-User`: identificador estable de usuario.
- `X-Forwarded-Roles`: roles separados por comas.

Los nombres de cabecera y roles se configuran con `APP_AUTH_*`. El rol de
consulta permite usar Calidad de produccion; modificar parametros de una
consulta requiere `quality_manager`; las metricas requieren `quality_admin`.
La integracion concreta con Active Directory, Entra ID o el proveedor
corporativo se configura en el proxy, no se sustituye con cabeceras que puedan
llegar directamente desde un navegador.

## Runbook de incidencia

1. Consultar `/api/ready`. Si devuelve 503, revisar los recursos concretos de
   `missing_resources` y restaurar el ultimo lote publicado, no editar
   Parquet en caliente.
2. Ante `429`, reducir simultaneidad o revisar el presupuesto de rendimiento.
   Ante `504`, conservar el `X-Request-ID`, revisar las metricas y los logs
   estructurados antes de elevar el timeout.
3. Para volver a una version de aplicacion, ejecutar el despliegue con la
   etiqueta/commit anterior. Para datos, cambiar el puntero atomico
   `current.json` al lote publicado anterior mediante el procedimiento de
   ingesta aprobado.
4. Configuracion versionada, perfiles DAR, manifiestos, informes de lote y
   agregados publicados deben estar incluidos en la copia corporativa. La
   restauracion se prueba en el entorno de validacion antes de cada cambio
   relevante.
