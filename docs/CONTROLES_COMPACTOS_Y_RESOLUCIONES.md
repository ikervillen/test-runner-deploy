# Controles compactos y resolución autorizada

Estado: implementación local. No implica publicación en GitHub, despliegue,
parada de servicios ni modificación del almacenamiento del servidor.

## Entregas y dependencias

1. **Retención y mantenimiento de controles.** `retention.py`, `maintenance.py`,
   lectura compatible en `Control.prepared`, compactación después de confirmar
   y protección de compatibilidad en runtime/despliegue.
2. **Escritor de decisiones.** `review_adapters.py`, `reviews.py`, filtrado de
   variantes expresamente rechazadas en cada fuente y exportación de capacidades.
3. **Integración con supervisión.** Migración PostgreSQL aditiva, autorizaciones,
   bandeja persistente, recibos verificados y presentación. Se desarrolla en el
   repositorio de supervisión, sin introducir sus dependencias en la ingesta.

La publicación posterior debe conservar estas entregas como cambios revisables
separados. No agrupar toda la modificación en un único PR.

## Qué se conserva y qué ocupa menos

Los JSON grandes de lotes confirmados y sus intenciones se comprimen sin pérdida
con zlib y SHA-256. Se conserva íntegramente su contenido, no solo su huella.
Permanecen consultables las claves, estados, versiones Delta y confirmaciones.
El decodificador acepta tanto los controles antiguos en texto como el formato 2.

No se eliminan los identificadores de control, recibos, colas de capturas,
confirmaciones, incidencias, evidencias, reglas de rechazo ni el índice actual de
inspecciones. Las operaciones PREPARED/WRITING/UNKNOWN permanecen intactas.
Los JSON pequeños se mantienen en texto para evitar sobrecoste. Los valores de
más de 256 MiB permanecen en texto: requieren un análisis específico, nunca una
compresión que después el decodificador no pueda recuperar.

Esta entrega no borra automáticamente auditoría histórica ni reconstruye Delta.
La compresión por lote reduce el crecimiento y libera páginas reutilizables,
pero **el tamaño físico de un SQLite existente no disminuye hasta sustituirlo**.
El tamaño final depende de sus contenidos y se mide, no se presupone.

## Sustitución segura del SQLite existente

La herramienta exige los mismos bloqueos globales y de familia que el escritor.
Una ingesta activa impide el mantenimiento. Primero se debe drenar el worker
mediante el procedimiento cooperativo existente, sin SIGKILL.

1. Inventario sin `--apply`: tamaño, ruta y operaciones pendientes.
2. Checkpoint completo del WAL y comprobación de integridad y claves foráneas.
3. Copia a un archivo nuevo, fila a fila, con las compresiones aplicables. No se
   materializa la base completa en RAM ni se copian datos Delta.
4. Validación de integridad y comparación por tabla del número de filas y SHA-256
   de su contenido lógico descomprimido. Se conserva el orden de inserción.
5. Enlace de respaldo al archivo anterior y reemplazo atómico del nombre activo.
   Si el sistema de archivos no admite el respaldo seguro, se aborta.
6. Nueva validación y reporte `<control>.compaction-report.json`. Solo
   `--remove-backup` elimina el archivo pesado anterior, después de verificar.

Una interrupción antes del reemplazo conserva el control original; después del
reemplazo el nombre activo apunta a una copia previamente validada. Un respaldo
ya existente bloquea otra sustitución hasta revisar la intervención anterior.
No se debe restaurar ese respaldo tras reanudar escrituras nuevas: quedaría
desfasado respecto a Delta.

Ejemplo local, sobre una configuración aislada explícita:

```powershell
python -m ingesta_delta_lake.maintenance --config fuente.toml
python -m ingesta_delta_lake.maintenance --config fuente.toml --apply --remove-backup
```

La imagen admite también `compact-control --family LISTA_RECH` para inventario,
y los mismos argumentos `--apply --remove-backup` para la sustitución, usando su
descriptor y volumen existentes. Debe ejecutarse con el worker detenido y la
imagen nueva validada, mediante el helper de mantenimiento con montaje RW.
El comando no activa ni reinicia la ingesta.

El formato de control se anuncia en el manifiesto de imagen. Una vez activada
una imagen con formato 2, el despliegue no puede reactivar automáticamente una
imagen anterior que solo interprete texto. Los fallos inciertos se recuperan
con la imagen nueva; un rollback binario incompatible queda bloqueado.

