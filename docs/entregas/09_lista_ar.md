# Entrega 09 — LISTA_AR

Estado: implementación local de ensayo completada. Contrato `lista_ar_v1`,
[sección 4.8 del plan](../PLAN_DELTA_LAKE.md).
GitHub, despliegue e integración de consumo se mantienen para después.

## Campos y significado

Se conservan las doce columnas originales, en su orden: `COD_AR`, `FECHA_ON`, `MAQ_IS`,
`CAVIDAD`, `STATUS`, `TIPO`, `MODELO`, `FECHA_OFF`, `COLOR`, `PROCESO`, `MOLDE`,
`ID_Agrupación`. Se añade al final `origen_fenix` técnico; planta fija en rutas/configuración.
No se proyectan etiquetas sobre esos códigos ni se inventa una agrupación para los vacíos.

Fechas timestamp(us) sin zona / Delta timestamp_ntz con política explícita de ensayo
`local_naive_test_v1`, día/mes/año y horas de una/dos cifras equivalentes. No asumir UTC
ni resolver DST por suposición; la política productiva sigue pendiente. Cierre anterior al
inicio o fechas imposibles se apartan. Ceros no sustituyen fechas ausentes.

COD_AR, MAQ_IS, STATUS, TIPO, MOLDE y agrupación son int64 exactos. Solo agrupación admite
nulo entre esos campos. CAVIDAD y FECHA_OFF también admiten nulo. Los otros textos son
obligatorios y solo pierden espacios extremos; MODELO conserva letras, `/` y ceros iniciales.
No se imponen enumeraciones nuevas ni se convierte MODELO a un identificador numérico.
CSV sin cabecera, `;`, separador final opcional; estructura o tipos incompatibles generan
incidencia con evidencia, sin evolución automática ni conversiones mediante float.

## Identidad y cierres

`K_A = (origen, COD_AR, FECHA_ON, MAQ_IS, CAVIDAD, STATUS, TIPO, MODELO, COLOR, PROCESO, MOLDE, ID_Agrupación)`.

Se comparan tuplas tipadas completas, con igualdad explícita de cavidad/agrupación nulas.
La huella incluye los doce campos de negocio; no reemplaza la identidad estable sin cierre.
Agrupación no es clave de fila, ni tampoco agrupación/STATUS. Otro inicio o componente de
identidad genera una candidata distinta que se valida; compartir grupo no permite deducir
una corrección ni fusionar tramos. No se crean referencias de producción en esta fuente.

Un único FECHA_OFF compatible completa solo ese campo de la fila existente. Abierto y
cerrado compatibles se consolidan sin depender del orden. Una copia abierta no reabre el
tramo aceptado. Dos cierres no nulos distintos conservan lo publicado y guardan íntegramente
las candidatas para revisión persistente, con hashes, diferencias y evidencia. Una ambigüedad
inicial no elige ganador y no desaparece al omitir una alternativa. Una versión inválida
con identidad interpretable retiene también otras variantes nuevas de esa identidad.

El MERGE compara todos los componentes, incluidos los nulos, y comprueba métricas contra
las intenciones: inserciones/cierres previstos, cero borrados. No actualiza otros atributos
ni cierra por ausencia, fin de turno, siguiente tramo o vencimiento. Repeticiones no escriben.

## Referencias y dependencias

Se leen los snapshots Silver confirmados de tres diccionarios de la misma planta/raíces:

| Campo AR | Diccionario | Columna usada para relacionar |
| --- | --- | --- |
| COD_AR | SIP_DEFECTO | codigo_aviso_rechazo |
| STATUS | SIP_DEFECTO_STATUS | codigo_status |
| TIPO | SIP_DEFECTO_AR | codigo_tipo_ar |

No se usan sus ID internos. STATUS indica procedencia, no zona, gravedad o abierto/cerrado.
Los códigos originales quedan en la tabla; las etiquetas y origen/zona siguen en sus
diccionarios. No se exige SIP_MODELO para conservar el MODELO textual ni se reincorporan
catálogos descartados. Un desconocido no recibe una etiqueta inventada.

Una referencia ausente genera REFERENCE_PENDING con candidata y evidencia Bronze persistentes;
no se pierde al sobrescribir/eliminar el CSV vivo. En la siguiente invocación se verifica su
evidencia y se reintenta contra las referencias actuales. Los tramos independientes válidos
pueden publicarse con cobertura parcial. Una revisión abierta de diccionario retiene de forma
conservadora toda la interpretación de esa familia; una operación técnica pendiente exige
recuperar primero el diccionario. No se edita su control desde la ingesta AR.

También se comprueban las referencias de los aceptados ausentes del CSV actual. Si un
diccionario entra en revisión, se conservan esos tramos y se informa
`accepted_reference_pending`, `complete=false`. La conservación física no equivale a
interpretación íntegra. Una resolución solo se confirma después de publicar/validar el tramo
compatible y sus referencias; nunca por contar una fila aceptada con otra versión de cierre.

El umbral opcional `pending_review_after_hours` escala dependencias conservando antigüedad y
eventos; 24 h en el ejemplo es de ensayo. No decide una corrección, inventa una referencia,
descarta eventos ni bloquea indefinidamente unidades independientes. No hay aún comandos de
resolución humana, avisos externos o scheduler.

