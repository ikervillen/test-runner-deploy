# Ingesta Delta Lake

**GitHub/CI/CD y Docker:** [guía de operación Windows/WSL2](docs/GITHUB_CICD.md).
El workflow sustituye la prueba de Nginx de `ikervillen/test-runner-deploy`: CI en PR y
despliegue automático independiente por rama `main`/`develop`, con volúmenes persistentes,
imagen por digest, validación aislada, drenaje y recuperación. El runner existente se reutiliza.

Proyecto independiente de `calidad_serie`. El código, configuración de ejemplo, pruebas,
scripts y workflow de esta ingesta residen aquí. Los datos, controles operativos y entorno
virtual permanecen fuera del checkout.

Implementación v2 del **19/10/2026**: se recupera `ID_MAQUINA` en MP9 y se incorporan
`SIP_MAQUINA_MOLDEO` y `SIP_LINEA_ZF` para distinguir partes de tándem y sus ramales.
La resolución utiliza los catálogos vigentes para cualquier tándem,
sin ramales fijados por nombre. Una producción sin cierre válido continúa abierta;
no se exige consulta SQL ni otra señal adicional. ZF_OK sigue pendiente de aclaración.
El código está implementado y probado con datos sintéticos; **no se han reconstruido ni
migrado los datos reales v1**. Los conteos históricos descritos debajo corresponden a nueve
fuentes. El alcance actual añade los dos catálogos físicos, hasta once fuentes, y derivados
v2. Contratos y operación en [11B/11C](docs/entregas/11bc_catalogos_derivados.md).
Alcance y entregas pequeñas en
[11A, identidad física](docs/entregas/11a_identidad_tandem.md); el contrato completo permanece
en el plan, sección 17.14. No ejecutar una adopción/evolución automática sobre las tablas v1.

Las entregas 01 a 11 implementan las nueve fuentes seleccionadas: los diccionarios `SIP_MODELO`, `SIP_DEFECTO`,
`SIP_DEFECTO_STATUS` y `SIP_DEFECTO_AR`, los registros temporales de `MP9_CAMBIO_MODELO`
y las observaciones `LISTA_DEFECTOS`, `LISTA_RECH`, `LISTA_AR` y `CONT_ZC`, además del catálogo de producciones
y la asociación temporal producción–ramal derivada:
CSV Fenix → copia estable Bronze →
validación del contrato → tabla Delta Silver compartida de planta. No modifica el backend,
el frontend, las fórmulas ni las rutas activas de la aplicación.

El [índice de entregas](docs/ENTREGAS.md) divide el trabajo en doce incrementos. El plan
arquitectónico detallado se mantiene en
[PLAN_DELTA_LAKE.md](../calidad_serie/fuentes-referencia/PLAN_DELTA_LAKE.md); no se duplica aquí.
Cada entrega tiene su contrato y aceptación resumidos en `docs/entregas/` para revisar cambios
pequeños. Al preparar un repositorio independiente, publicar este proyecto; no incorporar
los CSV reales o sus directorios vecinos.

## Entorno y pruebas

Python 3.12; dependencias y hashes resueltos en `uv.lock`. Se emplean delta-rs, Arrow,
SQLite y un bloqueo de filesystem local. Los contratos actuales no requieren Polars;
cualquier dependencia adicional se evaluará en este proyecto, separado del entorno de la app.

En PowerShell, desde este directorio y con `uv` instalado:

```powershell
./scripts/setup.ps1
$env:UV_PROJECT_ENVIRONMENT = Join-Path $env:LOCALAPPDATA 'Ingesta_Delta_Lake/envs/dev-0.1'
uv run --locked python -m pytest --cov=ingesta_delta_lake --cov-report=term-missing --cov-fail-under=85
uv run --locked ruff check .
uv run --locked ruff format --check .
```

En Linux, usar una ruta de entorno externa al checkout y `uv sync --locked --python 3.12`.
Las pruebas solo usan entradas sintéticas y directorios temporales. El workflow preparado
prueba Windows/Linux cuando este directorio sea la raíz del repositorio GitHub; todavía
no constituye una ejecución de CI ni un despliegue remoto.

## Ejecución controlada

1. Copiar `config/example.toml` (modelos), `config/sip_defecto.example.toml` (defectos),
   `config/sip_defecto_status.example.toml` (procedencias) o
   `config/sip_defecto_ar.example.toml` (tipos de AR) o
   `config/mp9_cambio_modelo.example.toml` (entradas/salidas de modelo) o
   `config/lista_defectos.example.toml` (defectos MIA) o
   `config/lista_rech.example.toml` (inspeccionadas/rechazos MIA) o
   `config/lista_ar.example.toml` (tramos de avisos/rechazos) o
   `config/cont_zc.example.toml` (contadores de zona caliente),
   `config/sip_maquina_moldeo.example.toml` (máquinas físicas) o
   `config/sip_linea_zf.example.toml` (ramales y máquinas) a una
   ubicación de configuración local y ajustar sus rutas
   absolutas. Las carpetas de entrada/orígenes deben existir. Esta entrega solo admite
   `environment = "test"` o `"development"`; producción, UNC/SMB y destinos solapados se rechazan.
