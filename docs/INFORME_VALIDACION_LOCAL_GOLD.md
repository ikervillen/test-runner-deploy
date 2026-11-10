# Validación local Silver → Gold

Fecha: 02/11/2026. Alcance autorizado: desarrollo y comprobaciones locales de
los puntos 1–5. Sin conexión de la app, GitHub/Actions ni despliegues en servidor.

**Resultado:** base técnica implementada y comprobada en local. La validación
integral del score de nuevas fabricaciones sigue pendiente; el punto 6 no se
considera autorizado por una comprobación parcial.

## Código y resultados

- Publicador, lector, contratos y cálculos: `backend/app/gold`.
- Fórmulas compartidas con el recorrido histórico: `backend/app/domain/score_math.py`.
- Pruebas: `backend/tests/gold`, más las regresiones históricas de score/perfiles.
- Instrucciones reproducibles: `gold/README.md`.
- Configuración y evidencias industriales: `var/gold_validation`, excluido de Git.
- Resultado estructurado: `var/gold_validation/validation-report.json`.
- Tablas: `var/gold_validation/gold-local/tables`.
- Restauración independiente: `var/gold_validation/restore-validation-v2`.

Se ha generado una réplica aislada utilizando los contratos y el código de
ingesta existentes sobre la instantánea Fenix de las cinco líneas. Los originales
solo se leen. El adaptador productor confirma versiones/operaciones y exporta
atribuciones; el consumidor Gold no consulta SQLite activos. Los parámetros,
gotas y evidencia ZC auxiliares se incorporan en esa réplica, no en el worker
del servidor. Su modo es explícitamente `offline_snapshot`.

## Comprobaciones obtenidas

| Comprobación | Resultado |
|---|---|
| Pruebas dirigidas de Gold y regresión de score/perfiles | 44 superadas; un aviso de deprecación existente de Starlette/httpx. |
| Ruff sobre código cambiado y pruebas Gold | Sin errores tras corrección de formato. |
| Cuatro consultas históricas completas, con entradas/configuración guardadas | MATCH reproducible. |
| Comparación independiente de cantidades y campos numéricos MIA | MATCH en cuatro casos reales: sin filtro y con máquina presente, AV23/AV32. |
| Defectos comparados por caso | 19, 8, 12 y 6; sin diferencias numéricas. |
| Manifiesto de entrada | 19 tablas con versiones, UUID, esquema, operación y justificantes verificados. |
| Tablas Gold locales | 29, incluidas tablas de score/desglose vacías cuando no son calculables. |
| Contexto obtenido | 24 producciones, 72 asignaciones, 24 tramos de capacidad. |
| Detalle obtenido | 202.931 filas de inspeccionadas, 33.937 de defectos, 177 A/R y 827 muestras físicas ZC. |
| Repetición con mismas entradas, configuración, código y corte | UNCHANGED, misma publicación, sin nuevos commits de negocio. |
| Fallos inyectados y reintento | Antes de escritura, después del commit, antes y después de promover; sin duplicación. |
| Sustitución por ámbito | Conserva otros orígenes; retira derivadas obsoletas dentro del ámbito. |
| Datos/propiedad no acreditados | Rechaza tablas ajenas o versiones modificadas fuera de la operación; no las adopta ni sobrescribe. |
| Restauración en otra carpeta | 29 tablas, UUID/versiones, recuentos y SHA-256 de archivos vigentes coincidentes; publicación anterior legible. |

La comparación MIA conserva la población del legado únicamente como oráculo
de equivalencia para reglas iguales. No activa `all_available` ni un perfil
por defecto en scores nuevos, donde se aplican D03/D05/D06 y P01.

Durante E00 se detectó orden no determinista del legado: etiquetas empatadas y
una última cifra decimal del promedio de un componente podían variar. Se guardó
la primera evidencia y se estabilizó el orden por producción/defecto sin cambiar
curvas, pesos o fórmulas. El corpus operativo es `legacy-v3-real-filters`;
sus peticiones se conservan para reproducir incluso los filtros exactos.

## Recursos medidos

Última reconstrucción medida sobre esta instantánea y este equipo:

- Construcción/verificación/publicación: **118,385 segundos**.
- Repetición sin cambios: **0,219 segundos**.
- Pico de memoria de trabajo del proceso Python real: **991.567.872 bytes**, unos
  **946 MiB**. Pico de memoria privada comprometida: **1.502.756.864 bytes**.
- Almacén Gold, incluyendo versiones retenidas de estas pruebas: **64.390.101 bytes**.
- Manifiestos, justificantes, progreso e intentos propios: **175.593 bytes**;
  mayor archivo de esos metadatos: **15.871 bytes**. No se crea SQLite de control Gold.

Las medidas proceden del trabajador Python, no del pequeño lanzador de un venv
Windows. No son una estimación de todo el servidor ni un benchmark de años de
histórico. El presupuesto de filas limita el trabajo; la planificación por
origen/periodo y exportación del productor por streaming deben completarse antes
de procesar de manera continua una réplica que lo supere.

