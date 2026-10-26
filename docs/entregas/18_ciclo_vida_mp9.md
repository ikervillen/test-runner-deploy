# Entrega 18 — Actualizaciones automáticas del ciclo de vida MP9

Decisión operativa del 26/10/2026. Sustituye la consideración de las diferencias
`ZF_OK=0/1` como conflicto descrita en las entregas históricas. Mantiene el esquema
`mp9_cambio_modelo_v2` y la identidad `(origen_fenix, ID)`.

## Regla de consolidación

Si el origen, ID, modelo, máquina, zona, fecha de entrada y relación ZC–ZF coinciden:

- Una `FECHA_OFF` vacía se completa automáticamente con el único cierre válido observado.
- `ZF_OK` avanza de 0 a 1 automáticamente. Puede hacerlo en el mismo lote que el cierre.
- Ambas operaciones actualizan la misma fila mediante MERGE; no insertan otro registro.
- Los archivos históricos abiertos o con cero no reabren ni degradan el estado confirmado.
- `ZF_OK=1` no inventa una fecha de cierre ni cambia el significado de STATUS.

Dos cierres distintos, fechas inválidas o cambios de campos inmutables siguen en revisión.
No se elige arbitrariamente el cierre máximo, el archivo más reciente ni el último renglón.

El cierre o confirmación de una fila ya aceptada no necesita volver a admitir su relación:
una incidencia pendiente del padre ZC no impide actualizar estos dos campos de su ZF.
Las nuevas inserciones conservan la validación de modelo y de relación ZC–ZF completa.

## Pendientes anteriores y trazabilidad

En cada turno MP9 se recuperan las dependencias y los antiguos conflictos que únicamente
contenían estas progresiones. Sus evidencias Bronze se verifican por ruta y SHA-256,
aunque el CSV original haya desaparecido. Los conflictos reales continúan bloqueados.

Tras comprobar la fila efectivamente publicada se marcan los casos compatibles como
RESOLVED, conservando su identidad, primera detección, candidatos y evidencias. La resolución
registra la regla `mp9_monotonic_lifecycle_v1`, lote, versión Delta y fecha. No se eliminan
controles ni se reconstruye el lago. Una elección humana que rechazó un candidato sigue
respetándose mediante el filtro de alternativas rechazadas.

La regla tiene huella propia en el lote y el commit, evitando reutilizar como válido un lote
antiguo evaluado con otra política. Las escrituras mantienen preparación durable, verificación
de hashes, commit identificado y recuperación antes/después del commit o del acuse SQLite.
Una repetición ya confirmada no genera otro MERGE ni otra versión de datos.

El catálogo y las asignaciones detectan el cambio de versión o de pendientes de origen y
se actualizan mediante sus turnos de derivación existentes.
