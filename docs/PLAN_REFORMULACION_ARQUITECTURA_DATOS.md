# Plan de reformulación de la arquitectura de datos y del control de ingesta

Fecha: 23 de octubre de 2026.  
Estado: planificación; implementación, migración, GitHub y despliegue pendientes.  
Ámbito: ingesta Fenix, Raw/Bronze, Silver, controles, recuperación, auditoría y proyecciones de supervisión.

## 1. Objetivo y relación con los planes existentes

Reducir la redundancia y el crecimiento de los controles conservando idempotencia, recuperación ante fallos, trazabilidad y resolución autorizada de contradicciones. Aplicar los principios de Auto Loader, Structured Streaming y Delta Lake mediante nuestra implementación Python/delta-rs, sin depender de la plataforma Databricks.

Este documento complementa [el plan del Delta Lake](PLAN_DELTA_LAKE.md) y [el plan de supervisión](../../Supervision_Pruebas/docs/PLAN_SUPERVISION_INGESTA.md). La copia publicada de `PLAN_DELTA_LAKE.md` mantiene su fuente de edición indicada en su cabecera; este documento es un plan específico nuevo y no modifica aquella copia ni su fuente.

Los contratos de negocio vigentes siguen siendo aplicables: columnas, tipados, claves compuestas, estados y cierres permitidos, reglas de agregación MIA, rendimiento ZC, producciones, asociaciones temporales y decisiones de revisión. No resolver automáticamente asuntos de negocio pendientes como ZF_OK ni reinterpretar registros para facilitar la migración.

Cuando este plan se implemente y valide, sustituirá la conservación indefinida de documentos completos y fotografías repetidas en los controles por el protocolo descrito aquí. La compresión y compactación existentes son antecedentes; no demuestran que este rediseño esté implementado o desplegado.

Crear este documento no autoriza ejecutar la migración, detener procesos, borrar controles, publicar código o desplegar contenedores. La implementación comenzará cuando el usuario lo indique y se realizará por entregas y PR pequeños.

## 2. Diagnóstico y principios

La revisión previa de LISTA_RECH encontró documentos completos de lotes confirmados, intenciones por fila, fotografías históricas de grupos e índices actuales coexistiendo en el control. Como referencia del diagnóstico anterior, se observaron unos 3,8 GB de documentos preparados y aproximadamente 13,8 millones de entradas históricas de grupos frente a 1,08 millones de entradas del índice actual. Estas cifras son antecedentes, no una medición actual ni el tamaño que tendrá el nuevo control.

Principios obligatorios:

- Cada contenido retenido debe justificar una función de recuperación, idempotencia, rendimiento o auditoría.
- Silver contiene el estado aceptado de negocio. El control registra progreso y referencias; no replica íntegramente Silver.
- Una operación pendiente conserva material suficiente para recuperarse. Una operación confirmada conserva un justificante compacto.
- Una incidencia mantiene su evidencia y decisiones, aunque se retiren documentos técnicos de lotes relacionados.
- Distinguir versión de archivo, registro de negocio, operación de escritura e intento de ejecución.
- Los controles pueden crecer con entradas únicas, pendientes reales e índices necesarios. No prometer tamaño constante ni un límite de MB sin mediciones.
- Las claves de idempotencia no caducan automáticamente mientras puedan reaparecer sus entradas.
- La atomicidad de Delta es por tabla en esta arquitectura. SQLite, archivos de auditoría, supervisión y distintas tablas no forman una única transacción.
- No trasladar la redundancia a PostgreSQL, RocksDB o numerosos archivos JSON; corregir primero el modelo y su ciclo de vida.

## 3. Arquitectura objetivo y responsabilidades

Las rutas siguientes son lógicas e ilustrativas. Se resolverán mediante configuración en cada entorno; no crean directorios ni cambian los montajes actuales.

