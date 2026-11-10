# Implementación Gold — entregas verificables

Inicio: 06/11/2026. Cambios acotados sobre la copia de trabajo existente.
Los archivos Gold previos y los cambios locales del usuario se conservan.

| Bloque | Entrega | Estado |
|---|---|---|
| G01 | Contratos ejecutables, dependencias y validación referencial | Implementado y validado: V3 con 36 tablas; V2 conservada |
| G02 | Dimensiones conformadas, SCD y claves de contexto de hechos | Primera entrega selectiva implementada y validada localmente |
| G03 | Publicación, consulta Gold y disponibilidad en API | Implementado y probado: catálogo, score, turnos, escenarios, pendientes, caché por publicación y roles |
| G04 | Planificación incremental, métricas y empaquetado del publicador | Índice por producción/periodo, reutilización, lotes, exportador continuo opcional, métricas y Compose implementados; activación servidor pendiente |
| G05 | CI reproducible, backup/restauración y pruebas integrales | CI con formato/tipos/tests; backup/verify/restore y job externo implementados y probados localmente; almacenamiento independiente/CI remota pendientes |
| G06 | Validación real, dependencias de Calidad y preparación de piloto | API real y paridad de cantidades verificadas; aceptación industrial y comprobaciones de servidor pendientes |

Segmentos: importados 575 modelos en 13 familias desde el Excel recibido.
P01 queda parcialmente resuelta según D18: DAR específicos pendientes; el
perfil común seleccionado expresamente se marca provisional. No se asignan
familias a modelos desconocidos ni se publican scores con bases incompletas.
El despliegue de servidor requiere sus rutas, identidad y presupuesto verificados;
las comprobaciones locales no se presentan como un despliegue realizado.

## Evidencia de cierre del primer bloque

- Fuente conservada intacta; clasificación y archivo por SHA-256; 20 de los 20
  modelos observados en esta réplica encuentran clasificación exacta.
- `gold/contracts-v2.json` y `gold/TABLES.md`, generados desde
  `app.gold.contracts`: grano, tipos/nulos, claves, relaciones, dependencias y
  reglas de agregación. Una prueba detecta divergencias del artefacto JSON.
- Validación antes de escribir: integridad referencial, asignación de la misma
  producción, ventanas/corte, unicidad del resultado, ocho componentes y
  conciliación total/peso/normalización. Bloqueo de cambios de padres sin derivadas
  y comprobación de todos los ámbitos antes del primer commit.
- Dimensiones de planta, fecha, máquina, modelo y defecto. Versiones de atributos
  con tiempo de registro, vigencia física independiente y claves de resultados
  cerrados conservadas. No se inventa una vigencia de negocio para el catálogo.
- Publicación real aislada de 35 tablas: 24 producciones, 202.931 inspecciones
  de origen y 33.937 observaciones de rechazo; `validate-gold` devuelve VALID.
  No confundir filas de observación con número de botellas.
- Suite completa: **136 tests correctos**; cobertura de líneas **84,77 %**,
  ramas **67,14 %**, combinada **80,84 %** (umbral existente 80 % conservado).
  Ruff correcto. Dependencias Gold instaladas desde el lock en un entorno nuevo.
- La copia real mantiene cero scores nuevos: faltan cobertura/bases e histórico
  suficiente. Las pruebas controladas sí ejercitan cálculo completo, DAR
  pendiente/provisional, reintentos y conservación de scores/dimensiones.
- `gold/Dockerfile` preparado; no se ha construido la imagen porque el daemon
  Docker no está disponible. Tampoco se ha ejecutado un workflow remoto.

Informe reproducible local: `var/gold_validation/contracts-dimensional-v2-report.json`.
Almacén nuevo: `var/gold_validation/gold-dimensional-v2-verified`. Las publicaciones
anteriores permanecen separadas, sin migración destructiva ni limpieza.

## Segunda entrega: código de aplicación y operación

El usuario solicita completar el código de las prioridades A y B. Se implementa
la conexión **opt-in** para validación, sin declarar resuelta la aceptación
industrial pendiente de Calidad. Procedimiento y límites en `OPERACION_GOLD.md`.

- Contrato V3: `zc_production_links` conserva la relación aceptada de cada
  contador compartido. `quality_scores.basis_publication_id` fija los datos que
  originaron un score cerrado. Los escenarios respetan esa base histórica.
- API con consulta limitada, filtros/columnas y publicación fija, caché asociada
  a la publicación, resultados pendientes explícitos y reclamaciones desconocidas.
  Gold no inicializa repositorios Excel/Parquet del recorrido anterior.
- Frontend: pendientes sin cero ficticio, indicación DAR provisional y corte,
  caché Gold sin reutilizar publicaciones anteriores y comprobación periódica
  del catálogo. La consulta ordinaria no envía una modificación de parámetros.
- Cálculo: lecturas MIA por lotes, índices por producción/periodo, reutilización
  de cantidades con entradas idénticas y recálculo histórico explícito con motivo.
  No se sustituye el límite del proceso por una promesa de escalabilidad ilimitada.
- Ingesta: hook posterior a ciclo confirmado, desactivado por defecto; exportación
  JSON/CAS por streaming y Arrow de 8.192 filas, vector de fuentes comprobado antes
  de promoción. Los fallos de exportación no revierten commits aceptados.
- Observabilidad: histogramas HTTP/publicación, último intento/éxito, fuentes
  leídas, cantidades y ventanas reutilizadas; alertas y dashboard Grafana preparados.
- Operación: Compose de tres servicios, imagen Gold y adaptador de productor,
  backup con manifiesto/hashes, verificación, restore en destino nuevo y job externo.
  La API consulta solo lectura; roles viewer/manager/admin probados.
- Pruebas de actualización detectaron y corrigieron la incompatibilidad Arrow 23
  con estadísticas string_view generadas por MERGE: Delta selecciona archivos y
  los predicados se aplican a lotes tipados, dentro de los presupuestos de lectura.

Evidencia final: suite completa de **149 tests correctos**, **81,51 %** de cobertura
combinada (líneas 85,33 %, ramas 68,18 %), controles negativos y pruebas de roles,
incluida recuperación de capacidad tras cancelar una petición con trabajador activo.
También tests de ingesta/worker, pruebas del frontend y compilación; formato,
Ruff y mypy sobre configuración/esquemas. El parser nativo se ha comprobado sobre
237.872 entradas reales de ledger y 72 episodios, sin cargarlo entero en memoria.
La API de la réplica real devuelve catálogo de 24 producciones y estado pendiente
coherente con sus fuentes. Seis tablas de cantidades MIA/A/R/ZC coinciden exactamente
con la versión anterior.

Release final: `var/gold_validation/gold-application-v3-release`, publicación
`39019b6e2e8158fbb115332bc5643278827616f7cbf9e5f854f8b23f41c49537`,
huella de contrato `cbe74286ffae344459c87d455b8cd0f3b80796c4a0a6f2b3e97f409c9dcb8039`.
El grafo incluye la dependencia de las bases ZC respecto a sus relaciones
aceptadas. Backup y restore reales de 106 archivos (32.974.753 bytes), verificados
por hash y publicación idéntica; API probada sobre la copia restaurada. Informe:
`var/gold_validation/release-v3-verification.json`. Los ensayos previos se conservan.

**No resuelto por escribir código:** DAR específicos, histórico y criterios
industriales, construcción de imágenes con Docker, despliegue/roles/certificados
reales, destino independiente de backup y validación RPO/RTO. No se han iniciado
contenedores del servidor ni modificado sus SQLite activos. Las comprobaciones
locales no se presentan como aceptación del piloto.