2. Seleccionar `family = "SIP_MODELO"`, `"SIP_DEFECTO"`, `"SIP_DEFECTO_STATUS"`, `"SIP_DEFECTO_AR"`,
   `"MP9_CAMBIO_MODELO"`, `"LISTA_DEFECTOS"`, `"LISTA_RECH"`, `"LISTA_AR"`, `"CONT_ZC"`,
   `"SIP_MAQUINA_MOLDEO"` o `"SIP_LINEA_ZF"`;
   si se omite, se mantiene el primero
   por compatibilidad con la entrega 01. Cada ejecución procesa una familia. Para defectos,
   procedencias y tipos de AR de la muestra Aiala usar `encoding = "cp1252"` explícito;
   no se adivinan codificaciones.
   Disponer en las carpetas de origen los CSV de la familia seleccionada. Se reconocen nombre
   fijo, fecha anterior `YYYYMMDD`, fecha sin hora `DDMMYYYY` y nombre de revisión
   `DDMMYYYY_HMMSS`/`DDMMYYYY_HHMMSS`. Una marca desconocida de la familia se reporta, no se ignora.
3. Invocar explícitamente la operación de datos:

```powershell
./scripts/run_ingest.ps1 -ConfigPath 'C:/Ingesta_Delta_Lake_ensayo/config.toml'
```

El setup solo instala código/dependencias. La ingesta se ejecuta únicamente con el comando
anterior o `python -m ingesta_delta_lake ingest --config <ruta-absoluta>`.
La salida es JSON. Códigos: 0 aplicado/sin cambios, 2 lote bloqueado, parcial o con dependencias,
3 fallo técnico/configuración/recuperación, 4 ausencia de archivos de la fuente seleccionada.
Cada familia se procesa por separado, con sus dependencias previamente confirmadas.
Para v2, adaptar todas las configuraciones a las mismas raíces **nuevas** de ensayo;
los ejemplos antiguos no son una autorización para reutilizar tablas/control v1.
En catálogo y asociaciones activar `topology_enabled = true` y declarar
`[line_code_by_origin]` para todos los orígenes, además de `[line_by_origin]`.
El orden de carga y las condiciones de compatibilidad se detallan en la ficha 11B/11C.

## Datos y control

- Bronze: `<bronze_root>/<planta>/<origen>/<familia>/<fecha UTC recepción>/<sha256>.csv`.
  Cada captura se valida mediante dos lecturas y metadatos, con límite/reintentos. No se
  altera el original. Esta estabilidad observada no reemplaza una señal de archivo completo
  del productor; esa señal y el almacenamiento remoto deberán validarse antes de producción.
- Silver: `<silver_root>/<planta>/sip_modelo`, tabla Delta con los dos campos acordados:
  `id_modelo_fenix: int64 NOT NULL` y `codigo_modelo_botella: string NOT NULL`.
- Silver de defectos: `<silver_root>/<planta>/sip_defecto`, con `id_defecto_fenix` y
  `codigo_aviso_rechazo` int64, `descripcion` y `descripcion_corta` string; todos no nulos.
  Cada ID y cada código son únicos; las descripciones pueden repetirse entre códigos.
- Silver de procedencias: `<silver_root>/<planta>/sip_defecto_status`, con los seis campos
  indicados en el contrato: ID/código, ambas descripciones y códigos originales de origen/zona.
- Silver de tipos de AR: `<silver_root>/<planta>/sip_defecto_ar`, con ID, código de tipo,
  descripción y código numérico de agrupación.
- Silver MP9: `<silver_root>/<planta>/mp9_cambio_modelo`, siete campos de negocio en el orden
  original, seguidos de `origen_fenix`. La planta queda fijada por su ubicación/configuración;
  origen e ID discriminan cada registro, incluso cuando coinciden ID entre líneas.
- Catálogo derivado: `<silver_root>/<planta>/catalogo_producciones`, nueve campos según
  sección 16 del plan; composición y cobertura operativa en su control separado.
- Observaciones MIA: `<silver_root>/<planta>/lista_defectos`, los 18 campos originales
  seguidos de `origen_fenix`; control propio `control-lista_defectos.sqlite`.
- Inspeccionadas/rechazos MIA: `<silver_root>/<planta>/lista_rech`, nueve campos originales
  seguidos de `origen_fenix`; control propio `control-lista_rech.sqlite`.
- Tramos AR: `<silver_root>/<planta>/lista_ar`, doce campos originales seguidos de
  `origen_fenix`; control propio `control-lista_ar.sqlite`.
- Contadores ZC: `<silver_root>/<planta>/cont_zc`, 17 campos originales seguidos de
  `origen_fenix`; control propio `control-cont_zc.sqlite`. Velocidades DECIMAL(12,2).
- Control de modelos: `<control_root>/control.sqlite`; el resto de familias usa
  `<control_root>/control-<familia en minúsculas>.sqlite`, por ejemplo
  `control-sip_defecto_status.sqlite`. Usan WAL y synchronous FULL. Conservan intentos,
  recepciones, lotes, intenciones por fila, confirmaciones e incidencias con evidencia íntegra.
  Las tablas, versiones confirmadas, operaciones y controles de las nueve fuentes y del catálogo son
  independientes. No se migra ni renombra el control de la entrega 01. Las raíces pueden
  compartirse mediante configuraciones distintas; el bloqueo Silver sigue siendo común.
- Escritor: bloqueo cooperante en `<silver_root>/.writer.lock`. Todos los ejecutores de
  este proyecto deben respetarlo. No dar permiso de escritura al consumidor u otros programas.

