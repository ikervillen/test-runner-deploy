# Operación del control reducido, formato 3

Estado: código local y pruebas funcionales disponibles; registrar separadamente los resultados de CI y del piloto real. Diseño en [el plan de reformulación](PLAN_REFORMULACION_ARQUITECTURA_DATOS.md).

## Qué cambia

- Los controles existentes se leen sin convertirlos durante un despliegue. La migración requiere una operación explícita y un único propietario del lago.
- Los controles nuevos del runtime se crean en formato 3. Un cliente actualizado admite formatos 2 y 3; el despliegue rechaza volver a una imagen que no admite el formato persistente nuevo.
- Las preparaciones pendientes se publican comprimidas y con huella antes de registrar su referencia. SQLite mantiene el identificador, estado y referencia, sin otra copia de todas las filas.
- Tras verificar Delta y confirmar el consumo, el control conserva un justificante compacto y retira las intenciones detalladas. Una limpieza interrumpida se reanuda; un fallo de mantenimiento se publica como aviso sin anular el resultado industrial confirmado.
- Los sondeos sin cambios conservan una observación actual, sin generar indefinidamente recibos por sondeo. Las versiones únicas y las claves de consumo permanecen identificables.
- LISTA_RECH y LISTA_DEFECTOS tienen una cola explícita de pendientes y consultas indexadas de identidades. El historial consumido no se materializa completo en Python en cada turno.
- Los CSV MIA grandes admitidos por el presupuesto de captura se fragmentan en registros CSV completos. La confirmación del archivo padre espera a todos los fragmentos. El límite de captura sigue siendo explícito: archivos superiores a él requieren ajustar el presupuesto o su entrega; nunca se ignoran silenciosamente.
- El índice actual de inspecciones permanece. Las fotografías históricas solo se retiran cuando su equivalencia con las preparaciones archivadas queda demostrada.
- El historial previo se archiva comprimido durante la primera migración. Las incidencias, evidencias, autorizaciones y decisiones no se descartan.
- Los cambios no alteran la política de negocio ni reconstruyen o borran tablas Delta. No activan VACUUM ni cambian todavía las retenciones de las tablas; esa activación requiere una operación transaccional independiente que mantenga sincronizados control y versiones.

## Compatibilidad y configuración

La imagen anuncia `control_payload_format=3`. La identidad del control y las operaciones de negocio existentes se conservan. El marcador de entorno y los contratos de origen siguen vigentes.

| Variable | Valor inicial | Función |
| --- | --- | --- |
| `INGESTA_CONTROL_RECEIPT_DAYS` | 90 | Detalle reciente de justificantes antes de archivarlo. |
| `INGESTA_CONTROL_ATTEMPT_DAYS` | 30 | Detalle operativo reciente de intentos. |
| `INGESTA_CONTROL_ARCHIVE_DAYS` | 365 | Retención de intentos archivados, salvo referencias necesarias para incidencias pendientes. |
| `INGESTA_CONTROL_ARCHIVE_COMPRESSION_LEVEL` | 1 | Compresión gzip de auditoría, entre 1 y 9; prioriza la velocidad del mantenimiento. La identidad y verificación se calculan sobre el contenido canónico, independientemente del nivel. |
| `INGESTA_CONTROL_RETRIES` | 5 | Reintentos tras el intento inicial; no se reinicia el contador al recrear el contenedor. |
| `INGESTA_CONTROL_FREE_RESERVE_BYTES` | 1073741824 | Reserva mínima de disco durante la reconstrucción, revisada por bloques. |

Estas variables son parámetros del proceso; la configuración de despliegue debe inyectarlas expresamente si se cambian los valores iniciales. Mantener las mismas políticas por entorno/release y registrar sus cambios. El mantenimiento ordinario trabaja en unidades limitadas, inicialmente 128 entradas antiguas por pasada horaria.

Las capturas Bronze actuales continúan en su formato compatible. Esta entrega comprime el trabajo recuperable y el archivo de auditoría; no convierte ni elimina capturas Bronze antiguas. La compresión general y la caducidad de Raw son tareas de almacenamiento independientes y no condiciones para reducir SQLite.

## Publicación y ensayo remoto

