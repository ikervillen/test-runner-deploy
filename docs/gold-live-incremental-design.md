# Gold Live incremental

## Estado y límites de la primera entrega

La evidencia Live agrupa hechos y linaje por familia, origen y tramo. Una versión
legada se convierte al producir la siguiente entrada efectiva. Los manifiestos
y Parquet anteriores siguen siendo legibles. La atribución lee una ventana
acotada y la cobertura se calcula con rangos firmados de los fragmentos.
El CDF incompleto, un cambio de identidad de tabla, una retirada acreditada
por revisión o un borrado que requiera verificar su traspaso disparan la
reconciliación por familia. No se activa History ni se modifica su retención.

La raíz conserva los descriptores planos y el límite actual de bytes. Por ello
la primera entrega elimina la decodificación ordinaria de los hechos acumulados,
pero todavía recorre descriptores. Tampoco cambia los recorridos del publicador
para materializar referencias bajo la publicación de salida.

## Medir crecimiento y diseñar la raíz segmentada

Tomar de forma externa e inmutable el manifiesto activo y varias generaciones
anteriores, incluidos cortes sin cambios. Medir bytes serializados por campo
superior, dentro de tables separar descriptores de hechos, linaje y catálogos.
El incidente de 4.001.060 bytes solo acredita el tamaño total.
Contar también fragmentos por tramo, origen y familia y sus tamaños percentiles.
No se dispone aquí de los manifiestos del servidor para un reparto numérico.

La raíz futura fija identidad Silver, configuración, corte y referencias
inmutables por familia. Un índice jerárquico de dispersión fija con páginas de
tamaño limitado señala segmentos por familia, origen y tramo. Cada entrada
del segmento contiene hechos, linaje y límites temporales acreditados. El CDF
localiza tanto la imagen anterior como la nueva mediante la clave y sus campos
de partición. Un movimiento modifica ambas rutas y su ancestro común. Los
descendientes intactos mantienen su referencia. La raíz y cada página tienen
capacidad y validación independientes. No se necesita otro conjunto de filas
Silver ni reconstruir el índice para cada corte.

El lector debe aceptar raíces planas y segmentadas antes de activar el escritor
nuevo. Probar caída en cada subida de segmento y antes de la promoción, lectura
de generaciones antiguas y replay con CDF incompleto. Medir el coste con el
mismo delta sobre 1x, 10x y 100x de datos antiguos. Presupuestar bytes de raíz,
páginas tocadas, fragmentos por tramo y memoria. Diseñar consolidación fuera
del camino crítico con ámbito y tamaño objetivo acotados. Validar igualdad
por clave, hash y linaje antes de cambiar referencias. No retirar segmentos
referenciados por una publicación antigua, recuperación pendiente o History.

## Bases y resultados

Partir del grafo de cambios por clave, producción, ramal y ventana. Una
corrección invalida su ámbito anterior y el nuevo. Conservar las 36 tablas,
el score oficial y los estados pendientes. Elegir la primera base según #98.

- MIA. Recalcular el grupo de inspección cuando cambia su máximo, incluyendo
  borrados del máximo y candidatos empatados. Reconciliar numeradores con
  denominadores deduplicados y probar cambios de referencia y cobertura.
- AR. Invalidar solo ventanas con intersección con el intervalo anterior o
  nuevo. Probar aperturas, cierres, solapamientos, cambios de categoría,
  capacidad y límites exactos de hora. Mantener la unión de exposición.
- ZC. Conservar identidad de máquina y contador. Probar una observación
  compartida en tándem, cambio de asignación y revisiones del teórico.

Comparar contra el motor actual con el mismo Silver, corte, configuración y
referencias. Exigir paridad exacta de observaciones, atribuciones, grupos,
bases, componentes, scores, alcance y pendientes. Las fórmulas permanecen.

## Recorridos y escrituras que quedan por perfilar

- Exportación: lectura CDF y resolución de cobertura. Separar los descriptores
  inspeccionados de los fragmentos abiertos y los bytes físicos leídos.
- Cálculo: las muestras de referencia cargan hechos MIA desde SQLite y
  _prepared_for vuelve a cargar hechos por producción. _history abre tablas
  previas para cada producción. Medir llamadas, filas y tiempos con #98 antes
  de combinar consultas o reducir columnas y ventanas.
- Publicación: la preparación de dimensiones reescribe hechos en el spill.
  Después se recorre para validar, calcular huellas y publicar. Compartir la
  representación normalizada o reutilizar la huella de un ámbito confirmado.
- Delta y referencias: medir Parquet creados o sustituidos, bytes únicos y
  versiones reutilizadas. Distinguir recorrido de descriptores, verificación
  de contenido y copia física. Conservar verificación de commit y recibo,
  recuperación y promoción atómica.

La línea de aceptación operativa mide Fenix a score consultable por corte y
producción, con History detenido y referencias históricas ya fijadas accesibles.

## Comparación A/B local de exportación

Delta 1.6.6, Python 3.12 y almacenamiento local Windows. Se tomaron tres
procesos por variante, alternando recorridos, con la misma evidencia previa
de 4.096 filas en 256 tramos. El mismo commit Silver añadió cuatro filas
de LISTA_RECH en un tramo existente. A usa el materializador acumulado de
referencia y B actualiza fragmentos. Ambas variantes emitieron exactamente
la misma identidad de evidencia. Se mide la actualización de datos y
linaje y la escritura de la evidencia, sin incluir la atribución
ni el runtime de publicación de las 36 tablas.

| Métrica | A | B |
| --- | ---: | ---: |
| Fragmentos previos abiertos, hechos y linaje | 512 | 2 |
| Filas previas decodificadas, hechos y linaje | 8.192 | 32 |
| Tiempo de pared mediano | 2,426 s | 0,064 s |
| Tiempo de CPU mediano | 2,125 s | 0,047 s |
| Pico RSS mediano de proceso, incluida carga de bibliotecas | 88,2 MiB | 73,7 MiB |
| Bytes asignados en disco para ficheros nuevos de evidencia | 135.168 | 135.168 |

Los bytes se midieron como asignación de fichero NTFS, sin contar otra vez
los archivos previos ni la infraestructura del sistema de archivos. No hay
ahorro de bytes nuevos en este A/B. La prueba funcional complementaria
reproduce un mismo corte y compara las 36 tablas Delta por filas exactas.
Los tiempos no se extrapolan al servidor ni acreditan el plazo operativo.
