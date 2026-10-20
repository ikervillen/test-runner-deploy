# Entrega 15 — Procesos independientes y presupuesto operativo

El supervisor mantiene el bloqueo global del entorno y lanza hasta dos procesos
independientes. Cada proceso posee exclusivamente el bloqueo de su tabla y su
control SQLite. No se comparten conexiones, objetos Delta ni memoria de trabajo.
Al terminar una unidad, el proceso sale y devuelve la memoria al sistema.
Una invocación manual sigue excluida mientras el supervisor está activo.

Las fases respetan las dependencias: diccionarios y máquinas; ramales y cambios
de modelo; catálogo; hechos; asociaciones. En hechos se alterna un lote MIA por
turno/origen, acompañado de CONT_ZC o LISTA_AR. Nunca se ejecutan simultáneamente
los dos trabajos MIA pesados. Las asociaciones se ejecutan después de los hechos
y continúan aplazadas mientras las colas inventariadas tengan trabajo pendiente.
La simultaneidad se limita a tablas distintas; no hay varios escritores sobre
la misma tabla ni dos supervisores sobre el mismo entorno.

Los resultados de cada proceso se escriben atómicamente en runtime y el supervisor
los incorpora al log duradero. Un proceso que termina sin resultado válido o con
código de salida contradictorio se registra como FAILED. Los controles de lote
siguen siendo la autoridad de recuperación: perder un resultado operativo no
autoriza repetir un append sin comprobar su operación Delta.

SIGTERM solicita la parada mediante un archivo propio de la activación. No se
inician nuevas unidades; los procesos activos terminan en su frontera segura.
El supervisor espera a sus hijos sin SIGKILL. El despliegue conserva la comprobación
de intenciones pendientes antes de activar otra imagen. Los bloqueos de tabla
también impiden solapamientos accidentales entre invocaciones del mismo destino.

El despliegue consulta la capacidad real de Docker: máximo 4 CPU y 6 GiB para el
contenedor, reservando al menos 1,5 GiB del motor y redondeando hacia abajo en
pasos de 256 MiB. No se presupone que los 16 GiB de Windows estén disponibles
en WSL2. Con menos de 4 GiB de presupuesto o menos de dos CPU se usa un solo
proceso; con menos de 2 GiB de presupuesto se rechaza el despliegue. RAM y
memory-swap tienen el mismo límite: no se habilita intercambio que esconda
presión de memoria. Estos límites son techos de consumo, no reservas dedicadas.
El despliegue conjunto de más entornos requiere dimensionar su demanda agregada.

En el servidor observado (4 CPU y unos 7,76 GiB en Docker), el resultado es
4 CPU, 6 GiB, dos procesos y un pesado simultáneo. Cada hijo limita los pools
Arrow/Rayon a dos hilos y los pools OpenMP/BLAS a uno. El descriptor persistente
registra el presupuesto; un rollback de descriptores anteriores conserva sus
límites originales de 2 CPU / 4 GiB / ejecución secuencial.

No se borran tablas, capturas ni auditorías. Se conservan los contratos y las
reglas de revisión. La validación cubre dos procesos reales con Delta, exclusión
por tabla, dependencias, parada cooperativa, fallo del protocolo de resultados,
límites efectivos y actualización compatible de descriptores anteriores.
