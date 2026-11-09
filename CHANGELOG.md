# Changelog

Formato basado en [Keep a Changelog](https://keepachangelog.com/es/1.1.0/);
versionado segun [SemVer](https://semver.org/lang/es/). Las entradas se
escriben para quien opera o consume la aplicacion, no para quien la programa.

## [Unreleased]

### Added

- Corte por cobertura: cada produccion se calcula hasta el ultimo corte de 10
  minutos que todas sus fuentes de Silver acreditan; la hora de publicacion es
  solo el techo. `publication_scope_status` incorpora una fila
  `coverage:<produccion>` con hasta cuando esta calculada y que familia falta,
  `/api/gold/status` la expone y la interfaz la muestra para las producciones
  seleccionadas. Ver `docs/DECISION_CORTE_POR_COBERTURA.md`.
- Comando `python -m app.gold coverage --settings ... --at <hora local>`:
  informe de solo lectura (`READY`, `BLOCKED`, `NOTHING_NEW`) para
  planificadores. El DAG `gold_publish` lo usa para esperar hasta 8 minutos
  cuando ninguna produccion puede avanzar, y publica igualmente despues.
- Metricas `calidad_gold_coverage_blocked_productions` y
  `calidad_gold_coverage_max_lag_seconds`; alertas `CalidadGoldCoverageBehind`
  (30 min) y `CalidadGoldCoverageStalled` (60 min); panel en el dashboard.
- Orquestacion del publicador Gold con Airflow 3.4 (`compose.airflow.yaml`,
  `orchestration/airflow/`): DAG `gold_publish` cada 10 minutos en hora de
  planta, `gold_nightly` (backup verificado y despues mantenimiento) y
  `gold_restore_drill` semanal. Los errores de datos fallan al primer intento;
  los transitorios se reintentan. El bucle `watch` queda como respaldo en el
  perfil `watch`. Ver `docs/ORQUESTACION_AIRFLOW.md`.
- Comando `python -m app.gold publish --settings ... --at <hora local>`: una
  iteracion de `watch` para planificadores externos.
- Comando `maintain --root ... --keep-days N`: compactacion y VACUUM de las
  tablas Gold conservando legibles las publicaciones de los ultimos N dias.
- Comando `restore-drill --root ... --scratch ...`: restaura la ultima copia en
  un directorio temporal, la lee entera y la borra.
- Metricas y alertas de frescura de Silver (`CalidadGoldSourceStale`,
  `CalidadGoldSourceFamilyBehind`): una ingesta parada ya no pasa por
  `UNCHANGED`. Alertas de backup caducado, ensayo de restauracion caducado y
  mantenimiento caducado.

### Changed

- El primer cálculo Gold admite el histórico de Silver: cada tabla puede ocupar
  todo el presupuesto total (20 millones de filas en el ajuste de contenedor; el
  tope del CLI pasa a 50 millones). El tope antiguo de 250.000 filas por tabla
  rechazaba `observation_assignments`. El scheduler reserva 8 GB para esa lectura.
- La publicacion desatendida (`publish --at`, `watch`) ya no aplica una hora
  fija de desfase: `attempt_cutoff` redondea la hora al escalon de 10 minutos
  y los datos acreditados fijan el corte de cada produccion.
- **Incompatible para scripts:** el CLI Gold sale con 65 en errores de datos,
  75 si el almacen esta bloqueado, 74 en errores de E/S y 70 en errores
  inesperados, en lugar de 2 para todo. El JSON de error incluye `retryable`.

- El repositorio legacy del score (`PolarsCustomerScoreRepository`, 1.900
  lineas) pasa a `app.infrastructure.scoring`: un scorer por componente
  (MIA, AQL, DAR, ZC), fuentes de datos separadas y ensamblado comun para
  produccion y turno. Resultados identicos; el argumento sin uso
  `four_hour_offset_hours` desaparece del constructor.
- El panel de calidad del frontend (1.500 lineas) pasa a
  `src/components/quality-score/` con un componente por unidad visual y
  helpers puros con tests; los dialogos se cierran con Escape.
- `CODEOWNERS` apunta al mantenedor real.
- `/api/metrics` usa `prometheus_client` y es correcto con varios workers:
  con `APP_WEB_CONCURRENCY > 1` el entrypoint activa el modo multiproceso
  (`PROMETHEUS_MULTIPROC_DIR`) y cada scrape suma todos los workers. Nombres,
  etiquetas y buckets de latencia se conservan; los valores se publican como
  decimales (`1.0`).
- **Incompatible:** el contador de cache sale de `calidad_score_queries_total`
  (que mezclaba las etiquetas `outcome` y `cache`) a
  `calidad_score_cache_lookups_total{result="hit|miss"}`. Ningun panel ni
  alerta de `ops/` lo usaba.
- El entrypoint rechaza un `APP_WEB_CONCURRENCY` que no sea entero positivo.

### Fixed

- El desglose por turno de una produccion sin ficheros de avisos ya no falla
  por una columna inexistente en un DataFrame vacio.
- `freeze-legacy` fallaba con `FileNotFoundError` al calcular la huella del
  motor sobre un fichero eliminado. La huella del motor legacy y la del
  pipeline Gold se calculan ahora sobre el grafo real de imports, de modo que
  cubren `domain/score_config.py` y cualquier modulo futuro. La primera
  ejecucion Gold tras actualizar recalcula en lugar de responder `UNCHANGED`.
- Mensajes de error de consultas Gold con tildes corruptas (`publicaciÃ³n`).
- Repetir una operacion Gold ya publicada y superada devolvia el puntero
  `current.json` a esa publicacion antigua sin avisar. Ahora falla con un
  error de integridad y el puntero no se mueve.

## [0.2.0] - 2026-11-06

### Added

- Publicador Gold en Delta Lake con contrato dimensional `quality-gold-dimensional-v3`
  (36 tablas), API opcional `APP_DATA_BACKEND=gold` y `compose.gold.yaml`.
- Modulo de dominio `app.domain.score_config` como unico contrato de la
  configuracion del score (validacion, overrides, valores efectivos) compartido
  por el backend legacy y Gold.
- Metricas Prometheus por etapa del pipeline Gold
  (`calidad_gold_stage_duration_seconds_last{stage=...}`).
- Particionado por `id_produccion` de los hechos historicos Gold
  (`mia_rejections`, `mia_inspections`, `ar_intervals`, `inspection_bases`,
  `mia_window_exposures`, `mia_window_bases`) con poda de ficheros en lectura.
- Gobierno del repositorio: `CONTRIBUTING.md`, `CODEOWNERS`, plantilla de PR,
  `pre-commit` y este changelog.

### Changed

- `GoldService` dividido en `catalog`, `scenarios` y ensamblado de score
  indexado por produccion; los joins O(n·m) pasan a indices.
- `pipeline.build` dividido en etapas con nombre y cronometro por etapa.
- `mypy` cubre todo `backend/app` (estricto fuera de `app.gold`, gradual
  dentro); `ruff format` se exige en todo el backend. CI ejecuta ambos sobre
  el arbol completo.
- El contenedor del backend arranca con `backend/entrypoint.sh` (exec form,
  sin `sh -c`) y `APP_WEB_CONCURRENCY` pasa a `1` por defecto: cache, semaforo
  de consultas y contadores de metricas viven en proceso.

### Fixed

- `Attribution.targets` declaraba poder devolver `None` como decision; nunca
  lo hacia. Tipos corregidos y codigo dependiente simplificado.
- Variable de bucle reutilizada en `inspected_totals` que ocultaba el tipo
  `Optional` del intervalo asignado.

## [0.1.0] - 2026-10

### Added

- Backend FastAPI con score de calidad cliente, catalogo y agregados Polars
  sobre Parquet; frontend React/Vite; despliegue Docker Compose para Windows.

[Unreleased]: ./CHANGELOG.md
[0.2.0]: ./CHANGELOG.md
[0.1.0]: ./CHANGELOG.md