## Lo que permanece pendiente antes de cerrar el punto 5

1. **P01:** correspondencia modelo → segmento → perfil aplicable, solicitada a
   Calidad. No se ha supuesto vino ligero ni inventado clasificación.
2. **Histórico necesario para referencias:** MP9 conserva fabricaciones antiguas
   cuyas observaciones no están en esta copia de Fenix. El catálogo por sí solo
   no permite calcular su tasa, media o sigma ni acreditar todo el periodo.
3. **Conciliación integral de nuevos scores:** faltan casos con todas las bases,
   referencia, atribuciones y categorías aceptadas. Paridad MIA y pruebas escalares
   no demuestran por sí solas igualdad integral de A/R, DAR y ZC sobre nuevas series.
4. **Antes del piloto continuo:** integración de la exportación periódica desde
   la ingesta y planificación incremental para el volumen real del servidor.
   El consumidor tiene lectura filtrada/en lotes y el escritor sustitución por
   ámbito; la primera orquestación local aún no sustituye esa planificación.

La publicación tiene **921 ventanas pendientes y un pendiente global P01**.
Son estados de disponibilidad de cálculos, no 922 incidencias humanas. Una ventana
puede tener varios motivos: falta de inspeccionadas/teóricos en periodos no
capturados, referencia insuficiente, huecos de capacidad o categoría/cavidad A/R
sin resolver. Los motivos se conservan por separado; una categoría desconocida
no se presenta como si faltaran parámetros de capacidad.

Por estas razones `quality_scores` contiene **cero scores oficiales nuevos** en
esta prueba. No se ha llenado con totales parciales ni valores ficticios. Las
cantidades intermedias y su cobertura sí están materializadas para revisar.

## Límites de operación

No se han tocado contenedores, SQLite, datos ni repositorios remotos de la ingesta
activa. No se han eliminado archivos ni activado VACUUM o limpiezas automáticas.
La restauración probada es local; no equivale a configurar Restic, almacenamiento
independiente o recuperación ante pérdida del servidor. El comando continuo
rechaza este snapshot offline y la app continúa con su lector histórico.

## Ampliación del 06/11/2026: segmentos, contratos v2 y dimensiones

Esta ampliación conserva la evidencia del 02/11 anterior. La autorización D18
permite DAR común provisional para la demostración; no supone aprobación de DAR
específicos ni modifica las condiciones de score completo.

- `Segmentos.xlsx`: 575 códigos admitidos, 563 vigentes y 12 en preparación,
  13 familias. SHA-256 `5e40d5c3a2625576eb8276394175bcd63a7e455a518042b9cbf20b982488e60a`.
  Los 20 modelos observados en esta réplica tienen correspondencia exacta.
- Nuevo destino aislado `var/gold_validation/gold-dimensional-v2-verified`:
  publicación `cf3de8b676d82bd9371bd850353c27ecbd9e9b306b68d6c6dac99556a793dfed`.
  Contrato `quality-gold-dimensional-v2`, huella
  `ddf97f26c8d11edc527db90d63d800afb39d504d2a1566e2e475237daa96d2f3`.
- 35 tablas publicadas y verificadas mediante `validate-gold`: 24 producciones,
  72 asignaciones, 202.931 observaciones de inspección y 33.937 de rechazo.
  Dimensiones: una planta, 22 fechas, 7 máquinas, 20 modelos y 656 defectos.
- 1.318 ventanas y un pendiente global DAR; **1.319 estados pendientes únicos**.
  Principales motivos: cobertura incompleta, falta de inspeccionadas/teóricos en
  ventanas no capturadas y referencias MIA insuficientes. Se corrigió el contador
  operativo para contar estados únicos después de deduplicar.
- Cero scores nuevos en la copia real; la clasificación recibida resuelve P01
  parcialmente, pero no aporta observaciones históricas ni aprobación DAR.
- Ejecución medida: 217,865 s, pico de memoria de trabajo 1.293.418.496 bytes y
  compromiso privado 1.489.682.432 bytes. Hubo pruebas concurrentes; no se utiliza
  esta medida como comparación de rendimiento con el ensayo anterior.
- Suite completa: 136 tests correctos, cobertura combinada 80,84 %; líneas
  84,77 %, ramas 67,14 %. Pruebas controladas de cálculo/publicación de las 35
  tablas, controles negativos, cambios SCD, configuración, reintento y cierre.
  El éxito sintético no acredita conciliación industrial integral.

Informe JSON: `var/gold_validation/contracts-dimensional-v2-report.json`.
El catálogo de contratos puede revisarse en `gold/TABLES.md` y
`gold/contracts-v2.json`. Imagen Gold preparada y CI con extra Gold, sin afirmar
construcción Docker ni ejecución remota. Continúa pendiente G03 y el trabajo
anterior al piloto indicado en `docs/ENTREGAS_GOLD_IMPLEMENTACION.md`.

## Ampliación V3: aplicación y operación

El estado anterior V2 se conserva como evidencia histórica. Se implementan los
bloques de código G03–G05 conforme a D19, con aceptación externa todavía pendiente.

