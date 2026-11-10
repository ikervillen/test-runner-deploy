# Plan del backend local y visualizador histórico

## 1. Objetivo

Construir un visualizador histórico local que permita consultar y comparar los
datos almacenados en los Parquet de calidad de producción.

La aplicación deberá permitir:

- Seleccionar una o varias líneas: `AV23` y `AV32`.
- Seleccionar una o varias producciones.
- Seleccionar uno o varios ramales de las producciones elegidas.
- Seleccionar una métrica.
- Seleccionar una ventana temporal, inicialmente `10m`, `1h` y `4h`.
- Calcular las agregaciones bajo demanda en el PC local.
- Mostrar un gráfico independiente por producción seleccionada.
- Superponer en cada gráfico los ramales seleccionados de esa producción.

El sistema será exclusivamente local. No tendrá tiempo real, InfluxDB, MLflow
ni conexiones con servicios externos. Se incluirá Docker básico como alternativa
de ejecución local, sin convertirlo en un requisito para usar la aplicación.

## 2. Terminología

Para evitar ambigüedades, la aplicación usará estos términos:

- **Línea:** máquina IS completa. Valores actuales: `AV23` y `AV32`.
- **Ramal:** subdivisión de una línea. Ejemplos: `AV23-1` y `AV32-3`.
- **Producción:** secuencia de días calendario consecutivos con datos para una
  misma línea.
- **Captura:** instante identificado actualmente por
  `LISTA_DEFECTOS_FECHA_REAL + LISTA_DEFECTOS_MAQ_IS_RAMAL`.
- **Serie:** valores temporales de una métrica para un ramal concreto.

El primer selector del frontend deberá llamarse **Línea**, no **Máquina**.

## 3. Fuente de datos

Directorio actual:

```text
C:\Users\iker.villen\Desktop\AI_tool\Datos\AV-2024-2026_parquet
```

Organización:

```text
AV23_PROD_001_20240213__20240215/
├── AV23-1.parquet
└── AV23-2.parquet
```

Archivos auxiliares:

- `manifest.csv`: catálogo de producciones, líneas, ramales, fechas y rutas.
- `metadata.json`: regla de particionado, columnas y recuentos generales.

Volumen actual:

- 17 producciones.
- 8 producciones de `AV23`.
- 9 producciones de `AV32`.
- 38 archivos Parquet.
- 516.855 filas.

El backend deberá usar `manifest.csv` como catálogo y no deducir las
producciones recorriendo todos los directorios en cada consulta.

## 4. Arquitectura propuesta

```text
React + TypeScript (127.0.0.1:5174)
                |
                | HTTP / JSON
                v
FastAPI (127.0.0.1:8001)
                |
                v
Motor local de consulta
                |
                v
Parquet + manifest.csv
```

### 4.1 Frontend

Se mantiene el stack existente:

- React.
- TypeScript.
- Vite.
- Tailwind CSS.
- Plotly.

### 4.2 Backend

Propuesta:

- Python 3.11 o superior.
- FastAPI.
- Uvicorn.
- Polars para leer y agregar Parquet.
- Pydantic v2 para contratos de entrada/salida y `pydantic-settings` para
  configuración.
- `structlog` para logging estructurado en JSON.
- Pytest para pruebas.
- `uv` para dependencias, entornos y ejecución reproducible.

Polars es preferible a cargar los archivos completos con Pandas porque permite:

- Lectura selectiva de columnas.
- Filtros y agregaciones eficientes.
- Ejecución lazy.
- Buen rendimiento con Parquet.
- Menor consumo de memoria.

El backend escuchará únicamente en:

```text
http://127.0.0.1:8001
```

Vite redirigirá `/api` a ese puerto durante el desarrollo local.

### 4.3 Principios de implementación

Se aplicarán prácticas profesionales siempre que aporten claridad, seguridad o
capacidad de prueba sin sobredimensionar el backend:

