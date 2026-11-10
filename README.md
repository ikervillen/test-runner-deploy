# Calidad de produccion

Aplicacion interna para evaluar y ordenar la calidad de una produccion de
botellas. El producto se limita deliberadamente a la pantalla **Calidad de
produccion**: seleccion, parametros de consulta, ranking, mapa de penalizacion,
detalle de produccion y detalle individual por turno.

El score conserva cuatro bloques: MIA, avisos/rechazos por criticidad, DAR por
segmento y rendimiento de zona caliente. Los datos industriales no se incluyen
en Git y siempre se consumen en solo lectura.

## Arquitectura

```text
Navegador -> Nginx (frontend estatico) -> FastAPI -> Parquet y Excel de Calidad
```

La API publica solo estos endpoints:

- `GET /api/health`: liveness del proceso.
- `GET /api/ready`: disponibilidad de datos y version del manifest.
- `GET /api/catalog`: lineas, producciones y ramales.
- `POST /api/customer-score/query`: score por produccion y turno.
- `POST /api/customer-quality/query`: etiqueta de calidad cliente para el mapa.
- `GET /api/metrics`: metricas Prometheus, restringidas al rol de operacion en
  produccion.

## Datos requeridos

La raiz corporativa de datos debe contener, como minimo:

```text
AV-2024-2026_parquet/manifest.csv
AV-2024-2026_parquet/<produccion>/<ramal>.parquet
avisos_rechazos_parquet/
inspeccionadas_totales_parquet/
Rendimiento_ZC.xlsx
Tabla defectos con criticidades.xlsx
Programacion defectos MDL_enviado V01.xlsx
COD_DEF.xlsx
```

La configuracion versionada se encuentra en `config/customer_score.json` y
`config/segment_dar_profiles.json`. El segundo catalogo utiliza exclusivamente
codigos MDL normalizados y permite definir DAR por segmento sin mezclar
descripciones ni convenciones historicas.

## Desarrollo local

1. Cree `.env` a partir de `.env.docker.example` y adapte las rutas locales.
2. Instale dependencias y ejecute las dos aplicaciones:

```powershell
uv sync --dev --python 3.11
npm ci
uv run uvicorn main:app --app-dir backend --host 127.0.0.1 --port 8001
npm run dev
```

La interfaz queda en `http://127.0.0.1:5174` y OpenAPI en
`http://127.0.0.1:8001/api/docs`.

## Validacion

```powershell
uv run ruff check backend
uv run ruff format --check backend
uv run mypy
uv run --extra gold pytest backend/tests -q
npm run format:check
npm test
npm run build
```

El pipeline de GitHub ejecuta estas comprobaciones para cada pull request y
mantiene las dependencias revisadas mediante Dependabot. Los mismos gates se
instalan en local con `pre-commit install --install-hooks`. El flujo de ramas,
los mensajes de commit, el versionado y los criterios de revision estan en
[CONTRIBUTING.md](CONTRIBUTING.md); los cambios por version, en
[CHANGELOG.md](CHANGELOG.md).

## Despliegue Windows

La operacion de servidor usa Docker Compose con frontend estatico y datos
montados en solo lectura. Consulte [DESPLIEGUE_WINDOWS.md](docs/DESPLIEGUE_WINDOWS.md)
para el flujo reproducible de release y rollback.

## Gobierno y seguridad

- No subir Excel, Parquet, `.env`, logs ni credenciales.
- Una actualizacion de datos se realiza mediante una tarea de ingesta externa,
  nunca desde el navegador.
- Antes de abrir la aplicacion a otros usuarios debe colocarse detras del proxy
  corporativo con HTTPS y autenticacion aprobada por TI.

La hoja de ruta de consolidacion, ingesta y operacion figura en
[PLAN_MIGRACION_CALIDAD_SERIE.md](docs/PLAN_MIGRACION_CALIDAD_SERIE.md).
Las medidas, agregados y limites de consulta estan descritos en
[RENDIMIENTO.md](docs/RENDIMIENTO.md), y el modelo de seguridad y runbook en
[SEGURIDAD_Y_OPERACION.md](docs/SEGURIDAD_Y_OPERACION.md).
