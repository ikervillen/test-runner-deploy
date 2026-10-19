# Entrega 11 — Asociación temporal producción–ramal

Estado: implementación local de ensayo. Contrato actual `asignacion_produccion_ramal_v2`,
regla `temporal_assignment_v2`, conforme a la [sección 17 del plan](../PLAN_DELTA_LAKE.md).
Incluye propuestas, publicación de vigencias expresamente revisadas y atribución trazable.
No aprueba por sí sola las vigencias reales ni integra todavía la aplicación.

**Actualización de contrato del 19/10/2026:** la identificación física de AV31 queda resuelta
en los originales: máquina 8/AV31B/1617–1618/ramales 1–2 y máquina 7/AV31A/1643–1644/ramales
3–4. Recuperación de ID_MAQUINA, catálogos físicos y derivación automática ya implementadas
y probadas con datos sintéticos. Las publicaciones reales anteriores **no se han reconstruido**
con esta relación. La ficha siguiente conserva el flujo manual y la evidencia histórica v1;
las extensiones actuales y operación v2 están en [11B/11C](11bc_catalogos_derivados.md),
contrato completo en plan 17.14–17.16. No se dan por resueltos
todos los AR, los límites efectivos de circulación, la rueda compartida ni ZF_OK de AV32.

## Entidad derivada

Se conservan las nueve tablas fuente, sin añadir columnas ni modificar valores. La segunda
entidad derivada, tras el catálogo, es `<silver_root>/<planta>/asignacion_produccion_ramal`.
Solo contiene episodios ZF aceptados; sin decisiones aceptadas no se crea una Delta vacía.

| Campo, en orden | Tipo Arrow / Delta | Nulo |
| --- | --- | --- |
| id_asignacion | string / STRING | No |
| planta | string / STRING | No |
| origen_fenix | string / STRING | No |
| linea | string / STRING | No |
| ramal | int64 / BIGINT | No |
| zona | string / STRING, ZF | No |
| id_produccion | string / STRING | No |
| vigente_desde | timestamp(us) / TIMESTAMP_NTZ | No |
| vigente_hasta | timestamp(us) / TIMESTAMP_NTZ | Sí |
| evidencia_origen | string / STRING, referencia por hash a decisión persistente | No |
| version_regla | string / STRING | No |
| revision_decision | int64 / BIGINT | No |

Identidad estable `fenix-association-v1:<planta>:<origen>:<id de episodio revisado>`, sin fechas
en la clave. Se admiten episodios sucesivos del mismo ramal/producción con IDs distintos.
Las correcciones conservan el ID. La producción debe existir en el catálogo publicado, ser
compatible con planta/origen/línea y tener composición válida, sin revisión pendiente.

Intervalos `[desde,hasta)`, final nulo abierto. Se comprueba todo el estado resultante,
incluidas correcciones, antes del MERGE: sin solapamiento en planta/origen/línea/ramal/zona.
Encontrarse en el límite es válido. Los huecos son válidos y no se rellenan. Fechas bajo
`local_naive_test_v1`: UTC, DST y política horaria productiva permanecen pendientes.

## Evidencia y decisión revisada

La derivación exige snapshots confirmados de las nueve fuentes y el catálogo, controles del
mismo ámbito, ausencia de operaciones técnicas pendientes y catálogo construido con las
versiones/calidad actuales de sus dependencias. Usa el bloqueo común de escritor Silver.
No lanza ingestas para reparar dependencias implícitamente.

Los cruces MIA–AR utilizan origen, molde, cavidad y coincidencia temporal del periodo de
muestra con el tramo. Resuelven modelo contra catálogo y registros de zona. Molde 0, cavidad
nula y `99Z` no son evidencia discriminante. La zona AR se interpreta por la descripción del
código TIPO terminada en ` ZF` o ` ZC`; otros tipos quedan pendientes. STATUS es procedencia,
no zona. Revisiones de los diccionarios AR retienen su interpretación, como en la entrega 09.

Las candidatas conservan producción(es), modelo, ramal, primer/último final de muestra
observado y referencias de cruce. Estos extremos son **cobertura de evidencia, no vigencias
efectivas ni prueba de continuidad**. Una agrupación no demuestra todo el intervalo intermedio.
`matched_evidence_pairs` cuenta pares de evidencia, no filas únicas, botellas o minutos.
Se muestran hasta ocho ejemplos por candidata; el snapshot completo y sus claves se archivan.

No se aprueba por mayoría, última llegada ni cantidad de cruces. El operador declara decisiones
ya revisadas en un JSON dentro de `control_root`, configurado mediante `association_decisions`.
Sin él se generan propuestas y pendientes. `config/association-decisions.empty.json` no
contiene aprobaciones. Ejemplo **sintético**, no aprobación para Aiala:

