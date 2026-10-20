# Entrega 16 — Artefactos de asociaciones con memoria acotada

La publicación anterior construía simultáneamente el JSON completo, su copia
UTF-8 y el gzip; su verificación volvía a deserializar los hechos completos.
Ahora el JSON canónico se genera incrementalmente, se comprime por bloques y
se calculan las huellas del payload y archivo sin cargar el archivo completo.
La identidad lógica del JSON, sus registros y las reglas de asociación se
conservan. El gzip determinista puede tener una huella física distinta del
compresor anterior; los artefactos antiguos continúan verificándose y leyéndose.

El temporal se sincroniza antes de enlazarlo a su nombre definitivo. Un archivo
existente nunca se sobrescribe: se comprueba su huella. Un fallo elimina solo
el temporal propio; un artefacto publicado sigue siendo evidencia inmutable.
La verificación de recuperación comprueba ambas huellas mediante streaming,
sin deserializar un segundo histórico. Las consultas explícitas que requieren
el contenido continúan usando la lectura JSON después de verificarlo.

El registro pequeño de decisiones aceptadas se conserva también en la publicación,
con su huella. Así no hay que cargar el bundle anterior completo para recuperar
unas pocas decisiones. Las publicaciones anteriores sin este campo usan su
artefacto original como compatibilidad, liberándolo antes de cargar las fuentes.

Una repetición con las mismas versiones/control de las fuentes, revisiones de
calidad, decisiones, correspondencias de línea, política de topología, horario,
contrato y regla reutiliza la publicación. Antes de hacerlo se verifican las
huellas de los artefactos, el registro de decisiones, el estado de revisión
propio y las asociaciones confirmadas. No se vuelven a cargar ni atribuir todos
los hechos y no se crea otra publicación o versión Delta. Cualquier diferencia
exige recalcular; se mantienen los pendientes de calidad, sin declararlos resueltos.

La construcción inicial de asociaciones sigue necesitando los hechos aceptados
en memoria: esta entrega acota sus copias de serialización/verificación, no
declara que toda la atribución histórica sea un algoritmo de memoria constante.
El supervisor ejecuta esa fase sin otros trabajadores simultáneos y después
de agotar las colas inventariadas.

Pruebas: identidad canónica con Unicode/nulos/números, determinismo, compatibilidad
gzip anterior, hashes corruptos, fallo previo a publicación, verificación sin
JSON/read_bytes, serialización de 12.000 filas con menos de 2 MiB adicionales
medidos por tracemalloc, repetición sin cargar hechos, invalidación por nuevas
fuentes y regresión de revisiones/recuperación/asignación a producción saliente.
