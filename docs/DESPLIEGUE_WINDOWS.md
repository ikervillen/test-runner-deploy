# Despliegue Windows

El despliegue requiere Docker Desktop configurado para contenedores Linux, Git y
una cuenta de servicio con acceso de lectura al repositorio y a los datos.

1. Cree fuera del repositorio un fichero de entorno basado en
   `.env.docker.example`. Debe contener `HOST_DATA_ROOT`, la raiz que incluye
   los directorios Parquet y los Excel de Calidad.
2. Ejecute una release inmutable, etiqueta o SHA, no una rama mutable:

```powershell
scripts\deploy.bat `
  -RepositoryUrl https://servidor-git/organizacion/calidad_serie.git `
  -ReleaseRef v1.0.0 `
  -InstallDirectory C:\Servicios\calidad_serie `
  -EnvironmentFile C:\ProgramData\calidad_serie\produccion.env
```

Para validar el flujo sin modificar el servidor, añada `-WhatIf`.

El script rechaza un checkout con cambios locales, registra el commit anterior
en `.deployment\previous-release.txt`, espera `GET /api/ready` y restaura ese
commit si la nueva release no alcanza readiness. Nunca ejecuta `git reset --hard`.
El readiness usa `FRONTEND_PORT` del fichero de entorno, o `5174` si no se ha
declarado. Puede sustituirse explicitamente con `-ApplicationUrl` cuando el
proxy de validacion exponga otra ruta.