- Diseño REST y contratos HTTP explícitos.
- Arquitectura por capas.
- Clean Code y principios SOLID aplicados de forma pragmática.
- Módulos pequeños y con una responsabilidad clara.
- Type hints modernos y tipado estricto uniforme.
- `from __future__ import annotations` en los módulos Python.
- `pathlib.Path` para todas las rutas de archivos.
- Pydantic para límites de la API y configuración.
- `dataclasses` con constructores automáticos para contenedores internos de
  datos cuando no necesiten validación Pydantic.
- `TypedDict` para estructuras de diccionario estables.
- `Protocol` para contratos entre capas e inyección de dependencias.
- Evitar duplicidad de código, funciones y reglas de negocio.
- Separar modelos HTTP, modelos de dominio y acceso a datos.
- No mezclar `print()` y logging; el backend utilizará exclusivamente logger.

No se crearán interfaces, repositorios o clases abstractas solo por cumplir un
patrón. `Protocol`, servicios y adaptadores se introducirán cuando permitan
aislar I/O, sustituir dependencias en pruebas o reducir acoplamiento real.

### 4.4 Arquitectura por capas

Dependencias propuestas:

```text
API / routers
      |
      v
Application / use cases
      |
      v
Domain / reglas y contratos
      ^
      |
Infrastructure / Parquet, manifest, caché y configuración
```

Reglas:

- La capa API valida HTTP y transforma modelos, pero no calcula métricas.
- Los casos de uso coordinan catálogo, consulta y agregación.
- El dominio contiene reglas independientes de FastAPI y Polars cuando sea
  razonable.
- Infraestructura implementa los contratos definidos mediante `Protocol`.
- Las dependencias apuntan hacia las reglas de negocio, no hacia los detalles
  de almacenamiento.

### 4.5 Async y trabajo bloqueante

Los endpoints FastAPI serán `async def` cuando coordinen operaciones asíncronas.
La lectura y agregación con Polars es trabajo síncrono y potencialmente
bloqueante, por lo que se ejecutará mediante `asyncio.to_thread()` cuando
proceda.

No se usará `async/await` de forma ornamental. Las funciones puramente síncronas
permanecerán síncronas y se aislará el trabajo CPU/I/O bloqueante para no
bloquear el event loop.

### 4.6 Errores, logging y trazabilidad

Toda la API utilizará un formato de error unificado:

```json
{
  "error": {
    "code": "INVALID_BRANCH_SELECTION",
    "message": "El ramal no pertenece a la producción seleccionada.",
    "details": {},
    "request_id": "01J..."
  }
}
```

Se implementará:

- Excepciones de aplicación tipadas.
- Handler global de excepciones.
- Traducción uniforme de errores de validación Pydantic.
- Códigos de error estables y documentados.
- Middleware que acepte o genere `request_id`.
- Propagación de `request_id` en logs y respuestas de error.
- Logging estructurado JSON mediante `structlog`.
- Niveles y campos uniformes: evento, request_id, ruta, método, estado,
  duración y contexto seguro.
- Ausencia de datos sensibles o filas completas en logs.

### 4.7 Configuración y variables de entorno

La configuración se cargará en runtime mediante `pydantic-settings`.

- Las variables de entorno no se incrustarán durante el build.
- Se proporcionará `env.local.example` sin secretos.
- Las rutas se validarán y resolverán con `pathlib.Path`.
- Los valores por defecto serán seguros para `127.0.0.1`.
- Docker recibirá la configuración mediante variables de entorno en runtime.

### 4.8 Gestión de dependencias y calidad

El backend tendrá un `pyproject.toml` como fuente de verdad para:

- Metadatos y dependencias.
- Dependencias de desarrollo.
- Configuración de Ruff.
- Configuración de Black.
- Configuración de mypy.
- Configuración de pytest.
- Cobertura.

Se utilizará `uv` como gestor principal y se versionará `uv.lock`. El
`pyproject.toml` seguirá estándares PEP 621, por lo que podrá ser consumido por
otras herramientas. No se mantendrán simultáneamente `uv.lock` y
`poetry.lock`, porque dos gestores de bloqueo crearían riesgo de divergencia.
Si posteriormente se exige Poetry, se realizará una decisión explícita de
migración del lockfile.

Comandos de calidad previstos:

