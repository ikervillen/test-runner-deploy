# Entrega 08 — LISTA_RECH

Estado: implementación local de ensayo completada. Contrato `lista_rech_v1`,
[sección 4.7 del plan](../PLAN_DELTA_LAKE.md).
La integración con la app y el despliegue se mantienen para sus entregas.

## Conservación y esquema

Nueve campos originales, en este orden, más `origen_fenix` técnico:

| Campo | Tipo Arrow / Delta | Nulos |
| --- | --- | --- |
| FECHA | timestamp(us) sin zona / timestamp_ntz | No |
| MOLDE | int64 / BIGINT, no negativo | No |
| LINEA | int64 / BIGINT, código original de ramal | No |
| COD_MAQ_ZF | int64 / BIGINT, código original de inspección | No |
| INSPECC | int64 / BIGINT, no negativo | No |
| RECH | int64 / BIGINT, no negativo | No |
| RECH_AUTO | int64 / BIGINT, no negativo | No |
| CAVIDAD | string / STRING | Sí |
| FECHA_R | timestamp(us) sin zona / timestamp_ntz | Sí |

Lectura estricta con `;`, sin cabecera, separador final opcional. No hay evolución automática
de esquema ni conversión numérica mediante float. Los códigos no reciben nuevas enumeraciones.
Un cero no descarta una fila. MOLDE=0 no es un total que sustituya a los demás moldes.
Vacío de cavidad → nulo; se conservan `8F`, `99Z` y el texto literal `null` como valores distintos.
Solo se retiran espacios extremos del texto. Los timestamps equivalentes con horas de uno/dos
dígitos coinciden. Política explícita `local_naive_test_v1`, sin UTC ni resolución DST supuesta;
la política horaria productiva sigue pendiente.

RECH y RECH_AUTO son contadores independientes. No se exige ninguna relación de subconjunto
entre ellos o con INSPECC. FECHA_R no se rellena con FECHA, no tiene que coincidir con ella y
puede quedar nula aunque RECH_AUTO sea positivo. Una fecha nueva para la misma clave es una
modificación pendiente de revisión, no una transición automática de cierre.

## Identidad y revisión

- `K_R = (origen, FECHA, MOLDE, LINEA, COD_MAQ_ZF, INSPECC, CAVIDAD)`.
- `S_R = (origen, FECHA, MOLDE, LINEA, COD_MAQ_ZF, CAVIDAD)`.

Se comparan tuplas tipadas completas, con igualdad explícita entre cavidades nulas; la huella
de los nueve campos no sustituye la clave. Planta queda fijada por rutas/configuración/control.
Una misma fila entre archivos, nombres nuevos, sobrescrituras o reintentos coincide sin commit.
Otra máquina, molde, ramal, cavidad, origen o fecha corresponde a otro contexto, aunque la
cantidad sea igual. No se usa `sum(distinct INSPECC)` como deduplicación.

Misma clave con RECH, RECH_AUTO o FECHA_R distintos conserva lo aceptado y aparta la candidata
íntegra. Misma firma con INSPECC distinto marca posible revisión: no publica 96 y 97 como dos
cantidades independientes ni las suma. Una ambigüedad inicial no se resuelve por orden, mayoría,
máximo/mínimo o última llegada. El contexto queda retenido aunque después falte una alternativa
o llegue otra cantidad para esa firma. Resolver revisiones exige una decisión explícita y un
procedimiento aún pendiente, no editar directamente SQLite ni borrar controles.

Inválidos con contexto interpretable impiden publicar nuevas variantes de ese contexto;
otros contextos válidos pueden avanzar. Una revisión hace que su base aceptada no aparezca
como resuelta; un error sin contexto identificable afecta las bases del origen. La familia
informa PARTIAL/complete=false o BLOCKED si no hay nada aceptado; CLI devuelve 2. La integridad
global no se vuelve completa al filtrar el diagnóstico por otro origen.

## Escritura, auditoría y recuperación

- Silver: `<silver_root>/<planta>/lista_rech`.
- Control independiente: `<control_root>/control-lista_rech.sqlite`.
- Configuración: `config/lista_rech.example.toml`.
- Ingesta: `ingest --config ...`.
- Consulta diagnóstica de solo lectura: `inspection-bases --config ... [--origin AV23]`.

Se reutiliza el protocolo MIA con adaptadores de contrato/escritor/control, sin alterar claves
ni identidades de operación de LISTA_DEFECTOS. La nueva Delta recibe append de candidatas nuevas
validadas, sin overwrite, actualizaciones automáticas, borrados por ausencia ni commits vacíos.
Los CSV y las copias Bronze no se modifican. El ámbito de escritor y la comparación del snapshot
evitan publicar decisiones sobre una versión cambiada.

`observation_intents` registra clave, firma/contexto, candidata completa, evidencia, hash y
operación antes del append, y después su confirmación/versión. `issues` conserva candidata,
aceptados, diferencias y motivo en PENDING_REVIEW; `issue_evidence` separa las recepciones/lotes
de la identidad semántica del caso. Repetir un conflicto no crea otro caso idéntico.
`inspection_groups` deja evidencia de bases por lote confirmado, sin otra tabla Delta.

Una operación incierta se reconcilia por identidad, versión y contenido antes de repetir.
El lote preparado conserva las Bronze y hashes originales, aunque cambie el CSV vivo.
Los fallos antes/después del commit o antes del acuse no duplican intenciones confirmadas,
filas o contadores. El lector exige control vinculado, versión confirmada y ausencia de
operaciones técnicas pendientes; no crea controles/tablas ausentes.