El nombre no identifica un registro: se comprueban archivos sobrescritos aunque ya se haya
visto su ruta, y un nombre nuevo con el mismo contenido no duplica Delta. Las recepciones
se auditan separadamente. Una copia de estado nueva tampoco modifica el diccionario.

Los diccionarios se leen con el snapshot Delta, se valida su correspondencia uno a uno y se insertan
solo novedades resueltas bajo bloqueo. No se ejecuta overwrite, no se borran ausencias y
no se invoca el escritor si no hay inserciones. Estos diccionarios pequeños se comparan
en memoria; no constituye una estrategia de escaneo completo para futuras tablas masivas.

En los diccionarios, un inválido o conflicto bloquea toda la unidad antes de escribir: no se publican las filas
válidas del mismo lote como si este estuviera completo. Se conserva lo aceptado y el candidato
para revisión. Si Delta confirma y falla el acuse SQLite, el siguiente arranque localiza el
`operation_id` en su historial y contrasta el contenido esperado antes de confirmar sin repetir.
Si no se puede demostrar el resultado, se detiene. La evidencia preparada no se limpia.

Un control de otra planta/ruta no se reutiliza; otra base de control no adopta silenciosamente
una tabla existente. Modificaciones/commits externos, mantenimiento o migraciones requieren
un procedimiento separado: esta entrega no los reconoce como operaciones propias. No borrar
`control.sqlite`, archivos Delta o copias pendientes para arreglar una incidencia.

## Reglas específicas de SIP_DEFECTO

El CSV conserva su estructura de cinco campos, con separador final opcional. Solo se publican
los cuatro acordados. `MEDIDA` no interviene en el contenido de negocio: cambiarlo no actualiza
la tabla ni crea conflictos. Las descripciones pierden únicamente espacios en los extremos;
se preservan acentos y contenido. No se completan vacíos ni se convierten enteros mediante float.

El marcador completo `NO USAR`, sin distinguir mayúsculas y con prefijo numérico opcional
seguido de `:`, excluye la entrada si aparece en cualquiera de las dos descripciones. Una frase
más larga no se filtra. Tras validar la estructura, la exclusión precede a las validaciones de
tipo/identidad de los candidatos. No se prohíbe el código cero por sí mismo. Cada exclusión y
su raw original se conservan en `exclusions`/`exclusion_evidence`, incluso si otra fila bloquea
el lote. Se comprimen repeticiones exactas conservando `duplicate_count`; MEDIDA no forma
parte de la identidad de negocio de la exclusión. No se borra un aceptado que después aparezca
como `NO USAR`.

Un cambio de ID/código o de cualquiera de las dos descripciones exige revisión. Se guardan
la candidata completa, los valores aceptados relacionados y su evidencia; nunca se elige por
orden de archivo ni se sobrescribe. Un conflicto o inválido bloquea la unidad completa para
esa familia. Las otras fuentes se ejecutan y recuperan de forma independiente.

La salida y el control distinguen `rows_read`, `valid_rows`, `consolidated_repetitions`,
`excluded_rows`, `excluded_entries`, `invalid_rows`, `unique_pairs`, `matched_entries`,
`conflict_entries` y `new_entries`. Las filas leídas corresponden a revisiones únicas por
origen/hash; todas las recepciones se auditan aunque no se vuelvan a parsear. Las entradas
únicas se cuentan después de la normalización acordada. `new_entries` son candidatas a
inserción; un lote bloqueado puede tener candidatas independientes sin publicar ninguna.
`inserted_this_attempt` es la escritura efectiva, incluida la recuperación que haya escrito
en ese intento. Un replay confirmado informa cero novedades e inserciones; mantiene las
estadísticas de lectura de su lote original sin volver a transformarlo. Un archivo solo con
exclusiones se audita sin crear una tabla Delta vacía.

La evidencia de aceptación se recoge en [la ficha de entrega 02](docs/entregas/02_sip_defecto.md).

## Reglas específicas de SIP_DEFECTO_STATUS

El CSV tiene siete campos, en el orden Fenix: ID, CODIGO, DESCRIPCION, ORIGEN,
DESCRIPCION_CORTA, ZONA, ACTIVO; el separador final es opcional. El esquema Silver conserva
en este orden `id_status_fenix`, `codigo_status`, `descripcion`, `descripcion_corta`,
`codigo_origen` y `codigo_zona`. Los cuatro códigos son int64 exactos y las dos descripciones
son strings no vacíos; todos son no nulos. Solo se retiran espacios en los extremos del texto.

`ACTIVO` no filtra entradas ni participa en el contenido o hash de negocio. Un valor vacío
es válido, incluido el de `Muestreos ZC`. Los nombres `Delfoz ZC` y `Delfoz ZF` permanecen
como vienen. Se conserva cualquier valor int64 válido de ORIGEN/ZONA sin imponer los códigos
observados como enumeración cerrada ni asignar categorías no confirmadas.

Cada ID corresponde a un código único y cada código a una sola entrada. Las descripciones
pueden repetirse. La futura interpretación de `LISTA_AR.STATUS` utilizará `codigo_status`,
no el ID; esta entrega incorpora el catálogo, sin implementar aún la ingesta/unión de AR.

Los cambios de cualquiera de los seis campos conservados se registran con candidata, valores
aceptados relacionados, `changed_fields` y evidencia íntegra en revisión. No se sobrescribe ni
se borra por ausencia. Una estructura incompatible no activa evolución automática de esquema.
Repeticiones o cambios solo de ACTIVO se auditan sin commit Delta; los fallos se reconcilian
con el mismo motor de intenciones y operaciones de las entregas anteriores.