```text
uv sync --dev
uv run ruff check .
uv run black --check .
uv run mypy --strict backend
uv run pytest
```

Ruff se usará para linting e imports. Black será el formateador canónico. La
configuración evitará reglas duplicadas o incompatibles entre ambos.

El pipeline local de calidad deberá superar:

- Ruff sin errores.
- Black sin diferencias.
- `mypy --strict` sin errores.
- Pytest sin fallos.
- Cobertura mínima acordada para servicios y casos de uso.

## 5. Estrategia de cálculo

### 5.1 Cálculo bajo demanda

En la primera versión, las ventanas se calcularán bajo demanda.

No se crearán tablas precalculadas inicialmente porque:

- El volumen actual es reducido.
- Cada consulta leerá solo las producciones y ramales seleccionados.
- Evita duplicar datos.
- Facilita corregir las fórmulas de las métricas.
- Permite añadir nuevas métricas sin regenerar todo el histórico.

### 5.2 Caché local

Se añadirá una caché en memoria por consulta normalizada:

```text
producción + ramales + métrica + ventana + filtros
```

La caché deberá tener tamaño limitado y podrá invalidarse usando la fecha de
modificación de `manifest.csv` o de los Parquet consultados.

No se añadirá persistencia de caché en la primera versión.

### 5.3 Posible precálculo posterior

Solo se estudiará precalcular tablas si las mediciones reales muestran tiempos
de respuesta insuficientes.

Antes de hacerlo se registrarán:

- Tiempo de apertura de archivos.
- Tiempo de agregación.
- Memoria máxima utilizada.
- Tamaño de la respuesta JSON.
- Tiempo de renderizado del frontend.

## 6. Catálogo y API

### 6.1 Estado del servicio

```http
GET /api/health
```

Respuesta:

```json
{
  "status": "ok",
  "data_root_available": true
}
```

### 6.2 Metadatos del visualizador

```http
GET /api/catalog
```

Devolverá:

- Líneas disponibles.
- Producciones por línea.
- Fecha inicial y final de cada producción.
- Ramales disponibles en cada producción.
- Modelo y colores disponibles.
- Métricas soportadas.
- Ventanas soportadas.

El frontend construirá sus desplegables con esta respuesta. Las 17 producciones
no permanecerán codificadas manualmente en React.

### 6.3 Consulta de series

```http
POST /api/trends/query
```

Petición propuesta:

```json
{
  "production_ids": [
    "AV23_PROD_001",
    "AV32_PROD_001"
  ],
  "branches": [
    "AV23-1",
    "AV23-2",
    "AV32-1"
  ],
  "metric": "rejected_count",
  "window": "1h",
  "filters": {
    "defect_codes": [],
    "cavities": [],
    "colors": []
  }
}
```

Respuesta propuesta:

```json
{
  "query": {
    "metric": "rejected_count",
    "window": "1h"
  },
  "productions": [
    {
      "production_id": "AV23_PROD_001",
      "line": "AV23",
      "start": "2024-02-13T07:20:00",
      "end": "2024-02-15T12:20:00",
      "series": [
        {
          "branch": "AV23-1",
          "points": [
            {
              "timestamp": "2024-02-13T07:00:00",
              "value": 42
            }
          ]
        }
      ]
    }
  ],
  "meta": {
    "query_time_ms": 28,
    "files_read": 3,
    "rows_scanned": 8992,
    "cache_hit": false
  }
}
```

La respuesta estará agrupada por producción para que el frontend pueda crear
un gráfico por cada una sin volver a reorganizar los datos.

## 7. Selección en el frontend

### 7.1 Línea

Selector múltiple:

- `AV23`
- `AV32`

Al cambiar la selección se actualizará la lista de producciones disponibles.

### 7.2 Producciones

Selector múltiple con:

- Identificador.
- Línea.
- Fecha inicial.
- Fecha final.

Ejemplo:

```text
AV23 · 13/02/2024 - 15/02/2024
```

### 7.3 Ramales

Los ramales se mostrarán agrupados por producción o línea.