| Componente | Contenido y responsabilidad | Autoridad y recuperación |
| --- | --- | --- |
| Raw/Bronze | Capturas estables de las versiones observadas de CSV, huellas y procedencia. Una captura idéntica dentro de su ámbito no se copia de nuevo. | Evidencia de entrada para reconstrucción. Conservar inicialmente las capturas únicas comprimidas. |
| Silver/Delta | Filas aceptadas según los contratos de origen y tablas derivadas vigentes. | Autoridad del estado aceptado; `_delta_log` determina qué archivos pertenecen a la tabla. |
| Control operativo | Entradas descubiertas, fragmentos/lotes, estados, secuencias, intentos recientes, confirmaciones y referencias. | Autoridad del progreso. Persistente, transaccional y versionado. |
| Trabajo recuperable | Manifiestos y, solo cuando se necesiten, artefactos preparados acotados, por ejemplo Parquet. | Necesarios mientras la operación siga pendiente; no duplicarlos como JSON en SQLite. |
| Índices auxiliares | Estado actual mínimo para localizar claves, huellas y contextos de inspección. | Reconstruibles desde Silver y referencias verificadas. Sin fotografías completas por lote. |
| Evidencias e incidencias | Comparación de aceptado/candidato, origen, motivo, decisiones, autorizaciones y resultados. | Conservar los hechos y decisiones; el escritor de ingesta sigue siendo quien aplica cambios en Silver. |
| Auditoría histórica | Intentos archivados, justificantes, migraciones y decisiones de mantenimiento. | Archivo comprimido consultable, con identidad y verificación; no es un sustituto de un backup. |
| Supervisión | Exportaciones consistentes, métricas y estado del flujo de resolución. | Proyección consultable; no decide por sí sola que una escritura industrial terminó correctamente. |

Organización lógica sugerida bajo la raíz persistente: `bronze/`, `silver/`, `control/`, `work/`, `audit/` y `evidence/`. Antes de cambiar ubicaciones, documentar la correspondencia con las existentes. Mantener independientes develop, main y el ensayo de migración, con identidades y permisos verificables.

## 4. Decisión sobre el motor de control

### 4.1. Decisión inicial: SQLite con un modelo reducido

Mantener SQLite como candidato inicial para el control operativo de cada familia, con una interfaz de repositorio pequeña que permita cambiar el motor si las pruebas lo justifican. No implantar un framework de persistencia genérico ni migrar toda la arquitectura para resolver esta entrega.

Condiciones de uso:

- Archivo en almacenamiento local persistente del host Linux del motor, con bloqueos y durabilidad comprobados; no operar SQLite sobre SMB/CIFS ni desde distintos hosts mediante un recurso de red.
- Un escritor propietario por archivo y transacciones breves. La concurrencia entre familias se conserva al mantener controles separados; no compartir conexiones entre procesos.
- Elegir y verificar explícitamente la política de journal. Si se usa WAL, utilizar `synchronous=FULL`, checkpoints controlados y medición de su tamaño, sin confundir ese checkpoint con el de progreso de la ingesta o el de Delta.
- El contenedor, los backups y el volumen deben respetar fsync, permisos y recuperación. SQLite no protege frente a pérdida del disco sin una política de backup y restauración.
- Supervisión consume exportaciones consistentes; no vuelve a escanear bases activas desde Windows ni mantiene lectores largos que impidan avanzar al WAL.
- Verificar la versión SQLite incluida en Python, sus correcciones y compatibilidad con el runtime objetivo.

Reconsiderar PostgreSQL si aparecen escritores distribuidos que comparten el mismo control, bloqueos sostenidos pese al modelo reducido o necesidad real de coordinación entre hosts. El PostgreSQL de supervisión mantiene su responsabilidad actual; no convertirlo accidentalmente en dependencia crítica de la escritura industrial.

RocksDB solo se evaluaría mediante una comparación representativa si el índice de estado exige un volumen de operaciones clave-valor que SQLite no pueda atender con el presupuesto establecido. Adoptarlo exige resolver también bindings, backups, compacción, recuperación y consultas operativas. No ofrece por sí mismo idempotencia entre archivos y Delta.

