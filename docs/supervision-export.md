# Contrato de exportación de supervisión, versión 1

El despliegue Docker habilita `INGESTA_SUPERVISION_EXPORT=1`. Fuera de él es
optativo. El único escritor de cada fuente publica después de confirmar su
intento, en `control/supervision/<nombre-control.sqlite>.json`. No modifica
esquemas SQLite, CSV ni tablas Delta. Los lectores montan el volumen en lectura.

El documento contiene ámbito (canal, planta, fuente, identidad del control),
fecha UTC de observación, incidencias y evidencias, estado del último intento
y última publicación Delta confirmada. El hash SHA-256 se calcula sobre el JSON
UTF-8 canónico, con claves ordenadas y separadores `,` y `:`, sin `sha256`.
`schema_version=1` y `complete=true` son obligatorios. La publicación incluye
versión Delta, identificador de operación y fecha de confirmación; contrasta el
commit exacto con el índice único de operaciones del control.

Una transacción de lectura usa la conexión del escritor después del commit de
negocio. No abre otra conexión WAL, no recorre batches ni sus grandes payloads.
Límites: 3 segundos de consulta, 20.000 incidencias, 256.000 bytes por payload,
20 evidencias por incidencia y 16 MB por documento. El total de evidencias y su
truncamiento explícito acompañan cada incidencia. Se exportan todos los casos,
incluidos sus estados; una observación parcial nunca se publica como completa.

Cada fuente exporta como máximo una vez por minuto y solo cuando ejecuta un
intento. No es un temporizador independiente: una fuente que deja de ejecutarse
conserva su fecha anterior y puede aparecer desactualizada. La escritura usa un
temporal en el mismo directorio, fsync y reemplazo atómico. Un fallo conserva el
archivo previo y se registra sin payloads; nunca cambia el resultado de ingesta.
El siguiente intento elegible vuelve a probar. No es una copia de seguridad del
control ni un sustituto del registro de auditoría.

El consumidor valida checksum, versión, ámbito, límites y fecha. No debe recurrir
silenciosamente al SQLite activo ni interpretar un archivo ausente como cero
incidencias. Debe conservar la fecha del productor y la última proyección buena.
