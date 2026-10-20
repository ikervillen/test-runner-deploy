# Entrega 13 — Lecturas MIA por contexto y bases incrementales

Esta entrega optimiza LISTA_RECH y LISTA_DEFECTOS. No cambia columnas, claves,
reglas de conflictos, el score ni la identidad del almacenamiento. No reinicia
el histórico y no elimina auditoría previa.

Cada intención nueva declara sus orígenes, intervalos y grupos de inspección.
Arrow filtra por origen/fecha y entrega bloques de 8192 filas; solo se conservan
los grupos afectados. La comparación incluye todos los defectos del contexto,
no únicamente la clave que llega, conservando conflictos de contadores y bases.

La reconciliación combina versión Delta, identidad de operación, hash del
contenido del contexto y manifiesto de archivos (ruta/tamaño/número de filas).
Un append debe preservar los archivos anteriores y añadir exactamente las filas
preparadas. Los Parquet confirmados se consideran inmutables conforme al modelo
Delta; esta optimización no constituye un escaneo criptográfico periódico de
todos los bytes históricos. Un escritor por tabla sigue siendo obligatorio.

El control incorpora inspection_current, con una base vigente por contexto y
su cobertura de filas. La primera ejecución adopta, dentro de una transacción,
el último snapshot completo de grupos confirmado por el protocolo anterior y
comprueba su cobertura frente a las estadísticas Delta. Las confirmaciones nuevas
actualizan únicamente los contextos tratados y las retenciones por incidencias.
El resumen global se calcula en SQLite sin reconstruir hechos en memoria.

Las intenciones anteriores sin read_scope se recuperan mediante su comprobación
completa original. La actualización exige una frontera segura sin operaciones
inciertas. No se modifican las tablas de auditoría históricas ni se presupone
que un cambio de imagen autorice limpiar datos. Las consultas explícitas de
bases mantienen su lectura y comprobación completa; no forman parte del bucle
optimizado de ingesta.

## Validación

Se verifican reintentos antes/después del commit y del acuse, conflictos entre
lotes, cavidades nulas, revisiones de contadores, incidencias de origen completo,
recuperación de intenciones anteriores, cobertura global con lectura parcial,
intervalos con huecos, manifiestos y replay sin nuevas versiones Delta.

Comparación aislada en Windows, con 20.000 observaciones previas y cinco nuevas
muestras: protocolo anterior 12,188 s y crecimiento de control de 77.987.840 bytes;
nuevo protocolo 1,391 s y sin ampliación del archivo en esa ejecución. Es una
medición local, no una garantía de rendimiento remoto. Evidencia fuera del repo:
C:/Users/iker.villen/AppData/Local/Ingesta_Delta_Lake/validation/20261020-incremental-state.

## Siguientes entregas autorizadas

1. Cuotas por turno y reparto persistente entre fuentes/orígenes, con métricas de
   cola y actualización; mantener detección por contenido para nombres estables
   y entregas con fecha/hora.
2. Procesos independientes para fuentes compatibles, presupuesto de memoria y
   CPU, un escritor por tabla y dependencias sobre versiones confirmadas.
3. Ajustar recursos según la capacidad efectiva de Docker/WSL2, conservar margen
   para Windows/runner/diagnósticos y validar la carga remota tras cada despliegue.

No se habilitan escritores simultáneos sobre la misma tabla en estas entregas.