### 4.2. Qué utiliza Databricks

Auto Loader registra los archivos descubiertos mediante RocksDB en su checkpoint. Structured Streaming coordina progreso, commits y, cuando corresponde, estado de operadores; el destino Delta aporta sus transacciones. No es una única base de datos que sustituya todo nuestro control.

RocksDB es un motor embebido clave-valor, no una tabla de negocio ni un servicio SQL equivalente a PostgreSQL. Compartir sus principios de separación de estado y datos es el objetivo; reproducir su motor no reproduce la plataforma ni sus garantías extremo a extremo.

## 5. Contrato operativo objetivo

### 5.1. Identidades y registros compactos

| Entidad lógica | Campos mínimos y reglas |
| --- | --- |
| Versión de entrada | Identidad, planta, origen/línea, familia, SHA-256 del contenido, ubicación estable, tamaño, descubrimiento, estado. Nombre/ruta originales son procedencia, no la única identidad. |
| Lote | Identidad estable, entradas y fragmentos mediante referencias, contrato y transformador, destino, estado, contadores y fechas. No incluir todas sus filas. |
| Operación por destino | Identidad persistente del escritor, generación, secuencia creciente, lote, versión previa, versión confirmada, huella del manifiesto y verificación. |
| Intento | Operación, número de intento, inicio/fin, categoría de error y próximo reintento. Diagnóstico grande fuera del registro operativo y sin secretos. |
| Incidencia | Identidad, motivo, referencias al aceptado/candidato, evidencia y estado de revisión. Compatibilidad con las propuestas y decisiones existentes. |
| Índice actual | Solo claves/huellas, ubicación o contexto mínimo necesario y versión Silver de referencia. Reconstruible, con cobertura comprobable. |

Son entidades de contrato, no una obligación de añadir una tabla SQL para cada una. Reutilizar estructuras adecuadas cuando no creen duplicación ni ambigüedad.

La identidad de una captura es por ámbito de origen y fuente; no fusionar entradas de líneas distintas por coincidir sus bytes. La huella de un archivo evita trabajo repetido sobre la misma captura; las claves y huellas de negocio siguen resolviendo solapamientos entre archivos diferentes.

Los fragmentos de archivos grandes deben ser deterministas, respetar la semántica del parser CSV y conservar el progreso por versión de entrada. No utilizar un offset de bytes arbitrario que pueda cortar una fila, una secuencia de codificación o un campo multilinea.

Las fechas técnicas se registrarán con zona explícita, preferiblemente UTC. Esto no modifica la política temporal de las columnas de negocio existente ni resuelve silenciosamente ambigüedades de Fenix.

### 5.2. Captura y preparación

1. Detectar la entrada y capturar una versión estable con verificación del contenido y de la estabilidad observada del original.
2. Publicar la captura durablemente antes de registrarla como disponible. Tratar interrupciones de la captura mediante nombres temporales y finalización verificada.
3. Preparar lotes limitados por filas, bytes y presupuesto real de memoria, conservando la alternancia entre fuentes/orígenes y la atención a llegadas recientes.
4. Guardar un manifiesto compacto con referencias a entradas, fragmentos, contrato, release y resultado esperado. Usar un artefacto preparado acotado si evita depender de recomputación incompatible o costosa.
5. Evitar duplicación entre artefacto preparado, manifiesto, intenciones SQL y archivo de evidencia. Cada representación debe tener una función distinta demostrable.

La captura no puede recuperar una versión que Fenix sobrescriba antes de que sea observada. Ante falta de protocolo de publicación atómica del productor, documentar esta limitación y no presentar una espera de estabilidad como garantía absoluta de completitud.

### 5.3. Escritura, verificación y confirmación

Estados conceptuales del lote: `DISCOVERED → CAPTURED → PREPARED → WRITING → DELTA_COMMITTED → VERIFIED → CONFIRMED`. `RETRY_WAIT`, `UNKNOWN` y `BLOCKED_REVIEW` conservan la relación con la operación original. Los nombres físicos se armonizarán con los estados existentes mediante una migración explícita.

