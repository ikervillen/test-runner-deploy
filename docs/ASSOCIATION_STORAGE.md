# Almacenamiento compartido de asociaciones

Inventario real del 23/10/2026: 543 publicaciones, 1.086 snapshots comprimidos,
36.150.705.596 bytes. Todos estaban referenciados; borrarlos por fecha destruiría
la evidencia histórica. La última publicación usaba solo dos archivos (75.644.546
bytes), pero las anteriores seguían siendo necesarias según el contrato vigente.

## Escritura y lectura

Los documentos JSON mantienen exactamente sus bytes y su hash de contenido.
Se dividen por contenido en bloques de hasta 256 KiB, comprimidos y direccionados
por SHA-256, compartidos entre publicaciones. Una inserción no desplaza todos los
bloques posteriores. Cada publicación guarda un manifiesto pequeño; no una nueva
copia de todos los datos. No se añaden fuentes Fenix ni tablas de negocio.

La lectura admite archivos antiguos y manifiestos nuevos, verifica hashes de
archivo, bloques y documento completo. El despliegue impide regresar a una imagen
que no pueda leer el nuevo formato.

## Conversión reanudable

La operación `compact-associations` usa la imagen activa aprobada y el bloqueo de
despliegue. Mantiene el escritor de ingesta en funcionamiento; el auxiliar tiene
límite de una CPU y 512 MiB. Procesa lotes de hasta 32 archivos sin deserializar
sus filas y guarda un justificante por archivo en `migration.jsonl`.

Para cada archivo: lectura gzip por bloques, comprobación de SHA-256 comprimido y
descomprimido, publicación durable de bloques/manifiesto, lectura verificada de
los bloques nuevos, publicación y fsync de la redirección y, solo entonces,
retirada del archivo antiguo. Las referencias originales de publicaciones,
recibos y operaciones pendientes no cambian. Un fallo conserva el original o una
redirección durable al mismo contenido. Repetir la operación retoma lo pendiente.

Se conservan los hashes y la evidencia completa de todas las publicaciones. No
se trasladan las copias a otro archivo histórico ni se cambia su semántica. Los
objetos compartidos nunca se borran por antigüedad: su retirada exigiría verificar
las referencias de todos los manifiestos.

El inventario de mantenimiento incluye ahora todos los subdirectorios de control,
incluido `association-artifacts`; el informe distingue SQLite, objetos compartidos,
manifiestos, redirecciones y archivos antiguos restantes. La reducción definitiva
debe medirse en el servidor, sin extrapolar el resultado de las pruebas locales.
