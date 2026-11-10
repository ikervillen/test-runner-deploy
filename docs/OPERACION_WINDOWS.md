# Contrato operativo Windows

## Objetivo

`calidad_serie` se desplegara en equipos Windows remotos mediante un proceso
repetible, versionado y reversible. El codigo, la configuracion y los datos se
mantienen en ubicaciones separadas para que una actualizacion de la aplicacion
nunca sobrescriba datos industriales ni secretos.

Este documento define el contrato base. El script de despliegue se implementara
en la fase de empaquetado, cuando las imagenes de produccion y los healthchecks
esten disponibles.

## Distribucion de directorios

| Recurso | Ubicacion recomendada | Propietario | Regla |
| --- | --- | --- | --- |
| Codigo Git | `C:\Servicios\calidad_serie` | Operacion TI | Solo Git y archivos versionados. |
| Configuracion local | `C:\ProgramData\calidad_serie` | Operacion TI | Fuera de Git, acceso restringido. |
| Datos industriales | `D:\CalidadSerieData` | Calidad / Datos | Solo lectura para la aplicacion. |
| Logs operativos | `C:\ProgramData\calidad_serie\logs` | Operacion TI | Rotacion y retencion definidas. |
| Lotes de ingesta | `D:\CalidadSerieStaging` | Proceso de ingesta | Temporal, con trazabilidad. |

Las rutas son una convencion. El despliegue final las recibira mediante
variables de entorno; no se escribiran en el codigo ni en el repositorio.

## Requisitos del equipo remoto

- Git instalado y accesible para la cuenta de servicio.
- Runtime de contenedores aprobado para Windows y compatible con Linux
  containers, o la alternativa corporativa equivalente.
- Acceso de red de solo lectura al origen de datos industrial, si los datos no
  residen localmente.
- Certificado y proxy corporativo para HTTPS.
- Cuenta de servicio sin privilegios de administrador para ejecutar la app.
- Espacio suficiente para imagenes, cache, logs y agregados publicados.

## Regla de actualizacion

El futuro `deploy.bat` no debe modificar archivos locales sin control ni usar
`git reset --hard`.

1. Comprueba herramientas, espacio, configuracion y estado limpio del checkout.
2. Obtiene una etiqueta o commit de release explicitamente indicado.
3. Rechaza ramas no protegidas, cambios locales o una version no verificable.
4. Construye o descarga las imagenes versionadas.
5. Arranca la nueva version y espera los checks de disponibilidad.
6. Conserva la referencia de la version anterior para una reversibilidad
   inmediata si falla la verificacion.
7. Registra version, fecha, operador y resultado de la operacion.

El repositorio no contiene datos, credenciales ni configuracion de cada
servidor. Un clon nuevo y una actualizacion de un clon existente deben seguir
exactamente el mismo flujo.

## Estado de disponibilidad

- **Liveness:** el proceso responde y no esta bloqueado.
- **Readiness:** la configuracion, los datos publicados y sus diccionarios se
  pueden leer; solo entonces recibe trafico.
- **Degradado:** se muestra un mensaje controlado cuando falte una fuente o se
  este publicando un lote, sin mostrar resultados parciales.

## Operacion de datos

La aplicacion no ingiere Excel desde el navegador. Un proceso de ingesta
independiente recibe el lote, valida sus contratos, genera Parquet y agregados
en staging, y publica una version atomica. La version anterior permanece
consultaable hasta que la nueva este validada.