- Un reintento conserva operación, secuencia y entrada; no constituye una nueva escritura de negocio.
- Un único escritor por tabla publica operaciones en orden. No avanzar otra escritura en esa tabla mientras haya un resultado incierto.
- Un despliegue no reinicia la identidad ni la secuencia. Cambiar la generación del escritor requiere un procedimiento explícito y controlado.
- Usar el mecanismo de transacciones de aplicación soportado por la versión fijada de delta-rs y comprobar su comportamiento en replay. `custom_metadata` por sí solo no suprime duplicados.
- Registrar la operación en el commit y verificar la versión, el esquema y las claves/contenidos afectados. Contar filas es necesario, pero no demuestra por sí solo equivalencia de contenido.
- No materializar toda Silver ni guardar su fotografía en cada lote. Verificaciones globales se programan separadamente; verificaciones por lote cubren su ámbito y precondiciones.
- Conciliar resultados de lectura, repetición idéntica, aceptación, filtrado contractual y cuarentena mediante categorías inequívocas; distinguir filas leídas de registros de negocio únicos.
- Confirmar solo tras persistir el justificante y las evidencias necesarias. Si no hay cambios, emitir justificante sin forzar un commit Delta vacío.
- Si la validación posterior al commit falla, detener nuevas escrituras en esa tabla y conservar la evidencia. El commit puede ser visible en Silver; no afirmar que una verificación posterior lo hace invisible. No promocionar derivados dependientes y abrir revisión.
- Varias tablas tienen confirmaciones independientes; los derivados consumen versiones coherentes y confirmadas según sus contratos.

### 5.4. Recuperación y reintentos

| Punto de fallo | Actuación obligatoria |
| --- | --- |
| Captura incompleta | No marcar consumida; retirar solo el temporal incompleto identificado y recapturar. |
| Preparación persistida, sin escritura | Verificar referencias y contrato; reanudar la misma operación. |
| Escritura sin respuesta concluyente | Estado `UNKNOWN`; buscar evidencia transaccional y verificar resultado antes de reintentar. |
| Commit confirmado en Delta, justificante pendiente | Completar conciliación y justificante; no insertar nuevamente. |
| Justificante confirmado, limpieza pendiente | Ejecutar limpieza idempotente exclusivamente sobre material elegible. |
| Archivo preparado perdido o huella incorrecta | Bloquear; reconstruir solo si las entradas y la versión del transformador permiten comprobar equivalencia. |
| Índice auxiliar obsoleto | Invalidarlo y reconstruirlo desde una versión Silver confirmada; no utilizarlo para aceptar filas sin verificación. |
| Archivo de auditoría publicado sin acuse de retirada | Verificar identidad/huella y continuar el archivado sin duplicar ni perder justificantes. |

Propuesta inicial configurable: cinco reintentos tras el intento inicial, espera exponencial con variación aleatoria entre 10 segundos y 5 minutos. Persistir el contador y la próxima fecha; reiniciar el contenedor no reinicia el presupuesto. Agotados los reintentos, pausar ese ámbito y emitir aviso. La reanudación posterior es explícita o responde a una política documentada; nunca a un bucle oculto ilimitado.

Reconciliar antes de reintentar errores de resultado incierto. Errores de integridad y contradicciones contractuales pasan a revisión, no a reintento automático. Los fallos de una familia no paralizan las familias independientes.

### 5.5. Retención y limpieza

Los siguientes valores son propuestas operativas iniciales, no defaults de Databricks. Se entregarán como configuración versionada, con mínimos y condiciones de elegibilidad comprobados.

