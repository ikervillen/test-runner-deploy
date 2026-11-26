# Activación conjunta de Fenix Live

Los cambios Silver y de despliegue de #110 y #111 se integran en develop. El
contexto separado de Gold y frontend fija a1b9df74927d99a942a54ba0d1d2ed72aae76bcc,
que contiene #112–#116, la parte Gold de #110 y los modelos activos de #111.
La publicación y activación esperan las comprobaciones nativas, Gold y el
smoke test de imagen dentro del mismo workflow.

## Activación

El push de integración a develop inicia una sola activación. El runner valida
la prueba de imagen, los montajes, T0 y el estado de History. Inicia primero el
lector y su frontend compatibles, drena Gold, comprueba sus operaciones abiertas,
actualiza Ingesta Live y arranca Gold Live. Conserva los Compose efectivos e IDs
de imagen de cada servicio en el archivo rollback que imprime el runner.

Si se requiere recuperar manualmente un despliegue que nunca llegó a iniciarse,
primero confirmar que la ejecución terminó correctamente y que el runner no
está activando los servicios. Desde una copia cuyo HEAD sea el SHA del workflow
y con el JSON del sitio efectivo en FENIX_SITE:

    $run = Read-Host 'ID de la ejecución exitosa'
    $sha = git rev-parse HEAD
    $bundle = Join-Path $env:TEMP "fenix-manual-$run"
    gh run download $run -R ikervillen/test-runner-deploy -n live-image-tar -D $bundle
    if ($LASTEXITCODE) { throw 'No se pudo descargar la imagen' }
    gh run download $run -R ikervillen/test-runner-deploy -n live-image-proof -D $bundle
    if ($LASTEXITCODE) { throw 'No se pudo descargar la prueba' }
    $proof = Get-Content (Join-Path $bundle 'image.json') -Raw | ConvertFrom-Json
    .\scripts\update_independent_workers.ps1 -ApplicationLive -IncludeIngestion -Bundle $bundle -Image $proof.registry_ref -Revision $sha -GoldRevision 'a1b9df74927d99a942a54ba0d1d2ed72aae76bcc'

La IP y el CIDR de la red autorizada proceden de la configuración del sitio.
El despliegue acredita el firewall cuando esa interfaz se configuró.
Ver docs/fenix-lan.md para el procedimiento en Windows.

## Verificación y simulación

    $sha = git rev-parse HEAD
    .\scripts\verify_fenix_live_rollout.ps1 -Revision $sha
    Test-NetConnection 192.168.10.37 -Port 8011

Repetir la prueba de red desde un cliente autorizado. Si el sitio usa el
enlace local, verificar desde el servidor con -BaseUrl http://127.0.0.1:8011.
La salida incluye modelos por zona, cierres, componentes, recursos y logs.
Los detalles AQL y DAR se consultan por resultado en
/api/live/scores/{publicacion}/{id_resultado}.

Para simular los 16 cierres, usar el informe original en la ruta comprobada
del servidor e iniciar un contenedor de solo lectura con el código fijado:

    $gold = 'a1b9df74927d99a942a54ba0d1d2ed72aae76bcc'
    $review = Join-Path $env:LOCALAPPDATA 'FenixIngesta/av31-ar-review'
    git fetch origin $gold
    if ($LASTEXITCODE) { throw 'No se pudo obtener la revisión Gold' }
    if (-not (Test-Path -LiteralPath $review)) { git worktree add --detach $review $gold }
    if ((git -C $review rev-parse HEAD) -cne $gold) { throw 'La revisión Gold de simulación difiere' }
    $incident = 'C:\Users\rdtiv001\fenix-diagnosticos\Fenix-AV31-datos.json'
    & (Join-Path $review 'scripts/Invoke-FenixArDryRun.ps1') -Informe $incident |
        Set-Content -LiteralPath (Join-Path (Split-Path $incident) 'AV31-revision-seca.jsonl') -Encoding UTF8

Para los cierres legados del incidente, la escritura selectiva se prepara
después de comprobar cada recibo y su configuración. La revisión candidata
solo incluye el simulador para esos pines, por lo que aquí no hay un comando
de escritura para los 16 cierres.

## Rollback

Antes del arranque del escritor nuevo, indicar la ruta de rollback impresa
por el workflow:

    $record = Read-Host 'Ruta del archivo rollback impreso por el runner'
    .\scripts\rollback_fenix_live_before_writer.ps1 -Record $record

Si se intentó iniciar el escritor nuevo, el script bloquea la reversión
automática. Mantener el lector que distingue los parciales y la revisión que
puede recuperar sus propias operaciones Delta. Un retorno del escritor previo
requiere verificar operaciones y compatibilidad con el pin publicado. Se
conservan los Compose, imágenes, volúmenes, checkpoints, evidencias y T0.