La evidencia se recoge en [la ficha de entrega 03](docs/entregas/03_sip_defecto_status.md).

## Reglas específicas de SIP_DEFECTO_AR

El CSV contiene ID, CODIGO, DESCRIPCION y AGRUPACION, en ese orden, con separador final
opcional. El esquema Silver conserva `id_tipo_ar_fenix`, `codigo_tipo_ar`, `descripcion`
y `codigo_agrupacion`, todos no nulos. Los tres campos numéricos son int64 exactos; la
descripción debe ser texto no vacío y solo pierde espacios en los extremos. Se preservan
acentos y nombres originales, incluido `Rechazo molde NP` para el código 5.

Cada ID corresponde a un código único; descripciones y agrupaciones pueden repetirse sin
fusionar tipos. AGRUPACION se mantiene como código original, sin inferir etiquetas o limitar
los valores a los cuatro grupos observados. El grupo 2 puede contener avisos y rechazos de
trampilla. La futura relación de eventos será `LISTA_AR.TIPO → codigo_tipo_ar`, no mediante
el ID. Esta entrega no incorpora todavía los eventos de LISTA_AR ni modifica sus referencias.

Se contrastan los cuatro campos entre candidatos y contra lo publicado. Cualquier cambio de
identidad, descripción o agrupación deja candidata, valores aceptados y campos discrepantes
para revisión. Un inválido/conflicto bloquea la unidad completa. Ausencias no borran filas;
repeticiones o cambios de nombre no generan commits. Se mantienen primera evidencia,
captura estable y recuperación de fallos antes/después del commit y de su confirmación.

El ejemplo de configuración y la [ficha de entrega 04](docs/entregas/04_sip_defecto_ar.md)
documentan ejecución y evidencia local.

## Reglas específicas de MP9_CAMBIO_MODELO

El lector comprueba las diez columnas Fenix y proyecta exactamente `ID`, `ID_MODELO`,
`FECHA_ON`, `FECHA_OFF`, `STATUS`, `ID_ST1_REL`, `ZF_OK`, en ese orden. `ID_ST1_REL` se lee
de la octava columna Fenix, antes del N_MOD descartado. `ID_MAQUINA`, `ID_PREPARACION` y
`N_MOD` se conservan solo en la evidencia original; sus cambios no escriben datos de negocio.
`origen_fenix` es la octava columna de la tabla Silver y completa la identidad de escritura.

Primero se debe ejecutar la ingesta de modelos sobre las mismas raíces/planta. MP9 lee su
snapshot aceptado y comprueba control, versión y metadatos sin modificar el diccionario.
Un modelo aún no recibido genera dependencia, nunca un modelo inventado. Cada ZF exige una
referencia ZC del mismo origen y modelo; referencias a sí mismo, otra zona o modelo quedan
en revisión. No se busca por cercanía temporal ni se cruza a otra línea por coincidencia de ID.

En ensayo es obligatorio `timestamp_policy = "local_naive_test_v1"`. Las fechas se leen
como día/mes/año y horas locales literales, y se almacenan como Arrow timestamp(us) sin zona /
Delta timestamp_ntz. No se convierten a UTC ni se resuelven horas DST por suposición.
La política productiva de zona/horas ambiguas o inexistentes sigue pendiente de confirmación;
esta representación de ensayo no acredita su resolución.

La única actualización automática es un cierre único validado: FECHA_OFF de nulo a fecha
no anterior al inicio, con todos los demás campos compatibles. El MERGE afecta solo esa
columna y empareja por origen/ID. Copias abiertas no reabren registros; dos cierres distintos,
cambios de modelo/inicio/zona/vínculo o ZF_OK van a revisión. ZF_OK nunca inventa una fecha.
Un conflicto se mantiene bloqueado aunque una copia posterior omita una alternativa;
su resolución manual/regla de precedencia aún no tiene comando de edición en esta entrega.

MP9 puede publicar unidades válidas independientes en un solo commit y dejar otras pendientes.
La salida `complete=false` y `PARTIAL` impide presentar esa publicación como íntegra; CLI
devuelve 2. Si no hay datos publicables, informa `WAITING_DEPENDENCIES` o `BLOCKED` según
el motivo. Una versión inválida con ID interpretable impide publicar alternativas de ese ID.
Las reglas conservadoras de los cuatro diccionarios no cambian.

`change_intents` registra INSERT/CLOSE, origen/ID, antes/después, hashes de los siete campos,
evidencia y confirmación. Mantiene inmutable la intención de primera inserción y audita cada
cierre. El lote fija snapshot MP9, revisión Bronze y snapshot/hash del diccionario de modelos.
Un commit incierto se reconcilia por operación, versión y contenido antes de repetir.

Las dependencias conservan su candidata y evidencia Bronze: se reintentan en la siguiente
invocación aunque el CSV actual ya no las contenga. `dependency_events` conserva observación,
escalado y resolución. Se informa de la antigüedad; `pending_review_after_hours` permite fijar
un umbral explícito de ensayo (24 h en el ejemplo, pendiente de acuerdo operativo productivo).
Sin configurar, no se aplica escalado automático. Superarlo no descarta datos ni inventa una
relación; las recepciones independientes continúan. Los conflictos de negocio no se resuelven
por reintento. No hay un servicio de avisos ni scheduler en esta entrega.

