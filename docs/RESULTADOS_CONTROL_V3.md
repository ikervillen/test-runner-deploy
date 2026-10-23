# Aceptación del control reducido — 23/10/2026

## Release y pruebas

- Código final de ingesta: `6b155cd496a1abafba267fb877d6169d7524bb3c` (develop).
- CI Windows/Linux e imagen Docker aprobadas. La imagen incluye pruebas de replay, recuperación y montaje real de solo lectura.
- Windows: 835 pruebas aprobadas, cobertura 87,69 %. Linux: 833 aprobadas y dos omitidas por plataforma, cobertura 87,94 %.
- [CI, imagen y despliegue final](https://github.com/ikervillen/test-runner-deploy/actions/runs/39961708764): aprobados. El servidor activó la revisión indicada con imagen por digest y mantuvo el volumen `fenix-aiala-develop`.

## Ensayo aislado sobre datos reales

[Informe de Actions](https://github.com/ikervillen/test-runner-deploy/actions/runs/39943804363), artefacto `control-maintenance-39943804363`.

Resultado: `TRIAL_VERIFIED` para las 13 familias. Cero cambios SQL en los orígenes y cero diferencias de versión Delta entre el inventario y la verificación detenida. El volumen industrial estuvo montado en solo lectura y la salida en un volumen distinto.

| Familia | SQLite original (MB) | SQLite reducido (MB) | Auditoría comprimida (MB) |
| --- | ---: | ---: | ---: |
| ASIGNACION_PRODUCCION_RAMAL | 26.85 | 23.03 | 2.41 |
| CATALOGO_PRODUCCIONES | 8.20 | 8.14 | 0.04 |
| CONT_ZC | 459.29 | 128.51 | 94.10 |
| LISTA_AR | 75.59 | 22.28 | 13.18 |
| LISTA_DEFECTOS | 994.45 | 42.93 | 127.98 |
| LISTA_RECH | 13567.72 | 269.29 | 945.19 |
| MP9_CAMBIO_MODELO | 88.65 | 5.50 | 4.44 |
| SIP_DEFECTO | 80.48 | 3.19 | 4.21 |
| SIP_DEFECTO_AR | 83.73 | 1.60 | 3.86 |
| SIP_DEFECTO_STATUS | 85.50 | 1.60 | 3.75 |
| SIP_LINEA_ZF | 88.87 | 13.21 | 4.48 |
| SIP_MAQUINA_MOLDEO | 85.05 | 1.58 | 4.07 |
| SIP_MODELO | 113.01 | 1.78 | 4.93 |

Total: 15.757.377.536 bytes de controles originales, 522.633.216 bytes de controles reducidos y 1.212.644.285 bytes de auditoría comprimida. Estas cifras pertenecen al ensayo, con compresión gzip de nivel 9; la sustitución definitiva usa nivel 1 para reducir el coste CPU y se medirá por separado.

El ensayo verificó identidad, contenido transformado, grupos de inspección, archivos de auditoría y referencias de operaciones pendientes. No resolvió incidencias de negocio ni cambió contratos.

## Sustitución definitiva

La [primera operación](https://github.com/ikervillen/test-runner-deploy/actions/runs/39951903508) convirtió cinco controles. Se detuvo de forma controlada la comprobación redundante del SQLite original de LISTA_RECH, antes de construir su sustituto. El original quedó intacto y se reanudó el escritor compatible.

La [operación de continuación](https://github.com/ikervillen/test-runner-deploy/actions/runs/39959812916) terminó correctamente (`REFORMULATED`, verificación `COMPATIBLE`, cero operaciones pendientes). Reconoció los cinco controles ya convertidos y convirtió los ocho restantes. La validación conserva la lectura completa del contenido necesario, la conciliación de hashes y la integridad completa de la base reconstruida; evita volver a recorrer los índices auxiliares del control antiguo que no se conservarán.

Medición de la instalación tras la conversión: los 13 SQLite activos suman **526.503.936 bytes**. Sus originales conservados suman **15.785.869.312 bytes**. LISTA_RECH pasó de **13.585.952.768 a 270.761.984 bytes**, una reducción aproximada del **98 %** del control activo. La auditoría comprimida se contabiliza por separado; estas cifras no incluyen su almacenamiento ni las copias antiguas.

La [retirada verificada de copias](https://github.com/ikervillen/test-runner-deploy/actions/runs/39968426775) terminó correctamente: `backup_removed=true` para las 13 familias y ninguna copia `.before-compaction` en el inventario posterior. Se liberaron 15.785.869.312 bytes de copias antiguas. La comprobación posterior volvió a indicar `COMPATIBLE`, sin operaciones pendientes, y se reanudó el escritor de la revisión final.

No confundir los resultados medidos del ensayo con el tamaño definitivo de la instalación activa. Los informes de creación de cada copia conservan su estado original; el justificante de su retirada es esta operación posterior.

La [inspección posterior](https://github.com/ikervillen/supervision_pruebas/actions/runs/39969735005) verificó el escritor final `running/healthy`, con una muestra de memoria de 168,6 MiB dentro del límite de 6 GiB. También constató nuevas escrituras: LISTA_RECH continuaba consumiendo su cola y LISTA_AR publicó datos. Es una comprobación puntual, no una medición de consumo máximo.

En esa inspección los SQLite activos sumaban **527.851.520 bytes**, la auditoría comprimida **1.425.459.529 bytes** (37 archivos) y los temporales de trabajo contabilizados **0 bytes**. Ese subtotal sumaba aproximadamente **1,95 GB**, frente a los 15,79 GB de SQLite antiguos. **No es el total de almacenamiento de control:** el inventario omitió `association-artifacts`, hallazgo descrito abajo. El incremento de SQLite desde la conversión corresponde a la ingesta reanudada; se mantiene la observación de crecimiento.

El volumen aislado del ensayo `fenix-aiala-develop-trial-8111fae1698c` se retiró tras comprobar nombre, propietario, propósito y ausencia de contenedores conectados. Los informes del ensayo y de la retirada permanecen conservados.

## Supervisión y operación

- [Despliegue y validación remota de supervisión](https://github.com/ikervillen/supervision_pruebas/actions/runs/39969668115): `PASSED`, revisión `7a0a76f80d8fc8bf8a07ae5d01c2c6f0c74d0ff0`. Se verificaron autenticación, permisos, recuperación de PostgreSQL, persistencia, validación GX de una versión Delta confirmada, scraping Prometheus y aprovisionamiento de alertas Grafana. El escritor de ingesta no se reinició durante esta prueba.
- Los resúmenes publican tamaño de SQLite y WAL, reintentos, fallos de mantenimiento y progreso de entradas sin abrir SQLite desde el panel.
- Las alertas distinguen ausencia de entradas, cola sin progreso, reintentos agotados, mantenimiento fallido y crecimiento de control sin una cola MIA que lo explique.
- Una asociación aplazada por cola MIA publica una observación reciente con su dependencia explícita; no simula un intento ni una nueva publicación de datos derivados.
- Los avisos por correo siguen pendientes de configurar SMTP, por decisión del usuario. La existencia de reglas en Grafana no implica envío de correo.

## Hallazgo abierto: snapshots de asociaciones

Tras el despliegue el usuario informó de más de 30 GB en `control/association-artifacts`. Esta cifra procede de su inspección del servidor; el inventario remoto anterior no midió ese directorio. Por tanto, la aceptación del almacenamiento total sigue abierta.

El código vigente (`association_service.process` y `association_storage.artifact`) conserva, por publicación, un bundle con todas las filas aceptadas de las fuentes y un ledger completo de atribuciones. Cambiar las versiones de entrada puede generar otra copia completa aunque solo hayan cambiado unas pocas filas. Los archivos idénticos se deduplican por hash; los casi idénticos no. La retención actual conserva todas las publicaciones y no recoge sus artefactos históricos. Esta carpeta no es la auditoría nueva de la conversión SQLite.

No borrar el directorio ni aplicar una regla únicamente por antigüedad: la publicación vigente, la recuperación y la inspección de asociaciones consultan referencias a estos archivos. Falta un inventario de archivos/tamaños y referencias desde publicaciones, operaciones pendientes y evidencias; debe distinguir activos, historia referenciada, huérfanos y temporales.

La siguiente corrección debe evitar snapshots acumulativos de todo el histórico, usar referencias a fuentes/versiones y evidencia mínima reproducible, y establecer retención y retirada verificadas. Toda sustitución debe preservar las decisiones y la explicación histórica necesaria, coordinarse con la retención de versiones Delta y comprobar la recuperación. La migración SQLite y el despliegue están completados; este hallazgo impide dar por cerrada la optimización global de almacenamiento.

## Alcance de la aceptación

Las pruebas de caída y replay son deterministas y complementan el ensayo real. La observación sostenida del crecimiento y una restauración tras pérdida física de disco requieren seguimiento específico. Las capturas Bronze y los datos activos Delta se conservan; no se activa VACUUM de Delta ni una purga general de directorios.