## Contrato de decisiones y aislamiento

Se utilizan dos volúmenes adicionales, configurables e independientes por entorno:

| Canal | Supervisión | Ingesta |
|---|---|---|
| `commands` | Lectura/escritura | Solo lectura |
| `results` | Solo lectura | Lectura/escritura |
| CSV Fenix | Sin escritura | Solo lectura |
| Delta y controles industriales | Solo lectura | Único escritor |

Ver `deployment/review-channel.example.json`: sus valores se incorporan a cada
entorno del descriptor del servidor cuando se autorice la activación. El gestor
valida nombres, etiquetas de entorno/instancia y propiedad; no adopta volúmenes
ajenos ni recrea silenciosamente un canal activo desaparecido.

Los comandos tienen UUID, ámbito, generación del control, huella de la incidencia,
acción y versión, candidato exacto, proponente, autorizador y fecha. No contienen
SQL, rutas de destino elegibles ni edición libre de valores.

El escritor procesa hasta cinco decisiones por turno bajo el bloqueo de su tabla.
Antes de una decisión nueva deben reconciliarse las operaciones ordinarias
pendientes. Una decisión PREPARED propia se recupera antes de ingerir nuevos datos.

## Acciones y límites de cada contrato

Se soportan los cuatro diccionarios, MP9, LISTA_AR, LISTA_RECH, LISTA_DEFECTOS,
CONT_ZC y los dos catálogos físicos. Los derivados se reconstruyen desde sus
fuentes y no se editan mediante este circuito.

- **Mantener el dato aceptado:** verifica que sigue siendo el dato publicado,
  registra el rechazo de las alternativas observadas, libera los bloqueos
  cubiertos y conserva evidencia y auditoría. No crea un commit Delta vacío.
- **Sobrescribir:** exige un candidato elegido de la evidencia y un único
  registro homólogo, con la misma clave. Valida tipos, unicidad y restricciones
  específicas; MP9 comprueba sus relaciones ZC/ZF y modelos; las MIA mantienen
  sus denominadores coherentes; topología comprueba vigencias y relaciones.
  El MERGE compara todos los valores anteriores, incluidos nulos, y debe
  actualizar exactamente una fila sin inserciones ni borrados.

Si la alternativa cambia la clave, puede rechazarse expresamente manteniendo el
dato aceptado; no se transforma en una sobrescritura entre identidades distintas.
Las dependencias MODEL_PENDING/ZC_PENDING/MACHINE_PENDING, errores de formato,
casos sin dato aceptado o conflictos de varias identidades no reciben una opción
de sobrescritura indiscriminada: el panel explica la limitación y permite
solicitar información. No se declara resuelto lo que requiere otra acción.

El rechazo se aplica solo a huellas exactas. Si llega un valor diferente se
ejecutan las comprobaciones habituales y, si procede, se genera otra incidencia.
Los casos relacionados solo se liberan si todas sus alternativas quedan cubiertas
por la decisión autorizada. No se resuelven por compartir archivo o fecha.

## Verificación, recuperación y seguimiento

Antes de escribir se persiste la intención con versión y huellas anterior/final.
El commit Delta incluye el identificador de decisión y operación. Tras escribir
se releen los datos y se comprueba versión, huella y número de filas afectadas.
Si el proceso cae después del commit, se reconoce esa operación y se confirma
sin escribirla otra vez. Un cambio externo incompatible mantiene la operación
pendiente y detiene esa familia para revisión.

Solo después de la verificación se confirman las reglas de rechazo, cierres de
incidencias e índices MIA dentro de la misma transacción SQLite. El resultado
duradero permite reemitir el recibo aunque falle el transporte. La repetición de
un UUID con otro contenido se rechaza.

La proyección exporta tamaño de SQLite, WAL y páginas libres para comprobar la
evolución posterior. No se modifica el tablero de Grafana en esta entrega.

## Puesta en marcha posterior a la aprobación

Publicar y revisar las entregas; validar imágenes en CI; drenar el worker;
inventariar y sustituir los controles con la imagen nueva; configurar los dos
canales y sus permisos; migrar PostgreSQL; activar escritor y supervisión;
comprobar tamaños, frescura y un caso real autorizado. Nunca borrar los controles
antes de conservar y validar sus claves e intenciones. El piloto remoto y los
resultados de permisos de Docker siguen pendientes de esa autorización.