Los registros fuente se conservan separados y no se les añade un ID de producción ni se
asignan aún defectos/contadores. La [ficha de entrega 05](docs/entregas/05_mp9_cambio_modelo.md)
recoge pruebas, conciliación real y pendientes de AV32/2007.

## Catálogo de producciones

La entrega 06 deriva desde MP9 y modelos Silver aceptados, sin leer los CSV vivos ni ejecutar
ingestas implícitamente. Copiar `config/catalogo_producciones.example.toml` fuera del checkout,
ajustar raíces y declarar la correspondencia autorizada `[line_by_origin]`. Familia:
`CATALOGO_PRODUCCIONES`; política horaria de ensayo `local_naive_test_v1`.

```powershell
python -m ingesta_delta_lake catalog build --config 'C:/Ingesta_Delta_Lake_ensayo/catalog.toml'
python -m ingesta_delta_lake catalog search --config 'C:/Ingesta_Delta_Lake_ensayo/catalog.toml' --model '2001/477'
```

La búsqueda no exige fechas ni escribe/deriva datos. Devuelve las producciones conocidas del
código de botella, en orden de inicio más reciente, con ID estable, límites, composición y
cobertura. El código de botella se resuelve desde la versión fijada de SIP_MODELO; no se copia
como otra correspondencia editable en la tabla. La consulta marca `STALE` si avanzaron las
fuentes, su estado de revisión, la configuración de líneas o un commit aún no confirmado.
Lee siempre la última publicación confirmada; no combina la última tabla con un control anterior.
Frescura significa coincidencia con los snapshots Silver aceptados, no detectar CSV aún no ingeridos.

El identificador canónico es `fenix-production-v1:<planta>:<origen>:<ID_ZC>`, por ejemplo
`fenix-production-v1:AIALA:AV32:2003`. No depende de modelo, fechas, cierre, orden o archivo.
No se fusionan raíces por compartir botella. El inicio procede de ZC; el final solo se fija
cuando todas las ZF conocidas relacionadas están cerradas y no hay una incidencia que lo impida.
Una ZC sin ZF conserva final y vínculo nulos, con cobertura `PENDING_ZF`. Una ZF sin cierre
puede tener cobertura `VALID`: estar abierta según los datos no equivale a falta de relación.

El esquema físico es `id_produccion`, `planta`, `origen_fenix`, `linea`, `id_zc_raiz`,
`id_modelo_fenix`, `fecha_inicio_zc`, `fecha_fin_zf`, `ID_ST1_REL`; esta última referencia
pertenece a la misma producción y no significa predecesora. La composición validada queda
en control junto con los snapshots y regla. El cierre completa la misma fila e identidad.
Repeticiones no escriben; nuevas composiciones tras un cierre, cambios de invariantes o
límites incompatibles requieren revisión, sin reabrir ni cambiar el final arbitrariamente.

`control-catalogo_producciones.sqlite` conserva `production_intents`, `publications` y
`composition`, con intención antes del commit y publicación confirmada atómica en SQLite.
Se fijan versiones y hashes de ambas fuentes y las incidencias conocidas; las versiones
necesarias para recuperar operaciones/publicaciones no deben eliminarse con mantenimiento.
Un fallo entre tablas se reconcilia o se reanuda desde esas versiones antes de derivar novedades.
No se presupone una transacción conjunta entre MP9 y catálogo. La fuente pendiente de
confirmación técnica bloquea su nueva derivación; una fuente parcial puede producir un catálogo
parcial explícito, conservando las candidatas no publicables en diagnósticos.

La conciliación actual identifica 24 candidatas, publica 23 —17 cerradas/seis sin cierre—
y mantiene AV32/2007 pendiente. La [ficha de entrega 06](docs/entregas/06_catalogo_producciones.md)
recoge el detalle y los límites. No se han migrado IDs del histórico de la aplicación ni
conectado todavía este catálogo a su interfaz. Esta primera implementación compara snapshots
en memoria; no acredita latencia o capacidad para millones de producciones. Esas medidas y
la optimización de consultas se mantienen para las entregas de consumo/operación.

## Reglas específicas de LISTA_DEFECTOS

El contrato `lista_defectos_v2` conserva los 18 campos en orden original: `FECHA`, `MOLDE`,
`LINEA`, `COD_MAQ_ZF`, `COD_DEF`, `INSPECC`, `RECH`, `RECH1`–`RECH10`, `CAVIDAD`.
Se añade únicamente el ámbito técnico `origen_fenix`. Cantidades y molde son int64 no
negativos; los tres códigos son int64 originales. `LINEA` es el ramal. Cavidad es texto
nullable: vacío → nulo; `8F`, `99Z` y el texto literal `null` se conservan y son distintos.
Fechas día/mes/año con `local_naive_test_v1`, timestamp(us) sin zona, solo para ensayo.

La clave completa incluye origen, fecha, molde, ramal, máquina, defecto, inspeccionadas y
cavidad, con igualdad explícita entre nulos. Coincidencias no escriben. Rechazos distintos
para la misma clave se conservan en revisión, sin reemplazar lo aceptado. Otra comprobación
sin INSPECC detecta revisiones del contador: pasar de 96 a 97 no inserta ambas cantidades.
Una tercera agrupa sin defecto ni INSPECC para validar la base compartida entre defectos.
No se elige máximo/mínimo ni se resuelve una contradicción por orden o última llegada.
RECH y los diez canales se mantienen intactos, sin imponer que su suma sea RECH.

