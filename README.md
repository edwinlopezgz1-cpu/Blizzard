# Planificador web v3.1 para Railway

Esta es la versión corregida para Railway.

## Qué corrige
- Incluye los archivos `templates/login.html` y `templates/index.html` que faltaban.
- Ya no debe dar `Internal Server Error` al abrir el dominio por falta de plantillas.
- Guarda la base SQLite en el volume de Railway si existe `RAILWAY_VOLUME_MOUNT_PATH`.

## Variables recomendadas en Railway
- `SECRET_KEY`
- `TEAM_PASSWORD`
- `APP_TITLE`
- `APP_BUILD`

## Volume recomendado
Mount path:

`/app/data`

## Local
```bash
pip install -r requirements.txt
python app.py
```
