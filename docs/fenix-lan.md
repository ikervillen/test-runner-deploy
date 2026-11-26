# Fenix en la red local

El frontend escucha en Docker en el puerto 8080 y se publica en el 8011 del servidor. Configura `frontend_bind_address` y `frontend_remote_subnet` en el JSON de sitio indicado por `INGESTA_SITE_CONFIG` del entorno `develop`. El valor por defecto es `127.0.0.1`. La configuración se aplica tanto al instalar la aplicación Live como al actualizar el frontend existente. El backend se mantiene en la red Docker y Nginx sirve `/api` en el mismo origen. Vite conserva su proxy para desarrollo local.

El perfil instalado originalmente utiliza `APP_AUTH_MODE=disabled`. Con acceso al puerto, un equipo puede consultar los datos. La autorización de red debe cubrir ese acceso antes de activar la interfaz LAN. Con autenticación por cabeceras de un proxy corporativo, el rollout rechaza la apertura directa y debe mantenerse la entrada a través de la pasarela de confianza.

## Preparación por el administrador de Windows

Ejecutar en PowerShell elevado. El operador debe introducir la IPv4 corporativa **asignada al servidor** y el CIDR **aprobado para los clientes**.

```powershell
$ip = Read-Host 'IPv4 del servidor'
$cidr = Read-Host 'CIDR autorizado'
$nic = Get-NetIPAddress -AddressFamily IPv4 -IPAddress $ip -ErrorAction Stop
$net = Get-NetConnectionProfile -InterfaceIndex $nic.InterfaceIndex -ErrorAction Stop
$profile = switch ($net.NetworkCategory) {
    DomainAuthenticated { 'Domain' }
    Private { 'Private' }
    default { throw 'Perfil de red no apto para el acceso a Fenix.' }
}
New-NetFirewallRule -Name 'Fenix-Frontend-LAN-8011' -DisplayName 'Fenix LAN TCP 8011' `
    -Direction Inbound -Action Allow -Enabled True -Profile $profile `
    -InterfaceAlias $nic.InterfaceAlias -LocalAddress $ip -RemoteAddress $cidr `
    -Protocol TCP -LocalPort 8011
$rule = Get-NetFirewallRule -PolicyStore ActiveStore -Name 'Fenix-Frontend-LAN-8011'
$rule | Format-List Name, Enabled, Profile, Direction, Action
$rule | Get-NetFirewallAddressFilter | Format-List LocalAddress, RemoteAddress
$rule | Get-NetFirewallPortFilter | Format-List Protocol, LocalPort
Get-NetFirewallProfile -PolicyStore ActiveStore -Name $profile |
    Format-List Enabled, DefaultInboundAction
```

Si la regla ya existe, inspecciónala y modifica sus filtros únicamente si corresponde a la configuración aprobada. Revisa con el administrador que la política efectiva y las demás reglas de Docker o del puerto 8011 no amplíen el acceso. No se requiere abrir puertos en routers.

A continuación, guarda en el JSON del sitio los dos valores aprobados. Usa la ruta que corresponde al runner.

```powershell
$sitePath = Read-Host 'Ruta absoluta del JSON de sitio del runner'
$site = Get-Content -LiteralPath $sitePath -Raw | ConvertFrom-Json
$site | Add-Member -NotePropertyName frontend_bind_address -NotePropertyValue $ip -Force
$site | Add-Member -NotePropertyName frontend_remote_subnet -NotePropertyValue $cidr -Force
[IO.File]::WriteAllText($sitePath, ($site | ConvertTo-Json -Depth 20), [Text.UTF8Encoding]::new($false))
```

## Validación y reversión

El workflow de la integración contiene una activación acotada a la aplicación. Se ejecuta tras integrar en `develop` desde la base revisada, con un mensaje que incluya `Fenix LAN and active production models`. Si la configuración de red se prepara más tarde, ejecuta `Independent pipelines validation` desde `develop` con `rollout_application` activado. Conserva el Compose anterior del frontend y del backend que imprime el job.

Ejecutar en el servidor y repetir desde un equipo autorizado con la misma IP.

```powershell
$ip = Read-Host 'IPv4 del servidor'
Test-NetConnection -ComputerName $ip -Port 8011
$base = "http://${ip}:8011"
(Invoke-WebRequest -Uri "$base/" -UseBasicParsing -TimeoutSec 15).StatusCode
(Invoke-WebRequest -Uri "$base/live/validacion" -UseBasicParsing -TimeoutSec 15).StatusCode
Invoke-RestMethod -Uri "$base/api/health" -TimeoutSec 15
Invoke-RestMethod -Uri "$base/api/live/scores" -TimeoutSec 30 |
    Select-Object publication_id, stale, active_productions
```

En el navegador verifica las descargas de `/assets/`, la llamada `/api/live/scores` cada 30 segundos, los gráficos, la apertura de detalles y Histórico. Comprueba un relevo en A y B por separado y que el modelo del score anterior aparezca diferenciado del activo. Desde un equipo fuera del alcance autorizado, verifica que el puerto no sea accesible. En el servidor verifica que Compose anuncia solo el puerto del frontend.

```powershell
docker --context desktop-linux ps --filter 'label=com.docker.compose.project=fenix-aiala-independent' --format 'table {{.Names}}	{{.Ports}}'
```

Para revertir de inmediato el acceso remoto, el administrador puede deshabilitar la regla dedicada y volver a guardar `127.0.0.1` en el sitio. Para restaurar la aplicación, usa los dos Compose anteriores acreditados por el rollout. Ajusta el contexto al configurado en el sitio.

```powershell
Disable-NetFirewallRule -Name 'Fenix-Frontend-LAN-8011'
$oldBackend = Read-Host 'Compose anterior del backend'
$oldFrontend = Read-Host 'Compose anterior del frontend'
docker --context desktop-linux compose -p fenix-aiala-independent -f $oldBackend up -d --no-deps --no-build --pull never --wait backend
docker --context desktop-linux compose -p fenix-aiala-independent -f $oldFrontend up -d --no-deps --no-build --pull never --wait frontend
```
