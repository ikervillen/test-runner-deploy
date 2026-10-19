# Entrega 06 — Catálogo de producciones

Estado: implementación local de ensayo completada. Contrato actual: `catalogo_producciones_v2`,
derivado de sección 16. Depende de SIP_MODELO y MP9 aceptados; no consume CSV vivos.

**Implementado el 19/10/2026:** `ID_MAQUINA` BIGINT obligatorio antes
de `ID_ST1_REL`, mantener este vínculo a la derecha y preservar todos los ID de producción.
Resolver la denominación AV31A/AV31B mediante el catálogo físico; origen AV31 permanece
separado de la parte de tándem. Con `topology_enabled=true`, exige el catálogo físico
confirmado y coherente con cada origen. Código probado con datos sintéticos; resultados
reales históricos conservados como v1. Reconstrucción real en servidor después de GitHub/CI,
según [11B/11C](11bc_catalogos_derivados.md).

## Identidad y esquema físico

La identidad canónica es `fenix-production-v1:<planta>:<origen>:<ID_ZC>`; los ámbitos se
validan sin `:` y el ID utiliza el entero decimal canónico. Es estable entre cierres,
reintentos, archivos y cambios de turno. No se deriva del modelo, fecha o hash de toda la fila.
Ejemplo: `fenix-production-v1:AIALA:AV32:2003`.

| Orden | Campo | Tipo | Nulo |
| --- | --- | --- | --- |
| 1 | id_produccion | string | No |
| 2 | planta | string | No |
| 3 | origen_fenix | string | No |
| 4 | linea | string | No |
| 5 | id_zc_raiz | int64 | No |
| 6 | id_modelo_fenix | int64 | No |
| 7 | fecha_inicio_zc | timestamp(us), local sin zona | No |
| 8 | fecha_fin_zf | timestamp(us), local sin zona | Sí |
| 9 | ID_MAQUINA | int64 | No |
| 10 | ID_ST1_REL | int64 | Sí |

El código de botella se consulta en el snapshot de modelos, sin duplicarlo como diccionario
editable. `linea` procede de `[line_by_origin]`, declarado explícitamente. Cambiar esa
correspondencia sobre una producción publicada requiere revisión. Los timestamps mantienen
la política de ensayo MP9 `local_naive_test_v1`; zona/DST productivas siguen pendientes.

`ID_ST1_REL`, a la derecha, enlaza la ZF aceptada con la raíz de esta misma producción y,
cuando está informado, debe coincidir con id_zc_raiz. No identifica predecesora ni implica
cronología entre raíces. Los CSV originales no cambian; MP9 Silver v2 conserva ocho campos
de negocio. La consulta devuelve nombre de máquina desde el snapshot físico y estado de
producción derivado, con cobertura/frescura separadas.

## Derivación y cobertura

Se construye una entidad por raíz ZC aceptada, conservando históricas y concurrentes del
mismo modelo. No se agrupan raíces por modelo/proximidad o se separan por turnos/paradas.
Composiciones excepcionales con varias raíces siguen pendientes de evidencia/regla explícita.

El inicio es la entrada ZC de esa raíz. El final es el máximo de las salidas de registros ZF
distintos ya aceptados y relacionados por ámbito/modelo, únicamente si todos los conocidos
están cerrados y no hay una incidencia que impida confirmar el final. No se ignoran ZF abiertas.
No se cierra por ZF_OK, salida de ZC, nueva producción, ausencia, timeout o recepción.

Una raíz sin ZF conocida se publica con final/vínculo nulos y `PENDING_ZF` en metadatos;
una relación válida con ZF abierta puede tener cobertura `VALID` y final nulo. `REVIEW`
separa contradicciones de ausencia de cierre. La composición y esos estados quedan en el
control de derivación, fuera de las columnas de negocio.

Las incidencias MP9 siguen visibles y condicionan la cobertura y los cierres afectados.
Una candidata no aceptada en MP9 no se introduce en el catálogo por leer sus variantes del
control: solo figura como diagnóstico no publicado. La información global de completitud
se refiere al conjunto conocido de snapshots de entrada; no acredita todo el histórico físico.

Un cierre compatible completa fecha_fin_zf en la misma identidad. Si se recibe después una
composición distinta cuando ya se había cerrado, o cambian invariantes/límites incompatibles,
se mantiene la última fila/composición aceptadas y se conserva la propuesta para revisión.
No se reabre ni se extiende automáticamente el cierre. Los conflictos no desaparecen al
revertir una configuración ni tienen todavía un comando de corrección humana.

## Consistencia, auditoría y recuperación

