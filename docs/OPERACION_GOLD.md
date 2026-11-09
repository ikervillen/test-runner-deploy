# Operación de Gold y aplicación

Implementación local del 06/11/2026. Las instrucciones de servidor son configuración
revisable; no acreditan un despliegue realizado. Mantener cambios por entregas
G03–G06, sin agruparlos en una única PR de refactorización.

## Compatibilidad y responsabilidades

- API histórica: predeterminada. API Gold: `APP_DATA_BACKEND=gold` y
  `APP_GOLD_ROOT` explícito, sin inicializar los repositorios Excel/Parquet legados.
- Contrato Gold: `quality-gold-dimensional-v3`, 36 tablas. Fórmulas escalares:
  `shared-score-math-v1`. API Gold: 0.2.0. Los pesos/umbrales tienen su propia
  versión y huella de configuración. Git identifica código; no sustituye estas versiones.
- El lector exige versión y huella compatibles, recibos, archivos y vector de
  dependencias. Cada consulta y su caché fijan una publicación. No mezclar un
  `current.json` de un cálculo con tablas de otro.
- V2 queda conservada. Construir V3 en una raíz nueva, comparar y seleccionar la
  nueva raíz; rollback seleccionando la imagen/configuración y raíz compatibles
  anteriores. No convertir tablas en servicio ni limpiar logs/temporales automáticamente.
- Python 3.11 para API/cálculo; Python 3.12 para el productor de ingesta.
  `uv.lock` y uv 0.11.6 fijan el entorno. El extra Gold incluye el parser `ijson`.

## Demostración local

Desde la raíz de `calidad_serie`, ajustar el JSON antes de construir:

```powershell
$env:PYTHONPATH = (Resolve-Path backend).Path
uv run --locked --extra gold python -m app.gold build --settings var/gold_validation/settings-application-v3-release.json --cutoff 2026-11-02T12:00:00
uv run --locked --extra gold python -m app.gold validate-gold --root var/gold_validation/gold-application-v3-release
$env:APP_DATA_BACKEND = 'gold'
$env:APP_DATA_ROOT = (Resolve-Path var/gold_validation/gold-application-v3-release).Path
$env:APP_GOLD_ROOT = $env:APP_DATA_ROOT
$env:APP_AUTH_MODE = 'disabled'
uv run --locked --extra gold uvicorn main:app --app-dir backend --host 127.0.0.1 --port 8003
```

En otra consola, `VITE_API_PROXY_TARGET=http://127.0.0.1:8003` y
`npm run dev -- --port 5175` permiten una vista aislada. Usar puertos libres.
`backend/env.gold.example` proporciona un ejemplo sin secretos.
Este modo sin autenticación es exclusivamente local.

La réplica tiene 24 producciones, 20 modelos observados clasificados y 13 familias
en el catálogo de 575 modelos. El corte es histórico y se muestra como tal.
Los scores que no tienen cobertura, capacidad o referencia suficiente permanecen
pendientes. No cambiar sus estados ni añadir observaciones ficticias para la demo.
La huella final V3 es `cbe74286ffae344459c87d455b8cd0f3b80796c4a0a6f2b3e97f409c9dcb8039`.
Las raíces V3 de ensayo anteriores conservan su huella y sus datos; no utilizarlas
como destino del publicador o lector final sin el código compatible correspondiente.

## Consulta y escenarios

- `/api/catalog`: producciones, ramales y filtros disponibles.
- `/api/customer-score/query`: resultado completo/desglose/turnos, o lista explícita
  de producciones pendientes. Nunca un total oficial formado por componentes parciales.
- Los escenarios con pesos/umbrales son de solo lectura. Para datos cerrados usan
  la publicación base conservada, no nuevas categorías aplicadas retrospectivamente.
- Los filtros físicos afectan a MIA y se comunican como tales; A/R y ZC mantienen
  el ámbito de producción. Los filtros sin dimensión acreditada no se inventan.
- `/api/customer-quality/query`: sin fuente actualizada, etiqueta `unknown`.
- `/api/gold/status`: publicación, motivos de disponibilidad y, por producción
  (`coverage`), hasta qué corte está calculada y qué familias de Silver faltan.
  La interfaz lo muestra para las producciones seleccionadas que van por detrás.
- `/api/ready`: integridad/compatibilidad del almacén, no aprobación industrial del score.

Una revisión histórica persistida exige ámbito y motivo:

