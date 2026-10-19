# Entrega 05 — MP9_CAMBIO_MODELO

Estado: implementación local de ensayo completada; la conciliación real conserva una
publicación parcial v1 por el conflicto conocido de AV32. Contrato actual: `mp9_cambio_modelo_v2`,
sección 4.5 del plan. Depende del diccionario de modelos aceptado, sin ejecutar su ingesta
implícitamente. No implementa todavía el catálogo de producciones de la entrega 06.

**Implementación del 19/10/2026:** `mp9_cambio_modelo_v2` recupera
`ID_MAQUINA` en la quinta posición de negocio y en el hash, valida máquina compartida ZC/ZF
y mantiene descartados ID_PREPARACION/N_MOD. Esquema y validaciones probados con datos
sintéticos. La evidencia real al final de esta ficha sigue siendo v1; la reconstrucción
real v2 queda para el servidor después de GitHub/CI. Véase
[11B/11C](11bc_catalogos_derivados.md). ZF_OK conserva sus conflictos pendientes.

## Esquema y ámbito

Se comprueban las diez columnas Fenix originales y se conservan las ocho acordadas:

| Orden | Campo | Tipo | Nulo |
| --- | --- | --- | --- |
| 1 | ID | int64 | No |
| 2 | ID_MODELO | int64 | No |
| 3 | FECHA_ON | timestamp(us), local sin zona | No |
| 4 | FECHA_OFF | timestamp(us), local sin zona | Sí |
| 5 | ID_MAQUINA | int64 | No |
| 6 | STATUS | int64, 1/2 | No |
| 7 | ID_ST1_REL | int64 | Sí |
| 8 | ZF_OK | int64, 0/1 | No |

La novena columna Silver, `origen_fenix` string no nulo, es metadato técnico. La identidad
completa es planta —ubicación/configuración— más origen e ID. No se mezclan registros ni
relaciones de líneas diferentes. La relación se lee de la columna Fenix 8, antes de N_MOD.
ID_MAQUINA participa en invariantes, relaciones y hash v2; ID_PREPARACION y N_MOD siguen
descartados. Un vínculo ZF–ZC exige igual máquina, modelo y origen.

Se exige `timestamp_policy = "local_naive_test_v1"`. Es una representación explícita para
ensayo en Delta timestamp_ntz, sin asumir UTC ni resolver ambigüedades DST. Se leen fechas
día/mes/año con hora/minuto/segundo, aceptando hora con uno o dos dígitos. Se rechazan fechas
imposibles y cierres anteriores al inicio. Vacíos de cierre/referencia son nulos, no cero.
La zona y resolución horaria productivas siguen pendientes según el plan; producción no se habilita.

## Decisiones y escrituras

`mp9.py` valida, consolida versiones y decide INSERT, cierre, coincidencia, dependencia o
revisión por identidad. Un abierto y un único cierre compatible consolidan sin elegir por
orden. Un cerrado no se reabre por una copia sin cierre. Dos cierres diferentes, cambios de
invariantes o ZF_OK contradictorio quedan en revisión persistente. Una candidata previamente
contradictoria no se acepta automáticamente porque una alternativa desaparezca del CSV.
Una versión inválida con ID interpretable impide publicar otras versiones de ese mismo ID.

`mp9_storage.py` valida el snapshot de modelos contra su control, metadatos y versión,
en lectura. No crea/adopta un control de modelos ausente ni usa un commit aún no confirmado.
ID_MODELO desconocido genera dependencia. Una ZF debe referenciar una ZC del mismo origen y
modelo; referencia propia, otra zona o modelo son conflictos. No se infieren relaciones.

El escritor comprueba otra vez snapshot y transiciones antes del MERGE. Empareja por origen
e ID, inserta solo nuevas identidades y actualiza exclusivamente FECHA_OFF de nulo a un
cierre validado. Contrasta las métricas del MERGE y el hash/snapshot resultante. No hay
overwrite, borrado por ausencia, last_seen, continuidad forzada o cierre inferido de ZF_OK.
Sin inserciones/cierres no se invoca ninguna escritura Delta.

## Control, publicación parcial y recuperación

`mp9_control.py` conserva intenciones INSERT/CLOSE por operación/origen/ID, antes/después,
hashes de los siete campos, evidencia y versión confirmada. La primera inserción permanece
inmutable. `mp9_service.py` prepara snapshot y fuentes antes de escribir y reutiliza la
reconciliación común para recuperar fallos antes/después del commit o del acuse SQLite.

