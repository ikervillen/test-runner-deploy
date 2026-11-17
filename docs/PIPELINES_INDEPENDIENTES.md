# Pipelines independientes de Fenix hasta Gold

Decisión del usuario: 2026-11-17. La ejecución se entrega mediante PR pequeños.
Este documento complementa los contratos industriales existentes; no los sustituye.

## Reglas acordadas

- Dos trabajadores y contenedores autónomos: live e histórico. Ninguno espera al
  vaciado de la cola, al reinicio ni a la transacción del otro para publicar.
- Bronze, Silver, Gold, controles, checkpoints y publicaciones físicamente
  independientes. Un escritor propietario por control SQLite y tabla de cada camino.
- Reglas de captura, transformación, claves, revisiones, cierres, cobertura,
  atribución y métricas compartidas en código. No resolver contradicciones mediante
  última llegada ni dar precedencia ciega a una fila por proceder de live.
- Live atiende el último bloque de diez minutos disponible en Fenix, aunque esté
  atrasado respecto al reloj. No se sustituye por el bloque de reloj actual.
- Objetivo configurable: publicar en Silver dentro de cinco minutos desde que el
  archivo está estable y disponible, también con el histórico trabajando.
- Retención live de ocho horas desde la publicación confirmada en Silver live.
  El instante industrial del dato y el instante técnico de publicación son distintos.
- El archivo reciente se selecciona antes de leer/capturar su contenido. La carga
  histórica de catálogos no forma parte del camino necesario para atender live.
- Los archivos tardíos, correcciones y dependencias no disponibles conservan su
  evidencia y un estado explícito. Nunca se inventa cobertura ni un cero industrial.
- Los checkpoints sobreviven a despliegues. Una nueva activación no invalida por
  sí sola el progreso ni obliga a releer todos los archivos de Fenix.
- El histórico utiliza recursos limitados. Compartir servidor/disco puede afectar
  al rendimiento y debe medirse; no constituye una garantía de aislamiento físico.

## Archivado y consulta

Recuperar archivos antiguos de Fenix y archivar resultados que envejecen en live
son trabajos diferentes, con progreso independiente. El histórico es el único
escritor de su almacenamiento. Lee publicaciones live confirmadas, copia de forma
idempotente y publica un recibo verificable. Solo después el propietario live puede
retirar su copia. Nunca borrar primero ni modificar desde histórico el control live.

Las tablas nuevas de los caminos independientes habilitan Change Data Feed de
Delta desde su creación. Se conservan inserciones, valores anteriores y posteriores
y eliminaciones con su versión real. No se habilita sobre tablas antiguas mediante
una actualización implícita. La limpieza automática del log queda deshabilitada:
no ejecutar VACUUM ni eliminar evidencia referenciada hasta disponer de una
política de limpieza que compruebe todos los recibos y lectores pendientes.

La consulta fija una publicación de cada camino y combina identidades y revisiones
confirmadas. Durante el traspaso puede haber copias en ambos caminos; el lector no
duplica observaciones. Las correcciones posteriores también deben conservarse.
Los resultados Gold que atraviesan la frontera se componen con las reglas vigentes:
no se promedian porcentajes ni scores de partes para deducir el total.

Gold live consume exclusivamente su entrada confirmada y referencias acreditadas.
Una nueva publicación histórica no es requisito para publicar live; el consumidor
puede utilizar la última publicación histórica confirmada. No hay una consolidación
física obligatoria en el camino de publicación o consulta live.

## Configuración

`deployment/pipelines.example.json` recoge las decisiones operativas. Todos sus
campos son explícitos; el código no sustituye valores ausentes por límites ocultos.
Las rutas son ejemplos para contenedores y se definen por entorno. La carga de esta
configuración no crea directorios ni mueve datos. Se rechazan rutas compartidas,
anidadas, alias y controles sobre UNC/SMB. Los recursos de los servicios se definen
en el despliegue y se validan contra la capacidad disponible.

## Entregas

1. Política, almacenamiento disjunto y pruebas de las decisiones temporales.
2. Descubrimiento selectivo, captura durable y consumo de Bronze sin releer Fenix.
3. Runtime autónomo, controles independientes y publicación de Silver por camino.
4. Archivado recuperable y lectura conjunta con identidades/revisiones explícitas.
5. Separación de Gold, publicaciones y consulta con métricas correctas.
6. Despliegue aislado, migración verificable y aceptación bajo carga.

Cada entrega debe conservar los datos actuales y pasar sus pruebas antes de la
siguiente. La actualización ordinaria no ejecuta una migración o borrado implícitos.
La activación usa una configuración revisada y un recibo de migración consistente.

## Aceptación

- Mantener un histórico grande con escritura abierta o bloqueada en sus propios
  controles e introducir archivos live. Live debe publicar dentro del objetivo.
- Repetir con fallo y reinicio del histórico; reiniciar live sin repetir el histórico.
- Verificar familias, orígenes, filas, versiones, cobertura y motivos de omisión.
- Interrumpir el archivado antes/después de cada confirmación: no perder datos ni
  duplicarlos en consulta; una reanudación debe ser idempotente.
- Medir el coste de disco/CPU/RAM y la latencia estable-a-Silver. Si el hardware
  compartido impide el objetivo, separar recursos antes de certificarlo.
- Contrastar Gold y las producciones que cruzan la frontera contra el cálculo
  completo con las mismas versiones y configuración; preservar pendientes reales.
