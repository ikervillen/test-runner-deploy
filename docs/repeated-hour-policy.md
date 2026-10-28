# Hora local repetida y correcciones de observaciones MIA

## Política operativa aprobada

Los CSV mantienen sus fechas locales originales. Para la hora duplicada por el
cambio de horario de otoño, Silver conserva la muestra de la exportación más
reciente y sustituye la anterior. Es una decisión deliberada de pérdida de una
de las dos ocurrencias; no reconstruye instantes UTC ni suma ambas muestras.

La regla `repeated_hour_latest_export_v1` se aplica a `LISTA_RECH`,
`LISTA_DEFECTOS` y `CONT_ZC`. El calendario corresponde a
`INGESTA_SOURCE_TIMEZONE`, por defecto `Europe/Madrid`. No afecta a otras horas.
La fecha de negocio de cada fila sigue siendo la del CSV.

## Selección y límites

- Solo se usan capturas completas con contenido verificado contra su SHA-256
  en Bronze; nunca fragmentos parciales de un archivo.
- La hora de exportación debe pertenecer a la hora local repetida y concordar
  con la modificación del archivo. La precedencia usa esa modificación absoluta
  y, como desempate, el instante de primera captura. Una exportación antigua que
  llega tarde no puede reemplazar una posterior.
- Solo se consideran muestras próximas a su exportación. Si la evidencia de
  orden es contradictoria o los datos son inválidos, no se fuerza la sustitución.
- La sustitución se limita a una fuente, un origen Fenix y las fechas de muestra
  verificadas. El resto de horas y líneas se conserva.

## Escritura, recuperación y auditoría

El escritor habitual de cada fuente ejecuta la política bajo su bloqueo. Antes
de escribir conserva una intención duradera, con alcance y huellas del estado
anterior y esperado. Una transacción Delta reemplaza la muestra completa; se
comprueban la versión, el identificador de operación y el contenido resultante.
Ante una interrupción se reconcilia esa intención antes de continuar la ingesta,
sin repetir el commit ya confirmado.

Los conflictos cubiertos por la muestra reemplazada se cierran con un justificante
de la regla aplicada. Las claves y los conteos auxiliares se actualizan sobre el
resultado verificado. Los registros de sustitución necesarios para recuperar una
operación incierta se retiran del plan una vez confirmada; Bronze y el historial
Delta conservan su evidencia según sus políticas de retención.

## Sustitución manual de una revisión MIA

Cuando cambia `INSPECC`, la clave técnica puede cambiar sin que cambie la
observación física. Se permite elegir el candidato homólogo de la misma fuente,
origen, fecha, molde, ramal, máquina, cavidad y, en defectos, código de defecto.
La autorización sustituye la fila anterior en una transacción; no inserta ambas
ni suma sus contadores. La decisión se comprueba y audita antes de cerrar.

Si un grupo de defectos necesita varias correcciones, su denominador permanece
pendiente mientras sus filas sigan mostrando cifras distintas. La selección no
permite sustituir arbitrariamente otra máquina, cavidad o defecto. Las decisiones
obsoletas se rechazan y requieren evaluar la evidencia actual.