El frontend no deberá permitir combinaciones inválidas, por ejemplo seleccionar
`AV32-4` para una producción que solo contiene `AV32-1` y `AV32-2`.

Por defecto se seleccionarán todos los ramales disponibles de cada producción.

### 7.4 Métrica

En la primera entrega se implementará una sola métrica correctamente validada.
Después se añadirán las siguientes de forma incremental.

Métricas candidatas:

- `rejected_count`: suma de `LISTA_DEFECTOS_RECH`.
- `inspected_count`: número de botellas inspeccionadas.
- `rejection_rate`: rechazadas / inspeccionadas.
- Rechazos por código de defecto.
- Rechazos por cavidad.
- Rechazos por sección.

No se implementará `inspected_count` ni `rejection_rate` hasta cerrar la regla
para eliminar la repetición de `LISTA_DEFECTOS_INSPECC`.

### 7.5 Ventana

Primera versión:

- `10m`.
- `1h`.
- `4h`.

La agregación deberá ser estable y reproducible. El backend devolverá el
timestamp inicial de cada ventana.

### 7.6 Representación

- Un gráfico por producción seleccionada.
- Una serie superpuesta por ramal.
- Misma métrica y ventana en todos los gráficos de una consulta.
- Colores de ramal estables entre gráficos.
- Leyenda con opción de ocultar o mostrar series.
- Cabecera del gráfico con línea, producción y fechas.
- Mensajes independientes de carga, vacío o error por producción.

La primera versión limitará el número simultáneo de producciones para evitar
respuestas y gráficos excesivos. Límite inicial propuesto: 6.

## 8. Reglas de agregación

### 8.1 Rechazos

Para `rejected_count`:

```text
SUM(LISTA_DEFECTOS_RECH)
```

Agrupación mínima:

```text
producción + ramal + ventana temporal
```

Los filtros por defecto, cavidad, color o código de máquina se aplicarán antes
de la agregación.

Advertencia: esta métrica representa registros de rechazo/defecto. No se
afirmará que son botellas únicas rechazadas mientras no exista un identificador
de botella que permita deduplicarlas.

### 8.2 Inspeccionadas

`LISTA_DEFECTOS_INSPECC` se repite entre filas de distintos defectos. No puede
sumarse directamente.

Antes de implementar la métrica debe determinarse su grano real. Una hipótesis
inicial para validar es:

```text
FECHA_REAL + MAQ_IS_RAMAL + COD_MAQ_ZF + MOLDE/CAVIDAD
```

El valor inspeccionado se tomaría una sola vez por cada grupo válido y después
se agregaría a la ventana.

Esta regla deberá contrastarse con casos reales antes de publicarse.

### 8.3 Porcentaje de rechazo

Solo se implementará después de validar el denominador:

```text
rejection_rate = rejected_count / inspected_count * 100
```

También deberá decidirse si el numerador representa defectos o botellas únicas.

### 8.4 Ventana de una hora

Propuesta inicial:

```text
07:00:00 <= t < 08:00:00
```

Se utilizará la hora local de planta.

### 8.5 Ventana de diez minutos

Las capturas presentan normalmente una cadencia de diez minutos. La ventana
`10m` agrupará cada captura en bloques naturales de reloj:

```text
07:00-07:10, 07:10-07:20, 07:20-07:30...
```

Se definirá semántica de intervalo cerrado por la izquierda y abierto por la
derecha:

```text
[inicio, fin)
```

La implementación deberá validar cómo tratar capturas ausentes y evitar
rellenar con cero intervalos sin datos salvo que la métrica lo requiera
explícitamente.

### 8.6 Ventana de cuatro horas

La primera implementación utilizará bloques civiles:

```text
00:00-04:00, 04:00-08:00, 08:00-12:00...
```

El origen será configurable mediante `APP_FOUR_HOUR_OFFSET_HOURS`, por lo que
podrá alinearse después con el horario industrial real sin cambiar el contrato
de la API.

## 9. Validaciones

El backend deberá rechazar:

- Producciones inexistentes.
- Ramales que no pertenecen a la producción solicitada.
- Métricas desconocidas.
- Ventanas no soportadas.
- Consultas sin producciones.
- Más producciones que el límite permitido.
- Filtros incompatibles.