## Auditoría, recuperación y ejecución

- Configuración: `config/lista_ar.example.toml`.
- Silver: `<silver_root>/<planta>/lista_ar`.
- Control: `<control_root>/control-lista_ar.sqlite`.
- CLI: `python -m ingesta_delta_lake ingest --config <ruta-absoluta>`.

Primero se ejecutan los tres diccionarios con sus configuraciones propias sobre las mismas
raíces/planta. Después AR consume solo Silver confirmado, sin lanzar ingestas implícitas.
Se aplican captura estable, nombres fijos/versionados mixtos, bloqueo único de escritor y
protección de rutas. La fuente y el entorno de la app no se modifican.

`ar_intents` registra INSERT/CLOSE con clave completa, hash técnico de identidad, antes/después,
hashes de negocio, evidencia y confirmación/versión. Ese hash no añade un ID Fenix a Delta.
La primera intención de inserción queda inmutable y el cierre tiene su propia intención.
`issues`/`issue_evidence` conservan contradicciones y recepciones por separado, sin duplicar
casos semánticos. `dependency_events` conserva observación, escalado y resolución.

El lote fija Bronze/hash, snapshot destino y versiones/hashes/controles/calidad de las tres
referencias. Una operación preparada se reconcilia o reanuda con esas versiones antes de
leer referencias nuevas y procesar novedades. Se verifica propiedad e integridad de los
snapshots fijados; deben conservarse para recuperación, sin eliminarlos por mantenimiento.
No hay transacción global entre tablas/SQLite. Los fallos antes/después del commit o antes
del acuse no duplican INSERT/CLOSE, filas ni confirmaciones. Un resultado que no pueda
demostrarse se detiene; no se adopta una escritura externa ni se repite a ciegas.

PARTIAL/complete=false distingue aceptados con pendientes; WAITING_DEPENDENCIES expresa
ausencia de filas publicables por referencias y BLOCKED ausencia por revisiones. CLI devuelve
2 en esos casos. Una revisión sin novedades no crea commits Delta ni reescribe Parquet.

## Conciliación real del 16/10/2026

Diez LISTA_AR de AV21, AV22, AV23, AV31 y AV32, días 13 y 14: **177 filas y 177 identidades**.
Comparación independiente csv/datetime/int de los doce campos y origen frente al snapshot:
igualdad íntegra. Los tres diccionarios se cargan en un destino de ensayo nuevo con 378,
ocho y nueve entradas, respectivamente; todos los códigos AR se resuelven, sin incidencias.

- AV21/39954 conserva dos filas del día 14: 09:59:33–10:33:51 STATUS=1 y
  10:33:51–11:13:43 STATUS=2, modelo 2031/487, cavidad 2F.
- AV32/19607 conserva dos filas con STATUS=2: 13/10 22:59:13–14/10 01:10:51 y
  14/10 01:10:51–01:34:49, modelo 2168/242, cavidad 8F.
- Se conservan las cinco filas sin agrupación de AV23/día 13. Todos los 177 tramos de esta
  muestra tienen cierre; las aperturas/cierres incrementales se prueban con casos sintéticos.
- Replay NO_CHANGE, versión 0, cero inserciones/cierres adicionales y mismos hashes Delta.
  Veinte recepciones y 177 intenciones INSERT confirmadas; cero revisiones/dependencias.
- Los cuarenta originales —AR y diccionarios— conservan sus hashes. Leer referencias no
  cambia sus Delta ni crea intentos adicionales en sus controles.

Informe y datos de ensayo fuera del checkout, bajo
`LOCALAPPDATA/Ingesta_Delta_Lake/validation/20261016T121055Z-lista-ar-c2cdd2c2/`.
Primera ingesta AR 0,71 s y replay 0,67 s en este equipo, con diccionarios ya cargados;
es una muestra pequeña y no acredita rendimiento productivo ni capacidad del histórico.

Las 48 pruebas nuevas cubren los doce campos, tramos repetidos/nulos, igualdad correcta de
claves, cierre único/no reapertura/conflictos, publicación mixta INSERT/CLOSE, interpretación
por código y dependencias, escalado, persistencia aunque desaparezca la fuente y recuperación
con diccionarios avanzados, además de errores y fallos alrededor del commit/acuse.
Resultado de la suite completa: **395 pruebas superadas y cobertura de líneas/ramas del
92,85 %**; Ruff y formato pasan. La prueba previa de familia desconocida ahora usa un nombre
reservado inexistente y verifica el motivo del rechazo; LISTA_AR ya es una fuente válida.
Sin dependencias nuevas ni cambios del lockfile; se conserva el entorno independiente.

No calcula minutos-cavidad ni decide su agregación; tampoco recorta/fusiona intervalos,
equipara avisos con paradas o asigna producciones. La integración del score, horario productivo
y optimización del control/escaneo detectadas en la entrega 08 siguen pendientes.
Siguiente unidad: entrega 10, CONT_ZC, última de las nueve fuentes de origen seleccionadas.
