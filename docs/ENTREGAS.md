# Entregas de la ingesta

La arquitectura y decisiones acordadas permanecen en el
[plan detallado](PLAN_DELTA_LAKE.md).
Este índice lo descompone en unidades revisables; no sustituye los contratos ni autoriza
cambiar los datos de la aplicación. Todas las entregas aplican aislamiento, idempotencia,
evidencia persistente, no escritura sin cambios y recuperación descritos en el plan.

Revisión de contrato del 19/10/2026: se autoriza ampliar las nueve fuentes implementadas
con dos catálogos físicos y recuperar ID_MAQUINA. El objetivo consta de once fuentes;
la ampliación está implementada y probada con datos sintéticos, sin migrar los datos reales.
La ambigüedad física AV31 se resuelve
con las fuentes originales; las publicaciones v1 conservan el resultado histórico anterior.
Revisión 32: AV32 verificada con la misma cadena; comprobar todas las tándem desde catálogos
en cada actualización. Producción sin cierre válido permanece abierta sin exigir señal extra
o consulta SQL. La muestra que cruza el cambio se atribuye íntegramente a la saliente;
ZF_OK conserva su revisión independiente. Reconstrucción real en servidor después de GitHub/CI.

| Entrega | Alcance | Estado |
| --- | --- | --- |
| [01](entregas/01_base_sip_modelo.md) | Base independiente y primer diccionario `SIP_MODELO` | Implementación inicial; ver evidencia de validación en su ficha |
| [02](entregas/02_sip_defecto.md) | `SIP_DEFECTO` | Implementación local; muestra real conciliada, exclusiones auditadas y replay sin escrituras |
| [03](entregas/03_sip_defecto_status.md) | `SIP_DEFECTO_STATUS` | Implementación local; ocho entradas verificadas, replay sin escrituras y recuperación probada |
| [04](entregas/04_sip_defecto_ar.md) | `SIP_DEFECTO_AR` | Implementación local; nueve entradas verificadas, conflictos auditados y replay sin escrituras |
| [05](entregas/05_mp9_cambio_modelo.md) | MP9 y cierres de zona | Implementación local; 48 ID revisados, 46 publicados, dos cierres y pendientes explícitos de AV32 |
| [06](entregas/06_catalogo_producciones.md) | Catálogo derivado de producciones | Implementación local; búsqueda por modelo, identidad estable y 23 publicadas/una candidata pendiente |
| [07](entregas/07_lista_defectos.md) | Defectos MIA | Implementación local; 33.937 filas conciliadas, bases compartidas sin multiplicación, conflictos y replay sin escrituras probados |
| [08](entregas/08_lista_rech.md) | Inspeccionadas/rechazos MIA | Implementación local; 202.931 filas conciliadas, conflictos/recuperación probados y replay sin escrituras; optimización de control pendiente |
| [09](entregas/09_lista_ar.md) | Tramos de avisos/rechazos | Implementación local; 177 tramos conciliados, agrupaciones/nulos preservados, cierres y dependencias probados |
| [10](entregas/10_cont_zc.md) | Contadores y rendimiento ZC | Implementación local; 827 observaciones conciliadas, decimales exactos, rendimiento/ceros/anomalías y recuperación probados |
| [11](entregas/11_asociacion_temporal.md) | Asociación temporal producción–ramal | Implementación local; 237.872 observaciones conciliadas y 16 candidatas; vigencias reales pendientes de revisión, publicación/corrección/recuperación probadas con datos sintéticos |
| [11A](entregas/11a_identidad_tandem.md) | Prerrequisito MP9 v2 y contrato tándem | Máquina recuperada/validada en código; migración real pendiente |
| [11B/11C](entregas/11bc_catalogos_derivados.md) | Catálogos físicos y derivados v2 | Implementados y probados con datos sintéticos; reconstrucción/conciliación real en servidor tras GitHub/CI |
| [12](entregas/12_consumo_operacion.md) | Consulta, operación y despliegue | CI/CD Docker implementado; piloto/consumo de la app y aceptación operativa pendientes |
| [13](entregas/13_ingesta_incremental.md) | Contextos MIA e índice incremental | CI Windows/Linux e imagen superadas; desplegado en develop conservando datos y copia verificada |
| [14](entregas/14_turnos_fuentes.md) | Turnos, continuidad de capturas y progreso | CI Windows/Linux e imagen superadas; desplegado en develop, validación continua de vueltas y colas |
| [15](entregas/15_procesos_acotados.md) | Procesos por tabla y recursos Docker/WSL2 | Implementado; pruebas locales con procesos/Delta y comprobación Docker en CI antes de despliegue |
| [16](entregas/16_artefactos_asociaciones.md) | Artefactos por bloques y reutilización sin cambios | Implementado; compatibilidad, hashes, memoria, repetición y regresión verificadas localmente; CI antes de despliegue |
| [17](entregas/17_cadencia_entrada.md) | Revisión periódica de entradas y consumo continuo | Implementado; cola independiente de la ventana de revisión, reintentos/activación/reloj y ambas modalidades de CSV probados antes de despliegue |

Cada cambio mantiene las pruebas de las entregas anteriores. Una entrega aceptada en local
no acredita operación productiva en el servidor. AV31 dispone de una relación física
verificada incorporada al programa; las fronteras físicas futuras, los AR insuficientemente
discriminados, el horario local y `ZF_OK` conservan sus comprobaciones pendientes.