```json
{
  "version": 1,
  "decisions": [{
    "id": "ejemplo-zf-ramal1-episodio1",
    "kind": "ZF_ASSOCIATION",
    "origin": "AV21",
    "branch": 1,
    "production": "fenix-production-v1:AIALA:AV21:10",
    "start": "2026-10-14T06:00:00",
    "end": "2026-10-14T07:23:00",
    "revision": 1,
    "actor": "responsable que revisó la evidencia",
    "reason": "Justificación del intervalo efectivo aprobado",
    "evidence": [{"external": "referencia de revisión", "note": "Conclusión contrastada"}]
  }]
}
```

Alternativamente se referencia evidencia por `{ "family": "LISTA_AR", "source_id": "<hash>" }`,
existente en el snapshot y sin revisión en su fuente. `actor` es una declaración de auditoría
local, **no autenticación, firma ni autorización corporativa implementada**. La referencia
externa registra conocimiento operativo revisado; proteger el archivo corresponde al despliegue.

Tipos: `ZF_ASSOCIATION` por ramal/producción, `ZF_PREPARATION` por ramal sin producción,
`ZC_PRESENCE` por máquina/producción y `ZC_PREPARATION` por máquina sin producción.
Preparación exige final finito y evidencia propia. Las presencias ZC permanecen en el control;
no crean otra tabla maestra ni reutilizan vigencias ZF.

## Atribución de observaciones físicas

MIA a las 07:30 representa `[07:20,07:30)`, sin ampliar desde el punto anterior ni decidir solo
por el final. Se comprueba el periodo completo:

- Una producción durante toda la muestra: `ASSIGNED`.
- Producción saliente más preparación confirmada posterior: `OLD_PLUS_PREPARATION`.
- Preparación confirmada seguida de entrante: `NEW_PLUS_PREPARATION`.
- Preparación completa sin flujo incompatible: `PREPARATION`, sin producción.
- Dos producciones, hueco injustificado, transición interna incompatible o evidencia
  contradictoria: pendiente/conflicto, sin repartir cantidades ni duplicar filas.

Inspeccionadas/rechazos positivos en preparación completa: `FLOW_DURING_PREPARATION`, sin
ocultar flujo. Los ceros son datos recibidos, no registros sintéticos. Molde 0/cavidad nula
pueden asignarse por vigencia revisada, sin inventar cruces. Una evidencia AR incompatible
con el modelo asignado bloquea la atribución.

AR usa su periodo propio, modelo y zona. Solo se atribuye si corresponde inequívocamente
a una fabricación válida. Abiertos, cruces de frontera, zona desconocida y producciones
simultáneas del mismo modelo quedan pendientes. No divide tramos ni computa minutos-cavidad.
Un AR instantáneo en el límite aplica el intervalo semiabierto de zona, sin asignarlo a una
zona saliente que ya terminó.

CONT_ZC exige presencia **ZC** revisada máquina–producciones. El mismo porcentaje máquina/periodo
se vincula a las producciones tándem coincidentes, conservando una observación física. La
consulta conjunta consolida claves físicas antes de sumar RUEDA/teóricos, con la fórmula y
elegibilidad de la entrega 10. No reparte transiciones, identifica modelo por A/B ni deduce
presencia ZC desde la vigencia ZF.

## Auditoría, correcciones y recuperación

Control: `<control_root>/control-asignacion_produccion_ramal.sqlite`. Cada publicación conserva
versiones/calidad de fuentes, decisiones, cambios antes/después y referencias a dos artefactos
JSON comprimidos inmutables bajo `association-artifacts`:

1. Snapshot normalizado completo, composición del catálogo, propuestas, decisiones aceptadas
   y entrada de revisión; permite reconstruir la derivación sin depender del CSV vivo.
2. Registro de atribución, una entrada por clave física MIA/AR/ZC, con clave, `source_id`,
   producción(es), episodio(s), regla y motivo. No es una décima fuente Fenix ni una tabla
   relacional obligatoria de observaciones.

Se verifican hashes de contenido y archivo comprimido. Todas las publicaciones quedan
conservadas, incluso sin nuevo commit Delta. Los controles fuente mantienen la procedencia
Bronze/archivo/fila. La auditoría no depende solo de la retención del log Delta.

Corregir requiere mismo ID/ámbito/tipo, revisión consecutiva, `previous_hash` de la decisión
aceptada previa, actor, motivo y evidencia. Omitir una decisión no elimina, revoca ni cierra
el episodio. Una contradicción permanece en revisión al omitirla o volver al valor anterior;
resolver requiere una decisión válida explícita. Un ámbito con decisión pendiente retiene
sus atribuciones, preservando la fila Delta aceptada e historial; otros ámbitos avanzan.
La resolución queda registrada con la publicación.