| Material | Política inicial |
| --- | --- |
| Operaciones pendientes/inciertas y sus dependencias | Sin caducidad automática; alertar por antigüedad y tamaño. |
| Filas preparadas e intenciones detalladas en SQLite | No conservar tras `CONFIRMED` salvo la evidencia mínima de una incidencia; el justificante y sus referencias deben haber quedado durables. |
| Artefactos de trabajo de operaciones confirmadas | Retirada posterior a verificación, justificante durable y ausencia de referencias activas. Limpieza recuperable y por unidades acotadas. |
| Temporales aparentemente huérfanos | Candidatos tras 24 horas, únicamente si el inventario completo y los bloqueos demuestran que ningún proceso u operación los necesita. |
| Intentos detallados | 30 días en control; archivo comprimido hasta un año. Mantener referencias necesarias para pendientes/incidencias. |
| Justificantes de lote | 90 días en control; archivo compacto consultable durante la vida del proyecto. |
| Identidades de entradas consumidas | Sin caducidad inicial mientras puedan reaparecer. La identidad mínima permanece localizable aunque el justificante detallado se archive. |
| Evidencias y decisiones de incidencias | Pendientes sin caducidad; resueltas conservadas en histórico durante la vida del proyecto. |
| Capturas únicas Raw/Bronze | Conservación inicial comprimida; no eliminar ni mover originales de `C:\DataSharing\Fenix`. |
| Historial Delta | Propuesta de 90 días para el log y 30 días para archivos retirados; validar soporte, recuperación y lectores antes de activar limpieza. Los datos activos históricos no caducan por estas ventanas. |

Archivar antes de retirar: publicar archivo comprimido con identidad estable, verificar su huella/contenido, registrar el acuse y solo entonces retirar el detalle elegible del control. El archivado debe sobrevivir a fallos en cada paso y permitir consultas por lote/origen/fecha.

No aplicar borrado por antigüedad a todo `control/`, `work/` o `_delta_log`. La limpieza de archivos Delta utiliza exclusivamente mecanismos soportados, preservando lectores, operaciones y referencias. Al arrancar tras una parada prolongada, recuperar antes de limpiar. Si la antigüedad de un pendiente supera las ventanas previstas, bloquear la limpieza afectada y avisar.

Eliminar filas de SQLite puede liberar páginas para reutilización sin reducir el archivo físico. La migración deberá crear un control reducido y verificado o realizar una compactación controlada con espacio y tiempo suficientes. No programar un `VACUUM` completo recurrente como sustituto del modelo reducido ni borrar el WAL manualmente.

La primera migración conservará todo el historial existente necesario para evidencia/compatibilidad. No aplicará retroactivamente una eliminación masiva de auditoría anterior sin inventario y validación.

## 6. Entregas cortas y secuencia de trabajo

Cada entrega termina en un resultado revisable, pruebas pertinentes y actualización de su estado. Separar PR por responsabilidad; si una entrega resulta grande, dividirla conservando compatibilidad. No avanzar un cambio de protocolo al entorno activo hasta que sus dependencias estén verificadas.

### Entrega 0. Inventario y línea base

- Inventariar tablas e índices SQLite, bytes usados/libres, WAL, tamaños de documentos y referencias cruzadas, sin detener la ingesta ni ejecutar mantenimiento pesado sobre controles activos.
- Clasificar cada estructura: progreso autoritativo, recuperación pendiente, evidencia, histórico o índice reconstruible; registrar quién la escribe y quién la lee.
- Identificar consumidores de formatos actuales: exportación de supervisión, revisiones, publicaciones derivadas y scripts operativos.
- Medir disco, RAM máxima, CPU, duración de lotes, frescura de cada fuente y contención. Los análisis costosos se realizarán sobre una captura consistente aislada con presupuesto explícito.
- Revisar el estado de los cambios previos de retención/compactación y reconciliarlos con este plan; no incluirlos automáticamente como solución definitiva.

**Salida y aceptación:** diagnóstico reproducible, mapa de dependencias y presupuesto de comparación. Sin cambios de esquema ni datos. Registrar los límites de precisión de cada estimación.

### Entrega 1. Contrato del control reducido y compatibilidad