```powershell
python -m ingesta_delta_lake ingest --config 'C:/Ingesta_Delta_Lake_ensayo/defectos.toml'
python -m ingesta_delta_lake inspection-bases --config 'C:/Ingesta_Delta_Lake_ensayo/defectos.toml' --origin AV23
```

La segunda operación es de solo lectura y devuelve una base por contexto resuelto, sin crear
otra tabla Delta ni multiplicar INSPECC por el número de defectos. Máquina, origen, fecha,
molde, ramal y cavidad diferentes son contextos separados, incluso con igual cantidad.
El total significa **inspecciones por contexto**, no botellas físicas únicas entre máquinas.
No se implementa con `sum(distinct INSPECC)` ni se suman defectos para deducir botellas únicas.

Unidades válidas independientes pueden insertarse mientras las contradictorias quedan
íntegras en revisión; `PARTIAL`/`BLOCKED`, `complete=false` y CLI 2 hacen visible la cobertura.
Las bases aceptadas afectadas por una revisión no se presentan como resueltas. Un error sin
contexto interpretable marca las bases del origen como no resueltas. `complete` expresa el
estado global de la familia, también al filtrar la consulta por origen. No hay todavía
comando de resolución humana ni precedencia automática para correcciones.

`observation_intents` fija clave completa, firma, grupo, candidata/evidencia y hash antes del
append. Una revisión semántica es única; recepciones/evidencias de diferentes lotes conservan
su trazabilidad. `inspection_groups` en SQLite registra las bases de cada lote confirmado.
Los reintentos recuperan el lote preparado y contrastan operación, versión y contenido antes
de volver a escribir. El lector de bases exige un snapshot técnicamente confirmado.

La [ficha de entrega 07](docs/entregas/07_lista_defectos.md) recoge la conciliación de las
33.937 filas reales. No se conecta esta salida al score: LISTA_RECH sigue siendo la fuente del
denominador preferente, incorporada por separado en la entrega 08. La comparación de tasas/score y la asociación a
producciones quedan para sus entregas. La comparación actual en memoria acredita esta
muestra; no acredita capacidad/latencia de un histórico masivo ni operación productiva.

## Reglas específicas de LISTA_RECH

El contrato `lista_rech_v1` conserva `FECHA`, `MOLDE`, `LINEA`, `COD_MAQ_ZF`, `INSPECC`,
`RECH`, `RECH_AUTO`, `CAVIDAD`, `FECHA_R`, en ese orden, y añade el origen técnico al final.
Cantidades y molde son int64 no negativos; ramal/máquina permanecen códigos int64. CAVIDAD
es texto nullable. Ambas fechas usan día/mes/año y la política de ensayo `local_naive_test_v1`;
FECHA_R vacía sigue nula. Se conservan ceros, rechazos automáticos superiores a inspeccionadas,
fechas automáticas anteriores/distintas a FECHA y RECH_AUTO positivo sin FECHA_R. No se suman
los dos contadores de rechazo ni se completa una fecha a partir de la otra.

La clave es origen/fecha/molde/ramal/máquina/INSPECC/cavidad, con igualdad de nulos. Se compara
la fila completa para detectar cambios de RECH, RECH_AUTO o FECHA_R; una fecha antes vacía
no se completa automáticamente. La firma sin INSPECC detecta posibles revisiones del contador.
En todos esos casos se conserva el aceptado y la candidata íntegra para revisión. Una
contradicción inicial no tiene ganador y no se resuelve por omitir después una alternativa.
El ámbito afectado permanece pendiente; las filas válidas independientes pueden publicarse
con cobertura parcial explícita. No hay todavía comando de corrección humana.

```powershell
python -m ingesta_delta_lake ingest --config 'C:/Ingesta_Delta_Lake_ensayo/rech.toml'
python -m ingesta_delta_lake inspection-bases --config 'C:/Ingesta_Delta_Lake_ensayo/rech.toml' --origin AV23
```

Copiar/ajustar antes `config/lista_rech.example.toml`. La lectura exige un snapshot confirmado,
no escribe datos y devuelve una base por firma resuelta. El total es diagnóstico de inspecciones
por contexto; **no es el denominador ya calculado del score ni botellas únicas de planta**.
El histórico filtra primero y toma un máximo por timestamp/ramal de máquina antes de sumar
ventanas. Esa agregación posterior no permite elegir un máximo entre revisiones contradictorias
de fuente. Las nueve columnas permanecen intactas para permitir los filtros de consumo.

El protocolo de lotes, recuperación, intenciones y consulta se reutiliza mediante adaptadores
acotados, conservando el comportamiento y las identidades de operación de LISTA_DEFECTOS.
Los controles/tablas de ambas fuentes siguen separados. No se suma INSPECC después de cruzarlo
con cada defecto: se obtiene la base una sola vez para los contextos pertinentes. La
[ficha de entrega 08](docs/entregas/08_lista_rech.md) recoge la conciliación de 202.931 filas,
las reglas de revisión y el alcance de las comprobaciones de compatibilidad. La integración
con filtros enriquecidos, producciones, tasas y score completo queda para consumo/operación.

## Reglas específicas de LISTA_AR

Copiar/ajustar `config/lista_ar.example.toml` fuera del código. Antes, ingerir los tres
diccionarios AR sobre la misma planta/raíces: `SIP_DEFECTO`, `SIP_DEFECTO_STATUS` y
`SIP_DEFECTO_AR`. Después:

