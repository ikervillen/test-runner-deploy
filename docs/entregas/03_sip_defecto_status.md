# Entrega 03 — SIP_DEFECTO_STATUS

Estado: implementación local de ensayo completada. Dependencias: entregas 01 y 02.
Contrato: `sip_defecto_status_aiala_v1`, sección 4.3 del plan maestro.

## Alcance implementado

El lector `status.py` comprueba los siete campos Fenix en su orden original, con separador
final opcional. Publica el esquema Silver acordado, sin nulos:

| Campo | Tipo |
| --- | --- |
| `id_status_fenix` | int64 |
| `codigo_status` | int64 |
| `descripcion` | string no vacío |
| `descripcion_corta` | string no vacío |
| `codigo_origen` | int64 |
| `codigo_zona` | int64 |

Los enteros se convierten sin float ni pérdida; se rechazan vacíos, fracciones y valores fuera
de rango. Las descripciones pierden únicamente espacios en los extremos. Se conservan texto,
acentos, abreviaturas y nombres Fenix, incluidos `Delfoz ZC/ZF`.

`ACTIVO` queda fuera del esquema, hash y comparación. Su valor no filtra entradas: `Muestreos ZC`
se conserva aunque lo traiga vacío. No se aplica a este contrato el filtro NO USAR de defectos.
ORIGEN/ZONA siguen siendo códigos numéricos sin etiquetas inventadas, booleanos ni enumeración
cerrada de valores observados. Su significado pendiente no impide conservarlos fielmente.

Se exige unicidad de ID y de código, sin fusionar descripciones coincidentes. Aunque en la
muestra los ID coinciden con los códigos, se mantienen separados. La relación futura de AR
es `LISTA_AR.STATUS → codigo_status`; la ingesta de AR y esa unión siguen pendientes.

## Escritura y auditoría

Cada revisión se captura de forma estable en Bronze y se valida antes de publicar. Solo se
insertan entradas nuevas resueltas. Ausencias, repeticiones y cambios solo de ACTIVO no borran,
sobrescriben ni generan commits. Un CSV vacío tampoco crea una tabla vacía.

Una contradicción de ID/código o un cambio de descripción, abreviatura, origen o zona deja
candidata íntegra, valores aceptados, campos discrepantes (`changed_fields`), procedencia y
estado pendiente de revisión. Un inválido o conflicto bloquea toda la unidad de esta familia,
sin publicar parcialmente las candidatas independientes. No se elige por mayoría, fecha o
posición física. Estructuras nuevas no provocan evolución automática de esquema.

Las recepciones, revisiones únicas por origen/hash, filas, repeticiones consolidadas,
coincidencias, conflictos, inválidos e inserciones quedan registrados. El replay de un lote
confirmado informa cero novedades y conserva su primera evidencia y la versión Delta.

## Configuración y compatibilidad

- Ejemplo: `config/sip_defecto_status.example.toml`; `family = "SIP_DEFECTO_STATUS"`.
- Codificación explícita de la muestra: `cp1252`.
- Silver: `<silver_root>/<planta>/sip_defecto_status`.
- Control: `<control_root>/control-sip_defecto_status.sqlite`.
- Bronze: `<bronze_root>/<planta>/<origen>/SIP_DEFECTO_STATUS/<fecha UTC>/<sha256>.csv`.

Se reutilizan CLI, captura, escritor, bloqueo Silver, intenciones y reconciliación. Las tres
familias pueden compartir raíces sin mezclar controles ni versiones. Se mantienen los nombres
de control y las operaciones pendientes de las entregas anteriores. La conversión int64 común
se comparte con defectos sin cambiar sus reglas. No se añaden dependencias ni cambios de lockfile.

La ejecución sigue limitada a destinos test/development locales. Esta entrega no cambia
la aplicación, los datos originales, GitHub, CI ni despliegues.

## Evidencia del 16/10/2026

**159 pruebas superadas; cobertura de líneas/ramas del 94,70 %.** Las 114 pruebas anteriores
se mantienen. Python 3.12.14, deltalake 1.6.6 y Arrow 23.0.1. Ruff y formato pasan.

Las pruebas nuevas cubren estructura, tipado, orden de campos, ACTIVO vacío/cambiante,
conservación de nombres, códigos nuevos de origen/zona, texto coincidente sin fusión,
ID distinto de código, discrepancias en los seis campos, ambas modalidades de entrada,
duplicados dentro y entre orígenes, convivencia de tres contratos y fallos antes/después
del commit o del acuse SQLite. También verifican recuperación desde evidencia preparada
aunque el CSV vivo cambie, ausencia sin borrado, corrupción de Bronze y errores de CSV/codificación.

Conciliación de los diez archivos reales del 13/14 de octubre de AV21, AV22, AV23, AV31 y AV32:
**760 filas de revisiones únicas por origen/hash → ocho entradas**, 752 repeticiones
consolidadas, cero inválidos y conflictos. La versión inicial Delta es 0. Se comprueban
`Muestreo IFA`, ambos Delfoz y `Muestreos ZC`, con los códigos originales intactos.

El segundo procesamiento mantiene versión y huellas de todos los archivos Delta, con cero
inserciones y ocho coincidencias. Los diez CSV originales también conservan sus huellas.
Auditoría: dos intentos, veinte recepciones y ocho intenciones confirmadas una sola vez.

El informe JSON y los datos de ensayo están fuera del checkout, bajo
`LOCALAPPDATA/Ingesta_Delta_Lake/validation/20261016T091225Z-status-518789fe/`.
No se publican datos reales ni se acredita operación productiva remota. Siguiente unidad:
entrega 04, `SIP_DEFECTO_AR`.