- Definir esquema mínimo, estados, identidades, secuencias, huellas y justificantes con versión explícita.
- Definir la interfaz de control y las condiciones de cada transición, incluyendo no-op, incertidumbre, revisión y archivo.
- Incorporar parámetros de retención/reintento con validación; preservar claves y reglas de negocio.
- Diseñar migración, restauración y lectores compatibles. Un binario incompatible rechaza el control nuevo en vez de adoptarlo silenciosamente.

**Salida y aceptación:** contrato ejecutable probado y formato de ejemplo pequeño; los controles existentes continúan funcionando. La identidad no cambia tras reinicio ni actualización.

### Entrega 2. Capturas, fragmentos y trabajo recuperable por referencia

- Reutilizar y ajustar la captura estable existente; soportar archivos nuevos con timestamp y originales sobrescritos.
- Introducir manifiestos compactos y artefactos preparados acotados cuando sean necesarios. Calcular huellas por streaming y evitar copias completas innecesarias en RAM.
- Establecer fragmentación determinista y finalización durable; persistir progreso de archivos grandes sin duplicar capturas.
- Registrar las referencias que impedirán una limpieza prematura.

**Salida y aceptación:** en sandbox, una versión capturada se procesa de forma reproducible aunque cambie el original; una interrupción de captura/preparación no pierde ni adelanta registros.

### Entrega 3. Identidad de escritura y recuperación Delta

- Implementar el protocolo de operaciones persistentes y transacciones de aplicación donde lo soporte la versión fijada del motor.
- Separar evidencia de commit, verificación y justificante. Cubrir inserciones, modificaciones autorizadas y lotes sin cambios.
- Recuperar antes de aceptar otra escritura en la tabla. Mantener paralelismo entre familias y alternancia de colas.
- Verificar ámbitos afectados sin conservar una fotografía histórica de toda Silver por lote.

**Salida y aceptación:** pruebas de fallos antes/durante/después del commit y antes/después del justificante; replay sin duplicados, mismo resultado y estados explicables. Incluir la respuesta perdida tras un commit efectivo.

### Entrega 4. Primer adaptador completo: LISTA_RECH e índice actual

- Integrar el protocolo nuevo en LISTA_RECH bajo configuración de ensayo.
- Retener un índice actual compacto y reconstruible, sin nuevas fotografías históricas por lote ni copias completas en el justificante.
- Conservar la clave de negocio, contadores, política de contradicciones y relaciones con LISTA_DEFECTOS; no alterar denominadores ni reglas de agregación.
- Emitir contadores y resúmenes con versión para que supervisión pueda comparar ambas implementaciones.

**Salida y aceptación:** igualdad de claves y contenido aceptado, incidencias y totales agregados frente a la referencia. Índice reconstruible; replay y sondeos sin novedades no producen crecimiento repetitivo de documentos.

### Entrega 5. Archivo histórico y retirada segura

- Implementar el archivo comprimido de intentos/justificantes y su consulta mediante índices/manifiestos compactos.
- Retirar detalle confirmado y temporales elegibles con transacciones breves, límites de trabajo y verificación de referencias activas.
- Gestionar checkpoints WAL y observar lectores largos; no forzar limpiezas sobre un escritor o recuperación activos.
- Mantener completas las evidencias pendientes y las decisiones KEEP/REPLACE, incluidos descartes exactos de candidatos y operaciones de revisión.

**Salida y aceptación:** interrupción recuperable de cada fase de archivo/limpieza; ningún justificante o evidencia perdido, ningún duplicado histórico y ninguna retirada de dependencias pendientes.

### Entrega 6. Migración y validación con datos reales en entorno separado

- Construir el control nuevo mediante lectura acotada de una copia consistente del anterior y las evidencias necesarias. No deserializar todos los lotes a la vez.
- Preservar identidades, consumos de entradas, colas, pendientes, decisiones, vínculos con versiones Delta y procedencia de evidencias.
- Clasificar qué detalle se archiva, qué estado se reconstruye y qué material pendiente debe conservarse; no descartar campos por desconocer su función.
- Validar contra capturas reales en directorios nuevos y con origen operativo montado en lectura. Las pruebas deterministas de fallos complementan los ensayos reales.
- Comparar controles, datos, incidencias y rendimiento; estimar el espacio máximo de coexistencia entre viejo, nuevo y archivo antes del cambio.