- Contrato `quality-gold-dimensional-v3`, 36 tablas; huella
  `805451372f14ce1390ddeb42b798a88f03c9988d0a4b8ae26978cde0a2c80dcd`.
  Publicación inicial local en `var/gold_validation/gold-application-v3`:
  `2f63ebc93a2b6863f425fa1d3d8a429d3436c30e7e7a97d0aef5a1b72c05eda0`.
  No se modifica el esquema ni la evidencia de la raíz V2.
- API sobre esa réplica: readiness 200, catálogo 200 con 24 producciones y query
  200 con los pendientes que corresponden. No se rellenan scores inexistentes.
- Comparación exacta V2/V3 de `inspection_bases`, `mia_window_exposures`,
  `mia_window_bases`, `ar_window_exposures`, `ar_window_bases`, `zc_window_bases`:
  **MATCH** en claves/cantidades, ignorando únicamente las claves dimensionales
  de las nuevas versiones. Informe: `amounts-v3-parity.json`.
- Exportador productor Python 3.12: **237.872 decisiones** del ledger, 72
  episodios de registro; exportación real por lotes/CAS. Sus seis proyecciones
  auxiliares coinciden por hash de filas con las anteriores: parámetros (24),
  gotas de máquina (7), diccionario (4), evidencia ZC (10.595), ledger (237.872)
  y presencias ZC (24). Conserva `offline_snapshot`, sin inventar descubrimientos.
  Informe: `producer-stream-v3-validation.json`; tiempo de exportación 18,782 s.
- Primera construcción V3: 83,631 s, pico de trabajo 1.270.419.456 bytes y
  compromiso privado 1.384.280.064 bytes. Incluye 1.167 relaciones ZC–producción.
  No constituye una prueba de carga del servidor ni una comparación controlada.
- Suite completa: **147 tests correctos**, cobertura combinada **81,33 %**;
  líneas 85,15 %, ramas 67,92 %. Se verifica el fallo real de Arrow tras MERGE,
  publicación histórica, recálculo explícito, pendientes, parámetros y roles.
  Pruebas adicionales de API tras limitar/cachear el catálogo, tests de frontend,
  TypeScript/build, Ruff/formato y mypy sobre configuración/esquemas.
- Hook/worker de ingesta: 16 tests correctos. Se prepara su imagen opcional,
  sin alterar el worker activo. Las dependencias auxiliares de desarrollo
  preexistentes se conservan y se comprueba la compatibilidad del entorno.

La aplicación Gold se activa mediante configuración explícita. La aprobación
de DAR específicos, conciliación industrial del score completo, pruebas de
servidor/Docker, identidad corporativa y almacenamiento independiente siguen
siendo requisitos externos. Procedimiento completo: `OPERACION_GOLD.md`.

### Cierre de la release V3

La huella final incorpora la dependencia explícita bases ZC → relaciones aceptadas
contador–producción: `cbe74286ffae344459c87d455b8cd0f3b80796c4a0a6f2b3e97f409c9dcb8039`.
Los ensayos de huella `805451…` anteriores permanecen como evidencia previa.

- Raíz final: `var/gold_validation/gold-application-v3-release`; publicación
  `39019b6e2e8158fbb115332bc5643278827616f7cbf9e5f854f8b23f41c49537`.
  Se publican y validan las 36 tablas con el nuevo grafo y huella.
- Backup real: 106 archivos, 32.974.753 bytes, hashes verificados. Restore en
  `var/gold_validation/restore-application-v3-release`, con la misma publicación.
  Readiness, catálogo de 24 producciones y query de score responden 200 también
  desde la copia restaurada; los scores no calculables permanecen pendientes.
- Cantidades MIA/A/R/ZC de las seis tablas comparadas: **MATCH** con V2.
- Prueba de entrada física idéntica en la raíz previa V3: 24 producciones
  reconocidas sin cambios y 1.335 ventanas MIA reutilizadas, cero recalculadas.
  Es evidencia de reutilización correcta; no una promesa de mejorar todo el
  tiempo de publicación, que también incluye lectura, hashing y validaciones.
- Suite final: **149 tests correctos**, cobertura combinada **81,51 %**;
  líneas **85,33 %**, ramas **68,18 %**. Se añaden comprobaciones de catálogo
  histórico en baseline/escenarios y recuperación tras cancelación de consultas.
  Frontend: cuatro tests, TypeScript/build y formato correctos. Ingesta/worker:
  16 tests correctos. Configuración Compose validada, imagen Docker sin construir.
- Los resultados cerrados y cada turno se presentan con su configuración y
  catálogo originales; las explicaciones al usuario no muestran hashes internos.

Informe consolidado: `var/gold_validation/release-v3-verification.json`.
La revisión automática impidió arrancar procesos persistentes de vista previa
(`blocked by policy`). No hay una demo levantada por esta sesión; los comandos
manuales están documentados. La verificación de API se realizó con TestClient,
sin modificar servicios del servidor.
