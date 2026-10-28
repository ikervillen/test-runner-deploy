# Corrección del atraso de asociaciones — 28 de octubre de 2026

## Medición de partida

Auditoría de solo lectura de la publicación confirmada a las 13:48:27 de Madrid:
1.908.826 observaciones únicas en el ledger y 1.019.109 pendientes. No son
incidencias individuales que requieran otras tantas decisiones humanas.

| Causa comprobada | Filas | Tratamiento |
| --- | ---: | --- |
| Cuatro ramales AV21/AV23 retenidos por fechas iniciales 1900/2000 distintas | 1.010.517 | Admitir la intersección de vigencias; validar cobertura completa de cada muestra. Conservar las fechas originales. |
| Ausencia de episodios automáticos de presencia ZC | 8.060 | Derivar desde la raíz ZC y la máquina física; asociar la misma rueda a producciones concurrentes sin duplicar contadores. |
| Espacio final en el código de modelo del catálogo | 131 | Comparar sin espacios exteriores en la derivación; preservar texto e IDs del diccionario. |
| TIPO 6, Rechazo Trampilla, sin zona en su descripción | 222 | Regla versionada confirmada por el usuario: ZF si no hay indicación contraria. La zona explícita tiene prioridad. |
| Dos producciones AV31 del mismo modelo compatibles con el mismo AR | 177 | Mantener separadas las producciones. No elegir la primera ni duplicar el evento. Requiere evidencia física que discrimine la parte de la tándem. |
| Dos AR AV23 terminan dos segundos después de la salida ZF | 2 | Conservar fechas: AR hasta 16/10 06:48:53, producción hasta 06:48:51. No introducir tolerancias silenciosas ni recortar minutos-cavidad. |

Los números son la foto de partida: la ingesta continúa y la validación posterior
debe informar sus propias versiones fuente, fecha y recuentos. Una corrección de
topología puede descubrir incompatibilidades de evidencia antes ocultas; no se
consideran resueltas sin comprobar el recálculo.

## Entregas independientes

1. Corregir las dependencias de topología. Reproducir desde Bronze las candidatas
   retenidas y cerrar únicamente las verificadas, con evento de auditoría.
2. Derivar presencia ZC. El final ZC se toma de la raíz MP9, no del cierre global
   ZF. Versionar cierres tardíos y respetar correcciones humanas.
3. Corregir comparación de modelos, interpretar el tipo confirmado y exportar
   recuentos por fuente, origen y estado. Una preparación sin observaciones
   asignadas no se confunde con una observación pendiente.
4. Recalcular en el servidor y conciliar los recuentos con el ledger confirmado.
   Mostrar expresamente los grupos que necesitan evidencia o criterio operativo.
5. Medir duración de cada fase y evolución de las colas MIA antes de ajustar el
   rendimiento. Conservar entradas pendientes, confirmaciones e idempotencia.

## Criterios de cierre

No borrar observaciones ni controles para poner el indicador a cero. La suma de
estados por origen debe conciliar con las filas fuente, y los pendientes del
resumen deben coincidir con el ledger. No hace falta una intervención por fila:
se corrigen causas comunes o se aprueba una regla para el grupo afectado.

## Presupuesto de cola tras la primera validación

La publicación del 28/10 a las 16:20:26 contiene 1.923.312 observaciones y solo
53 pendientes, conciliados con el ledger. Defectos y rechazos tienen cero
observaciones publicadas sin producción; quedan 24 AR sin zona, 21 ambiguos,
2 cierres que exceden la producción y 6 transiciones de rueda.

La cola de archivos es distinta de esos pendientes de asociación. Se midieron
397 segundos para un turno de rechazos y 340 para defectos, mientras que sus
lotes de ocho archivos consumían 28 y 46 segundos respectivamente. El coste de
explorar/verificar los archivos históricos domina el turno. El runtime usará
tres lotes acotados por turno para amortizar ese coste y atender más orígenes.
`INGESTA_MIA_BATCHES_PER_TURN` permite configurar el presupuesto; se mantiene la
validación de Config y la rotación por origen existente.

Los lotes se ejecutan secuencialmente: cada uno conserva sus límites de ocho
archivos y 4 MiB, lectura contextual, intención y confirmación propias. No se
triplica la memoria del lote ni se aumenta el número de escritores por tabla.
La parada sigue atendiendo el límite entre lotes y el trabajo confirmado se
recupera sin reinicializar el lago ni los controles.
