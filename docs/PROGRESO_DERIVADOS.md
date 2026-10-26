# Publicación continua de derivados

## Entrega 1 · Planificación

La cola pendiente ya no impide ejecutar asociaciones. El worker reserva una
ejecución con la cadencia configurada, aunque continúen llegando archivos.
Las esperas normales se identifican como `CADENCE`, no como una dependencia de
MIA bloqueada. Los lotes de origen siguen acotados y conservan sus confirmaciones.

## Entrega 2 · Versiones y publicaciones independientes

Cada cálculo fija versiones confirmadas de sus fuentes. Puede leer esas versiones
aunque después se confirme otra, manteniendo las comprobaciones de identidad,
contrato y revisiones. El catálogo fija también la publicación que contiene su
composición; no mezcla filas antiguas con una composición posterior.

Con topología habilitada se confirman primero los episodios producción–ramal,
utilizando catálogo, modelos y topología física. Después se calcula la atribución
de observaciones. Un fallo de este segundo cálculo no revierte la primera
publicación. Ambos alcances conservan manifiesto y confirmación propios, dentro
del protocolo existente de un único escritor y recuperación de operaciones.

El registro de decisiones vigente es común a ambos alcances: una corrección
humana aceptada con evidencias MIA tampoco puede revertirse al recalcular la
parte física. Una publicación física no acredita cobertura de observaciones.

La comprobación de incidencias se indexa una vez por cálculo. La búsqueda por
clave u origen conserva las reglas anteriores y evita recorrer todas las
incidencias para cada observación. El índice es temporal y no se persiste.

## Entrega 3 · Supervisión verificable

Se conserva la última ejecución real, incluido el fallo que motiva un reintento.
Se exportan por separado lectura, escritura Delta y publicaciones derivadas, con
las versiones utilizadas. El diagnóstico de solo lectura incluye el último
commit Delta y la distribución de motivos de las incidencias de origen.

## Entrega 4 · Incidencias

Las contradicciones de origen mantienen sus evidencias y resolución autorizada.
No se descartan ni corrigen automáticamente por actualizar el software.
Los candidatos pendientes del catálogo y las filas sin atribución se presentan
como pendientes de datos, separados de fallos técnicos y esperas programadas.

## Entrega 5 · Aceptación

Pruebas: cola permanente en ambos modos de worker, recuperación tras interrupción
del cálculo pesado, cierres tardíos, correcciones humanas con evidencias físicas
y MIA, versiones fijadas y equivalencia de bloqueos con el índice temporal.
CI ejecuta contratos y transacciones Delta reales en Windows y Linux, seguida de
pruebas de la imagen y de su ciclo de activación Docker.

El despliegue conserva CSV, tablas, controles y evidencia histórica. La aceptación
en servidor debe contrastar commits, publicaciones, errores, cola y recursos.
Los informes de supervisión verifican aislamiento y recuperación en su entorno
de pruebas, sin escribir sobre el lago industrial.