**Salida y aceptación:** informe de conciliación sin pérdidas ni diferencias inexplicadas, reducción medida de espacio y memoria, y recuperación completa. No borrar el control anterior para que la migración quepa en disco.

### Entrega 7. Piloto controlado y cambio de escritor

- Preparar imagen inmutable, CI y comandos de migración/validación separados del despliegue ordinario. Su ejecución remota requerirá la autorización correspondiente.
- Definir una ventana: pausa ordenada, liberación verificada de escritores, copia consistente, conversión final y activación de un único escritor nuevo.
- Contrastar nuevas llegadas reales, sobreescrituras, reinicio del contenedor, colas históricas e incidencias durante un periodo de observación acordado y medido.
- Conservar el control anterior como copia identificada hasta la aceptación. Si Silver avanza con el nuevo escritor, no restaurar sin más el control viejo: el retorno exige compatibilidad y reconciliación con las escrituras posteriores.

**Salida y aceptación:** piloto sano, fresco y recuperable; procedimiento de retorno probado. El borrado de copias antiguas será una tarea posterior con lista exacta de rutas, verificación y comprobación de espacio recuperado.

### Entrega 8. Extensión al resto de contratos

- Migrar LISTA_DEFECTOS mediante un cambio independiente y verificar grupos de inspección/denominadores compartidos.
- Continuar por LISTA_AR, CONT_ZC, diccionarios, cambios de modelo y catálogos/asociaciones, en PR separados o grupos pequeños compatibles según dependencias.
- Mantener las reglas de cierres, actualizaciones autorizadas, claves y contradicciones específicas. No convertir todos los casos en append simple.
- Actualizar consumidores de versiones y exportaciones consistentemente; ninguna transición exige que el panel abra SQLite activo.

**Salida y aceptación:** cada familia tiene su conciliación y pruebas de recuperación; ningún consumidor mezcla estados o contratos incompatibles. El protocolo compartido evita implementaciones redundantes sin borrar las diferencias de negocio.

### Entrega 9. Supervisión, crecimiento y aceptación de arquitectura

- Incorporar métricas de cola histórica/reciente, latencia captura-confirmación, lotes, reintentos, incertidumbre, incidencias y confirmaciones por familia/origen.
- Medir SQLite, WAL, páginas reutilizables, índices, material pendiente, temporales y archivo. Separar tamaño lógico de ocupación física.
- Publicar métricas mediante resúmenes consistentes y consultas baratas. No escanear todos los controles o Delta en cada scrape; no usar archivo, clave de fila o identificador de incidencia como etiqueta Prometheus.
- Configurar alertas iniciales: entradas recientes sin avance durante tres ciclos de llegada (aproximadamente 30 minutos para fuentes de 10 minutos), resultado incierto persistente, limpieza confirmada atrasada, crecimiento sin causa y disco al 80 %/90 %.
- No alertar por ausencia de nuevos commits cuando no han llegado entradas. Distinguir retraso histórico de falta de atención a datos recientes.
- Documentar presupuesto, backup/restauración, operación, escalado y condiciones para reevaluar SQLite frente a PostgreSQL/RocksDB.

**Salida y aceptación:** observación sostenida que incluya llegadas, intervalos sin novedades y reinicios; resultados comparables con la línea base. No declarar finalizada la solución solo porque el primer archivo sea pequeño.

## 7. Criterios globales de aceptación