`association_intents` prepara antes/después/hashes antes de escribir. El escritor valida el
estado final, identidad estable, metadatos de operación/propietario y conteos del MERGE.
No hay overwrite, borrado por ausencia ni commits vacíos. Preparación, commit y acuse se
reconcilian sin repetir efectos; SQLite/Delta no forman una transacción global. Los reintentos
verifican los artefactos congelados aunque cambie el CSV vivo.

Una revisión recalcula actualmente **todas las observaciones aceptadas**, incluidos los
límites antiguos/nuevos, conservando la atribución anterior. Recomputación incremental por
ámbitos y optimización del control quedan para la siguiente entrega; no se declara una
latencia productiva. La lectura de inspección carga el registro de auditoría completo.

## Comandos

Copiar y adaptar `config/asociacion_produccion_ramal.example.toml` fuera del código, con
raíces/planta/mapeos del catálogo. Configurar opcionalmente el JSON revisado.

```powershell
python -m ingesta_delta_lake associations build --config 'C:/Ingesta_Delta_Lake_ensayo/asociaciones.toml'
python -m ingesta_delta_lake associations inspect --config 'C:/Ingesta_Delta_Lake_ensayo/asociaciones.toml'
python -m ingesta_delta_lake associations inspect --config 'C:/Ingesta_Delta_Lake_ensayo/asociaciones.toml' --source-id '<hash físico>'
python -m ingesta_delta_lake associations inspect --config 'C:/Ingesta_Delta_Lake_ensayo/asociaciones.toml' --production '<id 1>' --production '<id 2>'
```

`build` prepara/publica explícitamente. `inspect` no escribe ni reconstruye: lee la última
publicación confirmada y verifica frescura de fuentes, decisiones, mapeos y operaciones.
Cambios: `STALE`; cobertura pendiente: `PARTIAL`. Un commit pendiente de acuse no expone una
mezcla: se conserva la publicación confirmada anterior y se marca obsoleta. `--source-id`
explica fila/periodo/decisiones; `--production` comprueba conjunto físico ZC y porcentaje
compartido. No constituye una API de consulta de la app.

JSON y códigos: 2 parcial/obsoleto, 3 fallo técnico/configuración, 4 ausencia de publicación.
El comando `ingest` rechaza esta entidad e indica usar `associations build`.

## Conciliación real del 16/10/2026

Se reconstruyeron nueve fuentes y catálogo en un ensayo separado. Noventa CSV originales
de AV21/22/23/31/32 de los días 13–14 conservaron sus hashes. La derivación y su replay
preservaron todos los archivos Silver fuente, sin tocar el backend/frontend.

| Familia | Filas físicas | Atribuidas | Pendientes |
| --- | --- | --- | --- |
| LISTA_DEFECTOS | 33.937 | 0 | 33.937 |
| LISTA_RECH | 202.931 | 0 | 202.931 |
| LISTA_AR | 177 | 118 | 59 |
| CONT_ZC | 827 | 0 | 827 |
| Total | 237.872 | 118 | 237.754 |

Las 59 AR pendientes: 20 ambiguas de AV31, una sin producción publicable y 38 sin zona
interpretable por la regla actual. Son filas conservadas, no pérdidas ni botellas.
Se verificaron 237.872 claves físicas distintas y fuente=atribuidas+pendientes.

Se obtuvieron **16 candidatas**: dos en cada AV21/22/23, ramales 1–2; cuatro ambiguas de AV31,
modelo 1358/242 entre raíces 1617/1643; seis de AV32: 2001/477 en 1–2, 2169/477 en 1–2 y
2168/242 en 3–4. Las dos de 2169/477 quedan `PRODUCTION_UNAVAILABLE` por el caso MP9/catálogo
AV32/2007–2008 pendiente. No se aprobaron extremos observados como fechas efectivas.

Sin decisiones reales: **cero episodios ZF aceptados**, ninguna Delta vacía y atribución MIA/ZC
pendiente explícita. Publicación/corrección de episodios, preparación y ZC se verifican con
casos sintéticos e integración Delta; no se inventaron aprobaciones de planta. Replay con
cero inserciones/actualizaciones y artefactos idénticos.

Evidencia externa al checkout:
`LOCALAPPDATA/Ingesta_Delta_Lake/validation/20261016T124135Z-associations-25d4fc06/`:
`foundations.json`, `association-validation.json` y `association-candidates.json`.
Derivación 10,95 s y replay 10,64 s en este equipo, sin constituir benchmark de concurrencia.

Este resultado histórico v1 se conserva. La identificación física AV31 y la convención de
transición quedan incorporadas en v2. La nueva conciliación real se ejecutará en el servidor
después de GitHub/CI; siguen pendientes presencias/preparación ZC, AR ambiguos, ZF_OK,
integración app/score, minutos-cavidad, optimización y política horaria productiva.
