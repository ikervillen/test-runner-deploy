# Revisión 11A — Identidad física de máquinas tándem

Estado: contrato acordado el **19/10/2026**, código implementado con pruebas sintéticas;
reconstrucción/migración real pendientes. Ejecución detallada en
[11B/11C](11bc_catalogos_derivados.md). Referencia
única: [plan, sección 17.14](../PLAN_DELTA_LAKE.md).
Esta ficha conserva la justificación del contrato; las publicaciones reales v1 no cambian.

La confirmación del usuario y los catálogos originales vinculan AV31B/8/1617–1618 con ramales
1–2 y AV31A/7/1643–1644 con 3–4, aunque ambas fabriquen 1358/242. Las raíces permanecen
separadas, con sus fechas originales. Código de ramal e ID interno ZF son campos distintos.

Comprobación ampliada el **19/10/2026**, plan 17.15: también AV32A/9/2005–2006 usa ramales
3–4, mientras AV32B/10/2003–2004→2007–2008 usa 1–2. La implementación deberá descubrir y
validar todas las tándem por sus catálogos vigentes en cada actualización, sin casos fijados
por nombre/letra. Los cinco orígenes disponibles muestran AV31 y AV32 como tándem; no es una
garantía sobre máquinas que no estén exportando a esa copia local.

Implementar por incrementos revisables:

1. **MP9 v2:** ocho campos en orden original relativo, recuperar ID_MAQUINA, hash versionado,
   máquina invariante por identidad y compartida entre ZF y ZC referenciadas. La clave MP9 y
   los IDs de producción permanecen estables. Recuperación desde Bronze verificable; ningún
   dato de máquina se inventa para completar Silver v1.
2. **Catálogos mínimos:** SIP_MAQUINA_MOLDEO y SIP_LINEA_ZF, con esquemas/posiciones de 17.14,
   vigencias, referencias, repeticiones sin escritura, cierres auditados y contradicciones
   persistentes. Modo mixto de nombres de archivos, idempotencia y recuperación como las
   demás fuentes. El objetivo pasa a once fuentes; las otras excluidas siguen fuera.
3. **Derivados v2:** máquina en catálogo antes de ID_ST1_REL; resolver MIA por ramal/máquina
   vigente y periodo completo. Mantener evidencia, IDs, exclusividad, fronteras y pendientes
   AR/ZC. No convertir correspondencias físicas en prueba de preparación o primera botella.

Diagnóstico real por periodo completo declarado ZF: AV31 resuelve una candidata en sus
37.208 rechazos y 6.678 defectos. AV32 resuelve 49.840/49.841 rechazos y 9.107/9.111 defectos;
cinco filas del ramal 1 a las 07:30 del 14/10 cruzan la frontera ZF 07:23:14. El usuario
confirmó después su atribución íntegra a la producción saliente, implementada en v2 y
probada con cinco filas sintéticas. No multiplicar su base repetida de 13 inspeccionadas ni
confundir este diagnóstico de originales con una publicación v2 o resolución de ZF_OK.

Migración explícita en raíces/control de ensayo separadas, sin adoptar silenciosamente v1,
sobrescribir tablas, borrar auditoría ni ejecutar migraciones al instalar código. Comparar
los campos v1, cierres, IDs y pendientes; validar recuperación/replay antes de promover.
ZF_OK 0/1 de AV32 sigue siendo un asunto separado pendiente de confirmación.

Los estados de producción son derivados: abierta en ZC, pendiente de cierre ZF, cerrada o
pendiente de validación, con frescura independiente. Por acuerdo del usuario, una producción
conocida sin cierre válido **continúa abierta**, sin exigir señal adicional, consulta SQL o
plazo máximo. Un cambio en otra parte, falta de novedades o fuente atrasada no la cierran;
los pendientes de calidad se señalan aparte. Ningún ID ni STATUS/ZF_OK acredita actividad
instantánea. Una futura señal/consulta SQL no es requisito actual del piloto.

Aceptar solo tras pruebas de esquema/invariantes, catálogos temporales, asociación AV31/AV32,
errores de referencia, cambios de máquina/topología, periodos de transición, migración aislada,
recuperación y replay sin efectos repetidos. Conservar los resultados anteriores como v1 y
registrar por separado la nueva conciliación. GitHub/CI precede al piloto remoto; integrar
la app sigue siendo una fase posterior a la validación de la ingesta.