```powershell
python -m ingesta_delta_lake ingest --config 'C:/Ingesta_Delta_Lake_ensayo/ar.toml'
```

Se conservan `COD_AR`, `FECHA_ON`, `MAQ_IS`, `CAVIDAD`, `STATUS`, `TIPO`, `MODELO`,
`FECHA_OFF`, `COLOR`, `PROCESO`, `MOLDE`, `ID_Agrupación`, en este orden, más el origen
técnico. CAVIDAD, FECHA_OFF y agrupación admiten nulo; los textos obligatorios se mantienen
como texto, con espacios extremos retirados. MODELO no se convierte a ID Fenix ni a número;
conserva letras, `/` y ceros iniciales. Fechas locales bajo `local_naive_test_v1` solo para
ensayo; enteros int64 exactos, sin fracciones ni rellenos.

La identidad incluye origen y todos los campos salvo FECHA_OFF. Agrupación sola, o agrupación
y STATUS, no identifican una fila. Otro inicio o componente de identidad es otro tramo candidato,
sin fusionarlo por contigüidad, agrupación o descripción. Un cierre único válido completa
exclusivamente FECHA_OFF de la misma fila, comparando nulos de cavidad/agrupación correctamente.
Dos cierres distintos quedan íntegros en revisión; versiones abiertas no reabren un cerrado.
No se cierra por recibir el inicio de otro tramo, por ausencia ni por timeout.

Las referencias se buscan por **código**, no por ID interno del diccionario:
COD_AR → `codigo_aviso_rechazo`; STATUS → `codigo_status`; TIPO → `codigo_tipo_ar`.
STATUS expresa procedencia; no es zona, gravedad ni estado abierto/cerrado. Los códigos
originales permanecen en Delta y las etiquetas siguen en sus diccionarios. No se necesita
reincorporar catálogos descartados ni exigir SIP_MODELO para conservar MODELO textual.

Faltas de código se guardan como dependencias con candidata/evidencia para reintento, incluso
si desaparecen del CSV vivo. Se reevalúan al invocar la ingesta; no hay scheduler o alertas.
Una revisión abierta en un diccionario bloquea conservadoramente la interpretación de esa
familia hasta aclararla. Los pendientes técnicos del diccionario exigen recuperarlo primero.
Se fijan versiones/hashes de las tres referencias para reconciliar una operación preparada
aunque después avancen. Los snapshots necesarios no se deben eliminar por mantenimiento.

`ar_intents` conserva INSERT/CLOSE, identidad completa, antes/después, hashes, evidencia y
confirmación. `issues`, `issue_evidence` y `dependency_events` registran revisión, recepciones
y resolución/escalado. `PARTIAL`, `WAITING_DEPENDENCIES` o `BLOCKED` distinguen cobertura;
no se da éxito íntegro con pendientes. Se revisan también las referencias de filas ya aceptadas
que el CSV actual omita: pueden permanecer conservadas con interpretación pendiente explícita.
El umbral opcional de 24 h del ejemplo es de ensayo; escalar no inventa ni descarta datos.

La [ficha de entrega 09](docs/entregas/09_lista_ar.md) recoge 177 tramos reales, incluidos los
dos de AV21/39954, los dos de AV32/19607 y cinco sin agrupación, con replay sin commits.
No calcula minutos-cavidad, no recorta intervalos a turnos/producciones y no conecta el score.
La normalización del control y optimización de escaneo observadas en la entrega 08 siguen
pendientes antes de producción; esta entrega no las sustituye.

## Reglas específicas de CONT_ZC

Copiar/ajustar `config/cont_zc.example.toml` fuera del código. No requiere un diccionario nuevo:
la procedencia y MAQ_IS conservan sus valores originales; no se aplica la equivalencia de
ID_LINEA_ZC del Excel histórico a los códigos Fenix.

```powershell
python -m ingesta_delta_lake ingest --config 'C:/Ingesta_Delta_Lake_ensayo/zc.toml'
python -m ingesta_delta_lake zc-yield --config 'C:/Ingesta_Delta_Lake_ensayo/zc.toml' --origin AV23 --machine 23 --start '2026-10-13T23:50:00' --end '2026-10-14T00:00:00'
```

Se conservan los 17 nombres y su orden original, incluidos contadores documentados como no
utilizados. Enteros int64 exactos, contadores no negativos e indicador SALIDA_MAQ_IS 0/1.
VEL_A/VEL_B son Arrow decimal128(12,2) / Delta DECIMAL(12,2): validar punto decimal y rango,
sin float ni redondeo. `80.010` equivale a `80.01`; `80.011` queda en revisión. Las fechas
usan día/mes/año y `local_naive_test_v1` solo para ensayo. No se imputan campos vacíos a cero.

La clave es origen/MAQ_IS/FECHA. Todos los contadores, velocidades e indicador son contenido;
cambiar cualquiera bajo la misma clave genera revisión íntegra, sin sumar, sobrescribir o
publicar otra versión activa. Otra máquina/fecha/origen con iguales cantidades es independiente.
La publicación es append de novedades validadas. `measurement_intents`, `issues` e
`issue_evidence` conservan intención/confirmación y candidatas con evidencia. La recuperación
reconcilia operación, snapshot y contenido antes de repetir, aunque cambie el CSV vivo.

