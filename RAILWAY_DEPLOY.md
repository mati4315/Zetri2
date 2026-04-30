# Deploy en Railway sin Git

## 1) Requisitos locales

- Node.js instalado
- Cuenta en Railway

## 2) Login y seleccion de carpeta

```powershell
npm i -g @railway/cli
railway login
cd "D:\WEB MATER\API N OFC YOUTUBE\Zetri"
```

## 3) Crear proyecto y subir codigo local

```powershell
railway init
railway up
```

## 4) Variables recomendadas en Railway

Configuralas en `Variables` del servicio:

- `SVX_TRUST_ENV=0`
- `SVX_CORE_DB_PATH=/tmp/svx_core.db`
- `ADMIN_SECRET=tu_clave_admin`
- `UNRAR_BIN=/usr/bin/unrar`

## 5) Verificar

Abre la URL publica y prueba:

- `/`
- `/admin/tokens`
- `POST /api/play/{token}/session`

## Notas

- El arranque usa `Procfile`: `uvicorn main:app --host 0.0.0.0 --port $PORT`.
- Railway instalara `unrar-free` por `nixpacks.toml`.
- Se excluyen archivos locales/pesados por `.railwayignore`.
