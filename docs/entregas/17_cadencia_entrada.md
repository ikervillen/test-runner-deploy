# Entrega 17 — Separar revisión de CSV y consumo de colas

La medición del piloto mostró un turno LISTA_RECH de ocho versiones en 68,373 s:
el intento volvía a verificar más de mil archivos vivos antes de consumir un lote.
La mayoría de esas capturas ya eran conocidas. Alternar fuentes sin limitar esta
frecuencia multiplicaba lecturas del directorio Windows al drenar el histórico.

El runtime revisa cada fuente cada 600 segundos, medidos desde el inicio de la
última revisión completa. Entre revisiones, los turnos siguen procesando las
versiones pendientes guardadas en Bronze/control y las dependencias confirmadas;
no vuelven a descubrir, estabilizar ni hashear los mismos CSV en cada vuelta.
Se mantienen las cuotas por lote, la rotación por origen y los escritores exclusivos.
Al agotarse las colas no cambia el mecanismo: las revisiones periódicas siguen
capturando archivos nuevos o sobrescritos para incorporarlos incrementalmente.

La ventana es duradera, por familia, y solo se confirma después de completar
descubrimiento, capturas y recuperación de huérfanos. Si esa fase falla se reintenta
sin esperar diez minutos; sus capturas ya confirmadas permanecen disponibles.
Un fallo posterior de transformación/escritura no retrasa los reintentos de la
cola hasta otra revisión. La recuperación de intenciones conserva prioridad.

Un cambio de activación Docker, orígenes o interpretación fuerza revisión inmediata;
un reloj que retrocede tampoco bloquea la entrada. Los nuevos despliegues detectan
archivos nuevos sin esperar a que venza la ventana anterior. Un reinicio de la misma
activación conserva su ventana y sus colas. No se usa tamaño/mtime como sustituto
del hash: cuando vence la revisión se vuelve a calcular el contenido, también para
sobrescrituras de igual nombre, tamaño y mtime.

El CLI conserva revisión en cada invocación como valor predeterminado
(`input_scan_interval_seconds=0`); la configuración permite de 0 a 86.400 segundos.
Los resultados exponen `input_scan_performed` y el intervalo. Un NO_INPUTS con
revisión omitida indica que no se han tratado nuevas capturas en esa unidad:
no significa que los archivos hayan desaparecido ni que se resuelvan revisiones
de calidad. Los diccionarios siguen mostrando sus incidencias abiertas.

La latencia de descubrimiento queda acotada por esta cadencia más el tiempo de
servicio de la vuelta. La cola histórica conserva FIFO por origen: una nueva versión
MIA puede esperar a versiones anteriores de ese origen. No se promete que la hora
de exportación o la entrada del archivo equivalgan a publicación instantánea.

Pruebas: consumo entre revisiones sin redescubrimiento, archivo fijo sobrescrito
con mismo tamaño/mtime y nueva versión con hora, capturas tras fallo de revisión,
reintento de transformación sin fuente original, nuevas activaciones, retroceso de
reloj, cambio de orígenes y CLI inmediato. Las dos familias MIA conservan replay
sin versiones Delta nuevas al terminar su cola.
