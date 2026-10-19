# Entrega 01 — Base y SIP_MODELO

Contrato: `sip_modelo_aiala_v1`, sección 4.1 del plan. Implementación inicial de ensayo.

## Alcance

Paquete y entorno independientes, CLI, configuración con raíces separadas, descubrimiento
de ambas modalidades, copia estable e inmutable Bronze, SQLite persistente y bloqueo de
escritor. Diccionario compartido de planta con `id_modelo_fenix` int64 y
`codigo_modelo_botella` texto, ambos no nulos. Se preservan letras, ceros y separadores;
el estado descartado no genera cambios. Esquema y bidireccionalidad se validan antes de insertar.

Las novedades resueltas se insertan una vez, con metadatos de operación. No se crean tablas
vacías para confirmar una revisión sin inserciones. Repeticiones y cambios solo de estado
no generan commits. Un conflicto/ inválido bloquea la unidad completa y se conserva para
revisión con sus filas originales. Una ruta nueva o ya recibida se comprueba por contenido.

Se preparan intención, evidencias y hashes antes del commit. Un fallo antes de escribir se
reanuda desde esa intención; uno posterior a un commit real se reconcilia por operación,
versión y contenido. Si no se puede demostrar el resultado, no se vuelve a escribir a ciegas.

## Aceptación

- Carga inicial y replay sin nuevas filas, Parquet ni versiones Delta.
- Misma asociación desde varias líneas: una única entrada de planta.
- Nuevas versiones, nombre fijo sobrescrito, nombre fechado que cambia y modalidad mixta.
- Campos/tipos, ambigüedades ID–código en ambos sentidos y candidatas completas en revisión.
- Copia estable, límites y evidencia alterada; ningún original se modifica.
- Falla antes del commit, después del commit y al confirmar en SQLite: recuperación sin duplicados.
- Bloqueo de escritor, rutas protegidas y rechazo de tablas modificadas por otro control/escritor.
- Conciliación local de los diez `SIP_MODELO` reales de Aiala: 2309 asociaciones distintas.
- Lint, formato y pruebas sintéticas automatizadas; workflow preparado para Windows/Linux.

## Evidencia de ejecución

Validación local del 16/10/2026: **62 pruebas superadas; cobertura 92,46 %** con Python
3.12.14, deltalake 1.6.6 y Arrow 23.0.1. Ruff y verificación de formato forman parte de
los checks de la entrega. La CI remota y el despliegue productivo quedan pendientes.

Conciliación con los diez SIP_MODELO Aiala del 13/14: **316333 filas físicas → 2309
asociaciones**, cero incidencias. La repetición dejó la misma versión Delta 0 y los mismos
archivos con idénticas huellas, cero inserciones adicionales y los originales intactos.
Auditoría: dos intentos, veinte recepciones y 2309 intenciones confirmadas una única vez.
El ensayo y su informe JSON están fuera del checkout; no se publican fuentes reales.

Las pruebas también cubren capturas que cambian, ambos tipos de nombre, sobreescrituras,
conflictos en ambos sentidos, corrupción de evidencia, bloqueo de escritor, control ajeno,
fallo tras commit, fallo de acuse y recuperación desde la revisión preparada aunque el CSV
vivo haya sido sobreescrito. Una operación pendiente no cambia de transformador implícitamente.
