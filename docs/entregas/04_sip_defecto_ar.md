# Entrega 04 — SIP_DEFECTO_AR

Estado: implementación local de ensayo completada. Dependencias: entregas 01 a 03.
Contrato: `sip_defecto_ar_aiala_v1`, sección 4.4 del plan maestro.

## Alcance implementado

`tipos_ar.py` valida las cuatro columnas Fenix en su orden original, con separador final
opcional y sin cabecera inferida. Publica el esquema acordado, sin nulos:

| Campo | Tipo |
| --- | --- |
| `id_tipo_ar_fenix` | int64 |
| `codigo_tipo_ar` | int64 |
| `descripcion` | string no vacío |
| `codigo_agrupacion` | int64 |

Los enteros se convierten exactamente, sin float, valores imputados ni pérdida de rango.
La descripción solo pierde espacios en los extremos; sus acentos y nombres se conservan,
incluido `Rechazo molde NP` para el código 5. La evidencia mantiene el valor previo a la
normalización. No se aplica el filtro NO USAR del catálogo de defectos a este contrato.

ID y código son únicos en el catálogo compartido de planta. No se confunden aunque coincidan
en la muestra. La relación futura es `LISTA_AR.TIPO → codigo_tipo_ar`; la ingesta de eventos
y esa unión siguen pendientes. Descripción y agrupación pueden repetirse sin fusionar tipos.
Se admiten nuevos valores int64 de agrupación sin inventar su significado ni cerrar el
dominio a los valores observados. El grupo 2 no se etiqueta como avisos: incluye también
rechazos de trampilla.

## Escritura y garantías

Cada revisión estable se captura en Bronze y se contrasta con las candidatas y el snapshot
publicado. Solo se insertan novedades resueltas, bajo bloqueo. No hay actualizaciones ni
bajas automáticas, overwrite, evolución automática de esquema o commits por repeticiones.
Un archivo vacío tampoco crea una tabla vacía.

Un cambio de ID, código, descripción o agrupación queda en revisión con candidata completa,
valores aceptados relacionados, campos discrepantes (`changed_fields`) y procedencia.
Ninguna alternativa se elige por fecha, orden o mayoría. Un inválido/conflicto bloquea la
unidad completa de esta familia, sin publicar parcialmente otras novedades.

Se auditan recepciones, revisiones únicas por origen/hash, filas, repeticiones consolidadas,
coincidencias, conflictos, inválidos, candidatas nuevas e inserciones efectivas. Se conserva
la primera evidencia de las entradas publicadas. La intención preparada y sus copias
inmutables permiten recuperar un fallo aunque se sobrescriba posteriormente el CSV vivo.
Un commit ya confirmado se reconoce por operación, versión y contenido antes de reintentar.

## Configuración y compatibilidad

- Ejemplo: `config/sip_defecto_ar.example.toml`; `family = "SIP_DEFECTO_AR"`.
- Codificación explícita de la muestra: `cp1252`.
- Silver: `<silver_root>/<planta>/sip_defecto_ar`.
- Control: `<control_root>/control-sip_defecto_ar.sqlite`.
- Bronze: `<bronze_root>/<planta>/<origen>/SIP_DEFECTO_AR/<fecha UTC>/<sha256>.csv`.

Se reutilizan captura, CLI, control y escritor sin cambiar los contratos ni las ubicaciones
de las entregas anteriores. Las cuatro familias pueden compartir raíces, con tablas y
controles independientes y un bloqueo Silver común. No hay nuevas dependencias o cambios
del lockfile. La ejecución sigue limitada a test/development locales. GitHub, CI remota,
despliegue y aplicación permanecen fuera de esta entrega.

## Evidencia del 16/10/2026

**196 pruebas superadas; cobertura de líneas/ramas del 95,25 %.** Se mantienen las 159
anteriores. Python 3.12.14, deltalake 1.6.6 y Arrow 23.0.1. Ruff y formato pasan.

Las pruebas nuevas cubren tipado/rango y orden de campos; ID distinto de código; nombres
con acentos; agrupación compartida y nuevos grupos; descripciones compartidas; conflictos
en los cuatro campos; ausencia sin borrado; no escritura al repetir, renombrar o normalizar
espacios; sobrescritura de nombre fijo/fechado; consolidación entre líneas; evidencia
íntegra y multiplicidad; estructura/codificación incompatibles; corrupción de Bronze;
fallos antes/después del commit y de acuse; recuperación desde revisión preparada y
convivencia de los cuatro diccionarios sin mezclar controles.

Los diez CSV reales de AV21, AV22, AV23, AV31 y AV32 del 13/14 de octubre contienen **855
filas de revisiones únicas por origen/hash**, consolidadas en **nueve entradas** y 846
repeticiones, sin inválidos ni conflictos. Se verifican código 2/Aviso ZC, código 5/Rechazo
molde NP, acentos de Anotación ZC/ZF y los códigos originales de agrupación.

El replay conserva la versión Delta 0, contenido y huellas de todos sus archivos; informa
cero inserciones y nueve coincidencias. Los diez CSV originales mantienen sus huellas.
Auditoría: dos intentos, veinte recepciones y nueve intenciones confirmadas una sola vez.

El informe JSON y los datos de ensayo se guardan fuera del checkout, bajo
`LOCALAPPDATA/Ingesta_Delta_Lake/validation/20261016T092613Z-tipos-ar-d1090a86/`.
Esta evidencia local no acredita operación productiva remota. Siguiente unidad:
entrega 05, `MP9_CAMBIO_MODELO`.
