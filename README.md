# Zetri - SVX Streaming

Plataforma para crear y reproducir archivos `.svx` con:
- modo Pro (WebCrypto/chunks),
- modo Legacy (stream directo),
- puente VLC,
- particionado por tamaño,
- playlist continua entre múltiples `.svx`.

## Novedades Implementadas

1. Particionado por tamaño (MB) al crear SVX.
2. Modo `SVX por parte`:
- genera un `.svx` independiente por cada parte,
- descarga un `.zip` con todas las partes,
- incluye `*.playlist.json` dentro del zip.
3. Reproducción continua (playlist):
- backend con sesión de playlist multi-partes,
- frontend Legacy con autoplay automático parte-a-parte.

## Requisitos

- Python 3.11+
- `pycryptodome`
- FFmpeg (`ffmpeg.exe`) para particionado reproducible por tamaño

Nota: en este proyecto se usa como fallback:
`C:\Program Files\Softdeluxe\Free Download Manager\ffmpeg.exe`

Si usás otra ubicación, configurá:
- `FFMPEG_BIN`
- `FFPROBE_BIN` (opcional; si no está, se usa fallback con `ffmpeg -i`).

## Variables de Entorno Relevantes

- `SVX_MAX_PART_SIZE_MB` (default `4096`)
- `SVX_PLAY_CHUNK_SIZE`
- `SVX_SESSION_TTL_MINUTES`
- `FFMPEG_BIN`
- `FFPROBE_BIN`

## Crear SVX (UI)

En la pestaña `Crear .SVX`:

1. Seleccioná videos.
2. Definí contraseña.
3. Opcional: `Tamaño por parte (MB)`.
4. Opcional: `Generar un archivo .SVX por cada parte (descarga ZIP)`.

Resultados:

- Sin particionado: un único `.svx`.
- Con particionado normal: un único `.svx` con múltiples entradas.
- Con `SVX por parte`: `.zip` con `parte_1.svx`, `parte_2.svx`, etc. + `playlist.json`.

## Endpoints Principales

### Crear SVX

`POST /api/svx/create` (multipart/form-data)

Campos:
- `files`: uno o más videos
- `password`: string
- `part_size_mb`: int (0 = sin partir)
- `svx_per_part`: bool (`true/false`)

### Inspeccionar SVX

`GET /api/svx/inspect?path=...&password=...`

### Stream Legacy por entrada

`GET /api/svx/stream?path=...&password=...&item=...`

## Playlist Continua (Nuevo)

### Crear sesión de playlist

`POST /api/svx/playlist/session`

Body JSON:

```json
{
  "title": "Pelicula X",
  "mode": "fallback",
  "password": "1234",
  "parts": [
    { "path": "https://.../parte001.svx", "label": "Parte 1" },
    { "path": "https://.../parte002.svx", "label": "Parte 2" }
  ]
}
```

Respuesta:
- `session_id`
- `manifest_url`
- `autoplay_start_url`

### Ver manifest de playlist

`GET /api/svx/playlist/session/{session_id}/manifest`

### Resolver parte específica

`GET /api/svx/playlist/session/{session_id}/part/{part_index}`

Devuelve `stream_url` y `next_part_url`.

## Frontend Legacy con Autoplay

En `URL Legacy` podés pegar múltiples URLs `.svx`:
- una por línea, o
- separadas por coma.

Al iniciar:
1. crea sesión de playlist,
2. reproduce la primera parte,
3. avanza automáticamente al terminar cada una.

## Estado Actual

Sí: flujo end-to-end de particionado + SVX por parte + playlist continua base está implementado.
