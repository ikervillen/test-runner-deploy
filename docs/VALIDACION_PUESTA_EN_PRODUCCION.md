# Validacion de puesta en produccion

Esta lista es el cierre obligatorio de la migracion. No se declara una puesta
en produccion por tener el codigo compilado: requiere evidencia conservada en
el repositorio de despliegue o sistema corporativo de cambios.

## Completado en codigo

- Pantalla unica de Calidad de produccion y API reducida.
- Configuracion de negocio validada y perfiles DAR versionados por segmento.
- Validacion, publicacion atomica y reversibilidad de lotes.
- Paquetes A/R por produccion con trazabilidad y fallback si una fuente cambia.
- Cache de score, limite de concurrencia, timeout controlado, readiness y
  metricas Prometheus.
- Imagenes no interactivas, datos de solo lectura, despliegue Windows por
  release/commit y rollback automatizado.
- Autenticacion preparada para proxy corporativo y roles de consulta,
  configuracion temporal y operacion.
- CI de backend, frontend, Compose y construccion de imagenes.

## Obligatorio en entorno de validacion

1. Crear el repositorio Git privado corporativo y configurar el remoto `origin`.
   Proteger `main`, exigir CI verde y publicar releases inmutables firmadas o
   etiquetas aprobadas.
2. Preparar un servidor Windows de validacion con Git, Docker Desktop/Engine,
   proxy corporativo HTTPS y un directorio de datos publicado en solo lectura.
   El servidor no debe exponer directamente los puertos de Compose.
3. Configurar el proxy para autenticar, eliminar cabeceras de identidad
   suministradas por clientes y emitir los encabezados descritos en
   `SEGURIDAD_Y_OPERACION.md`.
4. Ejecutar `scripts/deploy.bat` contra una etiqueta concreta y guardar la
   salida de readiness, la version desplegada y el commit previo de rollback.
5. Ejecutar las cuatro mediciones de `RENDIMIENTO.md` con datos representativos.
   Calidad e Infraestructura aprueban p95, memoria y concurrencia antes de
   variar workers, limites o cache.
6. Comparar producciones y turnos de referencia contra la aplicacion origen.
   Deben coincidir las entradas MIA, A/R, DAR y ZC, las contribuciones y la
   penalizacion, salvo una diferencia documentada y aceptada.
7. Simular: datos no disponibles, publicacion de lote, consulta agotada,
   backend detenido, reinicio, y rollback de aplicacion y datos. Conservar los
   `X-Request-ID`, logs y metricas como evidencia.
8. Ejecutar una aceptacion de usuarios de Calidad sobre ranking, detalle por
   produccion, detalle por turno y parametros temporales. Mantener la
   aplicacion anterior como respaldo hasta la aprobacion formal.

## Estado actual local

Las pruebas unitarias/integracion, lint, formato y build de frontend estan
verificados localmente. La construccion y ejecucion de Docker no se puede
confirmar en esta maquina mientras el daemon Docker no este disponible; CI la
ejecutara en cada cambio. Tampoco se ha realizado el despliegue de validacion:
este repositorio local no tiene remoto Git configurado ni acceso al servidor
Windows, proxy HTTPS o identidad corporativa.
