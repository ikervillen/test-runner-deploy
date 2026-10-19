# Entrega 07 — LISTA_DEFECTOS

Estado: implementación local de ensayo completada. Contrato `lista_defectos_v2`,
[sección 4.6 del plan](../PLAN_DELTA_LAKE.md).
GitHub, despliegue e integración en la app continúan aplazados.

## Datos conservados

Esquema: `FECHA`, `MOLDE`, `LINEA`, `COD_MAQ_ZF`, `COD_DEF`, `INSPECC`, `RECH`,
`RECH1`–`RECH10`, `CAVIDAD`, en ese orden, más `origen_fenix` técnico. Planta en
ubicación/configuración y control vinculado. FECHA es timestamp(us) sin zona, leído como
día/mes/año bajo la política explícita de ensayo `local_naive_test_v1`; zona/DST productivas
siguen pendientes. Los 16 campos numéricos son int64 exactos; molde y cantidades no negativos.
No se inventan códigos ni se interpreta COD_DEF mediante el diccionario de AR SIP_DEFECTO.

La cavidad vacía es nula y compara igual a otra nula; texto como `8F`, `1C`, `99Z` o `null`
permanece texto, sin filtrar filas. Solo se retiran espacios extremos. El separador final
opcional no añade otra columna. RECH y RECH1–10 se conservan sin sustitución ni recálculo:
la suma de canales no tiene que coincidir con RECH. Estructuras inesperadas, fechas imposibles,
vacíos obligatorios, fracciones o desbordamientos quedan apartados con evidencia original.

## Tres comprobaciones distintas

- `K = (origen, FECHA, MOLDE, LINEA, COD_MAQ_ZF, COD_DEF, INSPECC, CAVIDAD)`:
  misma clave y 18 campos iguales → coincidencia sin escritura. Misma clave con otros
  rechazos/canales → candidata íntegra para revisión; conservar lo aceptado.
- `S = (origen, FECHA, MOLDE, LINEA, COD_MAQ_ZF, COD_DEF, CAVIDAD)`:
  otro INSPECC es posible revisión, no otra cantidad que insertar/sumar automáticamente.
- `G = (origen, FECHA, MOLDE, LINEA, COD_MAQ_ZF, CAVIDAD)`:
  defectos distintos comparten una base solo si informan un único INSPECC compatible.
  Cantidades contradictorias no se resuelven por máximo, mínimo, mayoría o última llegada.

Se comparan tuplas tipadas completas; un hash técnico no reemplaza su semántica ni introduce
una columna de negocio. Máquina, origen, fecha, molde, ramal o cavidad diferentes separan
contextos. Igualdad numérica de INSPECC no fusiona esos contextos. Las cinco primeras filas
de AV23 aportan una base de 96, no 480; la sexta aporta 89 en otra máquina. La suma entre
máquinas representa inspecciones por contexto, no botellas únicas de la planta.

Las filas aceptadas de defecto se conservan separadas. Una revisión afecta la cobertura de
su base, sin borrar lo aceptado. Las unidades independientes válidas pueden publicarse con
`PARTIAL`/`complete=false`; si ninguna es publicable, `BLOCKED`. Una contradicción no desaparece
al omitir una alternativa en la próxima copia. Los casos sin contexto interpretable impiden
dar por resueltas las bases del origen afectado. El estado global sigue incompleto aunque
se filtre la consulta por otro origen. No se ha implementado resolución manual de revisiones.

## Escritura, auditoría y consulta

- Silver: `<silver_root>/<planta>/lista_defectos`.
- Control: `<control_root>/control-lista_defectos.sqlite`.
- Configuración: `config/lista_defectos.example.toml`.
- CLI: `ingest --config ...`; lectura: `inspection-bases --config ... [--origin AV23]`.

Se reutilizan captura estable Bronze, revisiones por origen/hash, bloqueo de escritor y
reconciliación operación/versión/contenido. Nombres fijos sobrescritos y nombres fechados
convivientes siguen el mismo contrato. Ausencias no borran; repeticiones no generan commits.
La Delta es append de novedades validadas, sin overwrite, actualización ni tabla por archivo.

`observation_intents` guarda clave/firma/grupo, candidata completa, evidencia, hash y estado
antes del append; después confirma versión una sola vez. `issues` mantiene la candidata y
las variantes relacionadas para revisión, con primera evidencia; `issue_evidence` separa
la trazabilidad de lotes/recepciones de la identidad semántica del caso. `inspection_groups`
registra las bases del lote confirmado. El lote preparado conserva sus Bronze/hash y se
recupera incluso si cambia el CSV vivo. Un commit sin acuse se confirma tras contrastarlo,
sin duplicar datos. La consulta de bases rechaza operaciones técnicas pendientes o versiones
que no coincidan con el control; no crea controles/tablas ausentes ni escribe otra Delta.

Esta consulta es diagnóstica: no sustituye LISTA_RECH como denominador preferido del score.
Se revisaron en modo lectura el esquema, `customer_score`, `inspected_totals` y los agregados
MIA del backend. No se ha conectado Delta a la app ni activado su fallback sobre defectos.
La comparación histórica de tasas/score se abordará con las fuentes restantes y el adaptador
de consumo. No se asignan todavía producciones ni se cambian filtros o fórmulas.

## Conciliación real del 16/10/2026

Lectura de diez CSV de los días 13/14 de AV21, AV22, AV23, AV31 y AV32, sin modificarlos.
Comprobación independiente con csv/datetime/int, comparando la totalidad de los 18 campos
y el origen contra el snapshot Delta:

| Comprobación | Resultado |
| --- | --- |
| Filas leídas y publicadas | 33.937; igualdad íntegra con la fuente |
| Grupos de inspección compatibles | 23.419 |
| Inspecciones por contexto resuelto | 2.462.774 |
| Suma directa incorrecta de INSPECC entre defectos | 7.473.317; no se usa como base |
| Conflictos, revisiones de contador e inválidos | 0 en esta muestra |
| Primer procesamiento | APPLIED, versión 0 |
| Replay | NO_CHANGE, cero inserciones, mismos archivos y hashes Delta |
| Auditoría | 20 recepciones, 33.937 intenciones confirmadas, ninguna pendiente |
| Originales | Los diez CSV conservan sus hashes |

Informe y datos de ensayo fuera del checkout, bajo
`LOCALAPPDATA/Ingesta_Delta_Lake/validation/20261016T102124Z-lista-defectos-48d77702/`.
Primera carga aproximadamente 17,56 s y replay 14,68 s en este equipo; son mediciones de
esta muestra, sin garantía de rendimiento productivo. Se compara el snapshot en memoria;
la optimización de históricos masivos y validación remota se mantienen pendientes.

Resultado: **307 pruebas superadas y cobertura de líneas/ramas del 93,03 %**; Ruff y formato
pasan. Las 39 pruebas nuevas cubren claves/nulos, bases compartidas, contexto entre máquinas,
rechazos/canales intactos, revisiones de INSPECC, persistencia de contradicciones, tipos,
recepciones duplicadas, sobrescrituras y fallos antes/después del commit/acuse. Se mantienen
las pruebas anteriores. Python 3.12.14, deltalake 1.6.6 y Arrow 23.0.1; sin dependencias nuevas
ni cambios del lockfile. Siguiente unidad: entrega 08, LISTA_RECH.