```text
python -m app.gold build --settings SETTINGS.json --cutoff CORTE_LOCAL --recalculate-closed ID_PRODUCCION --reason "Criterio aprobado y referencia de revisión"
```

Se registra en el estado durable del cálculo; las producciones no seleccionadas
conservan sus resultados cerrados cuando sus datos propios no cambian.

## Productor continuo e ingesta

El hook en `Ingesta_Delta_Lake/ingesta_delta_lake.gold_export` está desactivado
por defecto. `INGESTA_GOLD_EXPORT=1` lo habilita después de un ciclo completo
confirmado, sin errores/backlog y con asociación ejecutada. El exportador:

1. Verifica controles aceptados y compatibilidad del ledger con las fuentes.
2. Lee el ledger por streaming y lo escribe en lotes de 8.192 filas; los metadatos
   de episodios/A/R conservan límites explícitos.
3. Vuelve a comprobar el vector fuente y los recibos antes de promover el puntero.
4. Conserva fechas de descubrimiento observadas. Una muestra más reciente no
   certifica periodos anteriores ni entregas futuras; ausencia no equivale a cero.

Un fallo de exportación guarda `gold-export-status.json`, conserva la publicación
anterior y no revierte commits de la ingesta. El consumidor no abre sus SQLite.
`watch` sigue rechazando la réplica offline utilizada en las pruebas locales.

`Ingesta_Delta_Lake/Dockerfile.gold-export` instala el extra `gold-export` y copia
el adaptador mediante el contexto de construcción nombrado `quality`:

```text
docker build --build-context quality=../calidad_serie --build-arg GOLD_REVISION=COMMIT_GOLD --build-arg REVISION=COMMIT_INGESTA --build-arg SOURCE=URL_REPOSITORIO --build-arg BRANCH=develop -f Dockerfile.gold-export -t ingesta-con-gold:VERSION .
```

Construir y probar esta imagen antes de reemplazar el productor del servidor.
No activar el flag en una imagen que carezca del adaptador/dependencias.

## Despliegue y permisos

`compose.gold.yaml` define publicador, API y frontend con recursos limitados,
logs rotados y entrada/configuración de solo lectura. API y frontend no escriben
Gold. `gold/settings.container.example.json` utiliza `/silver`, `/gold` y `/config`.
Las rutas físicas de Silver y Gold deben ser diferentes y pertenecer al ámbito
correcto; asignar UID/permisos de salida al usuario 10001 antes de iniciar.

Para orquestar el publicador con Airflow en lugar del bucle `watch`, ver
`ORQUESTACION_AIRFLOW.md`.

Requiere `HOST_CONFIRMED_SILVER_ROOT`, `HOST_GOLD_ROOT`,
`HOST_GOLD_CONFIG_ROOT` y un archivo externo `APP_ENV_FILE`. Revisar primero
`docker compose -f compose.gold.yaml config --quiet` y construir las imágenes.
Un único publicador por almacén y un worker API/una consulta pesada simultánea
son el punto de partida para el presupuesto actual. No aumentar el límite de
filas para ocultar un volumen que exceda el ámbito ensayado.

Exposición corporativa: proxy HTTPS que valide identidad, elimine cabeceras
entrantes de identidad/roles y establezca únicamente las validadas. Puertos
locales detrás del proxy, `APP_AUTH_MODE=trusted_proxy`; roles viewer, manager
para escenarios y admin para métricas. El archivo de ejemplo no configura un IdP
real ni certificados. Su validación depende de la infraestructura corporativa.

## Supervisión y rendimiento

`/api/metrics` exporta histogramas HTTP, peticiones, saturación y tiempos de
publicador, fallos, último éxito, filas y ventanas reutilizadas. Se distinguen
última comprobación correcta y fecha de la publicación. Los estados de dato se
consultan aparte: una API sana puede contener cálculos pendientes.

`ops/gold-alerts.yaml` y `ops/gold-dashboard.json` se integran con el Prometheus/
Grafana existente. Ajustar el destino de scrape y roles al proxy/red corporativos;
no abrir el backend a la red para facilitar scraping. La configuración preparada
no acredita que el servidor de supervisión la haya cargado.

Frescura de la entrada: el productor republica Silver con cada descubrimiento
correcto, así que `calidad_gold_source_publication_timestamp_seconds` es el
latido de la ingesta y `calidad_gold_source_discovery_lag_seconds{family}` el
retraso de cada familia respecto a la más reciente. `CalidadGoldSourceStale` y
`CalidadGoldSourceFamilyBehind` avisan a los 30 minutos; ajustar el umbral al
ciclo real de la ingesta. Sin ellas, una ingesta parada se veía como `UNCHANGED`.