- Mismas claves, contenido de negocio, agregados y decisiones autorizadas; pendientes y conflictos íntegramente conservados.
- Reprocesar una captura o recuperar un commit confirmado no duplica datos ni crea otra incidencia idéntica sin necesidad.
- Sondear sin novedades no acumula documentos completos, fotografías ni intentos vacíos indefinidamente. Los indicadores efímeros se actualizan o agregan.
- El crecimiento está explicado por entradas únicas, pendientes, evidencia e índices actuales; la retención mantiene acotado el detalle operativo confirmado.
- Los controles nuevos ocupan sustancialmente menos que los anteriores sobre una carga comparable, con cifras medidas. No aceptar una reducción basada únicamente en trasladar la misma duplicación a otro directorio.
- No superar los límites de CPU/RAM vigentes del contenedor ni consumir todo el espacio del host durante coexistencia/migración. El presupuesto concreto se fija con la entrega 0; no aumentar recursos para ocultar redundancia.
- Identificar la procedencia de los cambios en consumo: control, índice, Bronze, evidencia y auditoría. La relación control/Parquet es una señal, no una regla universal de calidad.
- No introducir lecturas completas por lote o scrape, ni un nuevo cuello de botella que deje fuentes sin atender.
- Recuperar tras pérdida del proceso y demostrar restauración de backups en un entorno distinto. No afirmar protección frente a pérdida física del disco solo por superar pruebas de reinicio.
- Almacenamiento y configuración quedan fuera de las imágenes. Actualizar código nunca borra, reinicia o migra silenciosamente los datos.

## 8. Seguimiento

| Entrega | Estado inicial | Evidencia necesaria para cerrarla |
| --- | --- | --- |
| 0. Inventario | Pendiente | Diagnóstico, mapa de consumidores y línea base. |
| 1. Contrato | Pendiente | Esquema/versionado/transiciones probados. |
| 2. Capturas y referencias | Pendiente | Replay y recuperación de captura/preparación. |
| 3. Escritura y recuperación | Pendiente | Matriz de fallos y commits sin duplicación. |
| 4. LISTA_RECH | Pendiente | Equivalencia e índice reconstruible. |
| 5. Retención | Pendiente | Archivo y limpieza interrumpibles sin pérdida. |
| 6. Migración aislada | Pendiente | Conciliación real y mediciones de recursos. |
| 7. Piloto | Pendiente de implementación y autorización | Informe remoto y retorno probado. |
| 8. Resto de fuentes | Pendiente | Aceptación por contrato/consumidor. |
| 9. Supervisión y cierre | Pendiente | Alertas y observación sostenida documentadas. |

Actualizar esta tabla con fechas, revisiones y resultados verificables; distinguir código escrito, CI superada, migración ensayada y despliegue aceptado.

## 9. Referencias técnicas

Consultadas el 23 de octubre de 2026. Las decisiones de este plan adaptan sus principios a nuestro sistema; las funciones de Databricks no se presuponen disponibles en delta-rs.

- [Auto Loader: configuración de producción, RocksDB y checkpoint](https://docs.databricks.com/aws/en/ingestion/cloud-object-storage/auto-loader/production).
- [Structured Streaming: checkpoints y recuperación](https://docs.databricks.com/aws/en/structured-streaming/checkpoints).
- [Estado de archivos: diferencia entre procesado y confirmado](https://docs.databricks.com/aws/en/sql/language-manual/functions/cloud_files_state).
- [Delta y escrituras idempotentes](https://docs.databricks.com/aws/en/structured-streaming/delta-lake#use-foreachbatch-for-idempotent-table-writes).
- [Auto Loader: archivos sobrescritos y deduplicación](https://docs.databricks.com/aws/en/ingestion/cloud-object-storage/auto-loader/faq).
- [Auto Loader: limpieza de archivos procesados](https://docs.databricks.com/aws/en/ingestion/cloud-object-storage/auto-loader/clean-source).
- [Historial y retención Delta](https://docs.databricks.com/aws/en/tables/history).
- [delta-rs: propiedades de commit y transacciones de aplicación](https://delta-io.github.io/delta-rs/latest/api/transaction/).
- [SQLite: escenarios de uso y concurrencia](https://www.sqlite.org/whentouse.html).
- [SQLite: WAL, almacenamiento local y checkpoints](https://www.sqlite.org/wal.html).
