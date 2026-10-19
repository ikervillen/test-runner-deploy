# Entrega 02 — SIP_DEFECTO

Estado: implementación local de ensayo completada. Dependencia: entrega 01.
Contrato: `sip_defecto_aiala_v1`, sección 4.2 del plan maestro.

## Alcance implementado

`defectos.py` valida el CSV original y conserva, en orden, `id_defecto_fenix` int64,
`codigo_aviso_rechazo` int64, `descripcion` string y `descripcion_corta` string,
todos no nulos. Retira espacios en los extremos de las descripciones sin cambiar el resto.
No acepta fracciones, enteros fuera de rango, descripciones vacías ni estructura incompatible.
La codificación se configura explícitamente; para esta muestra se usa Windows-1252.

Se excluyen `MEDIDA` del negocio y las entradas cuyo marcador completo `NO USAR` aparezca
en cualquiera de las descripciones, con prefijo numérico y `:` opcional. El filtro se aplica
después de comprobar la estructura y antes de validar tipos y unicidad de entradas no
excluidas. Las exclusiones tienen motivo y raw completos en su auditoría, separados de
las incidencias. Se conserva el número de repeticiones exactas sin inflar las evidencias.

ID y código son únicos en el catálogo compartido de planta. Las descripciones no son claves.
Una modificación de código, ID o descripción se conserva para revisión, con lo aceptado y
la candidata. No se elige una versión por fecha o repeticiones; un inválido/conflicto bloquea
la unidad completa antes de publicar. Ausencias y nuevas observaciones NO USAR no borran
entradas aceptadas. Los cambios solo en MEDIDA no producen commits ni conflictos.

## Reutilización y aislamiento

La configuración selecciona una familia por ejecución mediante `family`; la omisión mantiene
`SIP_MODELO`. `contracts.py` adapta los dos contratos al mismo mecanismo de captura,
validación de snapshot, intenciones, commit y reconciliación. No incorpora los otros siete.

- Silver: `<silver_root>/<planta>/sip_defecto`.
- Control: `<control_root>/control-sip_defecto.sqlite`.
- Bronze: `<bronze_root>/<planta>/<origen>/SIP_DEFECTO/<fecha UTC>/<sha256>.csv`.
- Configuración de ejemplo: `config/sip_defecto.example.toml`.

Las raíces pueden compartirse con modelos, conservando controles, tablas y versiones por
separado, bajo el mismo bloqueo de escritor Silver. Se mantienen ubicación, formato de
intenciones, identidades y recuperación de los lotes preparados de la entrega 01; no se
migra su control ni se adopta una tabla ajena. No hay nuevas dependencias ni cambios del lockfile.

## Comprobaciones

**114 pruebas superadas; cobertura 93,97 %**, con Python 3.12.14, deltalake 1.6.6 y Arrow 23.0.1.
Ruff y la comprobación de formato pasan. Se mantienen las 62 pruebas de la primera entrega.

Las pruebas cubren filtrado desde ambas descripciones; exclusión antes de unicidad; códigos
551/552; códigos 41/42 sin fusionar; límites int64; estructura; textos y codificación estricta;
repeticiones dentro y entre orígenes; MEDIDA ignorada; renombrados y sobrescrituras;
exclusiones sin tabla vacía; exclusiones junto a inválidos; conflictos persistentes sin
sobrescritura; fallos antes/después del commit y en el acuse SQLite; recuperación de una
revisión aunque el CSV vivo haya cambiado; convivencia y compatibilidad con la entrega 01.

Se distinguen filas leídas por revisión única origen/hash, repeticiones consolidadas,
entradas únicas, exclusiones físicas/distintas, inválidos, conflictos, coincidencias,
candidatas nuevas e inserciones efectivas. Todas las recepciones quedan auditadas.

## Conciliación real del 16/10/2026

Los diez CSV de AV21, AV22, AV23, AV31 y AV32 del 13/14 de octubre se han leído sin alterarlos.
Las revisiones únicas por origen/hash contienen **44080 filas**: **35910 válidas** y
**8170 excluidas**. Se consolidan **378 entradas válidas distintas** y **86 exclusiones
distintas**, con cero inválidos y conflictos. Se publican 378 filas en la versión Delta 0.

Los códigos 551/552 conservan ID 238/239 y descartan las observaciones NO USAR 407/408.
Los códigos 41/42 permanecen separados pese a compartir descripción corta. Un segundo
procesamiento conserva el contenido, las huellas de archivos y la versión Delta, sin
inserciones; las huellas de los diez CSV originales también son idénticas.

El informe JSON y los datos de ensayo se guardan fuera del checkout, bajo
`LOCALAPPDATA/Ingesta_Delta_Lake/validation/20261016T083028Z-defectos-3b3061aa/`.
Son evidencias locales, sin publicar datos reales. Esta entrega no acredita validación
productiva remota; GitHub y despliegue se tratarán después.
