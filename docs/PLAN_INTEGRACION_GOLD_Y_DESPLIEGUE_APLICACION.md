# Integración de Silver, Gold y aplicación de calidad de serie

**Fecha:** 29 de octubre de 2026.  
**Última actualización de decisiones:** 6 de noviembre de 2026.<br>
**Estado:** código de contratos, dimensiones, lector API Gold opcional y operación implementado y probado localmente; sin despliegue en servidor ni aceptación industrial del piloto. Véanse `docs/ENTREGAS_GOLD_IMPLEMENTACION.md` y `docs/OPERACION_GOLD.md`.<br>
**Evolución dimensional:** V3 con 36 contratos, relación explícita contador–producción y publicación base de scores cerrados. V2 conservada; integración opt-in de catálogo, score, turnos, escenarios y pendientes. La validación industrial completa sigue pendiente.<br>
**Segmentos — D18:** recibidos 575 modelos en 13 familias; DAR específicos pendientes, perfil común explícito marcado provisional.<br>
**Prioridad inmediata:** demostración funcional acotada el 07/11/2026, conforme a D17 y 8.1; su ejecución y aceptación aún no están acreditadas.<br>
**Proyecto consumidor:** `calidad_serie`.  
**Objetivo:** consultar producciones históricas y en curso mediante datos de
servicio versionados, preservando los resultados analíticos de referencia.

## 1. Objetivo y condición principal de aceptación

La aplicación debe conservar los valores de scores, contribuciones, tasas,
contadores y análisis que ofrece actualmente con el histórico preparado a partir
de CSV y Excel. La integración con Delta Lake y la contenerización no autorizan
cambios de significado, fórmulas, denominadores, ponderaciones ni clasificación.

La equivalencia se comprobará para una misma combinación de:

1. Observaciones de origen y revisiones aceptadas de esas observaciones.
2. Producción, ramales, filtros e intervalo temporal solicitado.
3. Instante de corte y política de tratamiento de datos abiertos o incompletos.
4. Diccionarios, equivalencias de códigos, criticidades y perfiles DAR.
5. Configuración efectiva del score, incluidos parámetros temporales del usuario.
6. Conjunto de referencia histórico y versión del motor de cálculo.

**Con entradas equivalentes deben obtenerse resultados equivalentes.** Dos
fabricaciones diferentes del mismo modelo no tienen por qué producir el mismo
score. Tampoco puede exigirse la misma cifra si se cambia el histórico de
referencia o se aprueba una nueva interpretación de un denominador.

La salida pública y los valores mostrados deben coincidir aplicando las mismas
reglas de redondeo. Los contadores, identidades y clasificaciones se compararán
exactamente. Las diferencias numéricas internas se investigarán; una tolerancia
no podrá utilizarse para ocultar diferencias de negocio o de agregación.

### 1.1. Preservación frente a cambios de semántica

Se congelará un contrato analítico de referencia, inicialmente denominado
`legacy_analytical_v1`. Este nombre es una propuesta de identificación, no una
versión ya implementada.

Las dudas detectadas se registran en la sección 7. **Q01 queda acordada:
conservar el máximo histórico de inspeccionadas por producción, línea, ramal y
fecha/hora.** Q02 acuerda computar los A/R abiertos en tiempo real hasta el corte
de consulta, con duración provisional y sin alterar su cierre de origen. Q03
acuerda determinar las cavidades asignadas mediante `MP9_PARAM_MODELO` y la
configuración de gotas, manteniendo en el denominador las cavidades con bloqueos
temporales y computando sus A/R. Q04 acuerda siete puntos de horas completas y
un octavo parcial, con una hora de desfase, actualización mediante publicaciones
Gold y comprobación de nuevas versiones en pantalla cada 30 segundos. Las demás
preguntas pendientes requieren su propio acuerdo antes de implementarse. Q05
acuerda publicar el total y su desglose juntos únicamente cuando estén completos
y validados para el mismo intervalo; los cálculos intermedios no se muestran.
Q06 acuerda que solo las producciones terminadas alimenten la referencia MIA:
si existe histórico del mismo modelo, comparar exclusivamente con ese modelo;
solo si no existe, utilizar el segmento confirmado. Un nuevo cierre válido puede
actualizar la referencia de fabricaciones abiertas respetando esa prioridad.
No se congela durante toda una fabricación prolongada. El segmento también
determina las características/perfil DAR de cada correspondencia modelo–segmento.
El catálogo versionado modelo–segmento es un requisito aprobado: distinguirá
Vino Ligero de Vino premium y podrá utilizar MARBLE 25 como fuente inicial,
sin atribuirle una precisión de clasificación que el archivo no contiene.
Q07 acuerda versionar las equivalencias y criticidades con fecha de entrada en
vigor, aplicándolas a nuevos cálculos de producciones abiertas y nuevas. Las
publicaciones anteriores conservan sus reglas; recalcular el pasado requiere
una acción explícita que preserve ambas versiones.
Q08 acuerda perfiles de características por segmento basados en `Salud Serie
límites.pptx`, con condiciones de aplicabilidad como el tipo de cierre y
correspondencias explícitas de fuentes/códigos. No se convierten automáticamente
todas sus características o límites en términos o umbrales del componente DAR.
Q09 registra un criterio para un posible filtro de cavidad en análisis de detalle:
filtrar MIA y A/R cuando exista atribución fiable y conservar ZC como dato de
máquina, identificado expresamente. No se incluye ahora un filtro nuevo de este
tipo en la pantalla principal ni se exige implementarlo para este despliegue.
Q10 adopta como aproximación operativa inicial la frontera temporal derivada
de la entrada declarada ZC, incluyendo el tránsito hasta ZF. Esa frontera se
revisa si aparecen registros inequívocamente atribuibles a la saliente después
de ella. No se presenta como una medición del instante físico exacto de arranque.
Para la atribución ZC, D10.1 exige doble comprobación en todos los cambios y
registros afectados: frontera temporal y configuración física con vigencia.
La falta de evidencia no se presenta como una comprobación satisfactoria.
Q11 conserva la información histórica conocida de reclamaciones e identifica
su procedencia. Las nuevas producciones mostrarán «Sin información de
reclamaciones» hasta disponer de una fuente actualizada; esta ausencia no
equivale a buena calidad ni bloquea el score de fabricación.
Q12 acuerda conservar las identidades originales y vincular histórico y Fenix
solo mediante correspondencias demostrables de la misma fabricación. Las
coincidencias dudosas se revisarán y las reglas se afinarán con evidencia real,
sin fusiones basadas únicamente en modelo o proximidad temporal.
Q13 acuerda agregar las cantidades base de cada intervalo y calcular después
su score horario con las fórmulas y pesos aprobados. No sumar ni promediar
scores de muestras menores ni extrapolar el punto parcial a una hora completa.
Q03-S acuerda unir los intervalos solapados de una misma cavidad y categoría para
contar una sola vez el tiempo físico afectado, conservando los eventos íntegros
según D14. El usuario descarta el caso de categorías diferentes simultáneas para
el mismo modelo, cavidad y producción; no desarrollar una regla de prioridad.
D15 completa Q01/Q05: `LISTA_RECH.INSPECC` es la única fuente del denominador
MIA en la nueva solución. Si no hay una base válida, el cálculo queda pendiente;
no recurrir a inspeccionadas de `LISTA_DEFECTOS` ni inventar un resultado.

Si una decisión aprobada modifica un resultado respecto al histórico:

- Se identificará como cambio semántico, con versión, justificación y alcance.
- Se conservará la referencia anterior y una comparación de resultados.
- Se decidirá expresamente si afecta al pasado, al tiempo real o a ambos.
- No se presentará como una migración numéricamente equivalente.

Las partes técnicas independientes podrán avanzar mientras se resuelven las
preguntas. Ningún componente analítico bloqueado por una pregunta podrá darse
por validado mediante un valor supuesto, cero o un cierre artificial.

## 2. Alcance y límites

### 2.1. Incluido

- Contrato de lectura de Silver y sus atribuciones confirmadas.
- Tablas y agregados Gold orientados a consulta, con publicaciones versionadas.
- Formalización dimensional de Gold con criterios Kimball, dimensiones
  conformadas e historización selectiva conforme a 4.3 y D16.
- Compatibilidad con el histórico y convivencia con nuevas producciones.
- Producciones abiertas, máquinas tándem, ventanas horarias y turnos.
- Adaptación de repositorios de datos, API, cachés y actualización del frontend.
- Integración de la pantalla principal acordada como mesa de control.
- Tres contenedores: preparación de datos, API y frontend.
- Pruebas de equivalencia, recuperación, rendimiento, seguridad y operación.
- Preparación para despliegue independiente en validación y producción.

### 2.2. Fuera de esta entrega documental

- Cambiar código, dependencias, GitHub, servicios o datos existentes.
- Rehacer la ingesta Silver o sustituir sus controles por una tecnología nueva.
- Implantar Data Vault o convertir Bronze/Silver en esquemas dimensionales como
  requisito de esta integración.
- Incorporar fuentes industriales adicionales sin demostrar su necesidad.
- Cambiar el motor a .NET, incorporar Spark o añadir una base relacional para
  resolver consultas que no la necesitan.
- Desplegar Terraform/Ansible o ampliar la funcionalidad de supervisión.
- Convertir documentos antiguos del repositorio en funciones activas por defecto.

Este plan complementa el [plan de migración de la aplicación](PLAN_MIGRACION_CALIDAD_SERIE.md)
y el [plan de ingesta](../../Ingesta_Delta_Lake/docs/PLAN_DELTA_LAKE.md).
Los documentos anteriores conservan su valor histórico; sus estados pendientes
no sustituyen la comprobación del código y de las decisiones posteriores.

## 3. Estado comprobado en el estudio local

### 3.1. Aplicación de referencia

La base revisada es `calidad_serie`, rama local `main`, commit `c5a6bc0`, del
26/08/2026. La aplicación utiliza React/TypeScript, FastAPI/Python, Polars y
OpenPyXL. La construcción del backend se realiza en
[factory.py](../backend/app/factory.py).

Se revisaron contratos, repositorios, cálculos, cachés, API, frontend, publicación
de agregados, configuración y archivos de contenedores. El motor activo de
calidad contiene MIA, cinco criticidades A/R, DAR y ZC. Otros documentos, como
scorecards comparativos antiguos, no definen por sí solos el cálculo activo.

La mesa de control es todavía una referencia independiente:
[mesa-control-horizontal.html](../../design-references/pantalla-principal/mesa-control-horizontal.html).
Su integración en React constituye una entrega propia.

### 3.2. Evidencia funcional de solo lectura

Prueba realizada el 29/10/2026 sobre los datos reales locales de
`C:/Users/iker.villen/Desktop/AI_tool/Datos`, mediante una API dentro del proceso,
sin abrir un servicio permanente ni modificar los archivos:

| Comprobación | Resultado observado |
| --- | --- |
| `GET /api/health` | HTTP 200. |
| `GET /api/ready` | HTTP 200, sin recursos ausentes. |
| `GET /api/catalog` | 17 producciones, AV23 y AV32. |
| `POST /api/customer-score/query` | HTTP 200 para `AV23_PROD_001`. |
| Penalización de esa consulta | `206.557`. |
| Componentes presentes | MIA, Crítico, Grave, Mayor, Menor, Leve, DAR y ZC. |
| Detalle temporal | Siete turnos; sin advertencias en la respuesta. |
| Tiempo de esa petición | Aproximadamente 0,85 s con los agregados locales. |

Esta cifra es evidencia de una consulta concreta. No acredita rendimiento en el
servidor, concurrencia, equivalencia con Silver ni funcionamiento de la imagen
Linux. La referencia reproducible definitiva incluirá hashes y versiones de
todos los artefactos; esta medición inicial no los sustituye.

El daemon Docker local no estaba disponible. No se construyeron imágenes, no se
desplegaron contenedores y no se ejecutaron workflows de GitHub durante el estudio.

### 3.3. Diferencias entre los contratos actuales

| Concepto | Aplicación histórica | Silver y atribuciones actuales |
| --- | --- | --- |
| Catálogo | `manifest.csv`, con archivos por producción/ramal. | `CATALOGO_PRODUCCIONES`, con identidad basada en raíz ZC y máquina física. |
| Defectos MIA | Columnas preparadas `LISTA_DEFECTOS_*`. | `LISTA_DEFECTOS`, campos originales tipados y origen. |
| Inspeccionadas | Parquet preparado por producción. | `LISTA_RECH`, conservando la identidad y campos originales. |
| A/R | Eventos normalizados en Parquet. | `LISTA_AR` y diccionarios de defecto, estado y tipo. |
| ZC | Excel o agregado Parquet del histórico. | `CONT_ZC`, con rueda y teóricos originales. |
| Asignación | Incorporada al conjunto preparado. | Episodios temporales y registro de atribuciones por observación. |
| Lectura | Polars sobre Parquet concretos; sin lector Delta en la app. | Tablas Delta y publicaciones de atribuciones versionadas. |

Los episodios publicados en `ASIGNACION_PRODUCCION_RAMAL` y el registro de
atribuciones por observación no son intercambiables. La proyección Delta actual
contiene asociaciones ZF; la publicación completa conserva más información,
incluidas atribuciones de A/R y contadores. El publicador Gold deberá consumir
el contrato completo necesario, sin reconstruirlo a partir de nombres de archivo.

### 3.4. Hallazgos a tratar

| Hallazgo | Consecuencia y tratamiento en el plan |
| --- | --- |
| Máximo de inspeccionadas por fecha/ramal en el cálculo actual. | Q01 acordada: conservar la regla histórica en Gold y validar su equivalencia. |
| A/R con final nulo tratados como duración cero. | Q02 acordada: duración provisional hasta el corte de consulta en tiempo real, según D02; atribución fiable y fechas fuente intactas. |
| Equivalencia ZC fija `14 → AV23`, `16 → AV32`. | Sustituir el supuesto del histórico por correspondencias verificadas. Los CSV locales actuales utilizan 23, 31 y 32 para esas líneas. |
| Cavidades activas inferidas desde MIA. | Q03 acordada mediante D03: capacidad asignada desde parámetros de producción y gotas; los bloqueos temporales no reducen el denominador. La inferencia MIA no acredita esa capacidad y puede incluir marcadores sin cavidad identificada. |
| Catálogo del navegador cargado al entrar; resultados cacheados sin versión de datos. | Renovación por publicación e invalidación de caché. |
| Metadatos de filtros cacheados por nombres de archivos. | Deben renovarse si cambia el contenido sin cambiar los nombres. |
| API centrada en producción y turno. | Añadir un contrato explícito para los ocho puntos horarios y sus contribuciones. |
| Catálogo manual de reclamaciones. | D11: conservar la información histórica conocida con procedencia; nuevas producciones sin información hasta conectar una fuente actualizada, sin afectar al score de fabricación. |
| Readiness basado principalmente en existencia de recursos. | Comprobar compatibilidad de la publicación, sin escanear todo el lago en cada healthcheck. |
| Agregado ZC con huella de ruta absoluta. | Revisar portabilidad Windows/Linux y regeneración bajo rutas del servidor. |
| Nombre `Programacion` en Compose frente a archivo `Programación`. | Alinear rutas internas y nombres reales antes del despliegue. |

Los hallazgos describen diferencias observadas; no declaran incorrectos todos
los resultados históricos ni constituyen decisiones de negocio aprobadas.

## 4. Arquitectura acordada

```text
CSV Fenix ──> Ingesta existente ──> Silver Delta + atribuciones confirmadas
                                            │ lectura de versiones fijadas
                                            ▼
                               Preparación/publicación Gold
                                            │ único escritor Gold
                                            ▼
                                Gold versionado para consulta
                                            │ solo lectura
                                            ▼
                                  API FastAPI de calidad
                                            ▼
                                 Frontend React + Nginx

Histórico local ──> Adaptación histórica verificada ──> mismo contrato de consulta
```

### 4.1. Responsabilidades y permisos

| Componente | Lee | Escribe | Responsabilidad |
| --- | --- | --- | --- |
| Ingesta existente | CSV y controles propios. | Bronze/Silver, controles y atribuciones propios. | Aceptar y revisar datos industriales. |
| Publicador Gold | Silver, publicación confirmada de atribuciones y referencias de Calidad. | Gold, staging y control compacto propios. | Preparar datos coherentes y publicarlos incrementalmente. |
| API | Publicación Gold, configuración y referencias versionadas autorizadas. | Logs operativos; caché en memoria. | Consultar y calcular con el motor validado. |
| Frontend | API. | Estado de interfaz/caché del navegador. | Presentar histórico, tiempo real y detalle. |

La API no leerá los SQLite activos de la ingesta ni tendrá permisos de escritura
sobre Silver o Gold. El publicador no modificará CSV, Silver ni resoluciones de
incidencias de origen. Un problema de preparación se registra y conserva la
última publicación utilizable; no se corrige editando datos industriales.

Las actualizaciones de imágenes, repositorios o dependencias no inicializarán,
borrarán ni reconstruirán automáticamente los volúmenes persistentes.

### 4.2. Ejecución en tres contenedores

Se adopta la división aprobada por el usuario:

1. `quality-publisher`: preparación de datos para consulta.
2. `quality-api`: backend FastAPI.
3. `quality-web`: frontend estático y proxy Nginx.

El publicador puede ejecutarse primero como tarea acotada y evolucionar a
ejecución continua con espera configurable. No necesita una API pública para
realizar esa función. Cada imagen tendrá dependencias, usuario, configuración,
healthchecks, límites y ciclo de vida independientes.

La ingesta y la supervisión existentes mantienen su independencia. Compartir el
servidor no implica compartir bases de control, credenciales o directorios de
escritura. No se introduce PostgreSQL en la ruta de consulta por el mero hecho
de contenerizar la aplicación.

### 4.3. Formalización dimensional de Gold: Kimball e historización selectiva

**Decisión del usuario, 05/11/2026:** mantener Medallion y formalizar el modelo
dimensional de consumo en Gold con criterios Kimball. Data Vault queda fuera
del alcance actual; se podrá evaluar en una decisión posterior si la integración
corporativa de fuentes heterogéneas demuestra esa necesidad. El número de
plantas o de registros por sí solo no justifica incorporarlo.

**Estado:** decisión de diseño aprobada, pendiente de contratos detallados,
implementación y validación. La base Gold local de 7.18 ya separa observaciones,
catálogos, cantidades base y resultados; esa separación no acredita una
implementación integral de Kimball ni dimensiones SCD tipo 2 completas.

#### 4.3.1. Responsabilidad de cada capa

| Capa | Organización y responsabilidad |
| --- | --- |
| Bronze | Conservar originales, identidad, recepción y evidencia de origen para auditoría y reproceso. No imponer claves dimensionales ni transformar los originales en un esquema estrella. |
| Silver | Mantener contratos aceptados por fuente, identidades, calidad, vigencias y atribuciones verificadas. Puede aportar las claves y la historia necesarias a Gold sin rehacer la ingesta. |
| Gold | Publicar hechos de grano explícito, dimensiones de interpretación coherentes, bases y resultados preparados para las consultas autorizadas. |

Medallion organiza el flujo y la calidad entre capas; Kimball organiza el modelo
analítico. Su incorporación no crea una cuarta capa ni sustituye los tres
bloques internos de 5.1. Se formalizan sus responsabilidades y relaciones.
No cambiar permisos, fórmulas, atribuciones aceptadas ni contratos de entrada
para adaptar los datos a una forma de tabla. Cualquier ampliación mínima de
Silver requiere su entrega independiente y compatibilidad demostrada.

#### 4.3.2. Hechos, dimensiones y granularidad

E02 documentará el grano, las claves, la cardinalidad de las relaciones, la
aditividad de medidas y los filtros compatibles antes de materializar cada
familia. Se partirá de C01–C11 y de los esquemas locales existentes; los roles
de la tabla siguiente no obligan a duplicarlos ni a renombrarlos todos.

| Familia de hechos | Grano y restricciones que debe concretar el contrato |
| --- | --- |
| Inspeccionadas y bases MIA | Observación física y ámbito temporal de C05; conservar D01/D15 y una base de inspeccionadas independiente de la lista de defectos. |
| Rechazos por defecto | Observación/defecto y ámbito confirmado de C05; enlazar con la base compatible sin multiplicar el denominador. No interpretar eventos de defecto como botellas únicas rechazadas. |
| A/R | Evento o tramo atribuible identificado de C06. Conservar el evento de origen, sus relaciones y el cálculo de unión temporal de D14; no sumar duraciones solapadas como exposición física. |
| ZC y capacidad | Observación del contador físico y su intervalo, con las bases de capacidad correspondientes. Conservar D03/D10.1; una rueda compartida no se duplica al relacionarla con producciones concurrentes. |
| Resultados y componentes | Producción, ventana, selección/corte y versión de cálculo de C09. Conservar configuración, referencia y desglose; los scores y porcentajes no son medidas aditivas. |

Definir dimensiones compartidas de planta, línea/máquina, modelo/segmento,
defecto y tiempo donde las consultas las necesiten. Conformar significa acordar
identidades y significado, no igualar nombres: incluir el ámbito de planta,
origen y espacio de códigos cuando corresponda. Un ID local repetido en otra
planta o un código coincidente entre MIA y MDL no demuestra la misma entidad.
Las correspondencias modelo–segmento pendientes siguen sujetas a P01/D06.1.

Resolver las relaciones temporales y de muchos a muchos mediante relaciones
explícitas y verificadas, conservando los contratos de atribución. No introducir
uniones directas entre tablas de hechos que multipliquen cantidades. Agregar
cada familia a un grano común compatible antes de combinar sus medidas, o
utilizar sus bases/referencias explícitas. Mantener acceso al detalle y a su
evidencia; un agregado no sustituye los eventos que lo justifican.

#### 4.3.3. Historización y SCD

Los catálogos Silver con `vigente_desde`/`vigente_hasta` y las publicaciones
versionadas aportan historia, pero no constituyen por sí solos un modelo
dimensional SCD tipo 2 completo. E04 concretará selectivamente:

- Qué atributos requieren una nueva versión dimensional al cambiar su
  significado para el análisis histórico y cuáles admiten corrección en una
  proyección actual sin perder las publicaciones previas.
- Identidad de entidad y clave de versión, inicio/fin de vigencia, reglas de
  límites temporales, solapes, ausencias y referencias a la evidencia aceptada.
- Enlace de hechos físicos con la versión válida en su fecha de observación.
  Los catálogos de interpretación, criticidades y perfiles siguen D07/D08 y la
  versión fijada por cálculo; no imponerles otra política temporal por defecto.
- Distinción entre fecha efectiva de negocio y fecha de carga/aceptación cuando
  una corrección tardía pueda cambiar la interpretación del histórico.
- Recálculo explícito y acotado cuando proceda, preservando el resultado y las
  versiones anteriores. No actualizar silenciosamente los cálculos históricos
  para enlazarlos con la dimensión o configuración más reciente.

El historial de commits Delta conserva estados técnicos de tablas; no sustituye
las reglas de vigencia del negocio ni las referencias temporales de los hechos.
No introducir un motor bitemporal genérico ni nuevas tablas de control sin una
necesidad de consulta y un contrato acotado.

#### 4.3.4. Incorporación por entregas pequeñas y aceptación

1. **E02, contrato dimensional:** documentar primero MIA/inspeccionadas, sus
   dimensiones y relaciones; fijar claves, grano, versiones y compatibilidad.
2. **E04/E05/E07, primer modelo de consumo:** implementar las proyecciones
   mínimas en paralelo, sin sustituir el lector activo ni reescribir Bronze o
   Silver. Separar cambios estructurales de cambios semánticos aprobados.
3. **E04, historización selectiva:** añadir las dimensiones/versiones necesarias
   en PRs independientes, con casos de cambios, correcciones y límites temporales.
4. **E06/E09/E10, ampliación y activación:** incorporar A/R y ZC por separado,
   validar equivalencia y adaptar el lector solo tras acreditar compatibilidad.

Cada PR tendrá propósito y evidencia propios; no agrupar la remodelación de
todas las familias, ingesta y API en una única entrega. Validar al menos:

- Unicidad al grano declarado y referencias a dimensiones/versiones aceptadas.
- Mismas cantidades, contribuciones, estados y cobertura con entradas iguales.
- Base de 1.000 inspeccionadas con tres defectos sin transformarla en 3.000;
  conservación de la rueda ZC compartida y de los intervalos A/R originales.
- Dos versiones de una entidad, hechos a ambos lados del cambio, límites de
  vigencia, llegada tardía, conflicto y reproducción de publicaciones antiguas.
- Replay sin duplicados y correcciones sin publicaciones de versiones mezcladas.
- Rendimiento de las consultas representativas y concurrentes conforme a E13,
  sin atribuir una mejora automática al esquema dimensional.

Hasta superar estas comprobaciones, registrar la formalización como pendiente
o en curso, sin declararla implementada, desplegada ni equivalente integralmente.

## 5. Contrato técnico de Gold y de publicación

La organización y el reparto de cálculo siguientes quedan aprobados por el
usuario el 30/10/2026. Los nombres son identificadores de diseño lógico; los
campos, tipos, claves y rutas se concretarán por contratos pequeños antes de
crear tablas. Aprobar estos bloques no declara implementado su contenido.

### 5.1. Datos de servicio: tres bloques dentro de Gold

Los tres bloques organizan la misma capa Gold: contexto para interpretar,
detalle preparado para consultar y resultados frecuentes precalculados.
La API utiliza publicaciones coherentes de estos bloques. El detalle industrial
original permanece en Silver conforme a sus contratos; no se transforma Gold
en otra copia íntegra de los CSV ni en un registro de control de la ingesta.

#### 5.1.1. Identificación, relaciones y catálogos

| Conjunto lógico | Qué contiene | Granularidad propuesta |
|---|---|---|
| `productions` | Identidad estable, planta, línea, modelo, máquina física/parte tándem, fechas industriales, frontera operativa ZC y estado. | Una fabricación concreta; no una fila por turno, ramal o muestra. |
| `production_assignments` | Relaciones comprobadas entre producción, máquina, ramales y configuración física, con vigencia y evidencia de atribución. | Una asignación válida durante un intervalo. |
| `production_capacity` | Capacidad asignada, secciones/gotas y cavidades necesarias para el denominador de minutos-cavidad. | Una capacidad vigente durante un intervalo de una producción y su ámbito físico. |
| `reference_catalogs` | Versiones de códigos MIA/A/R, equivalencias, criticidades, estados/tipos, perfiles DAR, pesos y umbrales. | Correspondencias y reglas identificables/versionadas; concretar sus tablas tipadas, sin imponer un diccionario genérico único. |
| `model_segment_catalog` | Modelo → segmento MARBLE cuando exista → segmento de Calidad/perfil aplicable, con correspondencia inequívoca. | Una asignación de segmento aceptada por modelo y versión; P01 sigue pendiente. |

La capacidad representa cavidades **asignadas**, incluidas las temporalmente
bloqueadas o trabadas según D03; no solo las observadas como activas en MIA.
Los catálogos permiten interpretar, por ejemplo, 414 como Verticalidad o el
código interno Fenix como código de botella, y fijar reglas como el peso DAR 8.
Reutilizar las referencias ya aceptadas en Silver/configuración, conservando
la versión utilizada. Estos catálogos de interpretación se distinguen de la
referencia estadística MIA de 5.1.3; no son sinónimos de media/sigma.

#### 5.1.2. Detalle aceptado y preparado para las consultas

| Conjunto lógico | Qué contiene | Granularidad propuesta |
|---|---|---|
| `mia_rejections` | Rechazos por defecto, códigos interpretados y contexto físico atribuible. | Observación aceptada y atribuida, con fecha/periodo, ramal, máquina, molde y cavidad necesarios para los filtros. |
| `mia_inspections` | Inspeccionadas procedentes exclusivamente de LISTA_RECH, sin repetir la base por cada defecto. | Base de inspección identificable con contexto suficiente para aplicar filtros antes del máximo de D01. |
| `ar_intervals` | Avisos/rechazos atribuibles, defecto, cavidad, procedencia/tipo y apertura/cierre originales. | Un tramo A/R identificado y su atribución; el cierre nulo se conserva y el corte efectivo de cálculo permanece separado. |
| `zc_observations` | Rueda, teóricos e identidad física. | Una muestra física de máquina, normalmente de diez minutos; los vínculos con producciones no duplican sus contadores. |

Este bloque proyecta las columnas necesarias, interpreta códigos y utiliza
atribuciones versionadas; no reimplementa la aceptación de Silver ni descarta
observaciones por mostrar mala calidad. Se mantiene el detalle requerido por
los filtros soportados y la trazabilidad hacia las observaciones fuente.

Para MIA, conservar suficiente detalle de LISTA_RECH para filtrar máquina,
ramal, cavidad u otras dimensiones admitidas **antes** de obtener el máximo.
Un máximo global precalculado no sirve automáticamente para un subconjunto.
La proyección y deduplicación exactas se definirán en su contrato y se validarán
contra la consulta actual; no sumar máquinas ni multiplicar inspeccionadas por
los defectos. ZC conserva una sola observación física y sus relaciones, aunque
el rendimiento corresponda a varias fabricaciones concurrentes.

El nombre anterior `mia_observations` queda concretado como `mia_rejections`;
no representa otra copia adicional de esas observaciones. Los agregados no
sustituyen estas proyecciones mientras sean necesarias para consultas válidas.

#### 5.1.3. Cantidades agregadas y resultados precalculados

| Conjunto lógico | Qué guarda | Granularidad propuesta |
|---|---|---|
| `inspection_bases` | Máximo de INSPECC según D01 para el ámbito habitual sin filtros particulares. | Producción, línea, ramal y fecha/periodo de muestra; no una base repetida por defecto. |
| Bases MIA por ventana | Rechazos por defecto y denominador compartido correctamente agregado, con cobertura. | Producción, ventana y defecto, con la identidad del denominador compartido para evitar sumarlo varias veces. |
| Bases A/R por ventana | Minutos-cavidad disponibles y afectados por categoría o grupo DAR, con unión de solapes según D14. | Producción, ventana y categoría/grupo aplicable. |
| Bases ZC por ventana | Sumas físicas de rueda y teóricos, conservando su relación con producciones. | Máquina física y ventana; no multiplicar esos totales por el número de producciones asociadas. |
| Referencias estadísticas MIA | Media, sigma y número de muestras junto con identidad/composición de las producciones terminadas de referencia. | Conjunto de referencia versionado y defecto, para el ámbito de cálculo definido. |
| `quality_scores` | Total, valor normalizado cuando corresponda, configuración, referencia, corte, cobertura y disponibilidad. | Una producción y ventana calculada en una publicación/configuración identificadas. |
| `quality_score_components` | Score, peso y contribución de MIA, categorías A/R, DAR y ZC, con sus cantidades explicativas. | Un componente de un resultado calculado, vinculable a su desglose por defecto/grupo. |

`quality_window_metrics`, citado inicialmente, designa esta familia de bases
por ventana; no se añade por ese nombre otra copia completa de las mismas
cantidades. Concretar sus tablas y claves en contratos separados, sin mezclar
granularidades incompatibles en una tabla única por comodidad.

Los agregados parten preferentemente del detalle preparado de 5.1.2 y las
relaciones/capacidades de 5.1.1, fijados a la misma publicación compatible.
Conservar numeradores y denominadores: no guardar solo porcentajes o scores.
No sumar indiscriminadamente minutos por defecto/grupo si duplican tiempo
físico, ni usar cada muestra de diez minutos como muestra estadística histórica
si la unidad aprobada es una fabricación. No promediar scores horarios para
obtener el score del turno o de la producción.

#### 5.1.4. Qué precalcular y qué resolver durante la consulta

**Precalcular inicialmente:** las siete horas completas y la hora parcial de
la pantalla principal, los turnos de la serie, el resumen acumulado de producción,
las referencias estadísticas que utilizan y las cantidades base de las gráficas
habituales. La hora parcial utiliza su corte y duración reales; se recalcula por
cambios relevantes y solo se publica completa conforme a D04/D05.

| Uso de la aplicación | Resolución acordada |
|---|---|
| Pantalla principal y selección de una hora con configuración habitual | Leer score, componentes y desglose precalculados de una misma publicación. |
| Turno o resumen de producción con selección/configuración habitual | Leer el resultado precalculado compatible. |
| Combinación particular de filtros no cubierta por el agregado | Calcular desde el detalle Gold compatible, aplicando filtros antes de agregar las bases que cambien. |
| Ensayo de pesos, umbrales u otros parámetros admitidos | Recalcular la consulta con las reglas autorizadas, sin sobrescribir el resultado oficial ni cambiar los datos de Gold. |
| Comparación estadística particular | Calcular sobre las bases Gold seleccionadas, con límites de recursos y caché por publicación/parámetros. |

No precalcular todas las combinaciones posibles de filtros y parámetros. Si
una selección cambia la población histórica MIA, tampoco reutilizar una media
o sigma global incompatible: recalcular la referencia correspondiente bajo
D06, conservando su población y versión. Los nombres/tipos definitivos y la
matriz endpoint → datos → agregado válido se fijarán en E02/E10.

El publicador y la API utilizarán una única implementación autorizada de las
fórmulas. Extraerla del código actual por entregas pequeñas con equivalencia;
no mantener una fórmula para los resultados precalculados y otra para las
consultas particulares. La API no modifica Silver, Gold ni controles de ingesta.
Su caché identifica publicación, selección y configuración; un cambio de datos
o referencia no debe devolver un resultado antiguo como si fuese actual.