## Relación con el denominador histórico

La consulta devuelve una base por firma resuelta y su suma diagnóstica. **No representa el
denominador ya agregado del score ni botellas físicas únicas entre máquinas.** Se revisaron
en modo lectura `_real_inspected_total` y `_inspected_total`: el histórico aplica filtros
y después máximo por timestamp/machine_branch antes de sumar periodos. Esa regla posterior
no resuelve las revisiones contradictorias de una observación; estas permanecen apartadas.

La base se obtiene antes de cruzar códigos de defecto o mediante consulta por firmas únicas;
las cinco filas de defecto del ejemplo mantienen una base de 96, no 480. LISTA_RECH sigue
siendo la fuente preferente de inspeccionadas y LISTA_DEFECTOS la de rechazos por defecto.
No se activa el fallback legacy ni se suman rechazos distintos para deducir botellas únicas.

La verificación local de la regla numérica utiliza origen/fecha/ramal y filtros disponibles
de los CSV, sin inventar etiquetas enriquecidas. No acredita todavía los filtros de modelo,
color, sección, asignación de producción, tasas o score completo de la aplicación. Esa
comparación e integración se realizarán con las relaciones y el adaptador de consumo antes
de conectar Delta a la app. Las tablas de origen aún no llevan ID de producción.

## Validación local

Resultado: **347 pruebas superadas y cobertura de líneas/ramas del 93,12 %**; Ruff y formato
pasan. Las 40 pruebas nuevas cubren los nueve campos, fechas/nulos, cantidades y ceros, revisiones
de los tres campos fuera de clave, cambios de INSPECC, ambigüedades iniciales persistentes,
aislamiento entre contextos y entre fuentes, errores de tipo/estructura, nombres mixtos,
repetición sin escrituras, consulta sin efectos y fallos alrededor de commit/acuse.
Se mantienen las pruebas de las entregas anteriores. Sin nuevas dependencias ni cambios
del lockfile; entorno Python propio separado de la app.

### Conciliación real del 16/10/2026

Los diez CSV de los días 13 y 14 de AV21, AV22, AV23, AV31 y AV32 se contrastaron mediante
csv/datetime/int independientes con la totalidad de los nueve campos y origen publicados:

| Comprobación | Resultado |
| --- | --- |
| Filas leídas y publicadas | 202.931; igualdad íntegra con la fuente |
| AV23, día 14 | 17.831 filas conservadas |
| Firmas de inspección resueltas | 202.931, sin conflictos ni revisiones en esta muestra |
| Suma diagnóstica por contextos | INSPECC 5.086.248; RECH 107.481; RECH_AUTO 909, sin combinar contadores |
| RECH_AUTO positivo y FECHA_R nula | 84 filas, conservadas |
| Primera carga | APPLIED, versión 0 |
| Replay | NO_CHANGE, cero inserciones, misma versión y hashes de archivos Delta |
| Auditoría | 20 recepciones y 202.931 intenciones confirmadas, ninguna pendiente |
| Originales | Diez LISTA_RECH y el LISTA_DEFECTOS del cruce conservan sus hashes |

Los ejemplos AV23 del día 14 se mantienen: fila 1 con base 96/RECH 12, fila 2 con 18
inspeccionadas y cero rechazos, fila 5039 con INSPECC 5, RECH 0, RECH_AUTO 13 y FECHA_R
02:32:45. Se verificó contra las cinco filas de defecto del mismo contexto que su base
sigue siendo 96, no 480.

La regla numérica de máximo por origen/fecha/ramal y suma de periodos se comparó entre CSV y
Silver en seis escenarios: conjunto completo, AV23/día 14, AV23/máquina 13, AV23/cavidad 8F,
AV32/ramal 1 y filas sin rechazos. Coinciden. Por ejemplo, AV23/día 14 devuelve 24.022 en
118 periodos/ramales según esa proyección. Esto no acredita asignaciones/enriquecimientos
ni un denominador de producción o score integrado; el límite indicado arriba sigue vigente.

Informe y datos de ensayo fuera del checkout, bajo
`LOCALAPPDATA/Ingesta_Delta_Lake/validation/20261016T105024Z-lista-rech-a8e7d688/`.
Primera carga 330,17 s —con la suite completa ejecutándose en paralelo— y replay 50,96 s en
este equipo. No son un benchmark productivo. Tras cerrar la validación, Bronze ocupa 8.164.325
bytes, Silver 523.303 bytes y control 794.202.112 bytes. Esta sobrecarga de control es una
limitación observada que debe resolverse antes de producción.

La comparación actual de snapshots es en memoria. La prueba ha identificado un coste elevado
en el control: las intenciones y
los JSON preparados repiten evidencia por fila. Antes de producción habrá que normalizar
referencias a revisiones/evidencias, evitar escaneos completos innecesarios y medir memoria,
latencia y crecimiento del control por ciclo. Esto debe hacerse en otra entrega acotada, con
compatibilidad de controles y recuperación probada; nunca borrar auditorías o intenciones
pendientes para reducir espacio. La comprobación de integridad de esta muestra no acredita
capacidad para el histórico creciente. Almacenamiento remoto y política temporal productiva
siguen pendientes. Siguiente unidad: entrega 09, LISTA_AR.