Además:

- Solo podrá leer rutas contenidas dentro del directorio Parquet configurado.
- Nunca aceptará una ruta de archivo enviada por el frontend.
- La configuración de rutas se mantendrá en un archivo local o variable de
  entorno.

## 10. Rendimiento esperado

Objetivos iniciales en este PC:

- Carga de catálogo: menos de 200 ms.
- Consulta de una producción: menos de 1 s.
- Consulta de hasta 6 producciones: menos de 3 s.
- Respuesta sin filas originales; solo puntos agregados.

Estos valores deberán medirse. No se optimizará mediante precálculo sin datos de
rendimiento.

## 11. Estructura prevista del proyecto

```text
Calidad_produccion/
├── backend/
│   ├── app/
│   │   ├── api/
│   │   │   ├── errors.py
│   │   │   ├── middleware.py
│   │   │   └── routes/
│   │   ├── application/
│   │   │   └── use_cases/
│   │   ├── domain/
│   │   │   ├── models.py
│   │   │   └── protocols.py
│   │   ├── infrastructure/
│   │   │   ├── parquet/
│   │   │   └── repositories/
│   │   ├── schemas/
│   │   ├── config.py
│   │   └── logging.py
│   ├── tests/
│   ├── main.py
│   └── env.local.example
├── docs/
│   └── PLAN_BACKEND_VISUALIZADOR_HISTORICO.md
├── public/
├── src/
├── pyproject.toml
├── uv.lock
├── Dockerfile
├── compose.yaml
├── start_backend_local.bat
├── start_frontend_local.bat
└── start_all_local.bat
```

## 12. Plan de implementación por pasos

La implementación se hará mediante cambios pequeños y revisables. No se
realizará una refactorización o entrega única de gran tamaño.

### Paso 1. Esqueleto del backend

- Crear FastAPI.
- Configurar ejecución en `127.0.0.1:8001`.
- Añadir configuración de la ruta de datos.
- Implementar `/api/health`.
- Crear `pyproject.toml` y lockfile de `uv`.
- Configurar Ruff, Black, `mypy --strict`, pytest y cobertura.
- Crear la separación inicial de capas sin lógica innecesaria.
- Añadir logging JSON, middleware de `request_id` y handlers globales.
- Añadir pruebas del arranque y configuración.

**Criterio de aceptación:** el backend arranca localmente y detecta si el
directorio de datos existe; Ruff, Black, mypy y pytest pasan.

### Paso 2. Catálogo de producciones

- Leer y validar `manifest.csv`.
- Crear modelos de línea, producción y ramal.
- Implementar `/api/catalog`.
- Añadir pruebas con el catálogo real y fixtures mínimos.

**Criterio de aceptación:** el frontend puede obtener las 17 producciones y sus
ramales sin valores codificados en React.

### Paso 3. Consulta básica de rechazos

- Implementar lectura selectiva de Parquet.
- Añadir `POST /api/trends/query`.
- Soportar una producción, varios ramales y `rejected_count`.
- Implementar ventana de `10m`.
- Implementar ventana de `1h`.
- Validar recuentos contra cálculos independientes.

**Criterio de aceptación:** una consulta real devuelve series de `10m` y `1h`
por ramal.

### Paso 4. Integración mínima del frontend

- Sustituir el catálogo estático por `/api/catalog`.
- Cambiar el selector Máquina por Línea.
- Conectar el botón Consultar.
- Eliminar los datos de demostración.
- Mostrar carga, errores y ausencia de datos.

**Criterio de aceptación:** el gráfico actual muestra datos reales de una
producción.

### Paso 5. Selección múltiple de producciones

- Convertir Línea y Producción en selectores múltiples.
- Validar ramales por producción.
- Permitir consultar varias producciones.
- Crear un gráfico independiente por producción.
- Limitar inicialmente a 6 producciones.

**Criterio de aceptación:** se pueden comparar varias campañas en una misma
pantalla sin mezclar sus ejes temporales.