Se utilizarán tablas Delta para Gold y un lector compatible con su protocolo.
Una exportación Parquet compatible con la aplicación actual puede emplearse
como prueba de equivalencia o transición acotada; no constituye autorización
para mantener indefinidamente dos copias completas de todos los datos.

No se creará una tabla por producción, archivo o intervalo de diez minutos.
El particionado físico y los tamaños de archivo se decidirán con datos medidos;
no se impondrá una partición por identificador de alta cardinalidad.

### 5.2. Reglas de agregación

- Conservar contadores y estados suficientes para reproducir el cálculo; no
  guardar únicamente porcentajes o el score final.
- Agregar tasas a partir de numeradores y denominadores compatibles, no mediante
  una media simple de porcentajes.
- No obtener un score de hora o producción sumando scores de diez minutos o de
  turnos: se reaplica el motor sobre las entradas del intervalo correspondiente.
- Conservar intervalos/cavidades necesarios para minutos-cavidad y filtros. Las
  reglas de duración y capacidad siguen D02/D03; los solapes dentro de categoría
  se rigen por D14. El caso entre categorías distintas queda excluido según el
  alcance confirmado en Q03-S.
- Mantener detalle consultable para justificar un resultado y sus causas.
- Materializar las ventanas frecuentes sin generar todas las combinaciones
  posibles de filtros, parámetros y referencias estadísticas.
- Mantener una única implementación autorizada de las fórmulas. Si una función
  debe compartirse entre API y publicador, se extraerá en una entrega pequeña y
  con pruebas de equivalencia; no se copiará el algoritmo en dos servicios.

### 5.3. Entrada confirmada desde Silver

La ingesta deberá ofrecer un descriptor de publicación utilizable por el
publicador, de tamaño acotado y sin acceso del consumidor a SQLite activo.
Podrá aprovecharse la publicación actual, añadiendo un contrato externo estable
cuando sea necesario.

Debe identificar al menos:

- Entorno, planta, versión del contrato e identificador de publicación.
- Versión Delta y estado aceptado de cada tabla fuente utilizada.
- Publicación completa de atribuciones y referencias a sus artefactos, con
  verificaciones de integridad y regla aplicada.
- Catálogo y versiones de diccionarios relacionados.
- Confirmación de la publicación, cobertura conocida y pendientes que afectan
  al ámbito de consulta.

No se copiarán todas las filas en ese descriptor ni se sustituirá por un resumen
de supervisión: los recuentos de un panel no permiten reconstruir las atribuciones.

#### 5.3.1. Tres confirmaciones distintas

**Decisión aprobada el 30/10/2026:** separar la confirmación de entrega del
exportador, la confirmación de procesamiento Silver y la autorización para
calcular/publicar un resultado Gold completo.

| Confirmación | Responsable y significado | Lo que no demuestra por sí sola |
|---|---|---|
| Entrega del origen | En el modo operativo P02 aprobado el 02/11/2026 no hay señal del exportador: se acepta la entrega periódica y ausencia de novedades bajo la suposición autorizada, registrando las comprobaciones locales. | No constituye una certificación del origen ni demuestra que la ingesta haya escrito los registros. |
| Escritura Silver | La ingesta acredita que una operación terminó en una versión Delta concreta y registra su confirmación durable. | No demuestra que hayan llegado las demás fuentes ni que las atribuciones estén preparadas. |
| Disponibilidad analítica | El publicador comprueba versiones compatibles, atribuciones, referencias y cobertura de todas las dependencias de la producción/ventana. | No permite publicar componentes aislados ni garantiza que nunca lleguen correcciones futuras. |

Estas responsabilidades no se sustituyen entre sí. La existencia de un Parquet,
un heartbeat reciente, una notificación o una cola vacía no acreditan por sí
solos el conjunto de condiciones.

#### 5.3.2. P02 resuelto: modo operativo sin señal del exportador

**Decisión del usuario, 02/11/2026:** Fenix no proporciona marcador de entrega
completada ni otra señal adicional. Vuelca archivos aproximadamente cada diez
minutos; se acepta que la ausencia de nuevos datos corresponda a una producción
detenida o a que no haya novedades. Continuar con esta suposición operativa y
los archivos disponibles, sin mantener como bloqueo la obtención de un marcador.
La hipótesis forma parte de la política versionada de entrada, no de una regla
oculta en el código. P02 queda resuelto en su criterio operativo; falta implementar
sus metadatos y validaciones locales en los contratos correspondientes.

La ingesta seguirá descubriendo archivos nuevos y revisiones de archivos
sobrescritos. Para avanzar el consumo Gold deben existir comprobaciones locales
exitosas del ámbito y tratamiento confirmado de las revisiones conocidas que le
afecten. No exigir nuevos archivos, nuevas filas ni commits Delta en todas las
fuentes para cada ciclo: la versión previamente aceptada puede seguir vigente.
Registrar el corte de comprobación local, las versiones utilizadas, el estado
sin novedades cuando proceda y que la disponibilidad se basa en esta política.
Concretar sus campos y la asociación del corte con el ciclo en E02.

Un error de acceso, captura inestable, lectura fallida, operación de escritura
no confirmada o cola conocida sin procesar **no** se interpreta como ausencia de
novedades. No exigir que se vacíe todo el histórico si esos pendientes no afectan
a la producción/periodo evaluados. La espera por revisiones conocidas y por
atribuciones necesarias sigue vigente; no añadir un bloqueo por una señal externa
que el usuario confirma que no existe.

La ausencia de novedades no demuestra cuál de sus dos causas ha ocurrido: no
inventar una parada, fecha de cierre o cambio de producción. Tampoco crear
inspeccionadas, rueda, teóricos o eventos con valor cero para rellenar muestras.
Los A/R abiertos se computan hasta el corte según D02 y se conservan las
capacidades y diccionarios vigentes. Si con los datos aceptados falta realmente
una base o componente requerido, se aplica D05/D15; esta política no autoriza
scores parciales ni convierte un cálculo no disponible en cero.

Se mantiene la distinción entre confirmación de escritura, disponibilidad
operativa bajo hipótesis autorizada y certificación del origen. No afirmar que
el máximo timestamp, una cola vacía o el desfase de una hora prueban que Fenix
nunca enviará otra revisión. Si llegan datos tardíos o correcciones aceptadas,
actualizar Silver y recalcular los ámbitos Gold afectados conservando versiones.
Una futura señal del origen podrá reforzar el contrato, sin ser requisito actual.

#### 5.3.3. Qué publica la ingesta después de procesar

Antes de declarar una escritura confirmada, la ingesta verificará el commit
en el log de transacciones Delta, su identidad de tabla/entorno y de operación,
y la versión resultante; después persistirá el acuse de finalización de su
control. Aprovechar las comprobaciones existentes, sin sustituirlas por fechas
de archivos ni exigir a Gold que abra los SQLite activos. Una operación
preparada, en escritura o de resultado desconocido no se anuncia como confirmada.

La ingesta expondrá un manifiesto de entrada inmutable y acotado con el contenido
de 5.3, añadiendo la referencia a la entrega/corte del origen cuando esté
disponible, confirmaciones de operaciones, ámbitos cambiados y estado de
cobertura. Puede referenciar metadatos auxiliares compactos para los detalles
de cobertura y cambios; no copiar filas de negocio, todas las evidencias de
incidencias ni el registro completo de asignaciones dentro de un JSON de control.

Publicar primero el manifiesto completo y después promover atómicamente un
puntero pequeño a él, separado del puntero de publicaciones Gold. Los nombres,
rutas y tipos definitivos se fijarán en E02. El resumen de supervisión sigue
siendo un consumidor operativo y no se convierte en la API de entrada de Gold.

El identificador de publicación debe reflejar cambios relevantes de datos,
atribuciones, cobertura o aceptación/revisión; una revisión puede afectar al
consumo sin cambiar el número de versión Delta. La cobertura puede avanzar
con una tabla sin cambios, mediante la confirmación correspondiente, sin
inventar una escritura Delta ni otra copia de sus registros. Un heartbeat sin
cambios relevantes no obliga a crear publicaciones ni recalcular scores.

#### 5.3.4. Compatibilidad y cobertura por producción/periodo

Silver y Gold trabajan a ritmos independientes. Las tablas Silver no necesitan
el mismo número de versión ni la misma hora de escritura. Gold fijará un vector
compatible de versiones, incluido el manifiesto de fuentes con que se calcularon
catálogo y asignaciones. No combinar las últimas versiones disponibles por
conveniencia ni aplicar atribuciones de una versión a observaciones nuevas
sin validación. La protección de las entradas durante la lectura sigue 5.4.

La comprobación se realiza por planta, línea, parte física y producción/periodo
según corresponda. Distinguir cobertura acreditada, periodo comprobado sin
eventos, entradas pendientes de procesar, entrega ausente/incompleta y registros
bloqueados que afecten al cálculo. Conservar intervalos de cobertura y huecos;
un máximo global de fechas no demuestra que el intervalo intermedio esté cubierto.

| Componente | Dependencias que deben ser compatibles y utilizables |
|---|---|
| MIA | LISTA_DEFECTOS, bases válidas de LISTA_RECH, atribuciones y referencia histórica conforme a D01/D06/D15. |
| A/R y DAR | LISTA_AR, diccionarios, atribución, capacidad/gotas, perfil aplicable y corte de cálculo conforme a D02/D03/D07/D08/D10/D14. |
| ZC | CONT_ZC, rueda/teóricos e identidad física, atribuciones y reglas de transición; doble contraste cuando corresponda conforme a D10.1. |

El manifiesto puede describir escrituras confirmadas con dependencias analíticas
todavía pendientes: confirmar persistencia no equivale a declarar listo el total.
El publicador podrá preparar cálculos internos, pero solo promoverá resultados
completos de acuerdo con D05. Para una ventana 10:00–11:00, cobertura MIA solo
hasta las 10:50 impide publicar el total de esa ventana aunque A/R y ZC ya estén
disponibles. Mantener el resultado completo anterior y su corte real.

No exigir que toda la planta esté sin pendientes ni que se vacíe todo el
histórico para actualizar un ámbito cubierto. Delimitar el alcance de las
incidencias; no bloquear AV21 por un problema aislado y acreditado de AV32.
Cuando no pueda delimitarse el impacto, no suponer que el pendiente es inocuo.

### 5.4. Publicación coherente y lecturas concurrentes

Cada publicación Gold tendrá un manifiesto con:

- `publication_id`, entorno, planta, versión de esquema y fecha de publicación.
- Vector de versiones de Silver y publicación de atribuciones consumida.
- Vector de versiones de las tablas Gold que deben leerse juntas.
- Versiones/hashes de reglas, referencias, configuración y motor analítico.
- Identidad del histórico de referencia estadística.
- Límites temporales y cobertura por fuente y ámbito; no solo un máximo global.
- Resultado de conciliaciones y ubicación de su informe acotado.

Los nombres concretos y el esquema tipado se fijarán en E02.

El contrato de entrada fijará también cómo proteger las versiones y artefactos
que el publicador necesita mientras trabaja. Se acordará un mecanismo de
retención, pin o acuse de consumo con plazo y recuperación definidos. Referenciar
un artefacto en un JSON no impide que su propietario lo retire. No se ampliará
indefinidamente la retención de la ingesta para cubrir consumidores detenidos.

Los commits de tablas diferentes **no constituyen una transacción Delta conjunta**.
El publicador escribirá y validará las tablas afectadas y solo después cambiará
atómicamente el puntero a una publicación que las referencia por versión.

Además, D05 exige que la publicación visible de cada producción e intervalo
contenga el score total y todas sus contribuciones requeridas, con cobertura
acreditada y cálculos terminados. Los resultados intermedios permanecen internos.
La aplicación conserva la última publicación completa mientras se prepara la
siguiente; la coherencia técnica de versiones no sustituye esta validación de negocio.

La API fijará una publicación al comenzar cada consulta. El catálogo, hechos,
atribuciones, referencias y agregados de esa consulta deben ser compatibles con
ella, aunque el puntero cambie mientras se responde. Una respuesta incluirá su
identidad de publicación y parámetros temporales para poder reproducirla.

No se leerán todos los Parquet de una carpeta Delta con un glob genérico. Se
utilizará un lector Delta sobre versiones explícitas, compatible con las
características del protocolo de esas tablas.

### 5.5. Procesamiento incremental, correcciones y recuperación

1. Detectar una publicación Silver confirmada distinta de la consumida.
2. Fijar sus versiones y determinar producciones/ventanas afectadas.
3. Preparar cambios en lotes limitados y persistir referencias de recuperación.
4. Escribir Gold con identidad de operación y comportamiento idempotente.
5. Conciliar datos y comprobar los commits realizados.
6. Publicar el manifiesto coherente y actualizar el progreso confirmado.
7. Conservar el material temporal durante la fase inicial sin limpieza aprobada
   de 5.7. Una retirada futura exigirá autorización de su política y comprobar
   que no sea necesario para recuperación ni esté referenciado por lectores.

La identidad de operación incluirá entradas fijadas y versiones de transformación;
una repetición técnica no crea otra copia de los mismos hechos. Una nueva
revisión aceptada de un registro sí puede requerir reemplazar su resultado
derivado y recalcular agregados dependientes.

No se supondrá que una tabla ha cambiado únicamente por recibir filas nuevas:
también existen cierres, correcciones, revisiones de atribución y configuración.
No se exigirá Change Data Feed mientras no esté habilitado y validado. La
detección de cambios tendrá una estrategia explícita de lectura de versiones,
comparación acotada o reprocesamiento de ámbitos identificados.

Si cambia el conjunto de referencia MIA, pueden cambiar scores ajenos a la
producción que recibió datos. La invalidación seguirá las dependencias reales;
no se limitará a la última ventana por conveniencia.

| Punto de fallo | Recuperación requerida |
| --- | --- |
| Antes de cualquier escritura | Reintentar las mismas entradas fijadas. |
| Durante un lote | Mantener la publicación visible anterior y recuperar la operación. |
| Commit Delta realizado, acuse local perdido | Acreditar el commit por su identidad y comprobar el resultado antes de repetirlo. |
| Solo parte de las tablas escritas | No promover el puntero; recuperar o completar las restantes. |
| Puntero publicado, progreso local pendiente | Reconocer la publicación existente y completar el acuse. |
| Datos dependientes aún no utilizables | Registrar el motivo y conservar cobertura/última publicación válida según el contrato aprobado. |

Un único escritor será propietario de cada tabla/publicación Gold en esta fase.
Los controles guardarán progreso, identidades, versiones y recibos compactos;
los datos de negocio permanecerán en tablas. No se repetirá el almacenamiento
de filas completas o grandes JSON dentro del control.

Los tamaños de lote, retenciones, límites de staging y alertas serán configurables.
La prohibición de limpieza automática inicial de 5.7 prevalece sobre cualquier
retención por defecto de bibliotecas o servicios. E02 fijará unidades, valores
de ensayo y umbrales de aviso para tamaño de control,
staging, operaciones pendientes y publicaciones retenidas. El ensayo de crecimiento
de E08 deberá demostrar que confirmar más filas no añade copias de ellas al
control. La prueba de retirada futura de temporales se realizará en un entorno
aislado y no habilitará la limpieza en el piloto o en los datos reales.
La limpieza de Delta y artefactos respetará las versiones/publicaciones retenidas
y las consultas en curso. No se ejecutará una limpieza agresiva por antigüedad
sin verificar referencias y la política de recuperación.

### 5.6. Activación de Gold: comprobación cada 30 segundos

**Decisión aprobada el 30/10/2026:** el publicador Gold comprobará inicialmente
el identificador/puntero de publicación Silver cada **30 segundos**, con intervalo
configurable y ese valor inicial. No recorrer los CSV, cargar tablas completas
ni consultar SQLite para descubrir si hay novedades. La supervisión y la
actualización de pantalla tienen funciones e intervalos independientes.

| Proceso | Cuándo trabaja |
|---|---|
| Ingesta Silver | Conforme consume los archivos/revisiones, valida y confirma cada operación, conservando sus colas. |
| Publicador Gold | Comprueba el puntero cada 30 segundos; si hay cambios relevantes, fija las entradas, prepara los ámbitos afectados y publica cuando sus dependencias estén disponibles. |
| Aplicación | Comprueba nuevas publicaciones Gold según D04 y lee la última publicación completa compatible con su consulta. |

La consulta periódica no equivale a un cálculo periódico completo: sin cambios
relevantes, no recalcular ni escribir Gold. Con el patrón de llegada actual,
las publicaciones útiles se esperan normalmente cada diez minutos, sin imponer
esa cadencia como garantía ni esperar hasta el siguiente turno. Los 30 segundos
acotan la espera de detección cuando el proceso está disponible; no constituyen
un SLA de finalización si hay cálculo, cola o dependencias pendientes.

Persistir el último consumo confirmado y los trabajos/ámbitos pendientes en
metadatos compactos. Tras una caída, detectar la diferencia entre publicación
disponible y consumida y recuperar operaciones según 5.5. Si llegan novedades
mientras se calcula, conservar las versiones fijadas de esa operación y registrar
lo siguiente por hacer; no reiniciar indefinidamente el cálculo activo ni perder
periodos afectados. Si se agrupan publicaciones, conservar la unión de cambios
desde el último consumo o reconstruirla de forma comprobable; no mirar solo el
último aviso y descartar los cambios intermedios. No exigir una fila de control
con toda la información industrial por cada registro procesado.

**Kafka y otros buses:** no se incorporan en la fase inicial. El uso planteado
por el usuario es válido como futuro transporte del aviso «hay una publicación
nueva», separado de la comprobación de disponibilidad analítica. Mantener
independientes el mecanismo de activación y la transformación: poder sustituir
la consulta periódica por avisos sin rehacer fórmulas, validaciones ni tablas.

Una futura notificación incluiría la identidad/referencia del manifiesto ya
publicado; el manifiesto duradero seguiría siendo la referencia de datos.
Kafka no observa por sí solo nuestro archivo: la ingesta tendría que emitir
el evento. Diseñar entonces los reintentos y la reconciliación del caso
«manifiesto publicado, envío del aviso fallido», el procesamiento idempotente
de avisos repetidos y la recuperación tras desconexión. No asumir una
transacción conjunta entre Delta, un archivo de manifiesto y el bus.

**Llegadas tardías:** un aviso, un plazo de espera o un watermark no demuestran
que todos los datos hayan llegado. No descartar automáticamente correcciones
aceptadas por superar un retraso fijo. Recalcular y versionar los ámbitos
afectados conforme a 5.5/D07, preservando las publicaciones anteriores y las
políticas de referencia histórica aprobadas. Un checkpoint representa progreso
de procesamiento, no una prueba de completitud del origen.

