# Aceptación del control reducido — 23/10/2026

## Release y pruebas

- Código de ingesta: `6af699370dfdd66e76f50e11f45b370988edc88d` (develop).
- CI Windows/Linux e imagen Docker aprobadas. La imagen incluye pruebas de replay, recuperación y montaje real de solo lectura.
- [Despliegue de la release optimizada](https://github.com/ikervillen/test-runner-deploy/actions/runs/39949667693).

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

En curso: [ejecución auditada](https://github.com/ikervillen/test-runner-deploy/actions/runs/39951903508). Conservar las copias `.before-compaction` hasta validar el escritor reanudado. Actualizar esta sección con el resultado real y la retirada de copias.

## Alcance de la aceptación

Las pruebas de caída y replay son deterministas y complementan el ensayo real. La observación sostenida del crecimiento y una restauración tras pérdida física de disco requieren seguimiento específico. Las capturas Bronze y los datos activos Delta se conservan; no se activa VACUUM de Delta ni una purga general de directorios.
