# Entrega 12 — Consumo, operación y despliegue

Estado: CI/CD Docker implementado; consulta/consumo de la app y aceptación operativa pendientes.
Secciones 1.1, 10, 11, 15, 17.10 y 17.17 del plan. Detalle en
[GitHub y CI/CD](../GITHUB_CICD.md).

Preparar salidas compatibles con la app y versiones coherentes. Medir consultas p95,
concurrencia, últimas ocho horas y comparaciones históricas antes de elegir optimizaciones.
No cambiar pesos, fórmulas o reglas históricas como efecto de migrar la fuente.

Validar runtime remoto y almacenamiento, entrega reproducible con dependencias fijadas,
Docker si TI confirma soporte o alternativa nativa, ambas independientes de SSH. Instalación
y rollback de código nunca escriben o migran datos de producción. El plan de despliegue exige
pruebas aisladas, un escritor y recuperación; CI ya se prepara desde la entrega 01.

Aceptación: conciliación con histórico, operación sostenida, restauración ensayada,
releases main/develop aisladas, activación y rollback sin mutaciones de datos por instalar.