La consulta `zc-yield` es de solo lectura sobre Delta/control confirmados y devuelve resumen
y desglose por origen/máquina. Selecciona finales de muestra en `(start, end]`, con límites
locales ISO sin zona, y usa el periodo nominal de diez minutos. No crea ceros, interpola,
extiende una muestra por un hueco ni afirma continuidad del histórico: la cobertura es la
de observaciones disponibles, con primer/último final y conteos resueltos/elegibles/excluidos.

Rendimiento: **100 × SUM(RUEDA) / SUM(TEORICO_A + TEORICO_B)** sobre el mismo conjunto resuelto
con teórico positivo. No se promedian porcentajes, calculan diferencias de contadores ni se
cambia rueda por expulsor según SALIDA_MAQ_IS. Los teóricos cero se conservan y se excluyen
de ambos lados del cálculo, con conteo y rueda excluida visibles; sin denominador elegible,
el porcentaje es nulo. Los puntos superiores al 100 % se conservan y señalan, sin recorte.
El diagnóstico devuelve sumas enteras, fracción del porcentaje y presentación a cuatro decimales.
No añade porcentajes a la tabla de origen. Revisiones conocidas excluyen de la métrica sus
observaciones aceptadas afectadas; un error sin clave interpretable afecta al origen. PARTIAL
hace visible que el resultado es incompleto, también al filtrar otro origen sano.

El rendimiento pertenece a máquina/periodo, con una sola observación física por clave. La
entrega 11 incorpora presencias ZC revisadas y consulta conjunta de producciones tándem sin
duplicación física. No se reparte la rueda por A/B ni por producción.
La [ficha de entrega 10](docs/entregas/10_cont_zc.md) documenta los 827 registros reales,
los 135 puntos superiores al 100 %, reintentos y límites de compatibilidad. Fórmula numérica
contrastada; asignaciones, turnos y contribución al score se validarán antes de conectar la app.

## Asociación temporal producción–ramal

La [entrega 11](docs/entregas/11_asociacion_temporal.md) añade propuestas modelo–molde–cavidad,
vigencias ZF aceptadas y atribución por periodo completo de diez minutos, con preparación
confirmada, conflictos explícitos y sin prorrateos. Las nueve fuentes conservan sus esquemas.
Las fechas de la primera/última evidencia no se convierten automáticamente en vigencias.

Copiar `config/asociacion_produccion_ramal.example.toml` a configuración externa, compartiendo
raíces y mapeos con el catálogo. Configurar opcionalmente `association_decisions` con un JSON
revisado dentro de `control_root`; sin él se generan candidatas, sin aceptar episodios ficticios.
La ficha documenta el formato de decisiones, evidencias y revisión consecutiva con hash previo.

```powershell
python -m ingesta_delta_lake associations build --config 'C:/Ingesta_Delta_Lake_ensayo/asociaciones.toml'
python -m ingesta_delta_lake associations inspect --config 'C:/Ingesta_Delta_Lake_ensayo/asociaciones.toml'
python -m ingesta_delta_lake associations inspect --config 'C:/Ingesta_Delta_Lake_ensayo/asociaciones.toml' --source-id '<hash físico>'
python -m ingesta_delta_lake associations inspect --config 'C:/Ingesta_Delta_Lake_ensayo/asociaciones.toml' --production '<id 1>' --production '<id 2>'
```

Los episodios aceptados se publican en `asignacion_produccion_ramal`, con ID independiente
de fechas, referencias al catálogo y validación de no solapamiento. Las correcciones conservan
antes/después y se recuperan tras fallos. Omitir una decisión no borra episodios ni resuelve
revisiones. El control conserva snapshots y registro de atribuciones inmutables reproducibles.
Una consulta es de solo lectura; si cambiaron fuentes/decisiones devuelve `STALE`, sin reconstruir.
Las presencias máquina–producciones ZC se revisan separadamente de las vigencias ZF.

Validación real: **237.872 observaciones físicas y 16 candidatas**, sin modificar originales
ni tablas fuente. Cero episodios reales aceptados por faltar revisión de límites efectivos;
MIA/ZC conservan su cobertura pendiente. AR: 118 filas atribuidas, 59 pendientes. AV31 y
AV32/2007 siguen sin resolver. El replay no escribe en Delta ni cambia artefactos. La ficha
incluye los conteos y ubicación de evidencia; las vigencias aceptadas se prueban con casos
sintéticos, sin aprobar datos de planta automáticamente.

## Límites de estas entregas

Las nueve fuentes, catálogo y asociación temporal tienen implementación local de ensayo.
Faltan las decisiones reales de vigencia/preparación/presencia ZC, la integración y validación
del score, minutos-cavidad, Docker, el Programador de tareas y despliegues. La derivación
temporal recalcula actualmente todo el conjunto y su inspección carga la auditoría completa;
optimización incremental y latencias/concurrencia productivas siguen pendientes.
GitHub y despliegue se abordarán más adelante por decisión del usuario. No se han creado
repositorios remotos, PR, tareas programadas o ramas. La CI preparada solo usa datos
sintéticos; la conciliación con Aiala es local y sus datos no forman parte del proyecto.

Fuentes técnicas: [API DeltaTable](https://delta-io.github.io/delta-rs/latest/api/delta_table/),
[metadatos de transacción](https://delta-io.github.io/delta-rs/latest/api/transaction/),
[checkout](https://github.com/actions/checkout) y
[setup-python](https://github.com/actions/setup-python).
