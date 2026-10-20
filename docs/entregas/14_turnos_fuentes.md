# Entrega 14 — Turnos, continuidad de capturas y progreso

El worker asigna un lote MIA por turno. Dentro de cada origen mantiene FIFO;
entre orígenes rota mediante un cursor confirmado junto con el acuse de entradas.
El modo CLI conserva la posibilidad de consumir la cola completa; el presupuesto
mia_batches_per_turn es configurable de 1 a 100. No divide un commit ni pierde
la cola al ceder el turno o reiniciar.

CONT_ZC se atiende antes de las dos fuentes MIA. Mientras queda backlog se inicia
otra vuelta tras una pausa mínima de un segundo; al estar al día el intervalo
se mide desde el inicio del ciclo. No se añaden diez minutos al tiempo de proceso.
Los resultados distinguen calidad de datos y trabajo pendiente: BACKLOG_PENDING,
PARTIAL_BACKLOG, complete y quality_complete. Las asociaciones se posponen
explícitamente mientras las colas MIA inventariadas no se han agotado.

Los resultados incluyen versiones pendientes por origen, bytes, recepción más
antigua, total consumido, velocidad entre visitas a la fuente y una estimación
del tiempo pendiente. La estimación usa versiones por tiempo real transcurrido;
no presupone tamaños equivalentes ni incluye archivos todavía no descubiertos.
La ausencia de ETA en la primera visita o sin avance no significa duración cero.

Las fuentes no MIA reutilizan las capturas estables mediante capture_cache,
sembrada una vez desde recepciones previas. Siempre se vuelve a calcular el hash
del archivo vivo, incluso si nombre, tamaño y mtime coinciden. Las rutas nuevas
con bytes ya capturados evitan otra espera de estabilidad y otra copia, manteniendo
su recepción. Las revisiones capturadas de intentos fallidos se reintentan desde
Bronze aunque el original se sobrescriba o desaparezca. Se recuperan también las
capturas inmutables perdidas entre fsync y recepción. El único escritor de la
familia confirma su tratamiento con el resultado duradero del intento.

No cambian esquemas, claves de negocio ni reglas de actualización/revisión. Las
dependencias siguen consultando versiones confirmadas. No se habilita todavía
la ejecución de varias fuentes simultáneas; corresponde a la siguiente entrega.