Las unidades válidas independientes pueden publicarse dejando otras pendientes. El resultado
global mantiene `complete=false` y `PARTIAL`, con exit code 2; no acredita una carga íntegra.
Sin datos publicables devuelve WAITING_DEPENDENCIES o BLOCKED según el motivo. Los lotes
parciales no se tratan como pendientes técnicos ni se vuelven a escribir a ciegas. El control
distingue publicación de datos, resolución de dependencias y revisión humana.

Las dependencias conservan candidata y evidencias Bronze y se reintentan en la próxima
invocación aunque el CSV vivo se haya sobrescrito o desaparecido. Se comprueban otra vez las
huellas antes de usar esa evidencia. La llegada del modelo/ZC puede resolver la dependencia,
sin modificar ni eliminar su evidencia histórica. `dependency_events` registra estados y
resolución. Se informa de su antigüedad. `pending_review_after_hours` es opcional; el ejemplo
propone 24 horas solo para ensayo, sin fijar una regla productiva todavía no acordada.
El vencimiento escala el estado, no inventa datos ni bloquea las recepciones independientes.

No hay aún interfaz/comando para resolver conflictos humanos, notificador o scheduler.
Los conflictos no se resuelven por omisión de versiones o paso del tiempo. Las cuatro
entregas de diccionarios conservan sus reglas, ubicaciones y política de bloquear la unidad
completa ante incidencias; las nuevas capacidades temporales se aíslan en módulos MP9.

## Ejecución

- Ejemplo: `config/mp9_cambio_modelo.example.toml`.
- Familia: MP9_CAMBIO_MODELO; destinos test/development, explícitos y fuera del checkout.
- Silver: `<silver_root>/<planta>/mp9_cambio_modelo`.
- Control: `<control_root>/control-mp9_cambio_modelo.sqlite`.
- Bronze: `<bronze_root>/<planta>/<origen>/MP9_CAMBIO_MODELO/<fecha UTC recepción>/<sha256>.csv`.

Se comparte bloqueo Silver con los diccionarios. No hay nuevas dependencias, cambios del
lockfile, de la aplicación o del workflow, ni acciones en GitHub o despliegues remotos.

## Conciliación real del 16/10/2026

Se prepararon copias controladas de los CSV reales en un directorio externo de ensayo,
con huellas de los veinte originales —diez MP9 y diez SIP_MODELO— antes y después.
El diccionario aceptado consolidó 2309 modelos. Se reprodujeron los dos días en orden:

- Día 13: cinco archivos, 644 filas, 46 identidades válidas publicadas, versión Delta 0.
- Al añadir día 14: diez archivos, 839 filas en revisiones únicas origen/hash, 48 ID y 51
  variantes normalizadas. Se aplican dos cierres sobre los 46 registros existentes, versión 1.
- AV32/2003 se cierra el 14/10/2026 06:23:00 y AV32/2004 el 14/10/2026 07:23:14.
- AV32/2007 conserva la contradicción ZF_OK=0/1 en revisión, sin publicar una alternativa.
  AV32/2008 queda pendiente de ese registro ZC. Resultado PARTIAL, complete=false.
- El replay deja versión 1 y huellas Delta idénticas, cero inserciones/cierres, un conflicto
  y una dependencia pendientes. Los veinte originales mantienen sus huellas.

Auditoría del ensayo: tres intentos MP9, 25 recepciones, 46 INSERT y dos CLOSE confirmados
una sola vez. El informe JSON, copias y datos se guardan fuera del checkout, bajo
`LOCALAPPDATA/Ingesta_Delta_Lake/validation/20261016T094216Z-mp9-31cea086/`.

Las pruebas automatizadas cubren campos, fechas/nulos, ambas zonas, identidad entre orígenes,
relaciones, cierres/ausencias, conflictos persistentes, dependencias de modelos/ZC y escalado,
reintento desde Bronze, actualizaciones reales, historial anterior intacto y fallos de commit/acuse.
Resultado: **242 pruebas superadas, cobertura de líneas/ramas del 94,71 %**. Se mantienen
las 196 pruebas anteriores; 46 casos nuevos verifican MP9. Ruff y formato pasan. Entorno:
Python 3.12.14, deltalake 1.6.6 y Arrow 23.0.1, sin modificar las dependencias bloqueadas.

Siguiente unidad: entrega 06, catálogo derivado de producciones, manteniendo explícitas
las relaciones pendientes; no debe presentar esta publicación parcial como un catálogo íntegro.