1. Integrar cambios mediante PR pequeños. La validación completa y la imagen inmutable deben superar CI antes de desplegar develop.
2. Desplegar la imagen nueva conservando el volumen. No ejecutar una migración implícita en el arranque.
3. Ejecutar el workflow registrado `deploy.yml`, rama `develop`, con `operation=inspect`, para guardar tamaños, versiones, filas y estado de supervisión.
4. Ejecutar `operation=trial`. El coordinador detiene ordenadamente el único escritor y crea un volumen con nombre `<volumen-develop>-trial-<identificador>` y etiquetas de propósito/instancia.
5. Cada auxiliar de ensayo monta el lago operativo en `/lake` **solo lectura**, sin canales de revisión, y el volumen nuevo en `/trial` con escritura. Los controles resultantes quedan en `/trial/<FAMILIA>/`; las tablas y CSV industriales no son destinos de escritura del ensayo.
6. Guardar el informe por familia: bytes antes/después, archivo de auditoría, comprobaciones de equivalencia y total de cambios SQL en el origen, que debe ser cero.
7. Al terminar, el coordinador reanuda la misma release. Si un auxiliar no confirma su terminación, se declara recuperación necesaria y no se inicia otro escritor.

El ensayo pausa temporalmente el procesamiento, pero las llegadas Fenix pueden seguir acumulándose y se descubrirán después. No se copia otra vez todo el SQLite antiguo para el ensayo: se lee consistentemente y se construye el control reducido en el volumen separado.

Los informes quedan como artefactos de Actions `control-maintenance-<run_id>`. El volumen de ensayo se conserva hasta aceptar los resultados; registrar su nombre exacto antes de una posterior retirada. No usar limpieza general de volúmenes.

## Sustitución de los controles

Después de aceptar el ensayo:

1. Ejecutar `operation=reformulate`, inicialmente con `remove_backup=false`.
2. El coordinador drena el escritor, verifica el estado y reconstruye cada control en un archivo nuevo, bajo los bloqueos de mantenimiento.
3. El original se conserva como `<control>.before-compaction`. La reformulación lee las tablas originales por bloques y compara las huellas del contenido transformado y de la auditoría; reconstruye los índices y verifica completamente páginas y claves foráneas del destino. No recorre además los índices antiguos que va a descartar: en el servidor ese recorrido redundante de todo el SQLite domina el tiempo de mantenimiento. Una página original ilegible hace fallar la lectura; no se acepta un destino incompleto ni una diferencia de contenido.
4. Un informe `<control>.compaction-report.json` identifica el control, el original, los tamaños y las pruebas. Una repetición reconoce controles ya migrados y continúa con los restantes.
5. El coordinador comprueba las tablas y reanuda el escritor. Contrastar nuevas llegadas, ausencia de duplicados, revisiones, versiones Delta y exportaciones de supervisión.
6. Tras aceptar el piloto, repetir `operation=reformulate` con `remove_backup=true`. Para controles ya migrados, esto verifica la identidad y el informe antes de retirar únicamente su copia anterior. No reconstruye las tablas ni borra el control activo.

Conservar la copia anterior no autoriza restaurarla sin más después de que Silver haya avanzado. El retorno requiere una imagen compatible y reconciliar los commits posteriores; una restauración ciega del control antiguo podría repetir trabajo o interpretar incorrectamente las decisiones.

## Reintentos e incidencias

Una familia con errores transitorios conserva contador y próxima fecha de intento. Agotado el presupuesto, o detectado un fallo de integridad, queda pausada. Las familias independientes continúan. El estado se exporta para supervisión como `stats.control_storage.retry_state`.

Una ejecución manual deliberada puede reintentar esa familia tras corregir la causa. Si termina correctamente, limpia el presupuesto de fallos. Reiniciar el contenedor por sí solo no la desbloquea. Las contradicciones de negocio que el contrato ya clasifica como incidencias mantienen su flujo de revisión y no se convierten automáticamente en errores técnicos.

Los fallos de archivo/limpieza se publican en `stats.control_storage.maintenance_error`. Deben revisarse aunque los commits industriales continúen correctamente. Los datos de cola se exportan en `stats.input_progress`; las aplicaciones consumidoras podrán presentarlos sin abrir SQLite activo.

## Comprobaciones de aceptación

- Mismas versiones y filas Delta antes y después de la migración, salvo escrituras posteriores ordinarias identificadas.
- Misma identidad, evidencias y decisiones; pendientes recuperables; ningún segundo escritor.
- Reducción medida del SQLite y separación del tamaño de índices actuales, auditoría y copias anteriores. El espacio de una copia anterior no se recupera hasta retirarla.
- Nuevos lotes confirmados sin documentos de filas ni fotografías repetidas en SQLite.
- Replay, reinicio, sondeos sin novedades y recuperación tras pérdida de respuesta sin datos duplicados.
- Frescura de cada fuente, cola histórica/reciente, estado de reintentos y errores de mantenimiento visibles en las exportaciones y los informes.
- Las mejoras de gráficos o avisos externos de Grafana no se presuponen desplegadas por actualizar esta imagen. SMTP continúa pendiente de configuración autorizada.