### Paso 6. Ventana de cuatro horas

- Confirmar horario de inicio de turno.
- Implementar el alineamiento de `4h`.
- Añadir pruebas de límites de ventana y cambios de día.
- Incorporar la opción al frontend.

**Criterio de aceptación:** todos los puntos pertenecen al medio turno correcto.

### Paso 7. Inspeccionadas y porcentaje

- Analizar el grano de `LISTA_DEFECTOS_INSPECC`.
- Aprobar una regla de deduplicación.
- Implementar `inspected_count`.
- Implementar `rejection_rate`.
- Validar casos manuales representativos.

**Criterio de aceptación:** numerador y denominador son auditables y no duplican
inspecciones.

### Paso 8. Filtros de análisis

Añadir gradualmente:

- Código de defecto.
- Cavidad.
- Sección.
- Color.
- Código de máquina ZF.

Cada filtro deberá aparecer en el catálogo y aplicarse en el backend antes de
agregar.

### Paso 9. Caché y medición

- Medir tiempos reales.
- Añadir caché LRU en memoria.
- Exponer tiempo de consulta y `cache_hit`.
- Definir si hace falta precálculo.

### Paso 10. Ejecución local integrada

- Crear scripts de arranque y parada.
- Iniciar backend y frontend de forma conjunta.
- Mantener ambos servicios ligados a `127.0.0.1`.
- Añadir Dockerfile y `compose.yaml` básicos para ejecución local opcional.
- Publicar los puertos Docker únicamente en `127.0.0.1`.
- Inyectar rutas y configuración en runtime.
- Documentar instalación y operación local.

## 13. Estrategia de pruebas

### Backend

- Pruebas del catálogo.
- Pruebas de validación de solicitudes.
- Pruebas de pertenencia ramal-producción.
- Pruebas de ventanas de `10m`, `1h` y `4h`.
- Pruebas de métricas con pequeños Parquet controlados.
- Comparación de resultados contra ejemplos calculados manualmente.
- Pruebas para evitar acceso a rutas externas.
- Pruebas del formato de error y handlers globales.
- Pruebas de generación y propagación de `request_id`.
- Pruebas de configuración por variables de entorno.
- Pruebas de los adaptadores contra los `Protocol` de dominio.
- Comprobación estática mediante `mypy --strict`.

### Frontend

- Actualización de producciones al cambiar líneas.
- Selección múltiple.
- Selección válida de ramales.
- Creación de un gráfico por producción.
- Estados de carga, error y vacío.
- Persistencia de colores de ramal.

### Integración

- Arranque local completo.
- Consulta real al catálogo.
- Consulta real de varias producciones.
- Verificación visual en `127.0.0.1:5174`.

## 14. Decisiones pendientes de aprobación

Antes o durante la implementación deberán cerrarse estas decisiones:

1. Hora industrial definitiva para ajustar `APP_FOUR_HOUR_OFFSET_HOURS`; el
   valor inicial será `0`.
2. Regla correcta para deduplicar `LISTA_DEFECTOS_INSPECC`.
3. Significado deseado de rechazo: defectos registrados o botellas únicas.
4. Límite máximo de producciones visibles simultáneamente.
5. Si producciones de distinta duración compartirán escala vertical, pero no
   eje temporal.
6. Filtros prioritarios después de línea, producción, ramal, métrica y ventana.
7. Convención de colores fija para todos los ramales.

## 15. Fuera de alcance

- Datos en tiempo real.
- InfluxDB.
- MLflow.
- Despliegue en red o servidor.
- Autenticación y usuarios.
- Modificación automática de los Parquet originales.
- Precálculo de agregados sin mediciones que lo justifiquen.
- Orquestación Docker de producción, Kubernetes o despliegue remoto.

## 16. Resultado esperado

Al completar el plan, el usuario podrá seleccionar varias líneas y
producciones, elegir sus ramales, métrica y ventana temporal, ejecutar el
cálculo local y visualizar un gráfico por producción con sus ramales
superpuestos.

Los resultados procederán directamente de los Parquet y cada consulta será
reproducible, validada y auditable.