- Silver: `<silver_root>/<planta>/catalogo_producciones`.
- Control: `<control_root>/control-catalogo_producciones.sqlite`.
- Configuración: `config/catalogo_producciones.example.toml`.
- CLI: `catalog build --config ...` y `catalog search --config ... --model '2001/477'`.

Se reutilizan bloqueo Silver e identidades de operación. El control propio registra
INSERT/COMPLETE con antes/después, hashes, composición, regla, snapshots fuente y revisión.
La primera intención se mantiene inmutable. El MERGE solo inserta o completa final/vínculo
nulos validados; no hay overwrite, borrados o escrituras por coincidencias.

La intención fija versiones Delta y hashes de modelos/MP9, metadatos de control y casos
de revisión. Una operación pendiente verifica esas versiones antes de reanudar, aunque las
fuentes hayan avanzado; después deriva la siguiente publicación desde fuentes actuales.
Un commit sin acuse se reconcilia antes de repetir. No se presupone atomicidad entre tablas.
Unidades pendientes técnicas de fuente impiden consumirla como snapshot confirmado.

La publicación y su composición se confirman juntas en SQLite después de validar Delta.
Los cambios solo de cobertura/snapshots pueden generar nueva evidencia de publicación sin
crear un commit Delta de negocio. Deben conservarse las versiones fuente y catálogo usadas
por publicaciones/operaciones; eliminar su evidencia requiere un mantenimiento controlado.

## Búsqueda

La búsqueda por código de botella no exige fechas ni escribe/deriva. Resuelve el código
en el modelo fijado y devuelve fabricaciones separadas, de más reciente a más antigua,
incluyendo composición y cobertura. No asigna todavía defectos o contadores ni modifica
la interfaz o los IDs del histórico de la aplicación.

Lee el snapshot Delta de la última publicación confirmada y su composición/control, incluso
si existe otro commit aún sin confirmar. `fresh=false`/`STALE` distingue avance de fuente,
cambio de revisiones/configuración o commit no confirmado. Frescura es respecto a Silver
aceptado, no a CSV aún sin ingerir. `PARTIAL` y `complete=false` conservan incidencias conocidas;
no se informa de integridad global mientras falten candidatas requeridas. CLI devuelve 2 en
esos casos y 4 si no existe catálogo. Las consultas no crean controles ausentes.

La implementación inicial compara snapshots en memoria y se valida sobre la muestra local.
No se acredita todavía latencia productiva, concurrencia de consulta o rendimiento a gran
escala; quedan para pruebas/optimización de consumo. No se implementan tablas de ramales,
asignaciones de observaciones, métricas, migración del histórico, GitHub o despliegues.

## Conciliación real del 16/10/2026

Se reprodujo la llegada del día 13 seguida del 14 con copias controladas de los veinte
CSV reales de modelos/MP9 y el diccionario de 2309 modelos aceptado:

- Día 13: 23 producciones publicadas, 16 cerradas y siete sin cierre, versión catálogo 0.
- Día 14: 24 candidatas, 23 publicadas —17 cerradas/seis sin cierre— y una no publicada
  por AV32/2007. El conflicto ZF_OK y la dependencia de 2008 siguen visibles desde MP9.
- Un único cierre de producción actualiza la versión catálogo a 1. AV32/2003, modelo
  2001/477, conserva su ID y termina a las 07:23:14, no a las 06:23:00 de salida ZC.
- Las raíces AV31/1617 y AV31/1643, ambas 1358/242, permanecen separadas y sin cierre.
- La búsqueda señala STALE entre actualización MP9 y derivación; después refleja PARTIAL
  fresco respecto a Silver, con la candidata pendiente explícita.
- El replay no inserta/actualiza ni cambia huellas/versiones Delta. Derivar/consultar no
  cambia MP9; los veinte CSV originales también conservan sus huellas.

El informe JSON y los datos de ensayo quedan fuera del checkout, bajo
`LOCALAPPDATA/Ingesta_Delta_Lake/validation/20261016T100248Z-catalog-3a992feb/`.
Las pruebas verifican cierre tardío, composición, búsquedas sin fechas, repetición, aislamiento
de ámbitos, estados de cobertura y fallos antes/después de commit/acuse, incluidos avances
de fuente entre intento y recuperación. Resultado: **268 pruebas superadas, cobertura de
líneas/ramas del 93,67 %**; se mantienen las 242 anteriores y se añaden 26 casos de catálogo.
Ruff y formato pasan. Python 3.12.14, deltalake 1.6.6 y Arrow 23.0.1, sin nuevas dependencias
ni cambios del lockfile. Siguiente unidad: entrega 07, LISTA_DEFECTOS.