**Referencias de arquitectura:** aplicar los principios, sin incorporar Spark,
Kafka ni dependencias de la plataforma Databricks en esta fase. Databricks
documenta [disparadores por cambios en tablas](https://docs.databricks.com/aws/en/jobs/trigger-table-update)
y [comprobaciones por intervalos configurables](https://learn.microsoft.com/en-us/azure/databricks/structured-streaming/triggers).
Azure documenta [activación por eventos de almacenamiento](https://learn.microsoft.com/en-us/azure/data-factory/how-to-create-event-trigger?tabs=data-factory).
Los [watermarks de Structured Streaming](https://docs.databricks.com/aws/en/structured-streaming/watermarks)
definen políticas de estado/datos tardíos, no una garantía absoluta de entrega.
La [introducción oficial de Kafka](https://kafka.apache.org/intro/) describe el
transporte y almacenamiento de eventos entre productores y consumidores.

### 5.7. Conservación inicial sin limpieza automática

**Decisión del usuario, 02/11/2026:** no fijar plazos de limpieza de archivos
Delta ni de material temporal hasta haber comprobado personalmente durante
varias semanas que la integración funciona correctamente. No hay una fecha
de caducidad automática de esta decisión: habilitar una política de limpieza
requerirá un acuerdo posterior explícito.

En el diseño y despliegue Gold inicial deshabilitar la eliminación automática
por antigüedad, VACUUM programado, purga de versiones/archivos y limpieza
destructiva de staging, operaciones interrumpidas y temporales. Revisar también
la expiración automática de logs/checkpoints Delta de las dependencias, para
no prometer conservación de versiones que la configuración de la biblioteca
retiraría. Conservar manifiestos, referencias y metadatos necesarios para
reconstruir las publicaciones, no solo los Parquet de datos.

Esta conservación no implica generar una copia completa por comprobación:
mantener la escritura incremental, operaciones idempotentes, metadatos compactos
y ausencia de cambios sin nuevas versiones de datos. La promoción mediante
renombrado atómico conserva el contenido y no equivale a una purga por retención.
Monitorizar espacio libre y crecimiento de Delta, controles y staging; avisar
ante crecimiento anómalo. Si se alcanza un límite de capacidad, no borrar
datos silenciosamente para continuar: preservar la última publicación válida
y registrar el impedimento operativo para su resolución.

La conservación local no sustituye una copia de seguridad ante avería o borrado
accidental. El contrato de operación deberá incluir respaldo coherente de datos,
logs Delta, manifiestos, referencias y estado de recuperación, así como una prueba
de restauración. No se declara que esos respaldos ya estén configurados.

Esta actualización es documental y se refiere a la integración Gold por
implementar: no modifica ni desactiva por sí sola tareas existentes de limpieza
de la ingesta, Docker o el servidor. Cualquier configuración efectiva sobre esos
servicios se revisará en su entrega autorizada, preservando sus datos actuales.

### 5.8. Respaldo y restauración: contraste con Databricks

**Diseño aceptado por el usuario el 02/11/2026; implementación y validación
pendientes.** No se ha contratado almacenamiento ni instalado un respaldo.
Prever un respaldo cada diez minutos con un único trabajo activo; el presupuesto
de recursos es orientativo hasta medirlo. Distinguir tres mecanismos:

- Historial Delta/time travel: permite leer estados anteriores mientras existan
  sus datos y logs; no protege por sí solo contra la pérdida del almacenamiento.
- Copia independiente de un estado de tabla: Databricks ofrece DEEP CLONE y su
  sincronización incremental. Un SHALLOW CLONE depende de archivos de origen;
  DEEP CLONE no copia el historial original de versiones y no sustituye un respaldo
  de todas las publicaciones, reglas y estados necesarios del sistema.
- Recuperación del servicio: además de tablas, preservar metadatos, configuración,
  decisiones y progreso recuperable; restaurar y validar un conjunto compatible.
  La réplica continua tampoco sustituye versiones de respaldo: puede propagar
  una modificación equivocada.

Para el servidor Windows/WSL2 se propone conservar Delta con la política de 5.7
y utilizar una herramienta de respaldo existente, inicialmente Restic como
candidata, hacia almacenamiento independiente del servidor. Si Sistemas dispone
de una solución corporativa equivalente, evaluar primero su integración. No
desarrollar un motor de backups propio ni introducir Spark/Databricks solamente
para copiar archivos. No asumir disponibilidad de CLONE en el runtime delta-rs
local por estar documentado en Databricks.

El respaldo debe identificar un corte coherente: publicaciones y versiones
Delta fijadas, archivos inmutables necesarios, capturas consistentes de controles
y decisiones, configuración y material de recuperación. Coordinar el corte con
los escritores; copiar después los archivos protegidos mientras continúa la
ingesta. No recorrer carpetas vivas sin más ni copiar un SQLite activo ignorando
su WAL; utilizar su API de backup o un mecanismo consistente acreditado. Una
instantánea VSS del disco Windows no demuestra por sí sola consistencia del
sistema de archivos Linux dentro de un VHDX WSL2 activo.

Registrar ID del respaldo, publicación/corte, inventario verificable, resultado
y última restauración probada. Restaurar en volúmenes separados, validar lecturas
Delta y sus versiones, relaciones, cantidades de referencia y recuperación del
progreso; conmutar solo tras validación y sin dos escritores activos. Preservar
la ubicación anterior hasta comprobar el servicio. Proteger el repositorio y las
claves de recuperación fuera del servidor y del código. La frecuencia propuesta
de diez minutos no garantiza pérdida cero: el RPO efectivo depende del último
respaldo completo recuperable y de conservar las entradas posteriores para replay.

Dimensionar con medidas, sin prometer un multiplicador fijo:

```text
almacenamiento local = datos vigentes + archivos históricos exclusivos
                       + logs/manifiestos/estado + temporales
respaldo = bloques únicos respaldados + índices/metadatos del repositorio
```

Los datos añadidos pueden compartirse entre versiones; una reescritura o
compactación genera archivos adicionales aunque no cambie el número de filas.
Contabilizar Bronze, Silver y Gold, incluidas las versiones CSV originales que
se decida capturar. Medir bytes vigentes/históricos, archivos nuevos y retirados
por operación, incremento diario del respaldo, temporales, RAM máxima y espacio
libre. Los ejemplos de presupuesto en el chat son escenarios, no mediciones del
servidor. Sin limpieza aprobada no existe un tamaño máximo permanente: proyectar
el crecimiento y ampliar capacidad, sin borrar para cumplir un presupuesto.

**Estimación orientativa, no medición del servidor:** conjunto protegido inicial
de 100 GB y 1 GB diario nuevo durante treinta días. Las reescrituras generan
archivos históricos exclusivos adicionales, no otras copias completas por versión.

| Concepto al cabo de treinta días | Pocas reescrituras | Más reescrituras |
|---|---:|---:|
| Conjunto inicial | 100 GB | 100 GB |
| Datos nuevos | 30 GB | 30 GB |
| Archivos históricos exclusivos retenidos | 6 GB | 60 GB |
| Logs, manifiestos y estado, supuesto del ejemplo | 2 GB | 2 GB |
| Almacenamiento local, sin temporales voluminosos | 138 GB | 192 GB |
| Presupuesto de respaldo independiente con 10 % adicional, sin descontar compresión/deduplicación | 152 GB | 211 GB |
| Total entre ambas ubicaciones | 290 GB | 403 GB |

El 10 % es un margen del ejemplo, no una garantía sobre el formato del repositorio.
Para restaurar sin tocar lo existente reservar además espacio próximo al tamaño
del conjunto a recuperar, más margen. Medir también temporales, capturas CSV e
historial del respaldo: las cifras no fijan un límite ni sustituyen el inventario.

Presupuestar inicialmente 1–2 GiB de RAM para el proceso de respaldo, con CPU y
paralelismo limitados y un único trabajo concurrente. No es un consumo garantizado:
medir RAM máxima, índices y número de archivos antes de fijar límites definitivos.
Conservar versiones requiere principalmente disco, no cargarlas todas en RAM.
Si un trabajo dura más de diez minutos, evitar solaparlo y señalar la antigüedad
real del último respaldo completo. Notificar fallos sin borrar material ni perder
el último punto recuperable. Retener inicialmente los puntos de respaldo sin purga
automática, coherentemente con 5.7; su limpieza posterior requiere aprobación
explícita. Acreditar RPO y tiempo de restauración mediante medición y ensayo,
sin prometer pérdida cero por programar un trabajo cada diez minutos.

Referencias primarias consultadas:
[historial y límites de recuperación](https://learn.microsoft.com/en-us/azure/databricks/tables/history),
[clonación y ausencia del historial original](https://learn.microsoft.com/en-us/azure/databricks/delta/clone),
[recuperación de desastres](https://docs.databricks.com/aws/en/admin/disaster-recovery),
[respaldos Restic](https://restic.readthedocs.io/en/stable/040_backup.html).

## 6. Consulta histórica y tiempo real

### 6.1. Identidad y contexto de producción

- Mantener IDs estables aunque se complete una fecha de salida.
- Conservar las raíces ZC y vínculos `ID_ST1_REL` acordados en la ingesta; no
  interpretar ese campo como un predecesor sin comprobar su significado.
- No unir fabricaciones por compartir modelo ni dividirlas por turnos o archivos.
- Mantener separadas las partes de una máquina tándem, incluso con el mismo modelo.
- Relacionar IDs históricos y nuevos mediante una correspondencia comprobada.
  Nombre de carpeta, modelo y proximidad temporal no bastan por sí solos.
- Conservar fechas industriales, último dato recibido y último dato utilizable
  como conceptos distintos.
- La ausencia de cierre mantiene abierta la producción según la decisión actual;
  no acredita por sí sola que los datos estén frescos.

### 6.2. Instante de corte y cobertura

El tiempo real seguirá D04: una ventana móvil de ocho puntos horarios, siete
correspondientes a horas completas y un octavo a la hora parcial hasta el corte
efectivo disponible, manteniendo una hora de desfase. No equivale a las ocho
horas del turno actual ni a ocho horas completas de duración observada.

El contrato distinguirá hora del servidor, última observación disponible,
última hora calculable y corte solicitado. D04 fija la representación y la
frecuencia de actualización; D05 impide publicar un resultado nuevo si falta
cobertura necesaria o queda algún cálculo requerido pendiente.

Una producción abierta no recibirá una fecha final ficticia para poder
consultarse. Cada cálculo usará un corte explícito, conservando las fechas
originales y la condición provisional de los intervalos abiertos.

Se mantendrá la política temporal ya acordada para Fenix y la hora repetida,
identificando su versión. Esta integración no convertirá silenciosamente datos
locales sin zona a UTC. Deben comprobarse límites de muestra, cambios de día,
turnos 06:00/14:00/22:00 y cambios de horario.

### 6.3. Contratos de API y caché

Se preservarán los endpoints existentes durante la transición. Las ampliaciones
incluirán un catálogo de producciones abiertas/cerradas y una consulta temporal
con intervalo, granularidad, corte, cobertura y publicación identificables.

La respuesta para la mesa de control debe permitir obtener, para la misma
producción y publicación:

- Histórico de todos los turnos registrados de la serie.
- Ocho puntos horarios de la ventana móvil, con sus timestamps reales.
- Score exacto y contribución de cada término para cada punto.
- Datos del punto seleccionado y estado de cobertura; ausencia no equivale a cero.

La API conservará los filtros y parámetros autorizados; los agregados no podrán
eliminar la capacidad de aplicar los filtros que se declaren compatibles.

Las claves de caché incluirán publicación, producción, selección, intervalo,
corte, parámetros y versiones analíticas relevantes. Cambiar los datos manteniendo
los mismos archivos o IDs invalidará el resultado correspondiente.

El frontend comprobará periódicamente la versión disponible, con intervalo
configurable. Renovará catálogo y resultados cuando corresponda, evitando que
una respuesta antigua sobrescriba una nueva y sin perder la selección del usuario.
No recargará todo el histórico en cada comprobación de versión.

### 6.4. Pantalla y explicabilidad

La integración de la mesa de control respetará la referencia aprobada y sus
criterios de disposición horizontal, predominio de gráficas, histórico seguido
de actual y desglose horario con valor total exacto. No se inventarán nuevos
estados o reglas de coloreado por conveniencia de implementación.

La referencia gráfica de mediana/media y sigma de turnos anteriores es distinta
del histórico de referencia utilizado por el componente MIA del score. Sus
poblaciones, filtros y versiones deberán identificarse por separado.

## 7. Preguntas que deben resolverse y acordarse

Las preguntas pendientes se tratarán una a una, con ejemplos reales,
alternativas, impacto numérico y una decisión registrada. Las ya acordadas
figuran identificadas en la tabla y desarrolladas en el registro de decisiones.
Calidad valida la semántica; desarrollo presenta la evidencia y su aplicación.

| ID | Pregunta a acordar | Evidencia y decisión necesarias | Bloquea |
| --- | --- | --- | --- |
| Q01 — ACORDADA | ¿Cuál es la base exacta del denominador de inspeccionadas para el score, y cómo se concilia con el detalle de Silver? | Decisión D01: máximo por producción, línea, ramal y fecha/hora; suma de esos máximos en el periodo. Se conserva el detalle Silver. | Criterio de negocio resuelto; implementación y prueba de equivalencia pendientes. |
| Q02 — ACORDADA | ¿Cómo se computa un A/R todavía abierto en tiempo real? | D02: duración provisional hasta el corte de consulta, con FECHA_OFF nulo en origen; al recibir el cierre se actualizan los intervalos afectados y se conserva la trazabilidad. La regla no resuelve por suposición una atribución ambigua. | Criterio de cómputo resuelto; adaptación de atribución/cálculo y validación pendientes. |
| Q03 — ACORDADA | ¿De dónde se obtiene el número de cavidades asignadas a la producción y qué ocurre si alguna está temporalmente bloqueada? | D03: `MP9_PARAM_MODELO.NUM_SECCIONES`, vinculado a la producción, y valor de gotas de su configuración. Una cavidad trabada o bloqueada permanece en el denominador y sus A/R se computan. No inferir capacidad por presencia en MIA. | Criterio de capacidad resuelto; ampliación de fuentes/contratos, implementación y validación pendientes. |
| Q03-S — ACORDADA | ¿Cómo se agregan A/R superpuestos de una misma cavidad? | D14: dentro de la misma producción/cavidad y categoría, contar la unión temporal una sola vez y conservar ambos eventos íntegros. Ejemplo 10:00–10:30 y 10:20–10:50: 50 minutos-cavidad, no 60. El usuario descarta el caso entre categorías distintas para el mismo modelo/cavidad/producción; no implementar prioridades ni reparto para ese escenario. | Criterio acordado y alcance excluido explícito. Implementación y validación pendientes; capacidad de D03 intacta. |
| Q04 — ACORDADA | ¿Qué representan los ocho puntos de tiempo real y cuándo se actualiza el último? | D04: siete horas completas y una última parcial, identificada como provisional, con una hora de desfase. Recalcular con publicaciones Gold nuevas que afecten a la producción, normalmente cada diez minutos; la pantalla comprueba versiones cada 30 segundos. Mostrar el corte efectivo y conservar el valor si no hay novedades. | Criterio temporal y de actualización resuelto; implementación y validación pendientes. Publicación completa conforme a D05. |
| Q05 — ACORDADA | ¿Qué se muestra cuando un componente no es calculable o está incompleto? | D05: publicar total y desglose solo con todas las fuentes y cálculos completos para el mismo intervalo. Mantener el último resultado completo durante la espera. D15: si falta una base válida de LISTA_RECH, dejar pendiente el cálculo MIA, sin usar el respaldo histórico desde LISTA_DEFECTOS. | Criterio acordado; implementación y validación pendientes. |
| Q06 — ACORDADA | ¿Qué referencia MIA corresponde y cuándo se actualiza? | D06: solo producciones terminadas y validadas. Si existe histórico del mismo modelo, utilizar exclusivamente ese modelo; solo en ausencia de histórico propio, utilizar su segmento confirmado. Cada nuevo cierre elegible puede actualizar la referencia de fabricaciones abiertas respetando esa prioridad. D06.1 define el catálogo modelo–segmento y D08 su uso para DAR. Conservar versiones. | Criterio de negocio y prioridad acordados; correspondencias, implementación y validación pendientes. |
| Q07 — ACORDADA | ¿Cómo se mantienen las versiones históricas y actuales de equivalencias MDL, criticidades y códigos Fenix/MIA? | D07: versiones con fecha de entrada en vigor para nuevos cálculos de producciones abiertas y nuevas. Conservar las reglas y resultados de las publicaciones anteriores. Recalcular el pasado solo mediante una acción explícita, manteniendo ambas versiones. Verificar códigos, IDs y origen, sin doble normalización. | Criterio acordado; implementación y validación pendientes. |
| Q08 — ACORDADA | ¿Cómo se asignan modelo, segmento y perfil DAR al histórico y a nuevas producciones cuando el dato no está disponible? | D06.1 define el catálogo modelo–segmento. D08 utiliza Salud Serie límites.pptx como referencia de características por segmento, con condiciones por cierre y equivalencias explícitas de códigos/fuentes. DAR incorpora solo las características aplicables a su definición. No usar perfiles por defecto para suplir clasificaciones ausentes; aplicar D05 si falta información necesaria. | Criterio acordado; carga/validación de clasificaciones, códigos, cierre y perfiles e implementación pendientes. |
| Q09 — CRITERIO FUTURO REGISTRADO | ¿Cómo se trataría un filtro de cavidad en un análisis detallado? | D09: opción futura sin implementación nueva comprometida. Si se habilita, filtrar MIA/A/R correctamente atribuidos y conservar ZC de máquina dentro del score filtrado, con su alcance explícito y sin repartir contadores. La pantalla principal mantiene el score general de producción/línea. | No bloquea el alcance inicial. La disponibilidad de cada filtro requiere comprobar sus campos y atribuciones antes de habilitarlo. |
| Q10 — ACORDADA (inicio operativo y doble comprobación) | ¿Desde qué instante comienza el denominador de minutos-cavidad y cómo se contrasta la atribución ZC? | D10/D10.1: frontera temporal derivada de FECHA_ON como aproximación operativa, revisable ante registros comprobados de la saliente. Doble comprobación obligatoria temporal y física en todos los casos. Conservar fechas fuente y evidencia por separado; no equiparar el primer rechazo al inicio exacto. | Criterio operativo cerrado; incorporación de evidencias, implementación y validación pendientes. |
| Q11 — ACORDADA | ¿Cómo se presenta inicialmente la información de reclamaciones/calidad cliente? | D11: conservar reclamaciones y clasificaciones históricas conocidas con su procedencia. Nuevas producciones: «Sin información de reclamaciones» mientras no exista fuente actualizada. No asignar buena calidad ni ausencia de reclamaciones por falta de datos. Integración automática aplazada, independiente del score de fabricación. | Criterio inicial acordado; adaptación de presentación/estado y validación pendientes. No bloquea el cálculo de fabricación. |
| Q12 — ACORDADA | ¿Cómo se enlazan producciones históricas con IDs Fenix sin duplicarlas ni unir fabricaciones diferentes? | D12: conservar IDs originales y registrar equivalencias únicamente si está demostrada la misma fabricación, contrastando modelo, máquina física, registros y periodos. No unir por modelo o fechas próximas. Conservar los candidatos dudosos para revisión y mejorar reglas con evidencia de nuevas producciones. | Criterio acordado; correspondencias concretas, implementación y validación pendientes. |
| Q13 — ACORDADA | ¿Cómo se calcula y valida el score de cada intervalo horario? | D13: agregar cantidades base del intervalo conforme a sus contratos y calcular después cada componente y el total con las fórmulas/pesos aprobados. No sumar ni promediar scores de muestras de diez minutos, porcentajes o turnos. El punto parcial utiliza su duración efectiva y se publica completo según D05. | Criterio acordado; implementación y validación pendientes. |

### 7.1. Registro de decisiones

Cada resolución añadirá: ID de pregunta, estado, responsable de aprobación,
fecha, casos examinados, decisión, impacto, versión afectada y pruebas exigidas.
Estado actualizado: **Q01–Q04 ACORDADAS, publicación de Q05 ACORDADA e
incorporación/actualización y comparabilidad de Q06 ACORDADAS**. El catálogo
modelo–segmento de Q08 queda aprobado mediante D06.1; su carga requiere validar
las correspondencias. **Q07 y Q08 ACORDADAS** mediante D07 y D08. Q03-S queda
acordada mediante D14 para una misma categoría, con el caso entre categorías
descartado por el usuario. **D15 resuelve el respaldo histórico señalado en D01:
no utilizarlo en la nueva solución; denominador solo desde LISTA_RECH y cálculo
pendiente si falta una base válida.** Q11–Q13
quedan acordadas mediante D11–D13. El
inicio operativo de Q10 queda acordado mediante D10/D10.1 como aproximación
temporal revisable con doble comprobación, pendiente de implementación y validación.
Q09
queda registrada mediante D09 como criterio futuro, sin nuevo filtro comprometido.
La prioridad de población MIA queda acordada en D06: mismo modelo y, solo en
ausencia de histórico propio, segmento confirmado. Su contrato técnico se fijará en E02.

#### D01. Denominador MIA: preservar el máximo histórico

- **Pregunta:** Q01.
- **Estado:** aprobado por el usuario el 29/10/2026; implementación Gold y
  comprobación de equivalencia aún pendientes.
- **Fundamento operativo confirmado:** una misma botella puede pasar por varias
  máquinas de inspección del ramal. Sumar sus inspeccionadas contaría varias
  veces parte del mismo flujo. Se adopta el mayor contador como base de llegada
  a inspección ZF para ese periodo, conforme al criterio utilizado por el histórico.
- **Fuente única en la nueva solución, según D15:** `LISTA_RECH.INSPECC` en Silver.
  En el histórico preparado,
  `LISTA_RECH_INSPECC` se convirtió en `inspected_total`.

Para una producción `P`, línea `L` y periodo de consulta `W`:

```text
base(P, L, ramal, fecha_hora) = MAX(INSPECC de los registros del grupo)

denominador(P, L, W) = SUMA de base(P, L, ramal, fecha_hora)
                       para los ramales y muestras del periodo solicitado
```

El contexto de producción y los filtros autorizados se resolverán de forma
compatible con la consulta histórica antes de calcular la base correspondiente.
El grupo del denominador no se subdivide por máquina de inspección, molde,
cavidad o código de defecto para después sumar esas subdivisiones. Tampoco se
mezclan producciones, ramales o timestamps distintos.

**Silver permanece intacta:** conserva los contadores, máquinas, moldes y
cavidades por registro aceptado, con sus claves y garantías actuales. Esta
decisión se aplica a la preparación/consulta Gold; no autoriza a colapsar el
detalle industrial ni a alterar el contrato de idempotencia de la ingesta.

**Compatibilidad:** la regla principal no cambia la semántica del histórico.
Debe aplicarse de manera consistente al cálculo por producción, turno y ventana
horaria. D15 descarta para la nueva solución el respaldo histórico que suma
`LISTA_DEFECTOS_INSPECC` cuando no se obtiene un denominador positivo. Si falta
una base válida de `LISTA_RECH`, MIA queda pendiente y el total no se actualiza
conforme a D05. Conservar la ruta y publicaciones legadas para comparación;
no presentar como numéricamente equivalente un caso histórico que usara ese respaldo.

**Pruebas exigidas en E05/E09:**

- Varias máquinas en el mismo grupo: conservar el máximo, no la suma.
- Mismo máximo repetido: no aumentar la base por el número de registros.
- Diferentes ramales o muestras: calcular bases independientes y sumar sus máximos.
- Fabricaciones concurrentes: no cruzar las bases de ambas producciones.
- Llegada/revisión aceptada que cambia un máximo: actualizar la base y los
  agregados afectados, manteniendo su versión anterior reproducible.
- Equivalencia del denominador y del score con los casos históricos de referencia.

#### D02. A/R abiertos: duración provisional hasta el corte de consulta

- **Pregunta:** Q02.
- **Estado:** aprobado por el usuario el 29/10/2026; adaptación de atribución,
  preparación Gold y cálculo aún pendientes de implementación y validación.
- **Ámbito aprobado:** cómputo de A/R que continúan abiertos al consultar en
  tiempo real. Un evento no deja de aportar minutos porque no haya llegado su cierre.
- **Ejemplo confirmado:** un aviso de una cavidad iniciado a las 10:15 y abierto
  en una consulta con corte a las 11:00 aporta **45 minutos-cavidad provisionales**.

Para un tramo válido, correctamente atribuido, y una ventana consultada `[A, B)`
con corte temporal explícito `C`:

```text
final_efectivo = MIN(FECHA_OFF, C) si existe FECHA_OFF
                C                si FECHA_OFF es nulo

inicio_computado = MAX(FECHA_ON, A)
final_computado  = MIN(final_efectivo, B)

minutos_del_tramo = MAX(0, final_computado - inicio_computado), en minutos
```

La precisión de segundos del origen se conserva en la duración; no se redondea
cada tramo a minutos enteros antes de agregar. Se mantienen las reglas de
redondeo publicadas del motor analítico. Datos contradictorios se tratan según
el contrato de integridad y no se convierten silenciosamente en tramos válidos.

**Origen y derivación separados:** `FECHA_OFF` permanece nulo en Silver mientras
no exista cierre real. El límite efectivo y la duración calculada son datos
derivados vinculados al corte y a la publicación; no se escriben como un cierre
industrial. El resultado identifica que contiene minutos de eventos aún abiertos.

**Corte y vigencia:** `C` es el instante de corte de la consulta, no necesariamente
la hora del reloj. Su relación con el desfase de una hora, los datos consolidados
y las fuentes atrasadas sigue D04/D05. D02 no acredita por sí misma
que una fuente antigua siga actualizada ni extiende eventos históricos sin
cierre hasta la fecha presente automáticamente.

**Atribución:** la regla se aplica a la producción/cavidad que corresponda según
el contrato de atribución. El pipeline actual no debe seguir excluyendo un A/R
solo por estar abierto cuando exista atribución justificable. Si la atribución
es ambigua, D02 no autoriza a elegir una producción arbitrariamente: se conserva
el pendiente y su cobertura. Se mantienen los segmentos originales separados;
la capacidad total se rige por D03 y los solapes dentro de categoría por D14.
El caso entre categorías distintas queda descartado según el alcance de D14.

**Llegada del cierre:** una nueva publicación incorpora `FECHA_OFF` aceptado y
recalcula los hechos/agregados afectados. Para cada ventana solo computa el
intervalo que le corresponde, sin sumar el provisional anterior y el definitivo.
Un cierre recibido tarde puede reducir minutos calculados provisionalmente;
se registra la revisión y se invalidan las cachés dependientes. Una consulta
fijada a una publicación anterior conserva su reproducibilidad.

**Compatibilidad analítica:** sustituir duración cero por duración provisional
es una diferencia semántica aprobada para tiempo real. Se identificará mediante
una versión de política de A/R abiertos, a concretar en E02. No se ocultará como
una equivalencia exacta de la antigua ruta de eventos abiertos. Los eventos
cerrados conservarán el cálculo de referencia y se contrastarán con el corpus
histórico; cualquier reinterpretación retrospectiva adicional requiere acuerdo.

**Pruebas exigidas en E06/E09/E10:**

- Inicio 10:15, sin cierre y corte 11:00: 45 minutos para esa cavidad.
- Consulta de una subventana: recorte por ambos límites, sin contar fuera de ella.
- Evento posterior al corte: sin minutos anteriores a su inicio.
- Cierre recibido antes o después del corte: usar el límite aplicable, sin duplicar.
- Cierre tardío que corrige un provisional: actualizar solo los ámbitos afectados
  y conservar la publicación anterior reproducible.
- Segmentos y eventos sin modificación de su identidad ni fechas originales.
- Atribución ambigua o fuente atrasada: no presentar cobertura completa inventada.
- Repetir una consulta con la misma publicación y el mismo corte: mismo resultado.

Las decisiones ya confirmadas de ingesta se conservan: interpretación ZF de los
tipos A/R 5 y 6 cuando no exista indicación contraria, conservación de fechas
fuente, atribución a saliente en las transiciones acordadas, rueda compartida y
continuidad de A/R durante el cambio con evidencia física como salvaguarda. No
existe una tolerancia especial de dos segundos. Si esas decisiones difieren de
un supuesto del histórico, se documentará la diferencia; no se ocultará bajo
la prueba de equivalencia.

#### D03. Capacidad asignada: parámetros de producción y gotas

- **Pregunta:** Q03, limitada a la cantidad de cavidades y su conservación ante
  bloqueos temporales. Los solapes de A/R quedan separados en Q03-S.
- **Estado:** aprobado por el usuario el 29/10/2026; contratos, incorporación de
  los campos necesarios a la ingesta y cálculo Gold pendientes de implementación.
- **Fuente acordada:** `MP9_PARAM_MODELO.NUM_SECCIONES`, enlazado con el registro
  de producción mediante `ID_MP9_CAMBIO_MODELO`, complementado con la configuración
  de gotas de la máquina correspondiente. Respetar las vigencias y la identidad
  de cada fabricación; no elegir el último parámetro de toda la línea.
- **Gotas:** obtener `ID_NUM_GOTAS_SIMUL` de `SIP_MAQUINA_MOLDEO` y resolver su
  cantidad mediante `SIP_NUMERO_GOTAS_SIMULTANEAS.VALOR`. Aunque en la muestra los
  IDs coinciden con el número de gotas, no convertir esa coincidencia en una regla.

Para cada producción y tramo de vigencia confirmado:

```text
cavidades_asignadas = secciones_habilitadas_de_la_producción × número_de_gotas

minutos_cavidad_totales = SUMA(cavidades_asignadas_del_tramo × minutos_del_tramo)
```

Los tramos corresponden a cambios de asignación/configuración acreditados, no
a interrupciones deducidas por falta de muestras. Los límites temporales del
análisis incorporan la frontera operativa contrastada de D10 y el corte de D04.
Si la capacidad
no cambia en la ventana, basta multiplicarla por los minutos correspondientes.

**Bloqueos temporales:** el usuario confirma como criterio operativo que una
cavidad trabada o temporalmente bloqueada tendrá un aviso o rechazo asociado y
debe seguir computándose. No reducir el denominador porque deje de aparecer en
MIA o porque tenga un A/R. Computar el tiempo afectado del A/R, incluidos los
eventos abiertos conforme a D02, conservando la cavidad en la capacidad asignada.
Una parada temporal no equivale a una retirada de la producción. Si falta la
evidencia A/R esperada, no inventar un evento ni una duración: tratar su cobertura
según D05, sin excluir por ello la cavidad del denominador.

**Máquinas tándem:** utilizar la máquina física y la producción confirmadas.
Dos fabricaciones concurrentes del mismo modelo no comparten automáticamente
la capacidad: cada una conserva sus parámetros y su configuración. No asignar
la capacidad total de ambas partes a cada fabricación.

**Evidencia revisada:** en AV22, el registro de parámetros 730, enlazado al cambio
1302, declara ocho secciones para `1961/490`, mientras que la configuración de la
máquina declara diez. Con triple gota, la capacidad deducida de los parámetros
es 24 cavidades, frente a 30 si se utilizara toda la configuración de máquina.
La comprobación de las tablas de secciones respalda las ocho secciones asignadas.
Es una evidencia del criterio elegido, no una constante a programar por línea.

**Disponibilidad y alcance de fuentes:** los campos enriquecidos de capacidad
de los Excel históricos no están presentes en el CSV bruto de `LISTA_RECH`.
El contrato actual de `SIP_MAQUINA_MOLDEO` tampoco publica secciones ni gotas;
`MP9_PARAM_MODELO` y el catálogo de gotas permanecen fuera de la ingesta vigente.
Esta decisión requiere planificar su incorporación mínima y versionada y ajustar
el contrato de ingesta en una entrega específica antes de utilizarlos en Gold.
No acredita que esas tablas ya existan en Silver ni reincorpora automáticamente
las tablas de secciones descartadas. Conservar los CSV originales y evitar
duplicar configuración por ramal o por máquina de inspección.

**Compatibilidad analítica:** el método actual infiere valores distintos de
`LISTA_DEFECTOS_CAVIDAD_TOTAL` y puede contar `-` y `99Z` o perder una cavidad
asignada que no aparece en MIA. En la validación, AV23_PROD_001 devuelve 26 con
ese método frente a 24 declaradas en el histórico de inspeccionadas.
Sustituir esa inferencia puede modificar AQL/DAR y el score total: registrar una
versión semántica y diferencias justificadas frente a `legacy_analytical_v1`,
manteniendo la referencia anterior reproducible. No recalcular ni sobrescribir
publicaciones históricas de forma implícita.

**Pruebas exigidas en E02/E06/E09:**

- Parámetros de producción distintos de la capacidad de máquina: utilizar los
  primeros y resolver el valor de gotas por catálogo.
- Cavidad asignada que no aparece en MIA: mantener su capacidad.
- Cavidad temporalmente bloqueada con A/R: conservar el denominador y computar
  el tiempo afectado; el cierre tardío respeta D02.
- Marcadores `-`, `99Z` o cavidad desconocida: no contarlos como cavidades físicas
  adicionales ni usarlos para inferir la cantidad asignada.
- Producciones concurrentes, parámetros repetidos, vigencias y cambios de
  configuración: conservar identidades, evitar duplicados y no cruzar capacidades.
- Parámetros ausentes, contradictorios o sin vigencia demostrable: no inventar
  una cantidad, usar cero ni recurrir silenciosamente a la inferencia histórica.
  Registrar internamente el componente no calculable y no publicar un nuevo
  resultado incompleto, conforme a D05.

#### D04. Tiempo real: siete horas completas y un punto parcial

- **Pregunta:** Q04.
- **Estado:** aprobado por el usuario el 29/10/2026; preparación Gold, API y
  actualización de pantalla pendientes de implementación y validación.
- **Representación acordada:** siete puntos anteriores de horas naturales
  completas y un octavo punto de la hora parcial hasta el corte efectivo de datos.
  La ventana avanza por horas, independientemente de los cambios de turno.
- **Desfase:** mantener una hora respecto al reloj. Ese límite no demuestra que
  todas las fuentes dispongan de datos suficientes; el corte efectivo publicado
  y la cobertura deben ser explícitos. El tratamiento de faltantes sigue D05.

**Ejemplo de interpretación:** a las 12:35, si existen datos validados suficientes
hasta las 11:35 respetando el desfase, el punto parcial representa 11:00–11:35.
Los siete anteriores corresponden a 04:00–05:00, 05:00–06:00, 06:00–07:00,
07:00–08:00, 08:00–09:00, 09:00–10:00 y 10:00–11:00. Es un ejemplo del contrato,
no una garantía de disponibilidad de datos hasta las 11:35 en cada ejecución.

**Cálculo del punto parcial:** utilizar los datos aceptados y la duración efectiva
del intervalo, con los denominadores y reglas aprobados para cada componente.
No extrapolar artificialmente el resultado a sesenta minutos ni sumar scores
parciales para construir el score de la hora. Identificar el punto como provisional
y mostrar su intervalo/corte real. Los A/R abiertos siguen D02 hasta ese corte.

**Frecuencia de cálculo:** actualizar el punto cuando una publicación Gold nueva
incorpore datos validados que afecten a la producción. La cadencia esperada es
aproximadamente cada diez minutos, acorde con la llegada Fenix; no es una promesa
de publicación a intervalos exactos ni una obligación de reescribir si no cambian
los resultados. Una corrección aceptada puede revisar también horas anteriores.

**Frecuencia de pantalla:** comprobar nuevas versiones cada **30 segundos** mediante
una consulta ligera de versión/publicación. Si hay novedades, cargar una versión
coherente del score y sus contribuciones para la producción. Esa comprobación no
ejecuta la ingesta, no relee todo Silver ni recalcula el score cada 30 segundos.
Mantener cadencias y desfase en configuración, con los valores aquí acordados.

**Sin novedades:** conservar el último valor y el corte hasta el que fue calculado.
No mover su corte, aparentar frescura ni aumentar minutos de A/R únicamente por
actualizar el navegador. Diferenciar la comprobación de versión de la fecha de
datos y del cálculo publicado; aplicar D05 si falta cobertura para el nuevo corte.

**Cambio de hora:** cuando la publicación acredite el cierre de la hora parcial,
esta pasa a ser una de las siete horas completas y se inicia el nuevo punto
parcial con datos disponibles. Sale la hora completa más antigua. No fabricar
un score cero para una hora nueva sin observaciones ni presentar un punto parcial
como definitivo. La ausencia de datos se trata conforme a D05.

**Pruebas exigidas en E07/E09/E11:**

- Siete horas completas y una parcial, con intervalos, orden y corte correctos.
- Cálculo parcial con exposición efectiva, sin extrapolación a una hora entera.
- Nuevas publicaciones de la producción: actualización del score y su desglose
  desde la misma versión, sin mezclar generaciones.
- Comprobaciones repetidas cada 30 segundos sin novedades: mismo valor y corte,
  sin escrituras ni recálculos analíticos innecesarios.
- Paso de parcial a completa y avance de la ventana sin duplicar puntos.
- Retrasos, horas sin observaciones y reinicio de pantalla: conservar la última
  publicación completa acreditada y aplicar D05.
- Correcciones tardías y cambios de día/turno: reconstruir los intervalos afectados
  sin alterar las identidades temporales ni el contrato aprobado para Fenix.

#### D05. Publicar únicamente el score completo y su desglose coherente

- **Pregunta:** Q05, en su criterio de publicación y presentación.
- **Estado:** aprobado por el usuario el 29/10/2026; implementación y pruebas
  pendientes. No se ha modificado el motor analítico ni desplegado código.
- **Criterio operativo:** los tres bloques de datos (MIA, A/R y ZC), incluidas
  todas las tablas necesarias dentro de cada bloque, pueden llegar
  desacompasadas dentro de un margen habitual de uno o pocos minutos. Se acepta
  esperar ese tiempo adicional para mostrar un resultado completo y correcto.
  Esta expectativa no constituye una garantía técnica ni un timeout tras el cual
  se permita publicar datos incompletos.

**Preparación interna:** calcular cada componente únicamente cuando disponga de
todos sus datos necesarios. Los componentes ya calculables pueden prepararse y
validarse en segundo plano mientras llegan los restantes. Mantener esos resultados
como candidatos internos; no mostrarlos aisladamente como actualización del panel.

**Condición de publicación:** todas las fuentes, atribuciones, referencias y
denominadores requeridos deben estar validados para la misma producción y ventana,
con un corte común compatible con D04. Todos los cálculos necesarios deben haber
terminado y sus resultados deben ser conciliables. Entonces publicar juntos el
score total, sus contribuciones y el detalle correspondiente mediante una única
versión visible. No mezclar componentes nuevos con otros antiguos para completar
aparentemente un total.

**Cobertura comprobable:** la recepción de un archivo o una marca máxima de fecha
no demuestra por sí sola que esté disponible todo el intervalo. Definir en E02/E03
la evidencia de lectura, cobertura y aceptación necesaria por fuente. Tampoco
exigir que todas las fuentes tengan filas nuevas o timestamps idénticos: una fuente
sin eventos nuevos puede estar correctamente revisada y cubierta. La ausencia
acreditada de eventos se distingue de una fuente ausente o todavía no comprobada.

**Mientras se espera:** conservar en pantalla el último resultado completo,
incluido su desglose, publicación y corte reales. Si todavía no existe ninguno,
mostrar un estado de espera de datos completos, sin número de score inventado ni
contribuciones intermedias. Registrar el motivo del bloqueo y su antigüedad para
supervisión; si la espera excede el margen operativo habitual, señalar el retraso
sin relajar la condición de publicación. El umbral operativo se fijará como
configuración, sin introducir un plazo arbitrario aprobado implícitamente.

**Prohibiciones explícitas:** no publicar un total parcial, tratar la información
ausente como cero, redistribuir los pesos entre los componentes disponibles ni
mostrar un componente nuevo mientras el total y el resto del desglose pertenecen
a otra generación. D15 descarta el respaldo histórico de inspeccionadas desde
`LISTA_DEFECTOS` señalado en D01; no activarlo si falta el denominador de `LISTA_RECH`.

**Compatibilidad con D04:** el octavo punto puede cubrir una fracción de hora y
seguir siendo publicable: todas las fuentes y todos los cálculos deben estar
completos para esa fracción. «Provisional» describe que la hora continúa abierta,
no que se haya omitido un componente o una fuente necesaria.

**Pruebas exigidas en E07/E08/E09/E11:**

- Llegadas desacompasadas: preparar internamente y publicar solo al completar
  los datos y cálculos necesarios del mismo intervalo.
- Mientras falta una fuente: conservar íntegra la última publicación visible.
- Primera carga sin resultado completo: estado de espera, sin score parcial.
- Fuente revisada sin eventos nuevos: reconocer cobertura acreditada sin exigir
  filas artificiales ni interpretar una fuente ausente como cero eventos.
- Fallo, reinicio o espera prolongada: conservar el resultado completo anterior,
  motivo pendiente y capacidad de reanudación, sin forzar una publicación incompleta.
- Último punto de hora parcial: publicar todos sus componentes para el mismo corte
  y sustituirlo de forma coherente al llegar una nueva versión completa.

#### D06. Referencia MIA: incorporar producciones terminadas y actualizar las abiertas

- **Pregunta:** Q06, en su criterio de incorporación, actualización y
  comparabilidad inicial.
- **Estado:** aprobado por el usuario el 29/10/2026; implementación y validación
  pendientes. No se ha modificado código ni la configuración analítica vigente.
- **Regla de entrada:** una producción no aporta datos al histórico de referencia
  mientras siga abierta. Ni sus muestras parciales ni su antigüedad justifican
  incorporarla antes del cierre. El cierre se acredita mediante el catálogo y las
  reglas industriales acordadas; no basta con que deje de emitir datos.
- **Regla de actualización:** una producción terminada, validada y elegible puede
  incorporarse a la referencia utilizada por otras fabricaciones todavía abiertas,
  respetando la prioridad de modelo sobre segmento acordada a continuación.
  No mantener congelada la referencia hasta que termine la fabricación receptora.

**Caso operativo aprobado:** A lleva meses fabricándose. B, en otra línea, fabrica
la misma botella o una botella del mismo segmento durante unas semanas. Mientras
B siga abierta, sus datos no alteran la referencia de A. Cuando B termina y sus
resultados quedan validados, se incorpora si es elegible bajo la prioridad siguiente:
si A tiene histórico propio, otra botella del mismo segmento no lo amplía; B debe
ser del mismo modelo. Si no existe histórico propio, B puede formar parte de la
referencia de segmento. Los nuevos cálculos de A usan esa referencia actualizada.
Las mejoras o decisiones durante B influyen a través de sus resultados medidos;
no se introduce una ponderación adicional por decisiones o intervenciones.

**Prioridad de población aprobada por el usuario:**

1. Si existe histórico de fabricaciones terminadas y validadas del mismo modelo,
   la comparativa MIA se realiza exclusivamente contra ese modelo, aunque haya
   otras botellas de su segmento disponibles. No mezclar ambas poblaciones.
2. Solo cuando no exista histórico de ese modelo, utilizar fabricaciones
   terminadas y validadas de su segmento acreditado, manteniendo separados Vino
   Ligero y Vino premium. Si tampoco hay referencia de segmento utilizable,
   registrar la insuficiencia y aplicar D05; no incorporar fabricaciones abiertas.
3. Cuando termine la primera fabricación válida del modelo, los siguientes cálculos
   de producciones abiertas de ese modelo deben reevaluar la selección de referencia
   y su versión conforme a esta prioridad, sin incluir la fabricación consultada
   mientras siga abierta ni perder la publicación anterior.

La regla «no existe histórico» no equivale automáticamente a «hay menos muestras
que el mínimo estadístico»: no ampliar al segmento un histórico propio existente
solo para alcanzar ese mínimo sin otro acuerdo. La suficiencia de muestras sigue
el contrato/configuración analítica y, si no permite calcular, D05. Este criterio
de población no cambia el tratamiento D15 del denominador de inspeccionadas.
Compartir planta, tener aspecto parecido, usar una descripción similar o coincidir
en boca/sufijo no demuestra compartir segmento. No introducir ponderaciones nuevas.
El catálogo de segmento sigue siendo necesario para definir DAR aun cuando MIA
utilice únicamente el histórico del mismo modelo.

**Disponibilidad de la clasificación y catálogo aprobado en D06.1:** `SIP_MODELO` permite
resolver el código de botella, pero el contrato Silver actual no incorpora su
segmento. La aplicación admite `segment_id`; los perfiles DAR existentes definen
segmentos, pero no constituyen un catálogo general de asignación modelo–segmento.
El perfil DAR por defecto no acredita la clasificación de una botella desconocida.

La revisión local de `Datos/Insatis MARBLE 25.xlsx`, hoja `Listado`, identifica
`Segmento`, `Cuerpo` y `Boca`: hay 204 modelos con asociación a un único segmento
dentro del informe. Ejemplos: `4835/242` y `1358/242` figuran en `Vinos (B01)`;
`1961/490`, en `Aceites (B07)`. Es una fuente candidata parcial, ligada a un informe
de reclamaciones, no un catálogo maestro exhaustivo ni una clasificación ya
incorporada al sistema. Su categoría `Vinos` no separa por sí sola `Vino Ligero`
y `Vino premium`, que sí distingue la configuración de Calidad.

La ampliación por segmento utilizará el catálogo obligatorio de D06.1 y solo sus
correspondencias aceptadas. Si el segmento es desconocido, no asociar modelos
diferentes por suposición; la correspondencia inequívoca del mismo modelo sigue
siendo comprobable, sin resolver por ello otros requisitos del score o de DAR.

**Publicación y trazabilidad:** cada actualización identifica las producciones
incluidas, sus versiones de datos y la versión de referencia resultante. Recalcular
MIA y el total de las producciones abiertas afectadas y publicar el resultado
completo conforme a D05. Un cambio del score motivado por la referencia debe ser
explicable aunque no cambien los contadores propios de la fabricación consultada.
No sobrescribir las publicaciones anteriores: conservar su referencia y resultado
reproducibles. D06 no aprueba un recálculo retrospectivo general de todos los
scores de producciones cerradas ni su sustitución silenciosa en el histórico.

**Compatibilidad:** la configuración actual utiliza `all_available` y no excluye
expresamente la producción seleccionada (`exclude_current_production: false`).
La nueva ruta debe aplicar el criterio de producciones terminadas explícitamente,
sin suponer que esos parámetros ya lo garantizan. Registrar el cambio de población
de referencia frente al contrato legado y explicar sus diferencias numéricas.

**Pruebas exigidas en E04/E07/E09/E11:**

- Una fabricación abierta no altera la referencia, aunque reciba muestras nuevas.
- B termina y es elegible: actualizar la referencia de A aunque A siga abierta.
- B termina pero no cumple comparabilidad: no incorporarla a A.
- Histórico propio existente: excluir otras botellas del mismo segmento; histórico
  propio ausente: utilizar solo el segmento confirmado y registrar la población.
- Primera fabricación propia cerrada: reevaluar la referencia del resto de
  fabricaciones abiertas del modelo, sin mezclar modelo y segmento.
- Cierre y procesamiento repetidos: no duplicar una fabricación en la población.
- Una nueva referencia actualiza de forma coherente MIA y total; una consulta
  fijada a la versión anterior conserva el resultado y referencia originales.
- Ausencia de datos suficientes, cierre contradictorio o validación pendiente:
  no dar por incorporada la producción ni eludir D05.

#### D06.1. Catálogo obligatorio de conversión modelo–segmento

- **Preguntas:** completa el criterio de comparabilidad de Q06 y resuelve el
  requisito de catálogo y la fuente inicial de segmentos de Q08. La asignación
  de perfiles y características de esta última pregunta se completa mediante D08.
- **Estado:** requisito aprobado el 29/10/2026. **P01 — PENDIENTE DE CALIDAD**:
  el usuario confirma el 02/11/2026 que ha solicitado la correspondencia y está
  pendiente de recibirla. Se mantiene el requisito de definir y validar la relación inequívoca
  entre modelo de botella, valores de `MARBLE.Segmento` y segmentos de Calidad
  del PowerPoint. Catálogo aún no creado ni poblado; esta anotación no modifica
  código ni datos actuales.
- **Finalidad:** identificar de manera inequívoca el segmento de Calidad de cada
  modelo y habilitar la comparación entre fabricaciones cerradas del mismo
  segmento. Es una referencia de producto, compartida entre líneas; no una
  clasificación inferida por máquina, turno, incidencia o semejanza del código.
- **Granularidad aprobada:** Vino Ligero y Vino premium son segmentos distintos.
  La categoría comercial genérica «Vinos» no basta para asignar uno de ellos ni
  para mezclar sus poblaciones de referencia como si fueran un solo segmento.
  Mantener la taxonomía de Calidad en configuración versionada, sin reglas
  programadas por modelo ni equivalencias comerciales implícitas.

**Pendiente P01: correspondencia entre las tres referencias.** Preparar un
diccionario versionado que relacione `codigo_modelo_botella`, valor original de
`MARBLE.Segmento` cuando exista y segmento de Calidad del PowerPoint, con fuente
y validación de la asociación. No basta una traducción global de etiquetas:
`Vinos (B01)` puede corresponder a Vino Ligero o Vino premium, de modo que la
distinción deberá quedar resuelta por modelo mediante información autorizada.
Conservar la etiqueta original y una única asignación de Calidad aceptada por
modelo y versión, sin convertir la falta de MARBLE de un modelo en una
clasificación inventada. Concretar la fuente maestra, responsables de validación
y forma de actualización antes de dar por disponible esta correspondencia.

El usuario aplaza este punto para continuar con la integración técnica. Pueden
avanzar contratos, lectura Silver, hechos, publicación y adaptación de consulta,
y probarse scores completos para modelos cuya clasificación y perfil ya estén
expresamente validados. El aplazamiento no autoriza un perfil por defecto, un
DAR cero por desconocimiento ni un score total parcial para los demás modelos;
en esos casos sigue vigente D05.

**Contenido y contrato:** el catálogo publicado tendrá como relación mínima
`codigo_modelo_botella` → `segment_id`. Conservar el código de botella como texto
canónico y el segmento como referencia a una entrada existente de la taxonomía.
La misma versión no puede contener dos segmentos aceptados para el mismo modelo.
Su publicación identificará versión, fecha y procedencia; la evidencia de cada
asignación permitirá recuperar archivo/hoja/filas de origen, etiqueta original,
regla de conversión aplicada y aprobación. Fijar nombres/tipos definitivos en E02,
sin replicar todos esos justificantes en cada observación industrial.

**Fuente inicial autorizada:** utilizar, cuando aporte información suficiente,
`Datos/Insatis MARBLE 25.xlsx`, hoja `Listado`, con `Cuerpo`, `Boca` y `Segmento`.
Reconstruir y contrastar el código de modelo con `SIP_MODELO`; no relacionar por
nombre comercial aproximado ni interpretar los códigos de segmento como IDs de
modelo Fenix. El informe revisado aporta 204 modelos con asociación única dentro
del archivo, pero no constituye un maestro exhaustivo de toda la planta.

**Preparación y validación de la carga inicial:**

1. Extraer únicamente los campos necesarios y las referencias de evidencia,
   preservando el archivo original; no incorporar datos de clientes o reclamaciones
   que no sean necesarios para este catálogo.
2. Consolidar las repeticiones del mismo modelo–segmento sin asignar peso al
   número de reclamaciones ni duplicar filas. Si hay etiquetas incompatibles,
   conservar los candidatos y su evidencia para validación por Calidad.
3. Resolver las etiquetas comerciales mediante equivalencias explícitas y
   versionadas a segmentos de Calidad. Aceptar solo asociaciones inequívocas y
   validadas; «Vinos (B01)» queda pendiente de precisar como ligero o premium
   mientras no exista otra fuente autorizada o validación explícita de Calidad.
4. Completar los modelos ausentes y las clasificaciones demasiado generales
   mediante un maestro de producto disponible o aportaciones validadas de Calidad.
   No inferir el segmento a partir de peso, boca, numeración, color o clasificación
   comercial sin un criterio expresamente aprobado.
5. Publicar una versión validada del catálogo; las actualizaciones sin novedades
   no duplican correspondencias. Una reasignación contradictoria requiere revisión
   y una nueva versión, conservando la clasificación anterior y su justificación.

**Integración:** cruzar producción → código de modelo Fenix resuelto por
`SIP_MODELO` → segmento aceptado. Publicar en Gold el segmento y la versión de
catálogo utilizada. No cambiar los CSV originales ni incorporar todo el informe
MARBLE a la ingesta Fenix. El catálogo se administra como referencia de producto
y se carga al preparar datos de servicio. No crear un perfil DAR por defecto para
suplir un segmento desconocido. Si un cálculo requiere esa clasificación y no
dispone de ella, aplicar D05, sin fabricar una clasificación o un score completo.

**Pruebas exigidas en E02/E04/E09:** dos modelos de Vino Ligero pueden compartir
segmento; Vino Ligero y Vino premium no lo comparten. Las repeticiones de MARBLE
no duplican modelos, las etiquetas genéricas permanecen pendientes y un modelo
desconocido no recibe el segmento del perfil por defecto. Un cambio de catálogo
invalida solo los resultados/referencias afectados y preserva la reproducibilidad
de publicaciones anteriores.

**Comprobación de campos de segmento y cierre (29/10/2026):** se revisaron las
columnas de las fuentes locales disponibles y el catálogo usado por la app.
`Insatis MARBLE 25.xlsx`, hoja `Listado`, sí tiene `Segmento`, pero sus 283 filas
de vino figuran como `Vinos (B01)`. `Clasificación Modelo` contiene
`S - Estándar`, `P - Personalizado` y `E - Exclusivo`: esa P no significa premium
y esa columna no acredita Vino Ligero frente a Vino premium. `Boca` contiene
códigos como 242, 477 o 610; necesita un diccionario acreditado de boca/tipo de
cierre para resolver corcho o rosca, sin inferirlo de la numeración.

En los CSV `SIP_MODELO` de la instantánea local revisada se conservan ID,
denominación/código de botella y estado; no se encontró un campo explícito de
segmento ni cierre. El modelo de la aplicación admite `segment_id` y
`dar_profile_id`, y `config/segment_dar_profiles.json` define perfiles de Vino
Ligero y Vino premium, pero esas capacidades no constituyen una asignación
modelo–segmento ni la prueba de que esté disponible en las exportaciones Fenix.
El manifiesto histórico examinado tampoco contiene esos campos de clasificación.
Esta conclusión se limita a las fuentes examinadas: no afirma que un maestro
corporativo de producto carezca de ellos. Preferir su campo explícito y el
diccionario de bocas si están disponibles; no pedir clasificaciones manuales
que ya puedan obtenerse de una fuente autorizada.

**Comprobación posterior en servidor, 29/10/2026 a las 13:29 Europe/Madrid:**
la [ejecución de inventario de solo lectura](https://github.com/ikervillen/test-runner-deploy/actions/runs/40873358728)
examinó el directorio de entrada Fenix y sus subdirectorios: 58.297 archivos,
todos CSV, sin errores de inventario, sin archivos MARBLE ni hojas Excel y
sin fuentes cuyo nombre identificase segmento o clasificación ligero/premium.
Además se leyeron las tres versiones más recientes disponibles de `SIP_MODELO`
y `SIP_DEFECTO` por línea: 30 archivos, estables durante la lectura. Todos los
registros de `SIP_MODELO` examinados tienen tres campos y no aportan una
clasificación explícita de segmento/cierre. En AV21/AV22/AV23 hay versiones
del 29/10; en AV31/AV32 las últimas disponibles siguen siendo del 19/10.

Por tanto, MARBLE es una referencia externa disponible localmente, no una fuente
que esté llegando en ese directorio de exportaciones Fenix en la comprobación
realizada. Su catálogo derivado requiere una carga o una fuente de referencia
independiente y versionada, sin asumir que Fenix lo actualizará. No se ha
inspeccionado todo el disco del servidor ni se descarta otro maestro corporativo.

#### D07. Equivalencias y criticidades: versiones con entrada en vigor

- **Pregunta:** Q07.
- **Estado:** aprobado por el usuario el 29/10/2026; implementación y validación
  pendientes. Esta decisión no modifica ahora ningún diccionario ni score.
- **Regla acordada:** cada cambio aceptado de criticidad o equivalencia se
  publica como una nueva versión con fecha de entrada en vigor. Se aplica a los
  nuevos cálculos de producciones abiertas y a las nuevas producciones, sin
  esperar al cierre de las fabricaciones que ya estén en marcha.

**Ejemplo de negocio:** un defecto pasa de Menor a Mayor. Los nuevos cálculos de
una fabricación abierta utilizan el criterio vigente y pueden cambiar su
contribución y score total. Los resultados que ya se habían publicado conservan
el criterio y los valores con los que se obtuvieron.

**Referencias coherentes:** fijar las versiones efectivas de diccionarios,
equivalencias y criticidades al iniciar cada cálculo y registrarlas en su
publicación. No mezclar versiones de reglas entre total, contribuciones y detalle
ni reemplazar reglas durante una consulta. Publicar el resultado completo según
D05 e invalidar únicamente las cachés y derivados dependientes de la versión
modificada. Una consulta fijada a una publicación anterior seguirá siendo
reproducible incluso cuando exista una versión nueva de las reglas.

**Histórico publicado:** no recalcular automáticamente los resultados de
producciones cerradas ni sustituir silenciosamente resultados anteriores de
producciones abiertas. Si se solicita reinterpretar el pasado, ejecutar una acción
explícita, acotada y trazable que identifique ámbito, reglas anteriores/nuevas y
resultado de la comparación. Publicar una versión nueva y conservar ambas;
no alterar los registros industriales de origen para representar el cambio.

**Equivalencias e identidad:** conservar código original, fuente y referencia al
registro aceptado. No confundir ID interno Fenix con código de defecto ni aplicar
dos veces una conversión de legado a MDL. Una equivalencia nueva no supone que
todos los códigos deban convertirse ni que el mismo número signifique lo mismo
en todos los catálogos. Registrar versión y procedencia de cada normalización.

**Pruebas exigidas en E02/E04/E07/E09:** entrada en vigor antes/después del corte
de cálculo; cambio de criticidad en fabricación abierta; conservación del score
publicado con reglas antiguas; código ya normalizado sin segunda conversión;
publicación completa con referencias coherentes; recálculo explícito de un ámbito
histórico sin perder su versión anterior; ausencia de cambios sin escrituras o
invalidaciones innecesarias.

#### D08. Características por segmento, perfiles DAR y condiciones de aplicabilidad

- **Pregunta:** Q08, complementada por el catálogo modelo–segmento de D06.1.
- **Estado:** aprobado por el usuario el 29/10/2026; implementación y validación
  de las correspondencias concretas pendientes. No se han modificado perfiles,
  umbrales ni código de cálculo.
- **Referencia funcional:** `C:\Users\iker.villen\Desktop\AI_tool\Datos\Salud Serie límites.pptx`,
  diapositiva 1, fechada en el documento como diciembre de 2023. Conservar la
  identificación/hash de la fuente junto con la versión derivada del catálogo.

**Contenido examinado:**

| Segmento | Características específicas indicadas | Orígenes indicados |
| --- | --- | --- |
| Aceite | Capacidad; espesores ZCS. | SIPQ; MIA. |
| Cerveza | Presión interna; G+I. | SIPQ; SIP. |
| Alimentación y conservas | Boca malformada; choque térmico; fisuras de hombro. | AV&RECH; MIA. |
| Licores | Claros. | AV&RECH. |
| Vino Ligero | Ovalización; verticalidad; rechupe para corcho; line over para rosca. | AV&RECH. |
| Vino premium | Ovalización; verticalidad; rechupe interior a 45 mm. | AV&RECH. |
| Espumosos y cavas | Presión interna; interior de boca a 17 mm; G+I. | SIPQ; AV&RECH; SIP. |

**Estructura acordada:** definir perfiles comunes por segmento, con reglas
explícitas de aplicabilidad cuando lo requiera la característica. Para Vino
Ligero, el documento distingue «Corcho: Rechupe» y «Rosca: Line over». Resolver
el tipo de cierre desde un atributo de producto acreditado; no deducirlo por
aproximación al nombre ni por el segmento. No es necesario crear inicialmente
un perfil independiente para cada modelo: el modelo aporta segmento y atributos
de aplicabilidad, mientras que el perfil define las características comunes.

**Características y DAR separados:** el catálogo recoge las características del
segmento y sus fuentes. El componente DAR solo consume las que correspondan a
su definición y origen de datos. Una característica MIA no se transforma en un
evento A/R; una medición SIP/SIPQ no se inventa a partir de los CSV de inspección.
Fuentes aún no disponibles quedan identificadas como tales para sus métricas,
sin dar por implementados esos análisis ni incluirlas silenciosamente en DAR.
Fijar en E02 el contrato de cada característica, su fuente y su pertenencia al
componente que corresponda.

**Correspondencias de códigos:** traducir los conceptos del documento a códigos
concretos mediante un catálogo explícito y validado: identificador de característica,
segmento/perfil, condición de aplicabilidad, fuente, catálogo/espacio de códigos,
códigos incluidos y versión/vigencia. Un mismo texto aproximado no acredita una
equivalencia entre Fenix, MIA y MDL. Conservar el código original y aplicar D07
para evitar doble normalización. Los perfiles actuales de Vino Ligero y Vino
premium son un punto de partida técnico que debe conciliarse con estas reglas;
su existencia no demuestra por sí sola la validación de todos los cruces.

**Límites y fórmula:** la presentación separa límites de proceso, laboratorio y
línea; no aporta una fórmula de score DAR ni códigos de defecto. Preservar la
unidad y el contexto de cada límite. El 8 % de proceso citado para características
de vino no sustituye automáticamente los umbrales DAR/AQL actuales. Esta decisión
aprueba la referencia funcional y la estructura de perfiles, no una modificación
de pesos, fórmulas o umbrales. Cualquier cambio numérico adicional requiere un
acuerdo explícito y versionado, con comparación frente al cálculo de referencia.

**Asignación y ausencias:** resolver producción → modelo → segmento aceptado →
perfil y condiciones aplicables. Si falta segmento, perfil, equivalencia o atributo
de cierre necesario para un componente requerido, registrar el motivo y aplicar
D05. No convertir una ausencia en cero ni aplicar `vino_ligero_v1` u otro perfil
por defecto a una botella sin clasificación acreditada. Diferenciar ausencia de
datos de una característica demostrablemente no aplicable, sin introducir una
regla implícita de exclusión o redistribución de pesos.

**Versiones y validación:** publicar perfiles, equivalencias y aplicabilidad como
referencias versionadas con vigencia, procedencia y aprobación. Los cambios siguen
D07; cada resultado fija las versiones usadas y conserva la publicación anterior.

**Pruebas exigidas en E02/E04/E06/E09:** separación de vino ligero/premium;
reglas corcho/rosca con cierre acreditado; atributo de cierre desconocido sin
aplicación por suposición; correspondencias de códigos validadas por fuente;
ausencia de doble normalización; conservación de límites y fórmulas actuales;
fuente SIP/SIPQ no disponible sin datos ficticios; compatibilidad segmento–perfil;
publicación completa conforme a D05 y reproducción con versiones anteriores.

#### D08.1. Códigos DAR comprobados y conservación de parámetros actuales

**Instrucción del usuario (29/10/2026):** comprobar las correspondencias reales
y conservar las fórmulas, umbrales y ponderación DAR actuales. Las futuras
especificidades por botella o segmento se ajustarán de forma explícita y
versionada; esta revisión no cambia el motor ni despliega código.

Se contrastaron `config/segment_dar_profiles.json`, `config/customer_score.json`,
`Programación defectos MDL_enviado V01.xlsx`, `Tabla defectos con criticidades.xlsx`
y los cinco `20261014_SIP_DEFECTO.csv` de AV21, AV22, AV23, AV31 y AV32. Estas
cinco correspondencias ID/código/descripción son iguales en las cinco líneas
de la instantánea local; no se presentan como una nueva lectura del servidor.

| Grupo actual | ID interno SIP_DEFECTO | Código SIP_DEFECTO / MDL nuevo | Código legado Vidrala | Descripción | Fila de Programación MDL |
|---|---:|---:|---:|---|---:|
| Ovalización de boca | 60 | 53 | 307 | Boca ovalada | 54 |
| Ovalización de botella | 67 | 221 | 315 | Botella ovalada | 105 |
| Verticalidad | 35 | 414 | 212 | Verticalidad | 186 |
| Rechupe | 55 | 115 | 302 | Rechupe | 68 |
| Line over | 18 | 27 | 110 | Line-over en superficie de cierre | 28 |

`LISTA_AR.COD_AR` se interpreta por `SIP_DEFECTO.CODIGO`, no por su ID interno.
En particular, 414 es el código de Verticalidad; 35 es su ID interno en el
diccionario revisado. No confundir tampoco estos códigos A/R con los códigos
de defectos MIA de `COD_DEF.xlsx`, que pertenecen a otra fuente.

**Confirmación del usuario (29/10/2026):** utilizar estos códigos de
`SIP_DEFECTO` tanto para Vino Ligero como para Vino premium. La identidad del
defecto y su interpretación no cambian por el segmento. La pertenencia al perfil
DAR y sus condiciones de aplicabilidad se resuelven separadamente conforme a
D08; esta confirmación de códigos no introduce otros defectos ni cambia por
sí sola los conjuntos configurados de cada perfil. Mantener la distinción entre
`SIP_DEFECTO.CODIGO` e ID interno y la ponderación actual de D08.1.

**Verificación de esos códigos también en servidor:** la ejecución 40873358728
confirma las cinco correspondencias de la tabla en las tres últimas versiones
disponibles de `SIP_DEFECTO` de cada una de las cinco líneas (15 archivos), sin
diferencias de ID, código o descripción. La evidencia se conserva en
`output/validacion-segmentos-dar/servidor/segment-dar-evidence.json` del espacio
local de revisión; no se modificaron fuentes, Delta ni controles operativos.

**Perfiles actuales:** Vino Ligero incluye los cinco códigos 53, 221, 414, 115
y 27; Vino premium, 53, 221, 414 y 115. Conservar estos conjuntos como referencia
del funcionamiento legado. La configuración actual de Vino Ligero enumera tanto
Rechupe como Line over, pero no contiene una condición ejecutable por tipo de
cierre: la asociación corcho/rosca de D08 se validará como cambio semántico
explícito, sin fingir que ya está implementada ni deducir el cierre por el perfil.

**Parámetros a preservar:** `weights.dar = 8`, `dar.aql_percent = 0` y umbrales
progresivos de exposición `score_5 = 0.1 %` y `score_10 = 0.4 %`, habilitados.
La exposición es el porcentaje de minutos-cavidad afectados sobre los disponibles
según sus contratos. La curva actual es lineal: 0 % → score 0; 0,1 % → score 5;
0,4 % o más → score 10. La contribución DAR al total es `score_DAR × 8`, con
máximo 80 puntos en la configuración actual. El 8 es un coeficiente, no un 8 %,
ni una penalización independiente de ocho puntos por cada código. No cambiar
los pesos de las demás categorías ni sustituir estos umbrales por los límites
de proceso de la presentación.

**Adaptación necesaria para evitar doble normalización:** los catálogos Fenix
revisados ya expresan estos defectos en la convención nueva. El normalizador
histórico `ExcelAlertDefectNormalizer` interpreta su entrada como código legado;
la prueba directa muestra que aplicar otra vez esa conversión a 53, 221 o 414
los transforma, respectivamente, en 203, 35 o 131, y deja sin resolver 115 y 27.
Eso no es un cambio de fórmula: es una incompatibilidad de convención entre
entradas que el adaptador Silver→Gold debe tratar explícitamente por origen y
versión. Conservar la ruta histórica para sus fuentes antiguas; en Fenix nuevo,
resolver por el código y diccionario vigente y no volver a convertirlo como
legado. Prueba de aceptación real: los A/R de AV22 con COD_AR=414 del 14/10
deben seguir identificados como Verticalidad, no como Junta preliminar-cuello.

**Otros segmentos: identificación de candidatos, sin ampliar el score por
suposición.** En el diccionario se encuentran 220 «Claro» y 223 «Claro sonrisa»;
522 «Rompe choque térmico»; 112 «Fisura hombro» y 134 «Fisura hombro contrajunta»;
510 «Rompe presión»; 513/514 «Capacidad baja/alta»; y 240 «Espesor ZCS». Su
existencia permite preparar correspondencias, pero no demuestra que cada código
deba incluirse automáticamente en DAR. Respetar el origen SIP/SIPQ, MIA o A/R
indicado para cada característica en D08; un evento de rechazo no sustituye una
medición de presión o capacidad. Tampoco «Calibre interior entrada» 41/42 prueba
la condición concreta a 17 mm. No se localizó una equivalencia literal inequívoca
para «Boca malformada». Conservar los perfiles DAR ya acordados y su peso 8;
la extensión concreta de otros perfiles requiere correspondencias y aplicabilidad
validadas, sin transformar candidatos por parecido de texto en reglas aceptadas.

La criticidad genérica de Rechupe difiere entre las referencias examinadas:
Grave en Programación MDL y Menor en Tabla de criticidades. Esta revisión no
resuelve ni cambia esa criticidad automáticamente, ni la confunde con el peso
específico del componente DAR; preservar la referencia vigente y tratar sus
versiones conforme a D07.

#### D09. Filtro de cavidad: criterio para una posible vista de detalle

- **Pregunta:** Q09.
- **Estado:** propuesta aceptada para referencia futura por el usuario el
  29/10/2026. No se solicita implementar ahora este filtro ni añadirlo a la
  pantalla principal; su desarrollo no bloquea el alcance inicial de integración.
- **Contexto:** posible investigación de una cavidad concreta dentro de una
  producción, por ejemplo comprobar si un empeoramiento se concentra en 7F.
  La pantalla principal sigue mostrando el score general de cada producción/línea.

**Comportamiento si se habilita:** filtrar los defectos MIA y los A/R únicamente
cuando puedan atribuirse de manera comprobable a la cavidad seleccionada.
Mantener en el score filtrado el rendimiento ZC de la máquina y explicar que
esa contribución es de máquina, no una medida específica de la cavidad.
Los contadores de rueda no se reparten, duplican ni estiman por cavidad para
simular un detalle que la fuente no proporciona. Conservar también la política
acordada de rueda compartida en tándem.

**Alcance y datos:** antes de habilitar filtros de cavidad, sección, color o
máquina, verificar qué campos existen o pueden derivarse con garantías y qué
componentes admiten esa selección. No inventar metadatos ausentes ni dar a
entender que un filtro afecta a una contribución que permanece a nivel máquina.
D09 no garantiza automáticamente todos esos filtros para los nuevos datos ni
aprueba nuevas fórmulas de minutos-cavidad o ponderaciones para consultas filtradas.

**Compatibilidad:** este aplazamiento no elimina filtros ya existentes en la
aplicación ni sus contratos de referencia. Mantenerlos en la ruta histórica;
declarar y validar su compatibilidad en la nueva ruta antes de ofrecerlos como
equivalentes. No modificar el score principal de producción por este acuerdo.

**Validación si se implementa en el futuro:** cavidad con atribución fiable;
selección desconocida o no soportada sin resultados engañosos; trazabilidad de
la selección; MIA/A/R correspondientes a la cavidad y ZC de máquina identificado;
ausencia de reparto artificial de rueda y de cambios en el score general.

#### D10. Inicio de minutos-cavidad: frontera operativa ZC contrastada

- **Pregunta:** Q10, en su criterio de inicio del denominador de minutos-cavidad.
- **Estado:** criterio operativo aprobado y precisado por el usuario el
  29/10/2026; adaptación Gold e implementación/validación de evidencias pendientes.
- **Regla acordada:** utilizar la frontera temporal derivada de
  `MP9_CAMBIO_MODELO.FECHA_ON`, con `STATUS=1`, como aproximación inicial para
  el inicio operativo ZC y el denominador de minutos-cavidad. Revisarla si
  después aparecen registros inequívocamente atribuibles a la fabricación
  saliente, mediante la doble comprobación obligatoria de D10.1. No congelar
  el denominador en la primera propuesta ni presentarlo como una medición
  exacta del arranque. El tránsito anterior a las inspecciones ZF forma parte
  del denominador; no esperar a la primera muestra MIA.

**Ejemplo aprobado:** si la producción ya fabrica en ZC a las 06:00 y su primera
muestra MIA es de las 07:10, esos 70 minutos iniciales se incluyen. Con 24
cavidades asignadas constantes aportan 1.680 minutos-cavidad al denominador.
Los A/R de ese intervalo aportan sus minutos afectados según atribución, D02 y
el acuerdo D14 y su alcance confirmado en Q03-S.
La ausencia inicial de inspeccionadas ZF
no se interpreta como un componente MIA de valor cero; la publicación sigue D05.

**Preparación y fabricación separadas:** una entrada o cambio de modelo puede
preceder al comienzo efectivo mientras se preparan o ajustan las máquinas.
`MP9_CAMBIO_MODELO.FECHA_ON`, con `STATUS=1`, registra la entrada declarada del
modelo en ZC; no se convierte automáticamente en prueba de
fabricación. La aproximación operativa aprobada resuelve el cálculo cuando no
hay un sensor de arranque inequívoco: debe identificarse como aproximación y
revisarse con la evidencia admitida, sin afirmar que todo el intervalo previo
al primer rechazo fuera preparación. Una fecha física de arranque que llegue
a acreditarse se conservará separada de la declaración MP9 y de su aproximación.
No utilizar la fecha de exportación o llegada del archivo como inicio industrial.

**Evidencia y precisión por validar:** contrastar el cambio y su máquina física
con registros de producción ZC atribuibles a esa fabricación. Revisar qué
contadores de `CONT_ZC` u otra evidencia conservada permiten acreditar ese inicio.
En tándem, rueda positiva puede proceder de la otra fabricación: no demuestra
por sí sola que haya comenzado la nueva. No modificar la regla de rueda compartida
ni repartir contadores para fabricar una prueba de arranque individual.
Las muestras de diez minutos marcan el final del periodo según el contrato
acordado; no presentar su timestamp o el inicio de la ventana como un instante
exacto de arranque si la evidencia no permite esa precisión.

**Comprobación específica solicitada el 29/10/2026:** contrastar esa entrada
declarada con los registros originales disponibles en el servidor, incluyendo
`MP9_LISTA_RECH_ZC` como fuente candidata de actividad real. Esta familia está
fuera del contrato de ingesta actual: su evaluación no supone incorporarla
automáticamente a Silver ni adoptarla como nuevo denominador.

- Relacionar la máquina física y la parte tándem, la sección/cavidad, el molde
  y la vigencia temporal; contrastar la atribución al modelo con MP9 y las
  evidencias disponibles en A/R e inspecciones ZF. Un número de molde aislado
  o la actividad de la otra parte de la máquina no acreditan el arranque.
- Comparar actividad anterior y posterior a `FECHA_ON`, cambios de configuración
  molde–cavidad, el primer registro positivo atribuible y la posterior llegada
  a ZF. Registrar archivos examinados, cobertura, duplicados exactos excluidos
  del análisis y cualquier archivo que cambiase durante su lectura.
- `MP9_LISTA_RECH_ZC` contiene rechazos: un rechazo positivo acredita actividad
  observada cuando su atribución está comprobada, pero la ausencia de rechazos
  no acredita ausencia de fabricación. No convertir la primera aparición en
  la hora exacta de inicio ni confundir su fecha de exportación con la del evento.
- Documentar si la evidencia permite identificar un instante, únicamente una
  ventana temporal o deja el inicio pendiente. Acordar ese resultado antes de
  definir la incorporación de la fuente y el cálculo definitivo en Gold.

Esta comprobación conserva el criterio de fin ya acordado: última `FECHA_OFF`
válida de los registros ZF (`STATUS=2`) vinculados mediante `ID_ST1_REL` a la
fabricación correspondiente, sin darla por cerrada mientras tenga un registro
propio ZF abierto. No sustituir ese final por el cierre en ZC.

**Resultado de la primera comprobación en servidor (29/10/2026):** la
[ejecución de solo lectura](https://github.com/ikervillen/test-runner-deploy/actions/runs/40866761487)
encontró cambios de molde en una misma máquina/cavidad posteriores a la entrada
declarada en AV21, AV23, AV31A y AV32B. Por ejemplo, en AV23 la entrada del modelo
2092/242 se declara a las 06:11 del 29/10; la cavidad 2F todavía registra el molde
anterior a las 06:20 y aparece con el nuevo a las 07:00. Esto acredita la utilidad
del contraste, sin demostrar la hora exacta de arranque ni una parada de 49 minutos.
En AV22 los archivos de esta familia examinados no aportan observaciones para
contrastar su entrada reciente. La comprobación no autoriza un retraso fijo,
no convierte cambios de molde aislados en una asociación inequívoca de modelo
y deja pendiente la conciliación final de actividad atribuible y precisión.

**Persistencia y derivación:** conservar la fecha industrial de entrada ZC y
el final ZF del catálogo sin alterarlos. En la preparación Gold registrar por
separado la fecha declarada, la frontera operativa adoptada y sus revisiones,
fuente/registro de contraste, versión y precisión o incertidumbre. Si se acredita
un inicio físico, distinguirlo expresamente de la aproximación. La ausencia de
rechazos no invalida por sí sola la aproximación autorizada ni acredita una
parada. Si existen evidencias incompatibles que impiden resolver la atribución
o el denominador, registrar el pendiente y aplicar D05; no sustituir el inicio
silenciosamente por la primera MIA ni dar por probada la fecha de preparación.

**Ventanas y compatibilidad:** recortar el denominador al periodo de consulta,
al inicio operativo contrastado y al límite final aplicable, con las capacidades vigentes
de D03 y el corte de D04. Este acuerdo fija el criterio de inicio, sin reescribir
fechas fuente ni introducir descuentos de paradas temporales. La ruta histórica
usa límites de datos MIA; incorporar el tránsito previo puede cambiar AQL/DAR y
el total. Versionar ese cambio semántico, conservar el resultado legado y explicar
las diferencias en la validación, sin presentarlas como una equivalencia exacta.

**Pruebas exigidas en E03/E04/E06/E09:** fabricación ZC anterior a primera MIA;
preparación previa sin fabricación; producción tándem con rueda de otra parte;
evidencia insuficiente sin fecha inventada; precisión de ventanas de diez minutos;
capacidad variable por vigencia; recorte por ventana/corte y conservación de A/R
previos a ZF; comparación de denominadores y scores frente al histórico.

#### D10.1. Atribución ZC con doble comprobación obligatoria

- **Decisión:** aprobada expresamente por el usuario el 29/10/2026. La doble
  comprobación se aplica en todos los cambios de modelo y a todos los registros
  afectados por la atribución, no solo cuando se detecte previamente una anomalía,
  en una selección de ejemplos o únicamente en máquinas tándem.
- **Alcance:** regla de asignación en ZC. No sustituye las fronteras propias de
  ZF, no modifica los CSV ni las fechas industriales y no declara implementada
  esta funcionalidad por haber quedado acordada.

**Primera comprobación: frontera temporal.** Usar la entrada declarada
`MP9_CAMBIO_MODELO.FECHA_ON`, con `STATUS=1`, y la identidad de su máquina física.
La muestra de diez minutos que contiene el cambio se atribuye temporalmente a
la fabricación saliente; la primera ventana completa posterior, a la entrante.
Para una entrada a las 06:11, la muestra de las 06:20 representa 06:10–06:20 y
se propone para la saliente; la de las 06:30 representa 06:20–06:30 y se propone
para la nueva. Si la entrada coincide con el límite de las 06:20, la muestra
que termina a las 06:20 sigue representando el periodo anterior; la siguiente
representa el posterior. No crear muestras ausentes ni redondear los eventos
A/R originales, cuyos intervalos se conservan y tratan con sus reglas propias.

**Segunda comprobación: configuración física vigente, siempre.** Contrastar
esa propuesta con la correspondencia comprobada entre producción/modelo,
planta, línea, máquina física o parte tándem, sección/cavidad y molde en el
periodo observado. Incorporar las evidencias de `MP9_LISTA_RECH_ZC` y los cruces
disponibles con MP9, A/R e inspecciones ZF; respetar en cada fuente su significado
temporal y el tránsito entre zonas. No trasladar automáticamente una fecha ZF
a ZC ni considerar independiente una correspondencia deducida exclusivamente
de la misma frontera temporal que se está comprobando.

Un número de molde aislado, un ramal sin vigencia o el mero hecho de aparecer
después de `FECHA_ON` no confirman el modelo. Una sustitución de molde durante
la misma fabricación tampoco crea otra producción. Comprobar la correspondencia
para cada parte física tándem sin asumir que A/B tienen siempre los mismos
ramales o que la actividad de una parte acredita la de la otra.

**Resolución automática cuando la evidencia es inequívoca:**

| Resultado del contraste | Tratamiento |
|---|---|
| La configuración confirma la propuesta temporal | Confirmar la atribución a esa producción. |
| La configuración comprobada pertenece a la saliente después de la frontera temporal | Atribuir esos registros a la saliente, conservando y justificando la excepción temporal. |
| La configuración comprobada pertenece a la entrante | Atribuir esos registros a la entrante; no confundir la muestra con la hora exacta de arranque. |
| No hay evidencia física suficiente, sin contradicción demostrada | Mantener la aproximación temporal autorizada, identificando el contraste como no concluyente y sin afirmar confirmación física. No inferir parada por ausencia de rechazos. |
| Existen correspondencias incompatibles que impiden una atribución inequívoca | Conservar las evidencias y dejar pendiente el ámbito afectado; aplicar D05 si impide calcular un componente requerido. |

Una excepción respaldada por una correspondencia inequívoca se resuelve de
forma automática y auditable; no requiere una incidencia humana por cada fila.
La falta de rechazos no acredita ausencia de fabricación ni confirma por sí
sola la propuesta temporal. Las ambigüedades se agrupan por causa y contexto
para su seguimiento, sin crear copias voluminosas repetidas de los datos fuente.

**Ejemplo de aceptación:** para AV32B, entrada declarada del 14/10 a las 06:23,
la propuesta temporal asigna a la saliente la muestra de las 06:30 y a la nueva
las siguientes. Si están confirmadas las correspondencias de la cavidad 12F,
el rechazo de las 06:50 con molde 35 se mantiene en la saliente y el de las
07:00 con molde 7 se asigna a la nueva. El rechazo de las 07:00 documenta
actividad de esa ventana; no demuestra un inicio exacto a las 06:50 o a las
07:00. No desplazar toda la producción por una única fila ni aplicar un retraso
fijo inferido de este ejemplo.

**Trazabilidad y actualización:** registrar de forma compacta la producción
propuesta y la finalmente asignada, el resultado de ambas comprobaciones, la
regla y correspondencia versionadas y las referencias a las evidencias fuente.
La comprobación debe cubrir cada registro mediante las correspondencias vigentes;
puede ejecutarse por lotes, sin repetir consultas ni copiar evidencias por fila.
Repetir el contraste de los periodos afectados si llegan datos tardíos, cierres,
correcciones o nuevas versiones de las correspondencias. Una misma entrada y
versiones deben producir el mismo resultado; conservar la versión anterior si
se publica una revisión, sin duplicar observaciones ni contadores.

**Revisión del inicio operativo y del denominador, aprobada por el usuario:**
la aproximación inicial no se mantiene contra evidencia inequívoca de la
saliente. Si el contraste confirma muestras posteriores atribuibles a esta,
revisar la frontera del ámbito físico correspondiente y recalcular los
minutos-cavidad y scores afectados en una nueva versión. Mantener coherentes
la atribución de muestras y el denominador, sin duplicar exposición ni trasladar
esa revisión a otra parte tándem. En el ejemplo de las 06:23, si se confirma
que la ventana que termina a las 06:50 pertenece a la saliente y la siguiente,
06:50–07:00, a la entrante, la frontera operativa revisada es las 06:50. Sigue
siendo una aproximación por ventanas, no un arranque físico medido a esa hora.
No desplazar globalmente una fabricación por una sola sustitución de molde
sin correspondencia validada ni resolver evidencias incompatibles tomando
automáticamente el máximo timestamp. Si llegasen registros previos que ya
confirman la entrante, contrastar el conjunto antes de retrasar su frontera.

La atribución de un rechazo no fija por sí sola el primer instante físico de
fabricación ni modifica la capacidad configurada. Conservar la distinción de
D10 entre fecha declarada, frontera operativa y fabricación acreditada.
Los contadores compartidos de máquina tándem permanecen compartidos
según el contrato; no repartirlos en proporción a rechazos, moldes o cavidades
observadas para obtener cantidades por modelo.

**Pruebas exigidas:** doble comprobación ejecutada aunque coincida la propuesta
temporal; cruce de ventana y cambio exactamente en el límite; saliente todavía
observada después de la frontera; entrante confirmada; sustitución de molde sin
cambio de modelo; ausencia de rechazos sin inferir parada; correspondencia ausente
o contradictoria; vigencia histórica y separación de partes tándem; datos tardíos
con recálculo idempotente; fuentes ZC/ZF con fechas distintas; ausencia de reparto
de rueda y conservación de fechas y eventos fuente. Verificar cobertura de la
doble comprobación sobre el conjunto procesado, no solo sobre una muestra.

#### D11. Reclamaciones: conservar el histórico y explicitar la falta de información

- **Pregunta:** Q11.
- **Estado:** aprobado por el usuario el 29/10/2026; adaptación y validación
  pendientes. La integración automática con una fuente de reclamaciones se
  aplaza a una entrega posterior y no bloquea el despliegue del score de fabricación.
- **Histórico conocido:** conservar los registros y clasificaciones disponibles,
  identificando su procedencia y carácter de carga manual cuando corresponda.
  No presentarlos como una fuente completa y actualizada automáticamente.

**Nuevas producciones:** mientras no exista una fuente actualizada vinculada de
forma fiable a la fabricación, mostrar «Sin información de reclamaciones».
Ese estado es distinto de «Sin reclamaciones» y de una clasificación de calidad
buena. No conservar el comportamiento que convierte una lista vacía del catálogo
manual en una conclusión positiva para una fabricación sin cobertura acreditada.

**Independencia analítica:** la información de cliente es una etiqueta y evidencia
externa al score automático MIA/A/R/ZC. Su ausencia no aporta cero puntos ni
modifica pesos, contribuciones, totales o clasificación del score de fabricación.
No es una fuente requerida por D05 para publicar ese score; mantener separados
los estados de cobertura de fabricación y reclamaciones.

**Integración futura:** requerirá una fuente autorizada, cobertura/actualización,
identidad y asociación justificable con las producciones, tratamiento de revisiones
y reglas de clasificación versionadas. La autorización de MARBLE 25 para el
catálogo modelo–segmento de D06.1 no supone que su informe ya esté conectado como
fuente viva ni que sus reclamaciones se asignen automáticamente a nuevas
fabricaciones por coincidir en modelo. Conservar las evidencias históricas y
evitar duplicarlas si se incorpora más adelante una fuente estructurada.

**Pruebas exigidas en E04/E09/E10/E11:** histórico conocido con procedencia y
clasificación conservadas; nueva fabricación sin fuente con estado de información
ausente; diferencia entre ausencia y cero reclamaciones acreditadas; score de
fabricación idéntico con independencia de la disponibilidad de reclamaciones;
presentación sin etiquetas positivas generadas por una lista manual vacía.

#### D12. Identidad de producción: correspondencias verificadas entre histórico y Fenix

- **Pregunta:** Q12.
- **Estado:** aprobado por el usuario el 29/10/2026; implementación y validación
  de correspondencias concretas pendientes. El criterio se refinará conforme se
  examinen producciones reales a lo largo del tiempo.
- **Regla acordada:** conservar los identificadores originales de ambos orígenes
  y registrar una correspondencia solo cuando se demuestre que representan la
  misma fabricación. El catálogo común expone esa relación de forma trazable,
  evitando contabilizar dos veces una producción representada en ambas fuentes.

**Evidencia requerida:** contrastar modelo de botella, línea y máquina física o
parte tándem, registros de cambio y periodos de fabricación. Respetar la diferencia
entre fechas industriales y límites de observaciones del histórico, conforme a
D10; no exigir igualdad textual de fechas que representan hechos distintos ni
convertir su proximidad en prueba suficiente. Dos fabricaciones diferentes del
mismo modelo conservan identidades distintas, incluidas las concurrentes en
partes distintas de una tándem.

**Correspondencias y dudas:** conservar origen/ID de cada lado, evidencia,
regla/versión y resultado de validación. Los candidatos ambiguos permanecen
identificados para revisión, sin fusionar, borrar datos ni declarar una equivalencia
automática. Si existe posible duplicidad no resuelta, no contar silenciosamente
ambas representaciones como dos fabricaciones independientes en referencias o
agregados que dependan de esa identidad. No confundir falta de coincidencia
demostrada con una prueba de que dos entradas sean equivalentes.

**Aprendizaje operativo:** validar las reglas con sucesivas producciones y
documentar qué evidencia confirma o descarta cada relación. Automatizar solo
reglas contrastadas y versionadas, conservando la revisión para casos nuevos
o contradictorios. No usar semejanza de nombres, un porcentaje arbitrario de
solape temporal ni un umbral de proximidad no validado como identidad industrial.
El avance de esta validación no obliga a inventar correspondencias para todo el
histórico antes de servir producciones cuya identidad ya sea inequívoca.

**Publicación y compatibilidad:** una equivalencia aceptada se incorpora mediante
una nueva versión del catálogo; preservar IDs originales, trazabilidad y
publicaciones anteriores. Definir en E02 la identidad de consulta común y la
selección de observaciones cuando ambas fuentes representen el mismo hecho,
sin sumarlas por defecto ni introducir una política de sobrescritura implícita.

**Pruebas exigidas en E02/E04/E09:** misma fabricación con dos IDs reconocida
una sola vez; mismo modelo en periodos diferentes sin unión; concurrencia tándem
sin fusión; desfase ZC/ZF frente a MIA sin equivalencia inventada; candidato
ambiguo pendiente; repetición idempotente de una correspondencia aceptada;
nueva regla versionada con reproducción del catálogo anterior.

#### D13. Score horario: agregar cantidades base y calcular después

- **Pregunta:** Q13.
- **Estado:** aprobado por el usuario el 29/10/2026; implementación y validación
  pendientes. No se ha modificado el código de cálculo.
- **Regla acordada:** aplicar las fórmulas, pesos, referencias y criterios
  aprobados del score de producción a las cantidades base del intervalo horario
  correspondiente. Primero se agregan los hechos; después se calculan tasas,
  componentes, contribuciones y total. No sumar ni promediar scores anteriores.

**Intervalo completo:** para 10:00–11:00, utilizar exclusivamente las observaciones
atribuidas que correspondan a esa ventana, con la semántica de final de muestra
acordada para Fenix. Si hay seis muestras de diez minutos, no sumar o promediar
sus seis scores. Las reglas de pertenencia y límites de ventana serán explícitas
en E02 para no incluir dos veces una muestra ni desplazarla a la hora siguiente.

**Agregación por componente:**

- MIA: agregar rechazos y calcular la base de inspeccionadas conforme a D01
  (máximo por producción/línea/ramal/fecha-hora y suma de máximos en la ventana).
  No sumar indiscriminadamente `INSPECC` por máquina, defecto o cavidad ni
  promediar porcentajes de muestras. Aplicar después las reglas MIA y la versión
  de referencia de producciones terminadas acordada en D06.
- A/R y DAR: recortar los intervalos de eventos a la ventana y al corte; obtener
  sus minutos afectados y la capacidad disponible de D02/D03/D10. Resolver la
  agregación de solapes dentro de categoría mediante D14, respetando la exclusión
  del caso entre categorías confirmada en Q03-S. Aplicar
  después las fórmulas, límites y pesos del componente; no agregar scores de
  tramos. Mantener las características aplicables y equivalencias de D08/D07.
- ZC: agregar los contadores físicos y teóricos correspondientes al intervalo
  y calcular su rendimiento y contribución con la regla aprobada. No promediar
  rendimientos instantáneos sin su base ni repartir una rueda compartida entre
  fabricaciones. Conservar las atribuciones y transiciones industriales acordadas.

**Punto parcial y publicación:** el octavo punto usa exactamente el mismo
procedimiento hasta el corte efectivo de D04, con la duración/exposición real
disponible. No convertirlo artificialmente en sesenta minutos. Exigir todos los
datos y cálculos necesarios según D05 y publicar total y desglose coherentes.
Si cambia una entrada aceptada, recalcular las cantidades y resultados afectados
en una nueva versión, conservando las publicaciones anteriores.

**Compatibilidad y referencias:** no introducir umbrales horarios, suavizados,
promedios móviles de scores, pesos nuevos o reglas estadísticas distintas por
el hecho de representar una hora. La media y sigma del histórico que utiliza
la fórmula MIA siguen formando parte de esa fórmula; D13 prohíbe sustituir el
cálculo horario por promedios de resultados, no elimina sus operaciones estadísticas.
Fijar por publicación las versiones de referencias/configuración conforme a D06/D07.

**Pruebas exigidas en E05/E06/E07/E09/E11:** cálculo directo sobre hechos de una
hora igual al cálculo sobre sus cantidades base conciliadas; muestras de distinto
volumen sin media simple de porcentajes; inspeccionadas repetidas sin duplicación;
A/R que atraviesan límites y corte de consulta; tándem sin duplicación de rueda;
hora parcial con duración efectiva; límites entre horas sin muestras contadas dos
veces; diferencia esperada frente a una suma/media de scores de diez minutos;
total igual a las contribuciones ponderadas del intervalo y publicación completa.

#### D14. Solapes dentro de categoría: unión temporal sin alterar los eventos

- **Pregunta:** Q03-S, en su caso de una misma categoría.
- **Estado:** aprobado por el usuario el 29/10/2026 como criterio preventivo.
  No se ha acreditado que el ejemplo ocurra en las fuentes actuales.
  Implementación y validación pendientes.
- **Regla acordada:** para una misma producción, cavidad física y categoría de
  criticidad, calcular la duración de la unión de los intervalos A/R aplicables.
  El tiempo coincidente cuenta una sola vez aunque los avisos tengan defectos o
  identificadores diferentes. No unir cavidades ni fabricaciones diferentes.

**Ejemplo aprobado:** dos avisos de categoría Mayor para 7F, de 10:00–10:30 y
10:20–10:50, aportan 50 minutos-cavidad afectados a esa categoría, no 60.
Recortar primero cada intervalo a la ventana, al corte y al ámbito atribuible;
usar D02 para eventos abiertos y conservar la precisión temporal del origen.
Unir después los intervalos solapados y sumar sus duraciones, sin redondear por
evento antes de agregar.

**Evidencia intacta:** conservar ambos eventos originales, claves, defectos,
fechas y revisiones en Silver y en el detalle de consulta. La unión es un cálculo
derivado para el numerador de minutos afectados; no fusiona, elimina ni cambia
los registros industriales. Vincular el resultado a sus eventos. El desglose
por defecto puede tener coincidencias legítimas: no sumarlo para reconstruir
el total físico de la categoría, que se obtiene de la unión temporal.

**Caso descartado por el usuario:** el 29/10/2026 confirma que no se dará el
solape entre categorías distintas para el mismo modelo de botella, cavidad y
producción. Excluir ese escenario del alcance: no implementar la propuesta de
prioridad por mayor criticidad, reparto ni reasignación de minutos entre categorías.
No dejarlo como una decisión pendiente ni extender este supuesto a fabricaciones
o cavidades diferentes. Descartar el escenario no autoriza borrar eventos fuente;
se mantienen las garantías generales de conservación y revisión de contradicciones.

**Límites:** la unión temporal acordada no cambia la capacidad del denominador.
Versionar la diferencia frente a la suma directa de eventos de la ruta histórica
y conservar las publicaciones anteriores.

**Pruebas exigidas en E06/E07/E09:** caso 30 + 30 con solape de 10 igual a 50;
eventos contenidos y cadenas de solapes; intervalos sin solape; cavidades o
fabricaciones diferentes sin unión; recorte por ventana y eventos abiertos;
repetición idempotente; eventos y referencias preservados; misma regla en
producción, turno y hora.

#### D15. Denominador MIA único y ausencia de base como cálculo pendiente

- **Preguntas:** completa Q01 y Q05.
- **Estado:** aprobado por el usuario el 29/10/2026; adaptación del cálculo y
  validación pendientes. No se ha cambiado código, datos ni despliegues.
- **Fuente única:** obtener el denominador MIA de `LISTA_RECH.INSPECC` conforme
  a D01: máximo por producción, línea, ramal y fecha/hora; sumar esos máximos
  para el intervalo. No mantener dos fuentes alternativas para esta misma base.
- **Respaldo descartado:** no utilizar en Gold ni en su consulta el fallback
  histórico que suma inspeccionadas de `LISTA_DEFECTOS`. Tener esa columna
  disponible en el detalle no la convierte en una segunda fuente del denominador.

**Función de cada tabla:** `LISTA_DEFECTOS` sigue aportando los rechazos por
defecto del numerador y conserva íntegramente sus columnas aceptadas en Silver.
`LISTA_RECH` proporciona la base de inspeccionadas del denominador. D15 no elimina
tablas, columnas ni evidencia, ni modifica sus claves de idempotencia; fija su uso
analítico. Aplicar la misma procedencia a producción, turno y ventana horaria.

**Ausencia o base no utilizable:** si no se dispone de un denominador válido para
la producción/intervalo, no calcular su porcentaje ni score MIA. Registrar el
pendiente y su causa, distinguiendo fuente ausente, cobertura incompleta, datos
rechazados y un contador cero realmente observado. No dividir por cero ni convertir
la falta de cálculo en un score cero. Aplicar D05: conservar el último total y
desglose completos con su corte real; sin resultado previo, mostrar la espera.

**Recuperación:** reintentar cuando se publique información aceptada que resuelva
la dependencia o se aplique una corrección autorizada. Si la carencia persiste,
mantenerla visible para supervisión y analizar su solución posteriormente, como
indica el usuario; no habilitar por defecto otro cálculo para forzar la publicación.
No mezclar una base antigua con rechazos nuevos de otra cobertura.

**Compatibilidad y pruebas:** conservar las publicaciones y la referencia del
cálculo legado para reproducirlas. Documentar como diferencia aprobada los casos
que antes daban un score mediante el fallback y ahora quedan pendientes.
Comprobar máximo/suma de máximos de D01, ausencia de LISTA_RECH con datos presentes
en LISTA_DEFECTOS sin respaldo, base cero sin score inventado, conservación de
columnas Silver, recuperación al llegar una base válida y publicación completa
según D05 en E05/E07/E09/E10.

#### D16. Modelo dimensional Kimball en Gold y alcance de SCD

- **Estado:** aprobado por el usuario el 05/11/2026 como evolución del plan;
  implementación y aceptación pendientes conforme a 4.3.
- **Alcance:** formalizar hechos, dimensiones conformadas, grano y relaciones
  de consumo en Gold. Bronze conserva originales y Silver sus contratos,
  calidad, vigencias y atribuciones; no convertir ambas capas a Kimball.
- **Historización:** diseñar SCD tipo 2 donde lo requiera el análisis histórico,
  aprovechando las vigencias aceptadas sin confundirlas con una implementación
  dimensional completa ni con el versionado técnico de Delta.
- **Data Vault:** fuera del alcance actual. Una futura incorporación exige
  necesidad demostrada y decisión independiente.
- **Ejecución:** contratos y PRs pequeños en E02/E04–E07/E09/E10; conservar D01–D15,
  publicaciones anteriores, fórmulas y API hasta validar cada sustitución.

#### D17. Prioridad de entrega y demostración funcional del 07/11/2026

- **Estado:** prioridad acordada por el usuario el 06/11/2026; incorporación
  documental realizada, implementación y ensayo de la demostración pendientes.
- **Objetivo inmediato:** mostrar un recorrido funcional con datos reales,
  reproducible y de alcance conocido. Aplicar la matriz A/B/C de 8.1.
- **Separación de alcances:** demostración, piloto continuo y puesta en servicio
  tienen criterios distintos. La fecha objetivo no declara completos E09–E15.
- **Orden:** asegurar referencia/entorno y pruebas; demostrar la aplicación
  actual y la evidencia Gold local; completar después la integración continua,
  conciliación integral y formalización dimensional mediante PRs pequeñas.
- **Invariantes:** mantener D05/D15, P01 y los límites de 7.18. No generar scores
  parciales, inventar clasificaciones, presentar un snapshot offline como flujo
  vivo ni sustituir el lector activo para aparentar que Gold está integrado.
- **Tecnologías:** emplear el stack actual para la demostración. Las nuevas
  herramientas dependen de una necesidad y de las entregas indicadas en 8.1.

#### D18. Segmentos recibidos y DAR común provisional — 06/11/2026

- **Fuente recibida:** `Segmentos.xlsx`, hoja `WorkSheet1`: Código (A), Familia
  (C) y Estado (D). Se admiten exclusivamente Vigente y En preparación: 575
  modelos (563 + 12), en 13 familias. Se excluyen obsoletos y obsoletos técnicos.
- **Clasificación:** conservar etiquetas y códigos exactos, incluida la
  distinción Tarro/Tarros. No equiparar Vino pesado a Vino premium ni inferir
  tipos de cierre. Cada correspondencia conserva SHA-256 y fila de origen.
- **P01 parcialmente resuelta:** disponible Código→Familia del Excel. Siguen
  pendientes los DAR específicos de cada segmento y los códigos no incluidos.
  El Excel no acredita equivalencia automática con los segmentos del PowerPoint.
- **Excepción temporal autorizada expresamente por el usuario:** se permite
  copiar el perfil existente `vino_ligero_v1` a un perfil por segmento con los
  mismos grupos/códigos, marcado PROVISIONAL. Los resultados llevan
  `configuration_status=PROVISIONAL_DAR`; no se presentan como criterio
  industrial aprobado para ese segmento. Sin selección explícita de perfil
  común queda PENDING; ningún modelo desconocido hereda un perfil.
- **Alcance respecto de D06.1/D08/D17:** esta autorización sustituye únicamente
  la prohibición de un DAR común en la configuración provisional de demostración.
  Se mantienen la trazabilidad, las bases completas D05/D15 y el bloqueo por
  dependencias ausentes. No acredita conciliación ni aceptación para el piloto.
- **Implementación local:** importador `app.gold.segments`, contratos
  `segment_catalog`/`model_segment_catalog`, estados de cálculo y pruebas. Fuente
  original intacta y copia por SHA-256 en `var/gold_validation/classification-sources`.
  Detalle y comando reproducible en `docs/CATALOGO_SEGMENTOS.md`.

#### D19. Implementación de prioridades A/B y lector Gold de validación — 06/11/2026

- **Autorización de código:** el usuario solicita completar los cambios A/B,
  incluida la conexión de la aplicación, por entregas revisables. El lector
  Gold se implementa como opción explícita; el lector histórico permanece
  predeterminado. Esta decisión no acredita por sí sola aceptación industrial.
- **Contrato V3:** 36 tablas; `zc_production_links` conserva la relación del
  productor para ruedas compartidas y `basis_publication_id` fija los datos de
  resultados cerrados. La compatibilidad se comprueba por versión y huella.
  No se sobrescriben los almacenes V2 existentes para adaptar sus esquemas.
- **Consulta:** una publicación por petición/caché; catálogo, score, turnos y
  escenarios sin escritura; pendientes explícitos, DAR provisional visible y
  reclamaciones desconocidas cuando no existe una fuente actualizada.
- **Historización:** los escenarios de una fabricación cerrada usan su publicación
  base. La revisión persistida requiere IDs y motivo explícitos, conservados en
  el estado durable; no cambia por defecto otras fabricaciones cerradas.
- **Procesamiento:** lotes, índice por producción/periodo y reutilización con
  entradas iguales; exportador JSON/CAS en streaming y hook de ingesta opcional
  posterior a confirmación. Se mantienen los presupuestos y los bloqueos ante
  entradas incompatibles; no se declara escalabilidad distribuida ilimitada.
- **Operación implementada:** imágenes/configuración de tres servicios,
  métricas/histogramas, alertas/dashboard, backup/verificación/restore y job de
  copia externo. Las fechas de descubrimiento proceden del productor; el snapshot
  offline de validación no se convierte en una fuente viva.
- **Evidencia:** tests completos y de API/roles, frontend compilado, cantidades
  reales MIA/A/R/ZC conciliadas y exportación real del ledger sin cambios de datos.
  Resultados y comandos en los documentos de implementación y operación.
- **Aceptación aún externa:** DAR por segmento, histórico/evidencias de Calidad,
  construcción y ensayo de imágenes, host/permisos, proxy/IdP/HTTPS, supervisión
  activa y copia fuera del servidor con RPO/RTO. No se declaran cumplidos por
  disponer de código o ensayos locales. No se ha modificado la ingesta activa.

### 7.2. Pendientes para concretar la integración, a 06/11/2026

La arquitectura y las reglas principales de cálculo están acordadas. No se
necesita otra ronda general de preguntas de negocio para iniciar el desarrollo.
Además de P01, faltan los contratos técnicos concretos ya previstos en las
entregas; su definición no equivale a una funcionalidad implementada.

| Bloque | Qué falta concretar o acreditar | Entregas |
|---|---|---|
| Demostración funcional — D17 acordada | Preparar y ensayar el alcance A de 8.1 para el 07/11/2026, con datos reales y estado de cada recorrido visible. No confundirlo con conexión de la app a Gold, piloto continuo o aceptación de producción. | E00/E02/E08/E09 acotadas; E14 para la configuración de CI |
| P01 — PARCIALMENTE RESUELTA POR D18 | Recibido Código→Familia para 575 modelos vigentes/en preparación. Pendientes DAR específicos por segmento, equivalencias con el PowerPoint y modelos no incluidos; DAR común explícito solo provisional. | E02/E04 |
| Entrada Silver confirmada — flujo acordado | Aplicar 5.3/5.6: manifiesto confirmado, versiones compatibles, cobertura por ámbito, activación cada 30 segundos y mecanismo de avisos desacoplado, sin Kafka inicial. Concretar esquema, protección de artefactos y ampliaciones mínimas necesarias. | E02/E03/E08 |
| P02 — CRITERIO OPERATIVO RESUELTO | El usuario confirma que no hay señal y acepta la entrega aproximadamente cada diez minutos; ausencia de novedades por parada o sin cambios. Aplicar 5.3.2, con comprobaciones locales y escrituras confirmadas, sin esperar un marcador inexistente ni inventar datos. Falta concretar e implementar los metadatos del modo operativo. | E02/E03/E09 |
| Tablas y consulta Gold — contratos especificados | C01–C03 acordados en 7.4–7.6; ocho bloques C04–C11 redactados técnicamente en 7.9–7.16 para revisión e implementación. Aplicar 7.17 y validar las reglas contra el corpus real; P01 sigue pendiente. | E00/E02/E04–E07/E10 |
| Formalización dimensional — D16 acordada | Concretar hechos, dimensiones conformadas, grano, cardinalidades e historización selectiva de 4.3 sobre C01–C11. La base Gold local no acredita todavía Kimball integral ni SCD tipo 2 dimensional completo. | E02/E04–E07/E09/E10 |
| Incremental y recuperación | Concretar identidades, ámbitos y protección de lectores por tabla. La fase inicial conserva archivos Delta y temporales sin limpieza automática según 5.7; definir posteriormente su política con aprobación del usuario. No duplicar datos en controles ni en cada comprobación. | E02/E08 |
| Compatibilidad analítica | Corpus real e identidades histórico–Fenix demostradas; salidas de referencia y cantidades intermedias. Comparar reglas equivalentes y justificar separadamente los cambios semánticos ya aprobados, incluida la normalización de códigos y la aproximación ZC. | E00/E09 |
| Piloto y operación | Rutas/volúmenes y permisos definitivos de publicador, API y frontend; configuración y secretos externos; presupuestos medidos de recursos, observabilidad, criterios de aceptación y reversión independiente de la ingesta. | E12–E15 |

**Siguiente entrega técnica propuesta:** congelar E00 y definir E02 en cambios
pequeños, empezando por el contrato Silver→Gold y el esquema de producción,
ventanas y cobertura. Después implementar E03 contra versiones confirmadas en
solo lectura. No desplegar ni cambiar la ingesta al realizar esta actualización
documental, ni exigir que P01 esté completo para desarrollar los bloques
independientes.

### 7.3. Método de definición de cada contrato de tabla

**Acordado con el usuario el 02/11/2026:** tratar cada tabla de forma completa,
en pasos revisables, antes de pasar a la siguiente. Para cada contrato registrar:

1. Finalidad y granularidad; columnas, tipos, unidades, nulos, clave estable y relaciones.
2. Origen y transformaciones; inserciones, actualizaciones permitidas, invariantes,
   tratamiento de contradicciones, ausencia de novedades y datos tardíos.
3. Publicación: dependencias/versiones, integridad, cobertura, promoción y alcance
   de la disponibilidad de la tabla frente a la del score completo.
4. Consulta: selecciones/filtros, unión con otras tablas, precálculos utilizables,
   cálculos particulares y publicación fijada para toda la respuesta.
5. Recuperación: identidad idempotente de operación, fallos antes/después de commit,
   promoción y acuse, sin pérdidas ni duplicación de filas o cantidades.
6. Conservación: versiones/publicaciones referenciadas, historial de cambios reales,
   protección de lectores, limpieza y presupuesto de metadatos/temporales.

El contrato inicial es `productions`, aprobado en 7.4. Las propuestas nuevas de campos
y parámetros se distinguirán de decisiones ya aprobadas; no modificar código ni
desplegar tablas por la mera discusión o actualización documental.

### 7.4. C01 — Contrato de productions

**Estado:** estructura y comportamiento aceptados por el usuario el 02/11/2026;
pendientes implementación y validación. La conservación inicial sigue 5.7.

**Finalidad:** una fila por fabricación concreta y máquina física/parte tándem.
Identificar y buscar modelo, contexto, fechas y estado. No multiplicar la fila
por ramal, cavidad, turno, muestra o publicación ni guardar aquí sus scores.

#### C01.1. Esquema, identidad y relaciones

| Campo | Tipo lógico | Nulabilidad y significado |
|---|---|---|
| `id_produccion` | STRING | Obligatorio y único; reutilizar el ID Silver Fenix y conservar los históricos según D12. |
| `planta` | STRING | Obligatoria; planta acreditada. |
| `origen_fenix` | STRING | Obligatorio para Fenix; nulo en históricos sin correspondencia. |
| `linea` | STRING | Obligatoria; línea industrial. |
| `id_maquina_fenix` | BIGINT / int64 | Obligatorio en Fenix aceptado; nulo en históricos sin enlace acreditado. |
| `maquina_fisica` | STRING | Denominación comprobada, como AV31B; nula cuando el histórico no la acredita. |
| `id_zc_raiz` | BIGINT / int64 | Raíz ZC; obligatoria para Fenix, nula en históricos sin enlace validado. |
| `id_modelo_fenix` | BIGINT / int64 | ID interno; obligatorio para Fenix, nulo en históricos sin correspondencia. |
| `codigo_modelo_botella` | STRING | Código canónico como 1358/242; nulo solo si falta resolución y se identifica el pendiente. |
| `fecha_inicio_zc` | TIMESTAMP, microsegundos | Entrada industrial declarada; obligatoria para Fenix, nula si un histórico no la acredita. |
| `fecha_fin_zc` | TIMESTAMP, microsegundos | Salida ZC declarada; nula si no se ha recibido o no está acreditada. |
| `fecha_inicio_zf` | TIMESTAMP, microsegundos | Primera entrada ZF válida vinculada a la raíz; admite nulo. |
| `fecha_fin_zf` | TIMESTAMP, microsegundos | Última salida ZF válida cuando estén cerrados los registros propios; admite nulo. |
| `fecha_inicio_operativo_zc` | TIMESTAMP, microsegundos | Frontera aproximada contrastada de D10/D10.1; nula si todavía no está resuelta. |
| `criterio_inicio_operativo` | STRING controlado | Regla de frontera inicial o revisión contrastada; nulo si no hay frontera resuelta. La publicación fija su versión/evidencias. |
| `estado_produccion` | STRING controlado | Abierta/cerrada; desconocido solo si el histórico no permite acreditarlo. |
| `estado_dato` | STRING controlado | Válido/pendiente/en revisión para la fila del catálogo, independiente de la disponibilidad del score. |

No sustituir nulos por cero, cadena vacía, fecha de recepción ni límite de datos
observados. Preservar la interpretación temporal Silver y su política vigente
Europe/Madrid/hora repetida, sin convertir fechas locales a UTC implícitamente.
El tipo físico Delta/lector debe ser compatible; la precisión de almacenamiento
no inventa precisión de captura. Código de botella e identificadores de producción
son texto, sin conversiones decimales ni pérdida de sufijos.

El ID Fenix existente se basa en planta/origen/raíz ZC. No generar otro ID por
completar un cierre, corregir atributos o publicar una versión. No fusionar por
modelo ni impedir solapes industriales entre fabricaciones durante el tránsito
ZC→ZF. ID_ST1_REL se conserva en composición/trazabilidad, no como identificador
de la fabricación precedente ni como sustituto de la clave de producción.

Las fechas industriales proceden de MP9 aceptado y vinculado a la raíz:
entrada/salida ZC y primera entrada/última salida ZF válidas. El fin ZF no se
confirma mientras un registro propio ZF continúe abierto. La frontera operativa
permanece separada y se revisa según D10.1. Comprobar coherencia de fechas e
identidades sin corregir por suposición las anomalías de origen.

Ramales, cavidades y capacidad se resuelven por relaciones vigentes; segmento y
perfil, mediante catálogos versionados con P01 pendiente. Cobertura/frescura,
límites observados, número de filas y archivos son metadatos de la publicación
o del detalle. No requieren nuevas columnas industriales ni una fila por ramal.

#### C01.2. Inserciones, actualizaciones e invariantes

| Cambio aceptado | Tratamiento |
|---|---|
| Nueva fabricación Silver | Insertar una fila con su identidad existente. |
| Cierre ZF válido | Actualizar fecha_fin_zf y estado en la misma fila. |
| Fecha o correspondencia de modelo/máquina recibida o corregida | Enriquecer/corregir desde fuentes y decisiones aceptadas, compatibles y versionadas. |
| Revisión contrastada de inicio operativo | Actualizar la frontera y señalar los denominadores/ventanas afectados. |
| Contradicción aún no aceptada | Conservar lo aceptado y reflejar el ámbito en revisión; Gold no decide otro ganador. |
| Sin cambio relevante | No reescribir datos ni modificar atributos por un simple polling. |
| Ausencia de archivos o desaparición en un snapshot | No borrar, cerrar ni declarar parada por esa ausencia; aplicar P02 y los controles de integridad. |

No reabrir una producción por volver a leer una versión antigua. Una corrección
de cierre/modelo/máquina debe estar acreditada por su fuente o decisión vigente.
En Fenix la falta de cierre mantiene abierta la producción; abierta no significa
que tenga datos recientes. Motivos de pendiente/revisión y evidencias se
referencian mediante metadatos compactos, sin copiar todo el origen en cada fila.

#### C01.3. Publicación

Consumir catálogo Silver, MP9, SIP_MODELO y topología física en versiones
confirmadas compatibles. Para la frontera operativa incorporar las evidencias
y reglas de D10.1 que requiera su contrato. No leer CSV vivos ni combinar
simplemente las últimas versiones de cada fuente.

El manifiesto identifica versión Delta de productions, publicaciones fuente,
reglas, evidencias de derivación y producciones modificadas, junto con calidad
y cobertura operativa P02. Verificar la escritura antes de promover el puntero.
Relacionar catálogo/asignaciones/capacidad/resultados mediante la publicación
compatible; no repetir todos esos metadatos en cada fila ni reescribir el
catálogo completo por un avance ajeno a él.

Se puede mostrar una fabricación en el catálogo aunque su score no esté listo.
La disponibilidad de la fila no equivale a disponibilidad del total: aplicar D05
al cálculo y su desglose por separado, sin exigir MIA nueva para publicar una
fecha industrial aceptada que no dependa de esas observaciones.

#### C01.4. Consulta de la aplicación

Obtener por ID y listar/buscar por modelo, planta, línea, máquina física, estado
y periodos industriales, respetando sus nulos. La consulta fija una publicación
para la fila y todas las relaciones/catálogos utilizados. La petición no escribe
en Gold/Silver ni consulta SQLite de la ingesta.

La app actual necesita id, line, start_day/end_day, branches y metadatos de
modelo/segmento/perfil. El adaptador resolverá esos datos mediante este catálogo
y sus relaciones: ramas/límites observados desde asignaciones y cobertura;
segmento/perfil desde referencias; fechas industriales desde productions.
**Aclaración aceptada el 02/11/2026:** el end_day legado procede del último día
del tramo consecutivo con observaciones identificado por el particionador histórico;
es una fecha de datos observados, no el cierre industrial. Su obligatoriedad
actual requiere adaptar el contrato API. fecha_fin_zf continúa nula mientras la
producción permanezca abierta y se completa cuando se acredita su cierre ZF.
El límite observado, el fin industrial y el corte de cálculo son conceptos
separados; no rellenar un cierre con la fecha actual ni con el último dato recibido.

#### C01.5. Recuperación, conservación y aceptación

La identidad idempotente de operación depende de las entradas fijadas y la
transformación, no del intento o contenedor. Antes del commit, reintentar las
mismas entradas; si ya existe el commit y falta el acuse, acreditarlo antes de
repetir. Escritura confirmada sin promoción: mantener la publicación anterior
visible y completar la promoción coherente. Promoción realizada sin progreso
local: reconocerla y completar el acuse. No duplicar fabricaciones ni perder cierres.

Conservar revisiones materiales, fuentes y publicaciones necesarias para explicar
el catálogo según 5.7. El historial crece por cambios reales, sin otra copia
completa por comprobación. No confiar solo en retener Parquet si desaparecen
los logs Delta o las referencias que reconstruyen sus versiones. No habilitar
purga automática durante las semanas de validación ni hasta aprobación explícita
posterior del usuario. Medir crecimiento y verificar respaldo/restauración en
su entrega operativa; no declarar esas medidas implementadas en esta fase documental.

**Aceptación a verificar:** identidad igual a Silver; partes tándem separadas
incluso con el mismo modelo; cuatro fechas industriales y frontera operativa
sin confusión; cierre tardío sin otra fila; nulos históricos sin fechas inventadas;
catálogo visible con score pendiente; consulta coherente por versión; recuperación
sin duplicación; ausencia de escrituras sin cambios y de limpieza automática.

### 7.5. C02 — Contrato de production_assignments

**Estado:** propuesta aceptada inicialmente por el usuario el 02/11/2026;
pendientes implementación y validación, sin cambios en Silver ni despliegues.

**Finalidad y grano:** una fila por relación de una producción con su máquina
física y, cuando corresponda, ramal y zona, durante un intervalo de vigencia.
No una fila por muestra, defecto, cavidad, turno o consulta. Permitir reconstruir
asignaciones anteriores y separar las fabricaciones concurrentes de una tándem.

#### C02.1. Esquema e identidad

| Campo | Tipo lógico | Nulabilidad y significado |
|---|---|---|
| `id_asignacion` | STRING | Obligatorio, único y estable para la relación/episodio. |
| `id_produccion` | STRING | Obligatorio; referencia a productions de la misma publicación compatible. |
| `planta` | STRING | Obligatoria. |
| `origen_fenix` | STRING | Obligatorio en Fenix; nulo solo en históricos sin correspondencia acreditada. |
| `linea` | STRING | Obligatoria; línea industrial. |
| `id_maquina_fenix` | BIGINT / int64 | Máquina física/parte tándem acreditada; nulo solo en históricos sin enlace. |
| `zona` | STRING controlado | Obligatoria: ZC o ZF. |
| `ramal` | BIGINT / int64 | Código de ramal ZF; nulo si no aplica en ZC. No inventar un ramal a partir del ID de rueda. |
| `vigente_desde` | TIMESTAMP, microsegundos | Obligatorio; inicio de vigencia de la relación. |
| `vigente_hasta` | TIMESTAMP, microsegundos | Nulo mientras no haya final acreditado. |
| `estado_asignacion` | STRING controlado | Confirmada, pendiente o en revisión; solo la confirmada autoriza atribuciones automáticas. |
| `version_regla` | STRING | Versión de la regla que deriva la asignación. |
| `revision` | BIGINT / int64 | Revisión material de la relación; no número de sondeos ni reintentos. |
| `referencia_evidencia` | STRING | Referencia verificable a fuente/decisión; no JSON voluminoso repetido en cada fila. |

Reutilizar IDs Silver cuando representen exactamente la misma relación; no
generar otro al completar un cierre o corregir un atributo aceptado. Los nuevos
episodios tendrán identidad determinista apoyada en sus referencias estables,
sin depender de la hora del reintento o del final inicialmente nulo. La publicación
fija fuentes, versiones y regla, sin repetir el manifiesto en todas las filas.
Conservar la interpretación temporal de C01; usar intervalos semiabiertos
[vigente_desde, vigente_hasta) para evitar dos pertenencias por una misma frontera.
El tratamiento específico de muestras de transición se describe aparte.

La proyección Silver actual de asignaciones corresponde a episodios ZF; las
presencias ZC existen como decisiones del registro de asociaciones. No declarar
ambas zonas ya disponibles como esta tabla Gold ni confundir codigo_linea_zc,
que puede identificar una rueda compartida, con la máquina física Fenix.
La entrega deberá definir la publicación compatible de esas presencias y su
correspondencia física antes de derivarlas; no leer SQLite vivo desde Gold.

#### C02.2. Fuentes, cambios e invariantes

Consumir catálogo/composición, topología y asignaciones/decisiones aceptadas
en versiones compatibles. Incorporar la evidencia de D10.1 para la frontera ZC:
aproximación temporal y doble comprobación en todos los casos, con correcciones
trazadas cuando existan registros comprobados de la saliente. ZF utiliza las
fechas propias de esa zona, no las de ZC por sustitución.

- Nueva relación acreditada: insertar; cierre recibido: actualizar el mismo ID.
- Corrección aceptada: incrementar revisión y señalar producciones/ventanas
  afectadas para recalcular; no promover una alternativa todavía contradictoria.
- Ningún cambio relevante: no escribir. Silencio de Fenix no cierra vigencias.
- Comprobar producción existente, máquina/ramal/topología compatibles y extremos
  coherentes. Un nulo no es cero ni una fecha ficticia.
- No prohibir la coexistencia de producciones en diferentes partes tándem,
  ni el tránsito ZC/ZF de fabricaciones sucesivas. Dos asignaciones que permitan
  atribuir el mismo dato a fabricaciones incompatibles requieren resolución;
  no elegir la primera fila ni replicar el dato en ambas.
- La relación no contiene botellas ni contadores: compartir una rueda no duplica
  contadores físicos. El rendimiento común puede asociarse a ambas fabricaciones
  conforme al acuerdo, conservando una sola muestra física.

**Episodio temporal y atribución de una muestra no son lo mismo.** Las excepciones
aprobadas, como una muestra de diez minutos que cruza el cambio y corresponde a
la saliente, se conservan en el detalle/registro de atribución con su evidencia.
No desplazar toda una vigencia para acomodar una única muestra ni atribuir solo
comparando su hora final. Examinar el periodo representado y la regla aplicable.

#### C02.3. Publicación y contrato de consulta

Publicar con productions, topología y decisiones/evidencias compatibles; el
manifiesto fija sus versiones, reglas, ámbitos afectados y pendientes. Verificar
el commit antes de promover el puntero conjunto. La actualización de una relación
no exige esperar observaciones MIA ajenas a su determinación, ni acredita por sí
sola un score completo: mantener la separación de disponibilidades de D05.

La API consulta por producción, planta, línea, máquina, ramal, zona y periodo,
desde una publicación fijada para todas las uniones. Utiliza únicamente relaciones
confirmadas para la atribución automática, y aplica las decisiones específicas
de transición al detalle. No sumar capacidad ni datos físicos por el número de
ramales asociados. Una pendiente puede mostrarse como diagnóstico, nunca como
asignación confirmada utilizada silenciosamente en un score oficial.

#### C02.4. Recuperación, conservación y aceptación

Identificar la operación por entradas fijadas, contrato y transformación. Repetir
antes del commit; si existe commit sin acuse, reconocerlo y validar antes de
escribir otra vez. Si falta promoción, conservar visible la publicación anterior
y completar el conjunto compatible; si ya se promovió, recuperar el progreso
sin duplicar relaciones. Las correcciones pendientes no eliminan lo aceptado.

Conservar revisiones materiales, evidencia referenciada y versiones necesarias
para reconstruir las atribuciones anteriores, según 5.7. No confundir la vigencia
industrial con el momento en que se registró o publicó una corrección; el segundo
queda acreditado en versiones/decisiones. No copiar todo el origen por revisión.
Aplicar el futuro respaldo y ensayo de restauración del conjunto, no solo de
esta tabla aislada.

**Aceptación a verificar:** separación de ambas partes tándem incluso con igual
modelo; cierres sin cambio de ID; límites sin doble atribución; muestra de
transición saliente trazada sin distorsionar episodios; rueda compartida sin
duplicar contadores; nulos y contradicciones sin asignaciones inventadas;
recalculo de ventanas afectadas; recuperación sin duplicaciones; ninguna escritura
sin cambios ni limpieza automática. El siguiente contrato es production_capacity.

### 7.6. C03 — Contrato de production_capacity

**Estado:** aceptado por el usuario el 02/11/2026; pendientes ampliación mínima
de fuentes Silver, implementación y validación. Aplicar D03 sin cambiar las
fórmulas aprobadas ni inferir capacidad mediante presencia en MIA.

**Grano:** una fila por producción, máquina física/parte tándem y tramo de
capacidad asignada. No repetir por ramal, defecto, muestra, turno o publicación.
Esta tabla de cantidades no sustituye las relaciones para identificar una
cavidad concreta cuando una consulta lo requiera.

#### C03.1. Esquema e identidad

| Campo | Tipo lógico | Nulabilidad y significado |
|---|---|---|
| `id_capacidad` | STRING | Obligatorio; ID estable del tramo. |
| `id_produccion` | STRING | Obligatorio; producción del catálogo compatible. |
| `id_maquina_fenix` | BIGINT / int64 | Máquina física/parte tándem acreditada; nulo solo en histórico sin enlace y pendiente explícito. |
| `numero_secciones` | BIGINT / int64 | Secciones asignadas a esa producción; nulo si no están acreditadas. |
| `numero_gotas` | BIGINT / int64 | Cantidad resuelta mediante catálogo, no ID interno; nulo en pendiente. |
| `cavidades_asignadas` | BIGINT / int64 | Secciones × gotas; nulo si no se puede confirmar. |
| `vigente_desde` | TIMESTAMP, microsegundos | Inicio acreditado de aplicación de la capacidad. |
| `vigente_hasta` | TIMESTAMP, microsegundos | Final acreditado del tramo; admite nulo. |
| `estado_capacidad` | STRING controlado | Confirmada, pendiente o en revisión. |
| `version_regla` | STRING | Regla utilizada para obtener la capacidad. |
| `revision` | BIGINT / int64 | Revisión material del registro, no del sondeo/reintento. |
| `referencia_evidencia` | STRING | Referencia verificable a parámetros y configuración utilizados. |

Una fila confirmada requiere máquina, vigencia y cantidades válidas acreditadas;
valores pendientes no habilitan el denominador. Si no se puede delimitar siquiera
el tramo, registrar el pendiente en cobertura/calidad sin inventar vigente_desde.
Usar intervalos semiabiertos e interpretación temporal de C01/C02. Derivar la
identidad de producción y episodio estable de configuración, no del intento,
del final inicialmente nulo ni de un hash del contenido modificable. Completar
un cierre o corregir el mismo episodio conserva su ID y revisa su contenido.

#### C03.2. Fuentes, actualizaciones e invariantes

Origen: MP9_PARAM_MODELO.NUM_SECCIONES, vinculado mediante ID_MP9_CAMBIO_MODELO
a su producción; SIP_MAQUINA_MOLDEO.ID_NUM_GOTAS_SIMUL, resuelto mediante
SIP_NUMERO_GOTAS_SIMULTANEAS.VALOR. La ampliación mínima de Silver permanece
pendiente según D03; no afirmar que esos campos ya se publican ni elegir los
últimos parámetros de otra fabricación de la misma línea.

- Cambio real de capacidad: abrir otro tramo. Corrección aceptada: revisar el
  correspondiente e invalidar las ventanas afectadas. Cierre: mismo ID.
- Sin cambios relevantes: no escribir. Bloqueos temporales, avisos, rechazos,
  paradas temporales y ausencia de muestras no reducen la capacidad asignada.
- Respetar cada parte tándem: no asignar la suma de ambas a cada fabricación.
- La recepción del archivo no es la fecha del cambio. La vigencia se acredita
  desde fuentes y respeta la frontera operativa D10/D10.1; no anticipar o extender
  retrospectivamente una configuración sin evidencia.
- Comprobar cantidades positivas, producto secciones × gotas, extremos coherentes
  y ausencia de tramos contradictorios para un mismo ámbito físico. No sustituir
  ausencias por cero, capacidad total de máquina o cavidades observadas en MIA.
- Contradicción no aceptada: conservar evidencia, mantener lo aceptado y marcar
  su ámbito pendiente/en revisión; Gold no decide otra alternativa.

#### C03.3. Publicación y consulta

Confirmar producción, parámetros, topología y gotas en versiones compatibles;
fijarlas junto con regla/evidencia en el manifiesto y verificar el commit antes
de promover el puntero. Su disponibilidad no acredita un score completo: aplicar
D05. Si falta una cantidad necesaria, dejar pendiente el cálculo afectado, sin
total parcial ni sustituciones silenciosas.

Consultar por producción y periodo, con relaciones de la misma publicación.
Recortar tramos al periodo solicitado, inicio operativo y corte aplicable;
sumar cavidades_asignadas × minutos de cada tramo, sin multiplicar por ramales.
Ejemplo: 24 cavidades durante 60 minutos aportan 1.440 minutos-cavidad, aunque una
tenga un aviso durante parte de esa hora. El numerador A/R se calcula aparte con
D02/D14. No promediar capacidades ignorando las duraciones de sus tramos.

#### C03.4. Recuperación, conservación y aceptación

La operación idempotente fija entradas y transformación. Antes del commit,
repetirlas; después del commit sin acuse, acreditarlo y validar antes de reescribir.
Si falta promoción, mantener la publicación anterior y completar el conjunto;
si ya se promovió, recuperar progreso sin duplicar tramos ni cantidades. Una
corrección posterior genera trabajo explícito para las ventanas afectadas, sin
alterar a medias una operación en curso.

Conservar revisiones materiales, fuentes, decisiones y versiones referenciadas
según 5.7, incluyéndolas en respaldo/restauración del conjunto. Sin limpieza
automática, copias completas por sondeo ni controles con duplicados del negocio.

**Aceptación a verificar:** capacidad propia distinta de la total de máquina;
gotas por catálogo; bloqueos sin reducir denominador; partes tándem separadas;
cambios recortados por vigencia; cierres/correcciones sin otro ID del episodio;
ausencias sin cero ni inferencia MIA; consulta reproducible, recuperación sin
duplicaciones y ningún score parcial.

### 7.7. Inventario de los ocho bloques C04–C11

Tras C01–C03 se identificaron **ocho bloques de contrato, C04–C11**, dentro del
alcance ya previsto. Sus especificaciones se desarrollan en 7.9–7.16; ya no son
ocho bloques sin redactar. Un bloque puede contener varias tablas: cada una conservará esquema,
grano, clave y garantías propios según 7.3. Agrupar la discusión por dependencia
no autoriza una tabla genérica ni una implementación/PR enorme.

| Bloque especificado | Tablas o frontera | Qué define |
|---|---|---|
| C04 — Catálogos y configuración | reference_catalogs tipados y model_segment_catalog. | Códigos/equivalencias, criticidades, perfiles DAR, pesos/umbrales, aplicabilidad y versiones; esquema de segmentos con P01 pendiente de Calidad. |
| C05 — MIA: detalle y bases | mia_rejections, mia_inspections, inspection_bases y bases MIA por ventana. | Identidad/atribución, filtros antes del máximo, denominador D01/D15 y rechazos por defecto sin multiplicar inspeccionadas. |
| C06 — A/R y minutos-cavidad | ar_intervals y bases A/R por ventana. | Apertura/cierre originales, corte de abiertos, categorías/DAR, unión temporal D14 y enlace a capacidad. |
| C07 — ZC y rendimiento | zc_observations y bases ZC por ventana. | Una muestra física, rueda/teóricos, concurrencia y transición sin duplicar contadores. |
| C08 — Referencia estadística | Referencias MIA y composición versionada de su población. | Prioridad exclusiva de misma botella, segmento solo sin histórico propio, fabricaciones cerradas elegibles, granularidad estadística, media/sigma y actualización D06. |
| C09 — Scores y desglose | quality_scores y quality_score_components, enlazados al detalle explicativo. | Ventanas/turnos/acumulado, precisión, fórmulas/configuración, publicación conjunta del total y componentes, sin promediar scores ni completar ausencias con cero. |
| C10 — Publicación y recuperación conjunta | Manifiestos Silver→Gold y Gold→API, punteros y progreso compacto. | Esquema exacto, versiones compatibles, cobertura P02, ámbitos afectados, promoción/recuperación y protección de lectores según 5.3/5.6. |
| C11 — Consulta de la aplicación | Contratos API y matriz endpoint → datos/agregado válido. | Catálogo/fechas nulas, filtros, paginación, detalle, consultas habituales/particulares, caché versionada y equivalencia con resultados históricos. |

No reabrir reglas de negocio acordadas ni exigir otra ronda general de preguntas.
Proponer tipos y detalles desde el código real, usando ejemplos solo ante dudas
materiales. P01 no impide desarrollar los bloques independientes, pero no se
inventará un segmento para declarar calculable una producción afectada.
Concretar C10 en paralelo con los primeros contratos, antes de implementar su
consumo; el orden de esta lista no obliga a posponer ese diseño transversal.

Los ocho bloques son contratos de diseño, no ocho despliegues ni obligación de
esperar al último para escribir código independiente. Fijadas sus dependencias,
avanzar por E00/E02/E03 y entregas pequeñas verificables, sin publicar resultados
incompletos. Respaldo, permisos, dimensionamiento y ensayo de restauración siguen
en las entregas operativas previstas, sin añadir capas de tablas ni herramientas
por defecto. La secuencia ejecutable tras especificarlos se recoge en 7.17.

### 7.8. Convenciones técnicas comunes a C04–C11

**Especificación redactada el 02/11/2026 para revisión e implementación por
entregas.** Concreta las reglas acordadas; no declara nuevas tablas desplegadas
ni equivalencia numérica ya verificada. P01 y las ampliaciones Silver de D03/D10.1
siguen pendientes. No reabrir decisiones funcionales para resolver detalles de
tipado que puedan verificarse con las fuentes y el código.

En los esquemas siguientes, S = STRING, I = BIGINT/int64, F = DOUBLE/float64,
B = BOOLEAN, T = TIMESTAMP con microsegundos según C01 y D = DATE. El sufijo ?
indica nulo permitido; los demás campos son obligatorios. Contadores enteros
permanecen enteros; porcentajes se expresan en escala 0–100, pesos no son
porcentajes y duraciones se conservan como microsegundos enteros antes de convertir
a minutos. No introducir redondeos intermedios nuevos ni NaN/infinito. Reutilizar
el orden de cálculo/redondeo del motor actual para resultados equivalentes;
cualquier discrepancia se resuelve en E00/E09, no ajustando una tolerancia para
ocultarla. La precisión física no acredita más precisión de captura.

Cada tabla tiene clave explícita y contrato versionado. La publicación registra
URI relativa al almacén autorizado, identidad de tabla, versión Delta y schema_id.
Los identificadores y hashes usan serialización canónica versionada, orden estable
y distinción entre nulo, cero y vacío. No depender de mtime, orden de directorio,
ID de intento o rutas absolutas del equipo para definir identidades de negocio.

El contexto fuente común del detalle consiste en id_observacion:S, origen_fenix:S,
linea:S, referencia_origen:S. id_observacion se deriva de la clave Silver aceptada,
no de todos sus valores modificables. referencia_origen identifica fuente y clave;
la publicación fija su versión. Una corrección aceptada reemplaza el estado del
mismo registro lógico o del ámbito derivado afectado, sin perder las versiones
anteriores ni sumar valor viejo y nuevo. Si Silver considera una clave distinta
como otro registro, Gold no inventa una fusión correctora por su cuenta.

Para bases por ventana, K representa la clave compuesta explícita
(id_produccion:S, tipo_ventana:S, inicio:T, fin:T, corte:T, seleccion_id:S).
tipo_ventana = HOUR/SHIFT/PRODUCTION; fin es el extremo nominal o industrial
aplicable y corte el extremo efectivo calculado, con inicio < corte <= fin.
seleccion_id identifica filtros canónicos; el precálculo habitual usa una selección
sin filtros adicionales. tipo/fechas definen la ventana; el corte puede avanzar
sin crear otro punto horario visible. El ID estable del punto no incluye corte.
Una producción abierta usa un horizonte explícito de consulta, nunca un cierre
industrial ficticio. La publicación fija las versiones para reproducir K.

Los periodos continuos se recortan como [inicio, fin). Las muestras de diez minutos
representan el periodo que termina en FECHA: para una hora completa se incorporan
las muestras cuyo periodo pertenece a esa hora, sin contar dos veces la que
termina en su frontera. No repartir botellas de una muestra entre modelos ni
extrapolar fracciones no observadas. Probar específicamente fronteras horarias,
de turno y de producción frente al legado, separando las diferencias semánticas
ya acordadas de errores de implementación.

**Garantías comunes por tabla:** sin cambio material no escribir; cambios tardíos
invalidan ámbitos concretos; la operación fija entradas/contrato/configuración;
reconocer commits existentes antes de repetirlos; no exponer escrituras sin
promoción conjunta; reanudar promoción/acuse sin duplicar cantidades. Conservar
versiones, manifiestos y evidencias según 5.7/5.8, sin limpieza automática ni JSON
con copias completas del negocio en el control. Un pendiente se conserva en
cobertura; nunca se convierte en cero ni se descarta silenciosamente.

### 7.9. C04 — Catálogos de interpretación y configuración

**Fuentes verificadas:** SIP_MODELO/SIP_DEFECTO/SIP_DEFECTO_STATUS/SIP_DEFECTO_AR
aceptados en Silver; catálogos y normalizadores actuales de la app; configuración
customer_score.json y segment_dar_profiles.json. MARBLE y PowerPoint son referencias
externas, no un feed Fenix acreditado. Mantener P01 explícito.

#### C04.1. Esquemas tipados y claves

Cada fila de catálogo lleva catalog_version:S y referencia_evidencia:S; una
versión es inmutable. Las vigencias, cuando estén acreditadas, se expresan con
vigente_desde:T? y vigente_hasta:T?; si no existe fecha histórica fiable, no
atribuir una y registrar su aplicabilidad en la versión de configuración.

| Tabla | Columnas propias, además de las comunes | Clave dentro de catalog_version |
|---|---|---|
| defect_catalog | espacio_codigo:S, codigo:S, id_fenix:I?, descripcion:S, descripcion_corta:S?, categoria:S? | espacio_codigo, codigo |
| defect_code_mappings | espacio_origen:S, codigo_origen:S, espacio_destino:S, codigo_destino:S, regla:S | espacio_origen, codigo_origen, espacio_destino y vigencia aplicable |
| ar_status_catalog | id_status_fenix:I, codigo_status:I, descripcion:S, descripcion_corta:S?, codigo_origen:I, codigo_zona:I | codigo_status; validar asociación con id_status_fenix |
| ar_type_catalog | id_tipo_ar_fenix:I, codigo_tipo_ar:I, descripcion:S, codigo_agrupacion:I, zona_interpretada:S?, regla_zona:S? | codigo_tipo_ar; validar asociación con id_tipo_ar_fenix |
| dar_profile_members | profile_id:S, segmento_calidad:S, grupo_id:S, grupo_label:S, espacio_codigo:S, codigo:S, aplicabilidad_cierre:S? | profile_id, espacio_codigo, codigo |
| model_segment_catalog | codigo_modelo_botella:S, segmento_marble:S?, segmento_calidad:S?, profile_id:S?, tipo_cierre:S?, estado_correspondencia:S | codigo_modelo_botella y vigencia aplicable |

codigo es texto en los catálogos para preservar espacios de identificación;
convertir un código numérico de una observación a su representación canónica
sin modificar su significado. El espacio de códigos evita mezclar un ID interno,
un código A/R, un código MIA y un código legado numéricamente iguales. No suponer
que SIP_DEFECTO define por sí solo todos los defectos de MIA. Conservar cada
catálogo de origen y aprobar únicamente las equivalencias demostradas.

No crear una tabla EAV con parámetros numéricos y etiquetas mezclados en una
columna valor. La configuración de cálculo se publica como artefacto JSON
inmutable con modelo tipado estricto: config_id:S, schema_id:S, formula_version:S,
pesos, límites AQL, curva sigma MIA, política sigma cero, factor absoluto,
umbrales DAR, objetivos ZC y reglas de interpretación. Su descriptor contiene
hash y referencias de catálogos/perfiles; los valores se validan con esquema,
no se ejecuta código arbitrario procedente de JSON. Las listas de puntos de
curva y grupos son estructuras tipadas, no cadenas con JSON dentro de una tabla.

#### C04.2. Reglas, publicación y consulta

- Mantener pesos iniciales MIA=1 por defecto; Crítico=10, Grave=5, Mayor=3,
  Menor=2, Leve=2; DAR=8 y ZC=2. Mantener límites AQL 0,05/0,1/0,4/1/4 %, objetivo
  ZC inicial 95 % y divisor 2, salvo configuración histórica explícita distinta.
  Conservar las demás fórmulas/curvas actuales y las modificaciones D01–D15.
- DAR mantiene códigos 53/221/414/115/27 según perfiles y condiciones de D08.
  No convertir por segunda vez un código Fenix ya normalizado. La discrepancia
  de criticidad de Rechupe se conserva para revisión, sin elegir otra categoría
  silenciosamente ni impedir bloques independientes que no dependan de ella.
- TIPO=6 se interpreta ZF salvo evidencia contraria y TIPO=5 como ZF, conservando
  sus descripciones originales y la regla aprobada. STATUS conserva origen y zona.
- No heredar el default_profile_id del JSON actual para modelos desconocidos.
  P01 pendiente implica correspondencia pendiente, no vino ligero por defecto.
  Condiciones corcho/rosca requieren correspondencia acreditada.
- La configuración actual contiene infer_from_data, exclude_component y alcance
  all_available/exclude_current_production=false. Congelarlos para reproducir
  legacy_analytical_v1; la nueva configuración aplica D03, D05 y D06. No copiar
  esas políticas antiguas por omisión ni presentar el cambio como equivalencia
  con entradas semánticamente distintas.
- Nuevos códigos aceptados se insertan; cambios de descripción/regla/configuración
  crean versión, sin mutar una versión publicada. No fusionar códigos por etiqueta.
  Excluir NO USAR conforme al contrato Silver y conservar evidencia allí.

Publicar solo catálogos y configuración compatibles entre sí y con las fuentes
del ámbito. Validar claves, categorías conocidas, rangos, curva ordenada, pesos
no negativos y grupos DAR sin códigos ambiguos. No ignorar configuración inválida.
La API consulta la versión fijada por el resultado; un cambio no reinterpreta
automáticamente scores históricos. Consultas experimentales no modifican la
configuración oficial. Aplicar recuperación/retención de 7.8; una versión ya
confirmada no se vuelve a crear por reintentar su publicación.

**Aceptación:** códigos homónimos de fuentes distintas separados; códigos Fenix
sin doble conversión; modelo sin segmento sin default; configuración inválida
rechazada; perfil/segmento coherentes; reproducción de una versión anterior.

### 7.10. C05 — MIA: detalle, inspeccionadas y bases

#### C05.1. Esquemas y granularidad

En las dos tablas de detalle, conservar contexto fuente de 7.8 e incorporar
id_produccion:S, id_asignacion:S, periodo_inicio:T, periodo_fin:T,
ramal:I, codigo_maquina_inspeccion:I, molde:I, cavidad:S?,
seccion:S?, color:S? y regla_atribucion:S. Sección/color solo se completan desde
relaciones acreditadas; el CSV no los garantiza. Una muestra no atribuible queda
referenciada en cobertura/pendientes, sin asignar producción por proximidad.

| Tabla | Cantidades y columnas específicas | Clave / grano |
|---|---|---|
| mia_rejections | espacio_defecto:S, codigo_defecto:S, inspeccionadas_fuente:I, rechazos:I, rech1:I … rech10:I | id_observacion; fila LISTA_DEFECTOS aceptada y atribuida, sin sumar RECH1…10 a RECH |
| mia_inspections | inspeccionadas:I, rechazos_fuente:I, rechazos_auto_fuente:I, fecha_r:T? | id_observacion; fila LISTA_RECH aceptada con su identidad completa, sin colapsar máquinas/molde/cavidad antes de filtros |
| inspection_bases | id_base:S, id_produccion:S, linea:S, ramal:I, periodo_inicio:T, periodo_fin:T, seleccion_id:S, inspeccionadas:I | producción, línea, ramal, periodo y selección; máximo de INSPECC tras los filtros aplicables |
| mia_window_bases | K, espacio_defecto:S, codigo_defecto:S, rechazos:I, inspected_basis_id:S | K más defecto; referencia al denominador compartido, sin repetirlo como una cantidad sumable por defecto |
| mia_window_exposures | K, inspected_basis_id:S, inspeccionadas:I, muestras:I | K; suma de máximos compatibles, una exposición común para los defectos seleccionados |

mia_window_exposures es la representación física del denominador compartido de
las bases MIA ya previstas, no otro denominador ni una copia de todo el detalle.
inspected_basis_id identifica el ámbito de exposición; no usar SUM(DISTINCT
inspeccionadas), porque dos periodos diferentes con igual número deben sumarse.

#### C05.2. Derivación, cambios y consulta

Fuentes: LISTA_DEFECTOS y LISTA_RECH en versiones aceptadas, relaciones temporales
y catálogos C01/C02/C04. La clave completa Silver de defectos incluye origen,
fecha, molde, ramal, máquina, defecto, inspeccionadas y cavidad. La de rechazos
no incluye defecto porque esa columna no existe. No inventar otra deduplicación
que borre registros distintos ni sumar duplicados de archivos exportados.

El numerador usa RECH de LISTA_DEFECTOS. El denominador usa exclusivamente
INSPECC de LISTA_RECH: filtros físicos aplicables, máximo por producción/línea/
ramal/fecha de muestra y suma de esos máximos. El filtro por defecto selecciona
numerador, no elimina inspeccionadas por carecer LISTA_RECH de código de defecto.
Conservar suficiente detalle para recalcular el máximo cuando cambien los filtros.
No sumar bases de diferentes máquinas ni recurrir a LISTA_DEFECTOS si falta base.

Una corrección aceptada actualiza el detalle lógico y recalcula bases/ventanas
afectadas; no sumar el valor candidato al anterior. Las muestras de transición
siguen atribución saliente y evidencias acordadas, preservando su periodo original.
Las columnas RECH auxiliares se conservan para consulta, sin reinterpretarlas.

Publicar detalle y bases derivados del mismo vector compatible, con cobertura por
producción/periodo. Base ausente o no positiva no permite calcular una tasa por
división ni un score cero. Ausencia de una fila de defecto no prueba cero por sí
sola; se requiere cobertura del ámbito. El registro de muestras observado en el
legado se conserva al construir su referencia de equivalencia; cualquier cambio
de tratamiento de ceros debe identificarse y validarse, no introducirse oculto.

Consulta habitual: bases precalculadas. Selección física particular: detalle,
filtros y nuevo máximo antes de sumar. No presentar un filtro no acreditado como
aplicado. Recuperación y conservación de 7.8, con invalidación de todas las bases
afectadas por una corrección de cantidad o atribución, no del lago completo.

**Aceptación:** varias máquinas con 96/89 inspeccionadas producen máximo 96 para
el mismo grupo; otro ramal/periodo se suma; filtro de máquina cambia el máximo;
defectos múltiples no multiplican la base; ausencia de LISTA_RECH deja pendiente;
reexportación/reintento/corrección no duplica cantidades; RECH auxiliares intactos.

### 7.11. C06 — A/R y minutos-cavidad

#### C06.1. Esquemas y claves

ar_intervals lleva contexto fuente de 7.8 y las columnas siguientes:
id_tramo:S, id_produccion:S, id_asignacion:S?, codigo_ar_original:I,
espacio_defecto:S, codigo_defecto:S, fecha_on:T, fecha_off:T?, maquina_is:I,
cavidad:S?, status_original:I, tipo_original:I, modelo_original:S,
color:S?, proceso:S?, molde:I, id_agrupacion:I?, zona:S,
categoria:S?, grupo_dar:S?, regla_atribucion:S. Clave: id_tramo derivado de la
clave Silver completa, que excluye FECHA_OFF pero no los otros campos de identidad.
ID_Agrupación no es clave única; dos tramos propios se conservan separados.

ar_window_bases lleva K, ambito:S (CATEGORY o DAR_GROUP), codigo_ambito:S,
tiempo_afectado_us:I, capacity_basis_id:S, eventos:I. Clave K + ámbito/código.
ar_window_exposures lleva K, capacity_basis_id:S, tiempo_disponible_us:I.
Es la exposición compartida de las bases A/R previstas; no sumarla por categoría.
El microsegundo-cavidad cuenta una cavidad durante un microsegundo; minutos =
cantidad / 60.000.000. Registrar rechazos por desbordamiento, no truncar enteros.

#### C06.2. Cálculo, publicación y consulta

Fuentes: LISTA_AR, códigos/STATUS/TIPO, atribución aceptada y capacidad C03.
Preservar apertura/cierre y la interpretación origen/tipo/zona, sin sustituir
la descripción original. Categoria o cavidad sin resolución no se inventan;
registrar el impedimento para los componentes que las necesitan.

Para calcular, fin efectivo = mínimo de cierre recibido, corte y límite aplicable
de la producción. Si FECHA_OFF es nula, usar el corte sin escribirlo como cierre.
Recortar por ventana e integrar capacidad asignada de cada tramo, contando el
tránsito ZC→ZF conforme a D10. Unión temporal por producción/cavidad/categoría;
para un mismo conjunto DAR, unir las afectaciones de sus códigos en cada cavidad
antes de computar su exposición. Conservar detalle de pertenencia y eventos
originales; no sumar indiscriminadamente tiempos que se solapan en la misma base.
No implementar prioridades para el solape entre categorías excluido por el usuario.

FECHA_OFF recibida completa el mismo tramo; relecturas antiguas no lo reabren.
Corregir cierres/atribución/categoría aceptados recalcula sus ventanas. Un evento
abierto puede cambiar bases al avanzar el corte aunque no llegue otro CSV:
Gold planifica también ese trabajo temporal con fuentes/cobertura confirmadas,
sin depender únicamente de un nuevo commit de filas Silver.

Publicar con versión de capacidad, atribuciones, catálogos y corte. La ausencia
de eventos equivale a cero afectación solo con cobertura aceptada P02; no prueba
capacidad ni reemplaza un catálogo desconocido. La API devuelve eventos originales
y, separadamente, minutos recortados para el corte consultado. Base/precalculado
solo si selección compatible; filtros que no puedan aplicarse se rechazan.
Recuperación/retención de 7.8, sin duplicar intervalos al reanudar.

**Aceptación:** aviso abierto 10:15 consultado 11:00 aporta 45 minutos; cierre
tardío corrige sin otro evento; 10:00–10:30 y 10:20–10:50 aportan 50 minutos;
dos tramos con igual agrupación permanecen íntegros; capacidad de cavidad bloqueada
permanece; avance de corte sin nueva fila actualiza la afectación calculada.

### 7.12. C07 — ZC y rendimiento

#### C07.1. Esquemas y claves

zc_observations conserva contexto fuente de 7.8 más id_maquina_contador:I,
periodo_inicio:T, periodo_fin:T, rueda:I, teorico_a:I, teorico_b:I,
teorico_total:I, salida_maquina_is:I. Clave: id_observacion derivado de origen,
MAQ_IS y FECHA. teorico_total = TEORICO_A + TEORICO_B; no añadir ruedas ni
teóricos inventados. MAQ_IS del contador no se interpreta automáticamente como
el ID de una parte tándem. Los restantes contadores originales quedan disponibles
en Silver; se proyectan a Gold solo si una consulta real los necesita.

zc_window_bases contiene id_base_zc:S, origen_fenix:S, id_maquina_contador:I,
tipo_ventana:S, inicio:T, fin:T, corte:T, seleccion_id:S,
rueda:I, teoricos:I, muestras:I. Clave: máquina física de contador, ventana,
selección y corte calculado. La relación producción→base se resuelve mediante
las presencias/atribuciones aceptadas; no copiar contadores por producción.

#### C07.2. Cálculo, publicación y consulta

Fuente CONT_ZC aceptada, atribuciones ZC y configuración C04. Calcular
100 × SUM(RUEDA) / SUM(TEORICO_A + TEORICO_B), no media de porcentajes ni de scores.
Teóricos cero no equivale a rendimiento cero calculable. No extrapolar cantidad
desde velocidad ni asumir que ausencia de archivos equivale a cero producción.

Una rueda compartida conserva una muestra física: el mismo rendimiento puede
asociarse a las producciones concurrentes acreditadas, sin repartir botellas ni
sumar dos veces esa muestra cuando se comparen o agreguen ambas. Si una ventana
cruza el cambio, aplicar la regla saliente y doble comprobación acordadas;
conservar el periodo completo de la muestra y evidencia de su atribución.
Una selección particular de producción requiere sumar muestras únicas compatibles,
no sumar bases de ventanas superpuestas o bases por producción duplicadas.

Publicar versión CONT_ZC, presencias/atribución y configuración. Reemplazos de la
hora repetida siguen la política Silver de segundo registro aceptado; Gold no
resucita ambos ni abre otra política de resolución. Cambios aceptados recalculan
ventanas y producciones vinculadas. La API muestra rendimiento de máquina como
tal; no promete rendimiento medido por cavidad o por botella de una tándem.
Recuperación/retención de 7.8, una sola identidad física tras reintentos.

**Aceptación:** misma rueda y dos producciones sin duplicar contador; suma de
cantidades antes de tasa; teóricos cero pendiente; transición saliente;
corrección de hora repetida sin doble muestra; selección conjunta sin doble suma.

### 7.13. C08 — Referencias estadísticas MIA

#### C08.1. Esquemas, población y claves

| Tabla | Columnas | Clave |
|---|---|---|
| mia_reference_sets | reference_id:S, nivel:S MODEL/SEGMENT, valor:S, seleccion_id:S, reference_rule_id:S, catalog_version:S, estado:S | reference_id, hash canónico de población/regla/filtros y versiones de valores usados |
| mia_reference_members | reference_id:S, id_produccion:S, rates_version:S | reference_id, id_produccion |
| mia_reference_values | reference_id:S, espacio_defecto:S, codigo_defecto:S, muestras:I, media_percent:F?, sigma_percent:F?, estado:S | reference_id, espacio_defecto, codigo_defecto |

rates_version identifica las cantidades de la fabricación de referencia leídas
en una publicación concreta, no la última versión al ejecutar otra consulta.
Una muestra estadística es la tasa agregada por fabricación y defecto con base
válida; no cada registro de diez minutos ni cada turno. Mantener la semántica
observada de generación de tasas del motor legado en su baseline; no insertar
ceros en la población sin cobertura o para conseguir un número de muestras.

#### C08.2. Construcción, actualización y consulta

Solo fabricaciones cerradas, validadas y con cantidades calculables son elegibles.
Si existe histórico propio elegible, usar exclusivamente el mismo modelo; no
mezclar segmento ni recurrir a él porque no se alcance min_samples. Si no existe
histórico propio, usar el segmento confirmado; P01 pendiente no es segmento por
defecto. Excluir fabricaciones abiertas y auto-referencia indebida del objetivo.
La población de cada consulta y exclusiones quedan explícitas en reference_id.

Calcular media aritmética de tasas por fabricación y sigma muestral con n−1,
como _sample_sigma actual; min_samples inicial 3. Sigma cero conserva la política
actual fija de 5 si supera la media, y 0 en caso contrario. Una referencia no
calculable se declara pendiente, no se sustituye por media/sigma cero. No omitir
silenciosamente un componente requerido para conseguir total.

Una nueva fabricación cerrada elegible crea otra versión de referencia y puede
actualizar resultados de fabricaciones aún abiertas. No cambiar automáticamente
resultados históricos publicados de fabricaciones cerradas. Una corrección de
datos o reclasificación aceptada genera una revisión explícita con ámbitos
afectados y conserva la publicación anterior; no cambiar membresía a escondidas.

Publicar miembros y estadísticos juntos con tasas, catálogos y reglas compatibles.
Filtros que cambien población o tasas requieren referencia compatible propia,
no reutilizar estadísticos globales. La API devuelve reference_id, nivel,
muestras y reglas; detalle de miembros mediante consulta paginada, sin copiar
la lista completa en cada respuesta de score. Recuperación de 7.8; versiones
inmutables reconocidas al reintentar, sin duplicar miembros ni contar otra muestra.

**Aceptación:** misma botella gana a segmento; una/dos muestras no activan fallback;
fabricación abierta excluida; sigma cero reproducible; cierre nuevo actualiza
referencia de abiertas sin alterar resultados cerrados; filtros invalidan caché.

### 7.14. C09 — Scores, componentes y explicación

#### C09.1. Esquemas y claves

quality_scores contiene id_resultado:S, K, config_id:S, formula_version:S,
reference_id:S?, total_penalty:F, normalized_score:F, evaluated_weight:F,
estado_interpretacion:S, fecha_calculo:T. Clave lógica de resultado: producción,
tipo/inicio/fin de ventana, selección y configuración. id_resultado es estable
para ese punto; corte y revisiones cambian en nuevas versiones Delta. La versión
consultada/published_id permite reproducir cada revisión, no se requiere insertar
otra fila visible por actualización de la hora parcial.

quality_score_components contiene id_resultado:S, componente:S, score:F,
peso:F, contribucion:F, basis_reference:S. Clave: resultado + componente;
componentes MIA, categorías A/R, DAR y ZC conforme al motor y configuración.
basis_reference enlaza las bases y el desglose, no una descripción JSON gigante.

Detalle explicativo tipado para no perder los campos que la app actual muestra:

| Tabla | Columnas además de id_resultado:S | Clave adicional |
|---|---|---|
| mia_score_details | espacio_defecto:S, codigo_defecto:S, rechazos:I, inspeccionadas:I, current_percent:F, historical_mean_percent:F?, historical_sigma_percent:F?, z_score:F?, score:F, peso:F, contribucion:F, muestras:I | espacio/código de defecto |
| aql_score_details | categoria:S, affected_cavity_minutes:F, total_cavity_minutes:F, exposure_percent:F, aql_percent:F, ratio:F?, score:F, peso:F, contribucion:F, top_defect_code:S? | categoría |
| dar_score_details | grupo_id:S, eventos:I, affected_minutes:F, share_percent:F, exposure_percent:F, score:F, threshold_rule_id:S | grupo DAR; códigos desde perfil compatible |
| zc_score_details | target_percent:F, actual_percent:F, loss_points:F, score:F, peso:F, contribucion:F, muestras:I, basis_reference:S | un resumen ZC por resultado |

Son salidas explicativas ya previstas en el desglose, no cálculos alternativos.
Etiquetas proceden de catálogos compatibles. Causas principales se derivan de
contribuciones y reglas de empate actuales; no requieren otra copia de tabla.

#### C09.2. Cálculo, publicación y consulta

Entrada: bases C05/C06/C07, capacidad, referencia C08 y configuración C04 de un
vector compatible. Fórmulas compartidas con API, conservando curvas, pesos,
normalización, factor absoluto MIA y umbrales DAR acordados. El hecho de que un
JSON legado permita excluir un componente no autoriza total parcial en Gold.
Estado de disponibilidad/cobertura separado de Bueno/Observado/Malo.

Precalcular siete horas completas y octava parcial, turnos M/T/N (06–14, 14–22,
22–06 del día operativo de inicio) y acumulado de producción. Aplicar retraso
de una hora, corte efectivo y duración real de D04. Los periodos acumulados y
turnos se calculan sobre cantidades base propias, no desde scores horarios.
Los demás intervalos soportados por la app se resuelven según C11.

Una ventana solo entra en quality_scores y su desglose oficial cuando todos los
componentes requeridos son calculables con el mismo corte. Cobertura conserva
la espera y su motivo; mantener el último resultado completo con su fecha/corte
reales, sin presentarlo como recién calculado. Si una corrección aceptada invalida
la publicación anterior, advertirlo y no servir ese resultado como válido actual.
Total y detalle se promueven juntos; campos nulos no se convierten en ceros.

Publicar revisiones por datos/corte/referencia/configuración relevantes; no
recalcular cada 30 segundos sin cambio o nuevo corte aplicable. Las referencias
nuevas actualizan fabricaciones abiertas conforme a D06, sin reescribir cerradas
por defecto. Ensayos de configuración son consultas distintas, no publicaciones
oficiales. Recuperación de 7.8; un commit de total sin detalle no autoriza promover
su puntero, y un acuse perdido no duplica puntos horarios.

**Aceptación:** total y sumatorio de contribuciones consistentes según redondeo
del motor; números exactos por hora; instante parcial con duración correcta;
turno/acumulado desde bases; faltante sin parcial; legado reproducible para
reglas equivalentes y diferencias D01–D15 explicadas separadamente.

### 7.15. C10 — Publicación, cobertura y recuperación conjunta

#### C10.1. Contrato de documentos y estado de periodos

Dos manifiestos inmutables, entrada Silver→Gold y salida Gold→API, con punteros
current.json independientes. Formato JSON validado y versionado; no copiar filas.

| Estructura | Campos tipados mínimos |
|---|---|
| Cabecera | schema_id:S, publication_id:S, environment:S, plant:S, created_at:T, previous_publication_id:S?, producer_version:S, manifest_hash:S |
| Referencia de tabla | name:S, table_id:S, uri:S relativa, delta_version:I, schema_id:S, operation_id:S, receipt_reference:S |
| Dependencia derivada | publication_id:S, rule_version:S, input_versions:MAP[S,I], artifact_references:LIST[S], estado:S |
| Ámbito cambiado | production_id:S?, source:S, origin:S?, period_start:T?, period_end:T?, change_kind:S, reference:S |
| Puntero | schema_id:S, generation:I, publication_id:S, manifest_uri:S, manifest_hash:S |

No fijar created_at local de Fenix como instante UTC: los instantes técnicos de
publicación se generan en UTC con zona explícita, separados de las fechas
industriales locales C01. El hash del manifiesto usa representación canónica que
excluye su propio campo manifest_hash. Las versiones de tablas no tienen que
coincidir numéricamente: su compatibilidad la demuestra el vector de dependencias.

La cobertura extensa se publica como tabla Delta, no millones de entradas JSON:
publication_scope_status con scope_id:S, id_produccion:S?, source:S, origin:S?,
period_start:T?, period_end:T?, estado:S, reason_code:S?, evidence_reference:S?,
last_successful_discovery:T?, confirmed_source_version:I?,
pending_files:I?, earliest_pending_period:T?. Clave scope_id, derivada del ámbito
estable fuente/origen/producción/periodo; la versión del manifiesto fija esta tabla.
Estados controlados: READY, WAITING_INPUT, WAITING_DEPENDENCY, REVIEW, UNAVAILABLE.
Una incidencia no localizada queda en ámbito superior y no se declara inocua.

#### C10.2. Cuándo calcular y qué confirma una publicación

Silver acredita commit con recibo e identidad de operación; después publica
fuentes y derivados confirmados. Gold lee el pequeño puntero cada 30 segundos,
fija las entradas y usa ámbitos cambiados acumulados desde su último acuse.
Puede agrupar publicaciones, pero no perder cambios intermedios. Una lectura
correcta sin commit nuevo puede actualizar cobertura sin reescribir el negocio.

P02: no esperar señal externa inexistente. READY operativo requiere descubrimiento
accesible/estable, revisiones conocidas del ámbito confirmadas o contabilizadas,
dependencias y diccionarios compatibles, sin fallos/cola conocida que impidan
ese periodo. Archivos sin novedades permiten reutilizar estado aceptado, no
inventar muestras, parada o cierre. No exigir que toda la cola de toda la planta
sea cero para publicar un periodo independiente. Sin localizar el alcance de
un pendiente no acreditar completitud por suposición.

El reloj también puede activar trabajo por hora/corte/A/R abierto según C06/C09,
con evidencia operativa suficiente, aunque no cambie una tabla de filas Silver.
Una entrada tardía actualiza cobertura/ámbitos y genera revisión; no se descarta
por superar un watermark. Las notificaciones futuras son activadores, no prueba
de completitud; no Kafka inicial.

#### C10.3. Máquina de estados, lectores y recuperación

Una operación compacta guarda operation_id, input_publication_id, config/rule IDs,
ámbitos/referencia de particiones de trabajo, receipts y estado
PREPARED → COMMITTED → VERIFIED → PUBLISHED → ACKNOWLEDGED. No guarda todas las
filas ni snapshots JSON duplicados. operation_id deriva de entradas y objetivo,
no de cada intento. Un escritor autorizado por entorno para promoción, bloqueo
de instancia con lease/heartbeat y comprobación de generación esperada.

No hay transacción global entre tablas Delta. Escribir/reconocer commits de cada
tabla, validar claves/esquemas/integridad/disponibilidad de archivos, persistir
manifiesto y promover puntero atómicamente dentro del mismo filesystem soportado.
Verificar esa capacidad con los volúmenes Windows/WSL2 reales antes del piloto;
no asumir atomicidad de un movimiento entre volúmenes ni de un objeto remoto.
Si se cambia de almacenamiento, usar su actualización condicional equivalente.

- Fallo antes de commit: repetir con entradas fijadas y sin declarar escritura.
- Algunos commits realizados: reconocerlos mediante recibos, completar solo los
  ausentes y no promover aún. Publicaciones anteriores siguen legibles.
- Commit sin recibo local: reconstruir confirmación desde metadatos de operación
  Delta; si no puede acreditarse, bloquear esa operación para revisión técnica,
  no repetir append a ciegas.
- Verificado sin promoción: promover el conjunto. Promovido sin acuse: reconocer
  publication_id/generación y completar progreso, sin recalcular todo.
- Corrección posterior: nueva operación/versiones; no mutar un manifiesto visible.

La API fija una publicación durante toda la petición; trabajos largos referencian
explícitamente esa publicación. En la fase sin limpieza no hacen falta escrituras
de leases de lectura desde la API. Una futura limpieza exigirá un mecanismo de
protección de lectores/publicaciones antes de habilitarse; no otorgar escritura
a la API sobre Silver por esa necesidad. Aplicar respaldo 5.8 al conjunto de
manifiestos, archivos/versiones referenciados, estado y decisiones coherentes.

**Aceptación:** fallo en cada frontera, commit sin acuse, promoción sin progreso,
publicaciones agrupadas sin cambios perdidos, cola localizada sin bloqueo global,
cadencia sin novedades con abierto, lectura fija durante otra promoción y
restauración aislada. Comprobar un caso por frontera, sin repetir baterías sin causa.

### 7.16. C11 — Consulta y compatibilidad de la aplicación

#### C11.1. Contrato de petición/respuesta

Conservar la funcionalidad actual y adaptar sus modelos por entregas pequeñas:
no exigir al frontend rutas Parquet ni identidad de archivos de control. Toda
respuesta analítica identifica publication_id:S, schema_id:S, config_id:S?,
reference_id:S?, effective_cutoff:T?, availability:S y motivos tipados de espera;
los valores de score oficiales solo aparecen si están completos. La respuesta
puede incluir el último resultado válido con su propio corte/publicación y marca
de antigüedad, sin combinarlo con componentes recientes de otro cálculo.

La petición acepta selección de producciones/ramales, métrica, ventana y filtros
actuales, además de periodo/publication_id opcionales para consulta reproducible.
Catálogo incorpora inicio/fin industrial nullable y límites observados separados;
end_day legado no representa cierre. Labels/códigos canónicos se adaptan sin
cambiar los ID de producción aceptados. La API solo lee Gold/manifiestos.

| Funcionalidad actual | Fuente principal Gold | Resolución |
|---|---|---|
| Catálogo de líneas/producciones/ramales y filtros | C01/C02/C04 y cobertura/detalle | Buscar por modelo sin conocer fecha; paginar y respetar nulos/vigencias. |
| Tendencias MIA y filtros físicos | C05 | Agregado si selección coincide; detalle y máximo recalculado si cambia. |
| Eventos A/R sobre gráficas | C06 | Apertura/cierre original y duración efectiva al corte separados. |
| Score principal, turnos y acumulado | C09 | Resultados y desglose de la misma publicación; nunca total parcial. |
| Desglose por defecto/categoría/DAR/ZC | C09, bases y catálogos asociados | Números exactos, nombres interpretados y evidencia/base enlazable. |
| Pesos/umbrales alternativos | C04 y bases compatibles | Motor común en consulta; caché separada, sin escribir resultado oficial. |
| Comparaciones, estadística, gráficos y análisis de calidad actuales | C05/C06/C07 y referencias compatibles | Cálculo al consultar cuando no exista agregado válido, conservando opciones y semilla actuales. |
| Salud y preparación | Manifiesto/cobertura y recursos de API | Diferenciar proceso vivo, almacén disponible y datos calculables. |

No sustituir la exposición nominal/proxy declarada de los módulos estadísticos
actuales por inspeccionadas reales sin versionar y validar el cambio: no todos
los indicadores de la app son el score de salud de serie. Los contratos cubren
las entradas para esos módulos, no prometen igualdad cambiando su denominador.

#### C11.2. Filtros, caché y límites

Publicar capacidades por selección: filtro admitido y fuente de su dimensión.
Color/sección/cavidad sin correspondencia acreditada no se inventan; rechazar
de forma explicable la selección incompatible, sin ignorar el filtro ni excluir
silenciosamente datos. Filtros opcionales futuros de cavidad no cambian el carácter
compartido del rendimiento ZC y no obligan a incorporarlos en la pantalla principal.

Definir límites configurables de producciones, periodo, puntos, concurrencia y
coste; reutilizar los existentes y medir antes de aumentarlos. Catálogo/detalle
con cursor estable y desempate por ID bajo publication_id fijado; un cursor no
mezcla páginas de publicaciones distintas. No imponer paginación que trunque
una estadística: el motor usa su selección completa con presupuesto controlado.

Caché = endpoint + publicación + selección canónica + configuración + referencia
+ versión del motor; incluir corte de cálculo para operaciones dependientes del
tiempo. No TTL como única invalidación ni resultados experimentales en caché
oficial. El frontend consulta novedades cada 30 segundos; solo recarga resultados
cuando cambie su versión/disponibilidad, mostrando la hora de datos efectiva.

Adaptar respuestas/esquemas sin romper clientes inadvertidamente; si un cambio
de nulabilidad/forma resulta incompatible, introducir versión API y migrar el
frontend junto con pruebas de integración. Una publicación antigua solicitada
no disponible devuelve error explícito, no la actual como sustituta silenciosa.

#### C11.3. Verificación, recuperación y puesta en marcha

Preparar corpus real E00 con score, desglose, tasas/contadores, turnos, A/R,
estadística y filtros representativos. Comparar primero bases exactas y después
resultados/redondeos del motor. Mantener separadas las diferencias aprobadas
D01–D15, referencias nuevas y errores. Una coincidencia solo del total no prueba
equivalencia: comprobar componentes y cantidades intermedias.

API/frontend sin estado industrial persistente: reinicio recupera última
publicación confirmada, caché descartable, clientes conservan selección estable.
Reversión de imagen no implica revertir tablas: comprobar compatibilidad de
contrato/lector antes de cambiar el contenedor. Si requiere otra publicación,
activar una compatible validada sin borrar la nueva. Fallo Gold no obliga a
detener ingesta, ni API/frontend conceden permisos de escritura en sus datos.

**Aceptación:** misma funcionalidad histórica con reglas equivalentes; score
completo por hora/turno/producción; producción abierta sin fin inventado;
filtro real aplicado; misma publicación en todas las uniones/páginas;
actualización visible tras nueva publicación; reinicio/reversión sin pérdida.

### 7.17. Cierre documental y paso a implementación

C04–C11 quedan redactados como especificaciones técnicas revisables, apoyadas en
el código actual y los acuerdos. No quedan bloques de los ocho sin propuesta,
pero su redacción no demuestra implementación ni validación en servidor. P01
sigue pendiente de Calidad; faltan incorporar fuentes/campos Silver de capacidad
y evidencia ZC y verificar compatibilidad temporal, redondeos y corpus E00.

El siguiente trabajo ejecutable, cuando se inicie la implementación, es E00:
congelar casos reales y resultados de referencia; después E02/E03, esquemas y
lector de publicación confirmada, con pruebas de recuperación acotadas. A
continuación contexto/catálogos, proyecciones, bases, motor/resultados y adaptación
API/frontend en entregas pequeñas. C10 se implementa desde el principio, no al
final de las tablas. No desplegar ni modificar la ingesta por esta edición del
documento, ni iniciar servicios Gold que publiquen resultados parciales.

Fuentes locales contrastadas para esta especificación: config/customer_score.json,
config/segment_dar_profiles.json; backend/app/domain/models.py y customer_score.py;
backend/app/schemas/api.py y customer_score.py; repositorios customer_score.py,
segment_dar_profiles.py y catalog.py. En Ingesta_Delta_Lake: contratos de
lista_defectos, lista_rech, lista_ar, cont_zc, status y tipos_ar. Los valores de
configuración mencionados son los observados el 02/11/2026; su hash/commit
reproducible se fija en E00, no se deduce de la fecha del documento.

### 7.18. Ejecución local de los puntos 1–5, 02/11/2026

**Autorización:** implementar referencia, lector, tablas, cálculos y validación
local. No conectar la aplicación ni probar/desplegar en el servidor hasta
comprobar los puntos anteriores. No se han realizado cambios en GitHub/Actions
ni en contenedores o tablas del servidor.

Código: backend/app/gold, fórmulas compartidas en backend/app/domain/score_math.py,
pruebas en backend/tests/gold y guía ejecutable en gold/README.md. El extra gold
y uv.lock fijan el SDK Delta 1.6.6 y sus dependencias. La app sigue utilizando
su recorrido histórico. Su única refactorización es la extracción de doce
definiciones escalares y el orden determinista MIA; no se ha sustituido la API.

| Punto | Evidencia obtenida / estado |
|---|---|
| 1 — Referencia real | Cuatro consultas de AV23/AV32, incluyendo máquinas presentes: 254 archivos y unos 17 MB por corpus. Entradas, configuración, resultados y cantidades intermedias con SHA-256. Corpus operativo legacy-v3-real-filters. |
| 2 — Lectura confirmada | UUID, versión, esquema, operación/recibo y archivos comprobados; lectura fijada aunque avancen fuentes. Manifiesto de réplica Fenix con diecinueve tablas. El consumidor no consulta SQLite. |
| 3 — Tablas locales | Réplica de las cinco líneas mediante la ingesta existente, en destino nuevo. Contexto/capacidad/asignaciones, catálogos, proyecciones MIA/A/R/ZC, bases, referencias y disponibilidad; publicación Gold local versionada. Parámetros/gotas/evidencia ZC se incorporan mediante un adaptador auxiliar offline, sin modificar el worker activo. |
| 4 — Cálculo | MAX antes de SUM y filtros antes del máximo; unión de A/R/abiertos/capacidad; rueda única; población y fórmulas compartidas; total y desglose completos o pendiente explícito. Modelo/segmento desconocido no recibe perfil por defecto. |
| 5 — Validación | Regresión histórica completa del corpus reproducible y comparación independiente MIA con Gold: cantidades y campos numéricos por defecto coinciden exactamente. Pruebas de fórmulas, fallos/reintentos, sustitución acotada, lectura antigua y restauración aislada. La equivalencia integral de todos los componentes sobre nuevas fabricaciones NO queda acreditada por estas comprobaciones parciales. |

**Hallazgo de referencia:** el orden no estable de los grupos MIA del legado
cambiaba etiquetas empatadas y podía variar una última cifra decimal en el
promedio de un componente. Se conserva el corpus inicial sin modificar y se
fija orden por producción/defecto antes de calcular y desempatar. No se cambia
la curva, ponderación ni fórmula para ocultar una discrepancia. Los corpus v2/v3
registran el recorrido estabilizado; v3 añade filtros de máquinas con datos.

La prueba real MIA verifica AV23_PROD_001 con 377.919 inspeccionadas sin filtro
y 164.365 con filtro; AV32_PROD_001 con 1.361.604 y 1.233.440, respectivamente.
Se comparan diecinueve/ocho/doce/seis defectos, sus tasas y campos numéricos MIA.
La población all_available del legado se usa EXCLUSIVAMENTE en ese oráculo de
validación, no se aplica por defecto a referencias oficiales nuevas bajo D06.

**Límites explícitos antes de considerar cerrado el punto 5:**

- P01 sigue pendiente de Calidad; la instantánea no contiene el histórico de
  observaciones de todas las fabricaciones antiguas que sí constan en MP9.
  No puede fabricarse una referencia estadística suficiente a partir del catálogo.
- Disponibilidad de cantidades intermedias no equivale a score calculable.
  Las tablas de scores/desglose pueden existir vacías, con pendientes por periodo,
  en lugar de generar totales parciales o referencias ficticias.
- Las fuentes auxiliares están implementadas para la réplica aislada, no en el
  protocolo continuo del servidor. El exportador declara offline_snapshot, limita
  filas antes de cargar atribuciones y conserva cobertura temporal observada;
  no certifica periodos anteriores a esa captura ni futuras entregas Fenix.
- El comando watch rechaza el snapshot offline. Antes del piloto continuo falta
  integrar la exportación periódica en la ingesta y planificar la lectura por
  origen/periodo de réplicas que excedan el presupuesto local. El escritor ya
  admite sustitución por ámbito y actualiza solo filas distintas; no prometer
  escalabilidad ilimitada de la primera orquestación local.
- Comparación MIA exacta y tests escalares no sustituyen la conciliación integral
  A/R/DAR/ZC de nuevas producciones con datos/configuraciones completos. No
  declarar E09 terminado ni autorizar el punto 6 mediante un PASS parcial.
- La restauración local comprueba archivos/versiones/publicaciones; no se ha
  configurado Restic ni un destino independiente ni medido un RPO de producción.

Resultados detallados en INFORME_VALIDACION_LOCAL_GOLD.md y evidencias industriales
en var/gold_validation, excluidas de Git. La prohibición de limpieza de 5.7 sigue
vigente; no se han eliminado fuentes, versiones ni controles activos.

## 8. Plan de implementación por entregas pequeñas

Una entrega tendrá un propósito verificable, revisión acotada y evidencia
proporcionada al cambio. No se acumulará todo en una única PR. Los resultados
de una comprobación no se repetirán sin cambios o una causa que lo justifique.

### 8.1. Prioridades de prácticas profesionales y demostración inmediata

**Fecha de decisión:** 06/11/2026. **Objetivo de demostración:** 07/11/2026.
La siguiente matriz prioriza el trabajo; no certifica que esté implementado.
El estado de partida sigue siendo el de 7.18 y del informe de validación local.

- **A — Ahora / demostración:** trabajo acotado imprescindible para enseñar un
  recorrido real y repetible con el stack y los contratos actuales.
- **B — Próxima iteración / antes del piloto o producción:** requisitos de
  corrección, operación y entrega que siguen siendo necesarios aunque la
  aplicación no escale. No posponerlos como si solo afectaran al crecimiento.
- **C — Condicionado a escala o nueva infraestructura:** incorporar únicamente
  cuando una necesidad medida o la plataforma elegida lo justifique.

#### 8.1.1. Matriz de selección y alcance

| Práctica o tecnología | Prioridad | Qué se hace y criterio para ampliarlo |
| --- | --- | --- |
| Contratos, grano y reglas industriales | A | Mantener claves, tipos, bases independientes, atribuciones y D01–D15. Documentar el caso demostrado; no esperar al esquema dimensional completo para evitar duplicidades. |
| Referencia reproducible y datos reales | A | Fijar entradas, configuración, corte y resultados esperados. Registrar la revisión del código y las modificaciones locales; el commit base no identifica por sí solo los archivos aún no versionados. |
| pytest, regresión y pruebas de integración | A | Ejecutar las comprobaciones pertinentes al recorrido demostrado, incluidos fallos/reintentos del publicador local. Conservar alcance, resultado y exclusiones; un conjunto dirigido no acredita toda la cobertura del proyecto. |
| CI/CD y dependencias Gold | A | Preparar un cambio pequeño que instale explícitamente el extra gold y ejecute sus pruebas. Reproducir en un entorno limpio. Un YAML escrito o tests locales no equivalen a un workflow remoto ejecutado. |
| Publicación coherente, idempotencia y recuperación | A | Utilizar los mecanismos existentes de versiones fijadas, manifiesto, bloqueo y promoción. Verificar repetición sin cambios y lectura de la publicación anterior; pruebas de fallo solo en destinos aislados. |
| Aplicación demostrable y explicabilidad | A | Verificar una consulta y su presentación con fuentes disponibles. Identificar origen del resultado, corte y limitaciones; mantener total/desglose completos conforme a D05. |
| Observabilidad mínima | A | Guardar identificadores de petición/publicación, resultado de validación, tiempos y estado de disponibilidad. Reutilizar logs, informes y métricas existentes. |
| Seguridad y aislamiento de la demostración | A | Lectura de datos industriales, configuración y secretos fuera de Git, destinos de prueba separados y acceso local/controlado. No alterar la ingesta del servidor para preparar la demostración. |
| Kimball y dimensiones conformadas en Gold | B | Aplicar 4.3/D16 primero a MIA/inspeccionadas, mediante contratos y PRs separados. La formalización completa no bloquea la demostración del recorrido actual validado. |
| SCD e historización dimensional selectiva | B | Concretar versiones, vigencias, enlaces temporales y correcciones conforme a D07/D08/D16. Mantener la historia existente desde A; no rehacer todos los catálogos por una sola entrega. |
| Conciliación integral del nuevo score | B | Completar MIA/A/R/DAR/ZC con bases, referencias y perfiles aceptados antes de activar resultados oficiales Gold. Resolver P01 por Calidad; nunca utilizar un perfil supuesto. |
| Planificación incremental y exportación periódica | B | Completar por origen/periodo antes del piloto continuo; preservar límites de filas, memoria e idempotencia. No habilitar watch sobre el snapshot offline. |
| Empaquetado del publicador y despliegue reproducible | B | Imagen con dependencias Gold, servicios separados, versiones identificadas y rollback en aislamiento. Ampliar a servidor tras los criterios de E09/E12–E15. |
| Contratos y controles de calidad en ejecución | A/B | A: conservar Arrow, validaciones y pendientes explícitos. B: ampliar conciliaciones y aprovechar Great Expectations de supervisión si aporta controles faltantes, sin duplicar todas las reglas en otra herramienta. |
| SOLID, tipado, formato y revisión | A/B | A: cambios pequeños y coherentes con las capas existentes. B: extracción gradual de responsabilidades y comprobaciones de tipos/formato en CI. No iniciar una refactorización general para la demostración. |
| Versiones de aplicación, contratos y fórmulas | A/B | A: identificar exactamente lo demostrado. B: política de compatibilidad y releases diferenciadas para código, esquema, configuración y cálculo, sin confundir Git con versionado semántico. |
| Rendimiento, lectura selectiva y memoria | A/B | A: medir el caso de demostración. B: medir carga representativa y concurrencia; revisar planes Polars, filtros/columnas y lotes antes de aumentar recursos. |
| Particionado y mantenimiento Delta | B/C | B: inventariar archivos y costes de lectura. C: cambiar particionado o compactación si las mediciones lo justifican. No activar las limpiezas prohibidas en 5.7. |
| Prometheus/Grafana y alertas de dato | B | Integrar Gold en la supervisión existente; distinguir recepción, aceptación Silver, publicación Gold y dato visible. Añadir histogramas y objetivos medidos antes de operación continuada. |
| Backup independiente, restauración e identidad corporativa | B | Completar respaldo fuera del servidor, ensayo de recuperación, HTTPS/proxy y roles antes de la exposición/puesta en servicio correspondiente. La restauración local no acredita recuperación ante pérdida del servidor. |
| Orquestador DAG: Prefect, Airflow o ADF | C | Elegir uno si las dependencias, reintentos o backfills superan la planificación acotada actual. Mantener A/B con las herramientas existentes; evitar duplicar el control de ejecución. |
| dbt | C | Evaluar al disponer de un motor/adaptador compatible y transformaciones SQL que justifiquen su uso. No añadir otro motor solo para introducir dbt. |
| Spark/PySpark | C | Incorporar cuando volumen, memoria o plazo de proceso medidos requieran distribución. Las proyecciones hipotéticas multi-planta no son un benchmark de la carga actual. |
| Kafka/Event Hubs y streaming por eventos | C | Evaluar ante fuentes/eventos o requisitos de latencia nuevos. Conservar la cadencia y el protocolo de 5.3/5.6 en la integración inicial. |
| Terraform y módulos de infraestructura | C | Aplicar al ampliar infraestructura o entornos Azure, con recursos y responsabilidad de despliegue definidos. Docker/configuración actuales bastan para la demostración; Terraform no sustituye las pruebas de la aplicación. |
| Kubernetes, sharding y optimización distribuida del skew | C | Justificar por operación multi-nodo y límites medidos; no incorporarlos al despliegue inicial de tres servicios. |
| OpenTelemetry, OpenLineage o plataforma corporativa de observabilidad | C | Añadir trazas o catálogo transversal cuando existan recorridos que lo necesiten. Conservar desde A el linaje de manifiestos; métricas/trazas no sustituyen procedencia y versiones de datos. |
| Data Vault, ORC/Iceberg, Datadog o CloudFormation | C / decisión independiente | Sin necesidad actual demostrada. Mantener Delta/Parquet y la supervisión existente. Revisar integración de fuentes, interoperabilidad o plataforma corporativa; CloudFormation solo encajaría con infraestructura AWS. |

#### 8.1.2. Alcance funcional que se puede preparar para el 07/11/2026

Preparar una demostración acotada con dos recorridos expresamente identificados:

1. **Aplicación actual:** consulta histórica de una producción real con datos
   y configuración comprobados; ranking/detalle y componentes disponibles en el
   recorrido de referencia. Contrastar la respuesta de la API y la pantalla.
2. **Gold local:** inspección de una publicación aislada, sus fuentes/versiones,
   cantidades y estados; comparación MIA con el corpus real y repetición de la
   publicación sin cambios. Mostrar la evidencia técnica en consola/informe,
   sin convertir cantidades intermedias en un score público parcial.

Este alcance es una demostración funcional de la aplicación y del trabajo Gold
local, no una conexión Gold→API ya completada. El recorrido de 7.18 mantiene
P01 y la conciliación integral pendientes; no conectar el lector nuevo ni
presentar resultados oficiales nuevos hasta cumplir sus condiciones.

Antes de la presentación, una entrega pequeña documentará:

- Entorno y comandos de arranque efectivamente ensayados; rutas/configuración
  externas y producción seleccionada. Verificar que los datos sigan disponibles.
- Referencia de código, entradas/configuración, corte y resultados esperados.
  Si se demuestra una copia de trabajo, conservar su huella además del commit.
- Resultado de las pruebas pertinentes y comprobación de una consulta real.
- Publicación Gold legible y repetición sin duplicados; estado y motivo de lo
  no calculable, sin inventar valores para completar el ejemplo.
- Procedimiento para volver a la aplicación/publicación anterior sin borrar
  datos. Conservar un informe de la última ejecución correcta como evidencia,
  identificado como tal si se consulta durante una incidencia de la demostración.

Si falta un dato o una validación, recortar explícitamente el alcance a lo
comprobado. No prometer que el 07/11 estarán terminados todos los bloques B ni
dar por alcanzado el plazo mediante el mero cambio de este documento.

#### 8.1.3. Orden de cambios acotados tras fijar la demostración

1. **Dependencias/CI:** instalación reproducible de gold, selección de pruebas y
   comprobaciones actuales; corregir los fallos observados antes de ampliar.
2. **Contratos y conciliación:** completar las entradas aceptadas del caso
   integral y sus comparaciones. P01 y otras carencias siguen visibles.
3. **Incremental y operación:** exportación periódica, planificación por ámbito,
   recuperación y lector de entradas confirmadas; pruebas antes de piloto.
4. **Modelo dimensional:** primer modelo MIA y dimensiones mínimas de D16;
   continuar A/R y ZC en PRs distintas y conservar fórmulas/referencias.
5. **Consumo y despliegue:** adaptar API, imágenes y supervisión una vez
   acreditada la equivalencia; probar permisos, recursos, recuperación y usuarios
   conforme a E09–E15. La demo local no elimina esos requisitos.

El orden concreto respeta las dependencias E00–E15: observabilidad, seguridad y
pruebas acompañan cada cambio; no son una fase final. Una necesidad de contrato
dimensional previa al incremental se resuelve en E02 sin esperar al paso 4.

### E00. Congelar la referencia analítica

- Registrar commit de la app, dependencias, fuentes, referencias y configuración.
- Elegir casos reales representativos: ambas líneas históricas, turnos,
  extremos, datos ausentes y selecciones parciales.
- Capturar salidas y cantidades intermedias en almacenamiento de validación
  separado; los datos industriales no se suben a Git ni a artefactos públicos.

**Aceptación:** corpus reproducible, identidades/hashes y comparación automática
de resultados; ninguna fórmula modificada. Depende del acceso a los datos locales.

### E01. Resolver contratos analíticos prioritarios

- Aplicar D01–D08 de Q01–Q08, D06.1 y D10–D15; registrar el alcance futuro de D09.
- Aplicar D14 dentro de categoría, sin implementar el caso entre categorías
  descartado en Q03-S. Validar la
  evidencia de inicio de D10 con ejemplos reales y su impacto numérico.
- Aplicar D06.1 al catálogo y validar las correspondencias iniciales de segmentos.
- Implementar el contrato de población de D06 con prioridad exclusiva de modelo
  y segmento solo en ausencia de histórico propio. Validar la compatibilidad de
  filtros existentes según D09, sin exigir nuevos filtros de detalle.
- Aplicar D15 a la base MIA única y al estado pendiente cuando falte una base
  válida, sin respaldo alternativo ni publicación parcial.
- Registrar contratos de compatibilidad y cambios semánticos aprobados.

**Aceptación:** decisiones suficientes para cada componente que vaya a
implementarse. Lo no aprobado permanece bloqueado, sin impedir trabajo técnico
independiente. Esta entrega puede dividirse en decisiones individuales.

### E02. Definir publicación de entrada y esquemas Gold

- Especificar campos, tipos, nulos, claves, granularidad y restricciones.
- Aplicar D16/4.3 por familias: roles de hechos/dimensiones, grano, cardinalidad,
  aditividad, claves/versiones y compatibilidad de las proyecciones dimensionales.
- Desarrollar los contratos de 5.1 por tablas/familias pequeñas, comenzando por
  `productions`; distinguir relaciones, detalle, cantidades base y resultados.
  Fijar qué filtros admite cada agregado sin alterar máximos, solapes o referencias.
- Definir el contrato modelo–segmento y sus evidencias/versiones conforme a
  D06.1, con candidatos pendientes separados del catálogo aceptado.
- Diseñar manifiestos de versiones fuente y Gold, cobertura y dependencias.
- Aplicar 5.3: distinguir escritura Silver, disponibilidad operativa y certificación
  del origen. Concretar los metadatos de P02 ya aprobado sin señal externa, sin
  esperar un marcador ni etiquetar errores técnicos como ausencia de novedades.
- Definir el manifiesto inmutable/puntero de entrada, ámbitos afectados y lecturas
  compatibles; mantenerlo separado de los resúmenes de supervisión y del puntero Gold.
- Aplicar 5.6: activación configurable inicialmente cada 30 segundos, sin Kafka,
  con progreso durable compacto y mecanismo de aviso separado del cálculo.
- Especificar la ampliación mínima de fuentes de capacidad acordada en D03 y
  su contrato versionado; no darla por disponible en Silver hasta implementarla.
- Definir la lectura confirmada de las evidencias necesarias para D10.1,
  incluida `MP9_LISTA_RECH_ZC`, sus correspondencias vigentes y los resultados
  de ambas comprobaciones; no dar esas fuentes por incorporadas al contrato actual.
- Identificar qué parte del contrato de atribuciones necesita una exportación
  estable desde la ingesta; no duplicar su registro completo en JSON de control.
- Fijar límites de operación, política de retención y protocolo de lectura.

**Aceptación:** ejemplos y validadores de contrato; incompatibilidades claras y
sin creación de tablas productivas. Depende de E00 y decisiones aplicables de E01.

### E03. Crear el lector de entradas confirmadas

- Leer Delta y atribuciones por versiones fijadas y en solo lectura.
- Verificar integridad, entorno, planta y compatibilidad del descriptor.
- Comprobar el puntero cada 30 segundos sin escanear CSV ni tablas para detectar
  novedades; distinguir escrituras confirmadas de disponibilidad por producción/periodo.
- Mantener lotes y memoria limitados; no cargar todo el lago para descubrir cambios.

**Aceptación:** lee una instantánea coherente, detecta faltantes y soporta que la
ingesta continúe publicando. No escribe en Silver ni consulta SQLite desde la API.
Depende de E02.

### E04. Preparar catálogo y referencias

- Construir el catálogo Gold con identidad estable, partes tándem y fechas separadas.
- Formalizar las dimensiones compartidas de 4.3 sin fusionar IDs de distintas
  plantas/espacios de códigos; implementar SCD selectiva y sus relaciones
  temporales en entregas independientes conforme a D07/D08/D16.
- Preparar las relaciones vigentes y capacidad de 5.1.1 sin multiplicar la fila
  de producción por cada ramal/cavidad ni excluir cavidades temporalmente bloqueadas.
- Incorporar la frontera operativa contrastada de D10 y su evidencia sin
  sustituir las fechas industriales de entrada/salida del catálogo.
- Implementar D10.1 para toda atribución ZC afectada: propuesta temporal y
  contraste obligatorio con configuración vigente, excepciones automáticas
  justificadas y ausencia de evidencia identificada sin falsa confirmación.
- Preparar el catálogo modelo–segmento de D06.1 en una entrega acotada: extracción
  mínima desde MARBLE 25, validación de equivalencias, resolución de genéricos y
  publicación versionada. En otra entrega, enriquecer las producciones con sus
  segmentos aceptados, sin mezclar vino ligero y premium ni aplicar defaults.
- Versionar referencias de Calidad y correspondencias históricas verificadas.
- Aplicar D12 a la correspondencia histórico–Fenix: IDs originales conservados,
  relaciones demostrables y candidatos ambiguos para revisión, sin fusionar
  fabricaciones por modelo o proximidad temporal.
- Aplicar D07 a equivalencias y criticidades: fecha de entrada en vigor,
  referencias fijadas por cálculo y conservación de publicaciones anteriores.
- Preparar los perfiles y características de D08 con códigos/fuentes explícitos,
  condiciones por cierre y atributos de producto verificados; validar las
  correspondencias en una entrega separada de su incorporación al cálculo.
- Preservar desconocidos y advertencias sin asociar por proximidad o nombre.
- Aplicar D11: conservar las reclamaciones históricas identificadas y declarar
  la falta de información en nuevas fabricaciones, sin generar etiquetas positivas
  ni incorporar ahora una nueva fuente automática de reclamaciones.

**Aceptación:** catálogo consistente y trazable para casos históricos, abiertos
y concurrentes. Depende de E03 y Q07/Q08/Q12; puede dividirse en catálogo y referencias.

### E05. Preparar hechos MIA e inspeccionadas

- Transformar observaciones aceptadas y aplicar atribuciones confirmadas.
- Implementar D01: máximo por producción/línea/ramal/fecha-hora y suma de máximos
  para el intervalo, con actualización de las bases afectadas por correcciones.
- Aplicar D15: denominador únicamente de LISTA_RECH; no usar inspeccionadas de
  LISTA_DEFECTOS como respaldo y dejar pendiente MIA si no hay una base válida.
- Conservar filtros, identidades y trazabilidad, sin repetir denominadores por defecto.

**Aceptación:** numeradores/denominadores conciliados por producción y ventana;
replay sin duplicados y diferencias históricas explicadas. Depende de Q01/Q09/Q10.

### E06. Preparar A/R y ZC

- Implementar D02: computar A/R abiertos hasta el corte, con atribución fiable,
  recorte por ventana y resultado provisional, sin completar el cierre industrial.
- Aplicar D10/D10.1 al inicio del denominador: frontera operativa ZC contrastada
  y revisable, con su aproximación identificada e incluyendo el tránsito hasta
  ZF, sin presentar la fecha declarada como un arranque físico medido.
- Recalcular las ventanas afectadas al recibir el cierre, sin duplicar minutos
  provisionales y definitivos, preservando las versiones anteriores.
- Implementar D03: capacidad por producción y gotas, con bloqueos temporales
  incluidos en el denominador. Aplicar el acuerdo de solapes de Q03-S y las
  reglas de normalización aprobadas.
- Resolver ZC mediante correspondencias verificadas y rueda física compartida.

**Aceptación:** conciliación de eventos, minutos, teóricos y rueda; actualización
de cierres sin duplicar tramos ni contadores. Depende de Q02/Q03/Q03-S/Q07/Q10 y E03/E04.
Se implementará en dos PRs si A/R y ZC requieren cambios independientes.

### E07. Materializar agregados para consulta

- Generar cantidades base por las ventanas aprobadas, con cobertura y versiones.
- Mantener el grano y la aditividad de 4.3: bases independientes, dimensiones
  compatibles y combinación de hechos sin multiplicar cantidades ni exposición.
- Aplicar 5.1.3/5.1.4: bases por familia, referencias estadísticas, scores y
  componentes habituales por hora, turno y producción; mantener trazabilidad
  hacia el detalle sin copiar toda la tabla en cada publicación.
- Implementar D13: agregar cantidades base y calcular después los componentes
  y total horarios; no sumar ni promediar scores de intervalos menores.
- Aplicar D04 al punto horario parcial, con duración efectiva, actualización por
  publicación y cierre de hora; no extrapolar el resultado a sesenta minutos.
- Aplicar D05: los cálculos intermedios permanecen internos hasta que el total
  y todos sus componentes estén listos para la misma ventana y corte.
- Preservar rutas de detalle/filtros y referencias de cálculo.
- Recalcular solo ámbitos afectados, incluyendo dependencias estadísticas.

**Aceptación:** resultado igual a calcular sobre los hechos de la misma
instantánea; no hay medias de porcentajes ni sumas de scores incompatibles.
Depende de E05/E06 y Q04–Q06/Q13.

### E08. Publicación atómica y recuperación del publicador

- Implementar el progreso compacto, operaciones idempotentes y promoción del manifiesto.
- Completar los casos de fallo de 5.5; mantener deshabilitada la limpieza en
  datos reales según 5.7 y ensayar cualquier política futura solo en aislamiento.
- Verificar 5.6: sin novedades no se escribe; reinicio con publicaciones pendientes,
  llegada de cambios durante un cálculo y agrupación sin perder ámbitos intermedios.
- Mantener presupuestos de memoria, staging y número de lotes por vuelta.

**Aceptación:** reinicio en cada frontera crítica sin pérdidas, duplicados ni
publicaciones mixtas; crecimiento de controles/material temporal medido y acotado.
Depende de E02/E03 y se completa con los hechos/agregados de E04–E07.

### E09. Validar la equivalencia del recorrido completo

- Comparar el recorrido histórico y el recorrido Gold con las mismas entradas.
- Validar D16/4.3: unicidad, cardinalidades, versiones dimensionales, ausencia
  de multiplicación de denominadores/contadores y correcciones tardías sin
  alterar la reproducción de publicaciones previas.
- Contrastar total, componentes, causas, denominadores, turnos, filtros y warnings.
- Separar fallos de transformación, diferencias de precisión y cambios semánticos.
- Validar D13 con conciliaciones de cantidades base y cálculo horario directo;
  las medias o sumas de scores menores no son un oráculo de equivalencia.
- Validar 5.3/5.6: entrega incompleta, fuentes sin novedades con confirmación,
  dependencias a ritmos distintos, corrección tardía y pendientes limitados a
  su ámbito; ningún total parcial ni falsa cobertura por cola vacía o timestamp.

**Aceptación:** ninguna diferencia no aprobada en los casos de referencia.
La validación no se reduce al score total: dos errores pueden compensarse.
Depende de E00 y del publicador funcional.

### E10. Adaptar la API mediante repositorios de lectura

- Añadir lector Gold compatible y selección de publicación por consulta.
- Aplicar 5.1.4: leer resultados habituales y recalcular consultas particulares
  desde detalle Gold cuando el agregado no sea compatible con la selección.
  Ensayos de parámetros y cachés no modifican los resultados oficiales.
- Mantener fórmulas y endpoints existentes; versionar ampliaciones del contrato.
- Incorporar intervalos/cortes y metadatos de cobertura y reproducibilidad.
- Invalidar correctamente cachés de cálculo, filtros y catálogos.

Se dividirá como mínimo en cambios revisables de contrato/repositorios, conexión
del lector Gold y metadatos/cachés. Actualmente parte del cálculo vive dentro de
`PolarsCustomerScoreRepository`: separar una lectura no autoriza a reescribir
simultáneamente sus fórmulas. Cualquier extracción del cálculo se hará por
funciones o componentes, conservando el recorrido anterior y su comparación
automática en cada paso.

**Aceptación:** respuesta equivalente sobre Gold, lectura concurrente coherente
y ausencia de escrituras en datos/controles. El lector histórico se mantiene
disponible para validación/rollback durante la transición. Depende de E09.

### E11. Incorporar tiempo real y mesa de control

- Renovar catálogo/datos mediante versión y polling configurable.
- Exponer los ocho puntos de D04 (siete horas completas y una parcial) y turnos
  históricos con el desglose del mismo punto. Comprobar nuevas versiones cada
  30 segundos, conservando valor y corte efectivo mientras no haya novedades.
- Integrar la referencia visual aprobada y conservar consultas históricas.
- Aplicar D05: mostrar juntos total y desglose completos, conservando la última
  versión durante la espera. Diferenciar hora parcial de score incompleto y
  mostrar el corte real sin presentar ausencia como cero.

**Aceptación:** llegada de datos, cambio/cierre de producción y cambio de
selección se reflejan sin cachés obsoletas; las cifras coinciden con la API para
la misma publicación. Depende de E10 y acuerdos temporales pendientes.

### E12. Empaquetar los tres servicios

- Aprovechar Dockerfiles/Compose existentes y añadir la imagen del publicador.
- Fijar herramientas/imágenes y lockfiles; revisar compatibilidad del lector
  Delta con el Python elegido para la API antes de cambiar versiones.
- Añadir exclusiones de build, rutas portables, usuarios, permisos, mounts,
  readiness, logs y parada ordenada.
- Separar configuración, secretos, datos, staging y estado de cada entorno.

**Aceptación:** construcción y ejecución local/aislada sin rutas personales,
sin datos industriales en las imágenes y sin modificar los volúmenes de origen.
Depende de los contratos; la preparación técnica puede empezar antes de E11.

### E13. Medir y dimensionar el conjunto

- Medir producción individual, línea, comparaciones y usuarios concurrentes.
- Incluir consultas frías/distintas y consultas repetidas; una caché caliente
  no demuestra la capacidad general del sistema.
- Medir simultáneamente ingesta, publicación Gold y consultas: RAM, CPU, I/O,
  duración, colas, frescura y tamaño de tablas/controles/staging.

**Aceptación:** presupuesto acordado de latencia, recursos y recuperación;
ausencia de deterioro inaceptable de la ingesta. Depende de E12 y datos representativos.

### E14. Preparar CI/CD y desplegar un piloto aislado

- Preparar desde la prioridad A de 8.1 la instalación explícita del extra gold
  y sus pruebas, sin esperar al piloto. Comprobar un entorno limpio y no confundir
  el workflow configurado con su ejecución remota satisfactoria.
- Empaquetar el publicador con sus dependencias/runtime; la imagen histórica
  del backend sin el extra no acredita capacidad para ejecutar Gold.
- Reutilizar el runner autohospedado y la estrategia de imágenes inmutables,
  sin depender de minutos disponibles de runners alojados en GitHub.
- Mantener validaciones proporcionadas y no repetir una batería sin cambio relevante.
- Desplegar `develop` con Gold/control/staging propios; el piloto puede leer
  Silver confirmado con permisos de lectura, sin competir por sus escrituras.
- Conservar `main` separado, promover la imagen probada y documentar rollback
  de aplicación y publicación sin borrar datos.

**Aceptación:** versión activa identificada, permisos correctos, actualización
controlada y rollback probado en el entorno aislado. No se despliega nada por
la aprobación de este documento; requiere la fase de ejecución correspondiente.

### E15. Aceptación y puesta en servicio

- Validar con Calidad consultas históricas y casos reales de producción en curso.
- Verificar reinicio de Windows/WSL2/Docker, arranque de los servicios y recuperación.
- Integrar métricas relevantes con la supervisión existente y entregar el runbook.

**Aceptación:** criterios analíticos y operativos firmados, sin diferencias
no justificadas y con limitaciones conocidas visibles. No se declara producción
por tener un contenedor iniciado o un healthcheck verde. Depende de E14.

## 9. Organización de código y despliegue a concretar en ejecución

Se conservarán las capas existentes de dominio, aplicación, API e infraestructura.
El publicador tendrá un punto de entrada propio y módulos de lectura Silver,
transformación, publicación y recuperación. Podrá vivir en el mismo repositorio
si permite builds y despliegues independientes; la decisión de repositorio se
cerrará antes de implementar, sin duplicar el motor de score.

La configuración declarativa incluirá imágenes por digest, variables no secretas,
referencias a secretos, mounts, redes, healthchecks y recursos. Los scripts no
contendrán rutas personales ni credenciales. Esto permite incorporar posteriormente
Ansible/Terraform sin convertir esa incorporación en requisito de la integración.

Los datos persistentes estarán fuera de los checkouts y de las capas de imagen.
Se separarán al menos Silver de entrada, Gold, staging, progreso del publicador
y logs. La ruta exacta/volumen se fijará según los mounts disponibles en el servidor.
Un entorno no podrá adoptar por defecto el almacenamiento de otro.

La compatibilidad de una versión se declarará para esquema Gold, protocolo Delta,
contrato de publicación y versión analítica. Un cambio incompatible se preparará
sin promoverlo hasta tener lectores compatibles y un rollback definido.

## 10. Seguridad, recursos y supervisión mínima

### 10.1. Acceso y operación

- Frontend/API tras el acceso HTTPS y la identidad corporativa acordados.
- El proxy confiable eliminará cabeceras de identidad del cliente y establecerá
  las autorizadas; Nginx por sí solo no acredita identidad corporativa.
- Puertos internos restringidos; endpoint de métricas con acceso de operación.
- Usuario no privilegiado, datos de consulta de solo lectura y secretos externos.
- Readiness compatible con el arranque autenticado y con la publicación elegida.
- Interrupción del publicador: última publicación válida disponible, con su edad
  visible. Interrupción de API/frontend: la ingesta puede continuar.

### 10.2. Presupuesto inicial de medición

El servidor dispone de 16 GB de RAM; el límite configurado de un contenedor no
equivale a consumo real ni garantiza que todos quepan simultáneamente en WSL2.
Se comprobará su límite global y se reservará margen para Windows y caché de disco.

| Componente | Punto de partida para medir, no dimensionamiento aprobado |
| --- | --- |
| API | Un worker, una consulta pesada simultánea; contrastar el límite actual de 2 GB. |
| Frontend | Contrastar el límite actual de 256 MB. |
| Publicador | Lotes acotados y límite propio obtenido por medición; prioridad compatible con la ingesta. |
| Ingesta/supervisión | Mantener sus presupuestos separados y medir el conjunto antes de aumentarlos. |

Dos workers con dos cálculos permitidos por proceso pueden ejecutar cuatro
cálculos y duplicar cachés. Esa combinación no se habilitará sin medición.

### 10.3. Indicadores mínimos

- Publicación Silver consumida y Gold visible; edad de la publicación por ámbito.
- Ventanas completas, provisionales y bloqueadas, con motivo y filas afectadas.
- Cantidades conciliadas y divergencias contra la referencia.
- Duración de preparación, ámbitos pendientes y reintentos.
- CPU/RAM/I/O y tamaños de Gold, staging y controles.
- Latencia de API, errores, saturación, timeouts y aciertos de caché.

Los dashboards y alertas se integrarán con las herramientas ya existentes. Los
logs operativos no sustituirán la trazabilidad de datos ni reproducirán todas
las filas procesadas.

## 11. Cierre y siguiente paso

D17, acordada el 06/11/2026, prioriza la demostración del 07/11/2026 y clasifica
las prácticas profesionales en 8.1. Primero se prepara un recorrido real y
reproducible; los requisitos B siguen siendo obligatorios antes del piloto o
producción aunque no haya escalado. Las tecnologías C requieren justificación.
Esta actualización documental no declara ejecutada ni aceptada la demostración.

D16, acordada el 05/11/2026, incorpora la formalización dimensional Kimball en
Gold y la historización selectiva de 4.3. Se desarrolla dentro de las entregas
existentes, sin rehacer Bronze/Silver ni introducir Data Vault. La aprobación
documental no acredita su implementación o validación completa.

El orden inicial es **E00 → decisiones prioritarias de E01 → E02 → E03**.
Después se implementan hechos, agregados y publicación por entregas, y se acredita
la equivalencia antes de activar el nuevo recorrido de consulta.

Q01, Q02, la capacidad de Q03, Q04, la publicación de Q05 y los criterios de
incorporación/actualización de Q06 quedan resueltos mediante D01–D06. El criterio
de selección es exclusivamente mismo modelo si existe histórico propio; solo
en su ausencia se utiliza el segmento acreditado. D06.1
aprueba el catálogo obligatorio modelo–segmento, la separación de Vino Ligero y
Vino premium y MARBLE 25 como fuente inicial parcial; quedan su implementación,
validación y completado de correspondencias.
Q07 queda resuelta mediante D07: versiones con entrada en vigor, aplicación a
nuevos cálculos de fabricaciones abiertas/nuevas y recálculo histórico únicamente
explícito. Q08 queda resuelta mediante D06.1/D08: catálogo modelo–segmento,
perfiles por segmento y aplicabilidad basados en la presentación, con códigos y
fuentes verificados y sin sustitución automática de fórmulas/umbrales. Queda
implementar y validar las correspondencias y atributos de producto. Q09 queda
registrada mediante D09 como posible análisis de detalle, sin implementar ahora
un nuevo filtro de cavidad. Q10 registra mediante D10 la frontera temporal
derivada de FECHA_ON como aproximación del inicio operativo ZC y del denominador,
revisable con evidencia inequívoca de la saliente, sin presentarla como un
instante físico medido y conservando las fechas industriales originales.
D10.1 añade la
doble comprobación temporal y física obligatoria en todos los casos de atribución
ZC, con prioridad de correspondencias comprobadas y trazabilidad del resultado.
Su implementación y la incorporación de las evidencias siguen pendientes.
Q11 queda acordada
mediante D11: histórico conocido conservado y nuevas fabricaciones sin información
de reclamaciones hasta disponer de una fuente actualizada. Q12 queda acordada
mediante D12: correspondencias demostrables e identidades originales conservadas,
con reglas que se validarán progresivamente sobre fabricaciones reales. Q13
queda acordada mediante D13: cantidades base agregadas antes de calcular el score
horario, sin sumar ni promediar scores de muestras menores.
Q03-S registra mediante D14 la unión temporal dentro de la misma categoría con
eventos originales intactos y la exclusión del caso entre categorías distintas
confirmada por el usuario. D15 completa Q01/Q05: denominador MIA solo de LISTA_RECH,
sin respaldo desde LISTA_DEFECTOS y con cálculo pendiente si falta una base válida.
Los criterios de la ronda de preguntas quedan recogidos; los detalles de población,
contratos, correspondencias y evidencias aún deben concretarse y validarse en sus
entregas. No se declara terminada la implementación por haber acordado estos criterios.
La aceptación de la arquitectura Gold y de los tres
contenedores no constituye una respuesta implícita a las preguntas restantes.

El 30/10/2026 queda acordado el funcionamiento Silver→Gold de 5.3 y 5.6:
confirmaciones separadas, manifiesto de entrada, versiones compatibles y
cobertura por ámbito; consulta del puntero cada 30 segundos, sin Kafka inicial,
con progreso durable, recuperación y activación desacoplada del cálculo. El
02/11/2026 P02 queda resuelto mediante el modo operativo sin señal externa
autorizado en 5.3.2; falta concretar sus metadatos e implementarlo. P01 conserva
pendiente de recepción de Calidad el catálogo de segmentos; tampoco se
dan por cerrados los esquemas tipados, retenciones o contratos de consulta
que se definirán en las siguientes entregas.

También queda aprobada la organización de 5.1 en tres bloques internos de Gold:
identificación/relaciones/reglas, detalle aceptado preparado para consulta y
cantidades/resultados precalculados. Los usos habituales de la pantalla, turnos
y resumen de producción se materializan; las selecciones particulares se calculan
desde Gold con el mismo motor y caché versionada. La referencia estadística MIA
se distingue de los diccionarios de interpretación. El contrato C01 de productions
queda aprobado el 02/11/2026 en 7.4, pendiente de implementación y validación.
El contrato C02 de production_assignments queda aceptado inicialmente en 7.5,
con publicación de presencias ZC y validación pendientes; end_day queda aclarado
como límite legado de días observados, separado del cierre industrial nulo
mientras la producción sigue abierta. C03 de production_capacity queda aceptado
en 7.6, pendiente de ampliar fuentes e implementar; los ocho bloques C04–C11
quedan especificados técnicamente en 7.9–7.16, con cierre documental y secuencia
de implementación en 7.17. Su redacción no equivale a validación ni despliegue.
El diseño de respaldos de 5.8 queda aceptado,
con presupuesto orientativo y ejecución/validación operativas pendientes.
No habilitar limpiezas automáticas de
Delta ni temporales en la integración inicial según la decisión de 5.7.

Este documento queda como referencia viva para anotar decisiones, entregas,
evidencias y aprobaciones. Tras la autorización del 02/11/2026 existe código
Gold y validación local según 7.18. No se han realizado despliegues ni conectado
la aplicación; los acuerdos y especificaciones no acreditan por sí solos E09.