Corte por cobertura (`DECISION_CORTE_POR_COBERTURA.md`): la hora de
publicación es solo el techo; cada producción se calcula hasta el último corte
de 10 minutos que todas sus familias acreditan. `python -m app.gold coverage
--settings SETTINGS.json --at HORA_LOCAL` informa, sin escribir, de si Gold
puede avanzar (`READY`), de si Silver cambió sin que ninguna producción avance
(`BLOCKED`) o de si no hay nada nuevo (`NOTHING_NEW`), con el detalle por
producción. `calidad_gold_coverage_blocked_productions` y
`calidad_gold_coverage_max_lag_seconds` alimentan `CalidadGoldCoverageBehind`
(30 min, aviso) y `CalidadGoldCoverageStalled` (60 min, crítico). Una ventana
sin cobertura durante 24 horas pasa de `WAITING_DEPENDENCY` a `INCOMPLETE` y
conserva en `reason_code` la familia que falta.

`python -m app.gold inventory --root GOLD` inventaría versiones, archivos, tamaños
y particiones. MIA utiliza un índice de periodo por
producción y reutiliza bases solo con entradas idénticas; el informe registra
ventanas reutilizadas/recalculadas y filas fuente leídas. Las consultas filtran
archivos mediante Delta y lotes Arrow con límites de lectura/memoria.

## Mantenimiento Delta

```text
python -m app.gold maintain --root GOLD --keep-days 30
```

Cada publicación reescribe los ficheros que toca y Gold conserva todas las
versiones para poder volver atrás, así que sin mantenimiento el almacén crece
sin límite y las escrituras parciales fragmentan las tablas. `maintain` toma
los bloqueos del pipeline y del escritor, compacta cada tabla y ejecuta VACUUM
conservando toda versión que fije la publicación actual o cualquiera más
reciente que `--keep-days`. Las publicaciones más antiguas dejan de poder
abrirse: esa es la ventana de vuelta atrás.

Los commits de mantenimiento llevan su propia operación y se cierran con una
publicación de mantenimiento (mismas filas, misma entrada), así el publicador
sigue siendo el único autor de cada versión. Si se interrumpe, el publicador
rechaza escribir con "Recover the pending operation" hasta que se vuelva a
ejecutar `maintain`, que reanuda. Con Airflow lo ejecuta `gold_nightly` tras el
backup; con `watch` hay que programarlo aparte, siempre después de un backup.

## Backup y recuperación

```text
python -m app.gold backup --source GOLD --destination DESTINO_NUEVO
python -m app.gold verify-backup --root DESTINO_NUEVO
python -m app.gold restore --source DESTINO_NUEVO --destination GOLD_RESTAURADO_NUEVO
```

La copia adquiere los bloqueos del escritor, guarda datos/versiones, configuración,
manifiestos y progreso, y verifica cada archivo por SHA-256. Restore rechaza
destinos existentes, rutas no válidas y copias alteradas; valida la publicación
restaurada. No borra ni sobrescribe el almacén original ni copias anteriores.
`ops/backup-gold.ps1` sirve para un job externo; exige que el destino ya esté montado.

```text
python -m app.gold restore-drill --root GOLD --scratch DIRECTORIO_TEMPORAL
```

El ensayo restaura la última copia registrada en `backup-status.json`, la abre
con el contrato Gold, lee todas las tablas y borra la copia también si falla.
`CalidadGoldBackupStale` (26 h sin copia) y `CalidadGoldRestoreDrillStale`
(8 días sin ensayo) avisan aunque nunca haya habido un éxito.

Para el piloto, configurar ese destino fuera del servidor, permisos, credenciales
externas y frecuencia/RPO acordados. Restaurar en otro almacén/equipo y medir el
RTO: el ensayo semanal prueba la copia, no la independencia del destino. La
retención y eliminación de copias requieren una decisión adicional.

## Pendientes externos de aceptación

DAR específicos por segmento; histórico/atribuciones/categorías necesarios para
conciliar scores industriales completos; host, permisos y presupuesto real de
contenedores; construcción de imágenes con Docker; proxy/identidad corporativos;
destino independiente, RPO/RTO y activación de supervisión. La implementación de
código y las pruebas locales no sustituyen esas comprobaciones.
