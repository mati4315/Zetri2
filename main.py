# Zetri Streaming Platform - isolated version

import asyncio
import threading as _threading

import traceback

import json
import sqlite3
import secrets
import uuid

import os
import tempfile
from pathlib import Path
import shutil
import svx_builder as _svx

UNRAR_BIN = os.getenv("UNRAR_BIN", "").strip()
if not UNRAR_BIN:
    if os.name == "nt":
        UNRAR_BIN = r"C:\Program Files\WinRAR\UnRAR.exe"
    else:
        UNRAR_BIN = (
            shutil.which("unrar")
            or shutil.which("unrar-free")
            or shutil.which("bsdtar")
            or "unrar"
        )
ARCHIVE_CACHE_DIR = Path(r"D:\WEB MATER\API N OFC YOUTUBE\temp_svx")
ARCHIVE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
MIME_MAP = {
    '.mp4': 'video/mp4',
    '.mkv': 'video/x-matroska',
    '.avi': 'video/x-msvideo',
    '.webm': 'video/webm',
    '.mp3': 'audio/mpeg',
    '.m4a': 'audio/mp4',
    '.srt': 'text/plain',
    '.vtt': 'text/vtt'
}

import re

import struct

import base64

import tempfile

import requests

import io

import hashlib

import zipfile
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Request, HTTPException, UploadFile, File, Form, BackgroundTasks
from pydantic import BaseModel, Field

from fastapi.responses import JSONResponse, HTMLResponse, FileResponse, Response, StreamingResponse

from starlette.background import BackgroundTask

from fastapi.staticfiles import StaticFiles

from fastapi.templating import Jinja2Templates

from pathlib import Path

from urllib.parse import parse_qs, urlencode, urlparse, urlunparse, urljoin, quote

from urllib.request import Request as UrlRequest, urlopen

from urllib.parse import quote, urlparse
from urllib.error import HTTPError


import os
import tempfile
import json
import asyncio
from pathlib import Path

tmp_dir_path = os.path.join(os.getcwd(), "temp_svx")
os.makedirs(tmp_dir_path, exist_ok=True)
os.environ["TMPDIR"] = tmp_dir_path
os.environ["TEMP"] = tmp_dir_path
os.environ["TMP"] = tmp_dir_path
tempfile.tempdir = tmp_dir_path

app = FastAPI(title="Zetri - SVX Streaming & RAR Live")

from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=".*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

VIDEO_EXTS = {'.mp4', '.mkv', '.avi', '.webm', '.ts', '.m2ts', '.mov', '.flv'}
ARCHIVE_DOWNLOADS = {}
ARCHIVE_CANCEL_FLAGS = {}
DB_FILE = Path("videos.json")
if not DB_FILE.exists():
    with open(DB_FILE, "w") as f:
        json.dump([], f)

_default_db_dir = Path(tempfile.gettempdir())
SVX_CORE_DB_FILE = Path(
    os.getenv("SVX_CORE_DB_PATH", str(_default_db_dir / "svx_core.db"))
)
TOKEN_DEFAULT_TTL_HOURS = int(os.getenv("SVX_TOKEN_DEFAULT_TTL_HOURS", "24"))
SESSION_TTL_MINUTES = int(os.getenv("SVX_SESSION_TTL_MINUTES", "30"))
PLAY_CHUNK_DEFAULT_SIZE = int(os.getenv("SVX_PLAY_CHUNK_SIZE", str(1024 * 1024)))
INDEX_CACHE_MAX_AGE_HOURS = int(os.getenv("SVX_INDEX_CACHE_MAX_AGE_HOURS", "168"))
ADMIN_SECRET = (os.getenv("ADMIN_SECRET", "") or "").strip()
REQUESTS_TRUST_ENV = (os.getenv("SVX_TRUST_ENV", "0").strip().lower() not in {"0", "false", "no"})
DB_LOCK = _threading.Lock()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _parse_iso(dt_str: str | None) -> datetime | None:
    if not dt_str:
        return None
    try:
        dt = datetime.fromisoformat(dt_str)
        # Compatibilidad con datos legacy guardados sin timezone.
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _mask_url(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    if len(u) <= 12:
        return "****"
    return f"{u[:6]}...{u[-6:]}"


def _mediafire_http_fallback_url(url: str) -> str:
    u = (url or "").strip()
    if u.startswith("https://download") and ".mediafire.com/" in u:
        return "http://" + u[len("https://"):]
    return u


def _token_from_input(raw: str = "") -> str:
    raw = (raw or "").strip()
    if raw:
        return re.sub(r"[^a-zA-Z0-9_-]", "", raw)[:64] or f"svx-{secrets.token_hex(4)}"
    return f"svx-{secrets.token_hex(4)}"


def _sha256_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _new_requests_session() -> requests.Session:
    sess = requests.Session()
    sess.trust_env = REQUESTS_TRUST_ENV
    return sess


def _http_get_with_proxy_fallback(url: str, *, timeout: int = 20, headers: dict | None = None, stream: bool = False, allow_redirects: bool = True):
    last_error = None
    trust_candidates = [REQUESTS_TRUST_ENV, not REQUESTS_TRUST_ENV]
    # Evitar duplicados cuando REQUESTS_TRUST_ENV ya es booleano opuesto imposible.
    seen = set()
    for trust in trust_candidates:
        if trust in seen:
            continue
        seen.add(trust)
        sess = requests.Session()
        sess.trust_env = trust
        try:
            return sess.get(
                url,
                timeout=timeout,
                headers=headers,
                stream=stream,
                allow_redirects=allow_redirects,
            )
        except requests.exceptions.RequestException as e:
            last_error = e
            try:
                sess.close()
            except Exception:
                pass
            continue
    if last_error:
        raise last_error
    raise RuntimeError("No se pudo crear conexión HTTP")


def _source_fingerprint(url: str) -> str:
    return _sha256_text((url or "").strip().lower())


def _db_conn() -> sqlite3.Connection:
    SVX_CORE_DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(SVX_CORE_DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("PRAGMA foreign_keys=ON")
    except sqlite3.OperationalError:
        # En algunos hosts/discos (o ACLs restringidas) los PRAGMA pueden fallar.
        # Seguimos con la conexión base para no tumbar el flujo principal.
        pass
    return conn


def _log_view_event(token: str, session_id: str, event: str, detail: str = "") -> None:
    with DB_LOCK:
        conn = _db_conn()
        try:
            conn.execute(
                "INSERT INTO view_logs(token, session_id, event, detail, created_at) VALUES (?, ?, ?, ?, ?)",
                (token, session_id, event, detail, _iso_utc(_utc_now()))
            )
            conn.commit()
        finally:
            conn.close()


def _init_svx_core_db() -> None:
    with DB_LOCK:
        conn = _db_conn()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS videos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    token TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    expires_at TEXT,
                    revoked_at TEXT
                );

                CREATE TABLE IF NOT EXISTS video_sources (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    video_id INTEGER NOT NULL,
                    url TEXT NOT NULL,
                    provider TEXT,
                    priority INTEGER NOT NULL DEFAULT 100,
                    active INTEGER NOT NULL DEFAULT 1,
                    direct_url TEXT,
                    ext TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(video_id) REFERENCES videos(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS svx_index_cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_hash TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    header_size INTEGER NOT NULL,
                    index_len INTEGER NOT NULL,
                    index_json TEXT NOT NULL,
                    salt_b64 TEXT NOT NULL,
                    iv_b64 TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(source_hash, password_hash)
                );

                CREATE TABLE IF NOT EXISTS source_health (
                    source_id INTEGER PRIMARY KEY,
                    ok_count INTEGER NOT NULL DEFAULT 0,
                    fail_count INTEGER NOT NULL DEFAULT 0,
                    last_latency_ms REAL,
                    last_error TEXT,
                    last_checked_at TEXT,
                    FOREIGN KEY(source_id) REFERENCES video_sources(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS play_sessions (
                    id TEXT PRIMARY KEY,
                    token TEXT NOT NULL,
                    video_id INTEGER NOT NULL,
                    source_id INTEGER NOT NULL,
                    resolved_url TEXT NOT NULL,
                    password TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    manifest_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    FOREIGN KEY(video_id) REFERENCES videos(id) ON DELETE CASCADE,
                    FOREIGN KEY(source_id) REFERENCES video_sources(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS view_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    token TEXT,
                    session_id TEXT,
                    event TEXT NOT NULL,
                    detail TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_video_sources_video_id ON video_sources(video_id);
                CREATE INDEX IF NOT EXISTS idx_video_sources_priority ON video_sources(video_id, priority, active);
                CREATE INDEX IF NOT EXISTS idx_play_sessions_token ON play_sessions(token, active);
                CREATE INDEX IF NOT EXISTS idx_index_cache_lookup ON svx_index_cache(source_hash, password_hash);
                """
            )
            conn.commit()
        finally:
            conn.close()


def _migrate_legacy_json_once() -> None:
    with DB_LOCK:
        conn = _db_conn()
        try:
            migrated = conn.execute("SELECT value FROM meta WHERE key = 'legacy_json_migrated'").fetchone()
            if migrated and migrated["value"] == "1":
                return

            count_row = conn.execute("SELECT COUNT(*) AS c FROM videos").fetchone()
            if count_row and int(count_row["c"]) > 0:
                conn.execute(
                    "INSERT OR REPLACE INTO meta(key, value) VALUES ('legacy_json_migrated', '1')"
                )
                conn.commit()
                return

            if not DB_FILE.exists():
                conn.execute(
                    "INSERT OR REPLACE INTO meta(key, value) VALUES ('legacy_json_migrated', '1')"
                )
                conn.commit()
                return

            try:
                with open(DB_FILE, "r", encoding="utf-8") as f:
                    payload = json.load(f)
            except Exception:
                payload = []

            if not isinstance(payload, list):
                payload = []

            for item in payload:
                if not isinstance(item, dict):
                    continue
                source_url = (item.get("url") or "").strip()
                if not source_url:
                    continue
                token = _token_from_input(item.get("id", ""))
                title = (item.get("title") or Path(urlparse(source_url).path).name or token).strip()
                created_at = _iso_utc(_utc_now())
                expires_at = _iso_utc(_utc_now() + timedelta(hours=TOKEN_DEFAULT_TTL_HOURS))
                conn.execute(
                    "INSERT OR IGNORE INTO videos(token, title, status, created_at, expires_at) VALUES (?, ?, 'active', ?, ?)",
                    (token, title, created_at, expires_at)
                )
                row = conn.execute("SELECT id FROM videos WHERE token = ?", (token,)).fetchone()
                if not row:
                    continue
                video_id = int(row["id"])
                ext = ((item.get("ext") or "").strip() or Path(urlparse(source_url).path).suffix.lstrip(".")).lower()
                direct_url = (item.get("direct_url") or "").strip()
                conn.execute(
                    """
                    INSERT INTO video_sources(video_id, url, provider, priority, active, direct_url, ext, created_at)
                    VALUES (?, ?, ?, ?, 1, ?, ?, ?)
                    """,
                    (video_id, source_url, "legacy", 100, direct_url or None, ext or None, created_at)
                )

            conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES ('legacy_json_migrated', '1')"
            )
            conn.commit()
        finally:
            conn.close()


class TokenSourceInput(BaseModel):
    url: str
    provider: str | None = None
    priority: int = 100
    active: bool = True


class TokenRegisterInput(BaseModel):
    token: str | None = None
    title: str = Field(min_length=1)
    expires_in_hours: int = Field(default=TOKEN_DEFAULT_TTL_HOURS, ge=1, le=24 * 365)
    sources: list[TokenSourceInput] = Field(min_items=1)


class TokenSourcesPatchInput(BaseModel):
    sources: list[TokenSourceInput] = Field(min_items=1)


class PlaySessionInput(BaseModel):
    password: str = ""
    preferred_source_id: int | None = None
    mode: str | None = None


def _require_admin(request: Request) -> None:
    if not ADMIN_SECRET:
        return
    incoming = (request.headers.get("x-admin-secret") or "").strip()
    if incoming != ADMIN_SECRET:
        raise HTTPException(401, "Acceso administrativo requerido")


def _record_source_health(source_id: int, ok: bool, latency_ms: float | None = None, error: str = "") -> None:
    now = _iso_utc(_utc_now())
    try:
        with DB_LOCK:
            conn = _db_conn()
            try:
                row = conn.execute(
                    "SELECT source_id, ok_count, fail_count FROM source_health WHERE source_id = ?",
                    (source_id,)
                ).fetchone()
                if not row:
                    conn.execute(
                        """
                        INSERT INTO source_health(source_id, ok_count, fail_count, last_latency_ms, last_error, last_checked_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (source_id, 1 if ok else 0, 0 if ok else 1, latency_ms, None if ok else (error or "error"), now)
                    )
                else:
                    conn.execute(
                        """
                        UPDATE source_health
                        SET ok_count = ?,
                            fail_count = ?,
                            last_latency_ms = ?,
                            last_error = ?,
                            last_checked_at = ?
                        WHERE source_id = ?
                        """,
                        (
                            int(row["ok_count"]) + (1 if ok else 0),
                            int(row["fail_count"]) + (0 if ok else 1),
                            latency_ms,
                            None if ok else (error or "error"),
                            now,
                            source_id,
                        )
                    )
                conn.commit()
            finally:
                conn.close()
    except sqlite3.OperationalError:
        # Telemetría no debe romper creación de sesión/reproducción.
        return


def _resolve_source_url(source_row: sqlite3.Row) -> tuple[str, str]:
    raw_url = (source_row["url"] or "").strip()
    provider = (source_row["provider"] or "").lower()
    ext = ((source_row["ext"] or "").strip() or Path(urlparse(raw_url).path).suffix.lstrip(".")).lower()
    direct_url = (source_row["direct_url"] or "").strip()

    if direct_url:
        return direct_url, ext or Path(urlparse(direct_url).path).suffix.lstrip(".").lower()

    if "mediafire.com" in raw_url.lower():
        info = get_mediafire_info(raw_url)
        direct = info["download_url"]
        ok_https, _lat, _err = _probe_source(direct)
        if not ok_https:
            direct_http = _mediafire_http_fallback_url(direct)
            if direct_http != direct:
                ok_http, _lat2, _err2 = _probe_source(direct_http)
                if ok_http:
                    direct = direct_http
        ext = (info.get("ext") or ext or "bin").lower()
        with DB_LOCK:
            conn = _db_conn()
            try:
                conn.execute(
                    "UPDATE video_sources SET direct_url = ?, ext = ? WHERE id = ?",
                    (direct, ext, int(source_row["id"]))
                )
                conn.commit()
            finally:
                conn.close()
        return direct, ext

    return raw_url, ext


def _probe_source(url: str, timeout: int = 6) -> tuple[bool, float, str]:
    t0 = datetime.now()
    if not url.startswith(("http://", "https://")):
        p = Path(url)
        elapsed = max((datetime.now() - t0).total_seconds() * 1000.0, 1.0)
        if p.exists() and p.is_file() and p.stat().st_size > 0:
            return True, elapsed, ""
        return False, elapsed, "local_file_not_found"
    try:
        r = _http_get_with_proxy_fallback(
            url,
            headers={"Range": "bytes=0-0", "User-Agent": "SVX-Pro/1.0"},
            timeout=timeout,
            allow_redirects=True,
        )
        ok = r.status_code in (200, 206)
        elapsed = max((datetime.now() - t0).total_seconds() * 1000.0, 1.0)
        if not ok:
            return False, elapsed, f"status={r.status_code}"
        ctype = (r.headers.get("Content-Type") or "").lower()
        if "text/html" in ctype:
            return False, elapsed, "unexpected_html_response"
        return True, elapsed, ""
    except Exception as e:
        elapsed = max((datetime.now() - t0).total_seconds() * 1000.0, 1.0)
        return False, elapsed, str(e)
    finally:
        try:
            r.close()  # type: ignore[name-defined]
        except Exception:
            pass


def _select_best_source(video_id: int, preferred_source_id: int | None = None) -> tuple[sqlite3.Row, str, str]:
    with DB_LOCK:
        conn = _db_conn()
        try:
            query = """
                SELECT vs.*,
                       COALESCE(sh.ok_count, 0) AS ok_count,
                       COALESCE(sh.fail_count, 0) AS fail_count,
                       COALESCE(sh.last_latency_ms, 999999.0) AS last_latency_ms
                FROM video_sources vs
                LEFT JOIN source_health sh ON sh.source_id = vs.id
                WHERE vs.video_id = ? AND vs.active = 1
                ORDER BY
                    CASE WHEN ? IS NOT NULL AND vs.id = ? THEN 0 ELSE 1 END,
                    vs.priority ASC,
                    (COALESCE(sh.fail_count, 0) - COALESCE(sh.ok_count, 0)) ASC,
                    COALESCE(sh.last_latency_ms, 999999.0) ASC
            """
            rows = conn.execute(query, (video_id, preferred_source_id, preferred_source_id)).fetchall()
        finally:
            conn.close()

    if not rows:
        raise HTTPException(404, "No hay fuentes activas para este token")

    errors = []
    for row in rows:
        src_id = int(row["id"])
        try:
            resolved_url, ext = _resolve_source_url(row)
            ok, latency, err = _probe_source(resolved_url)
            if not ok:
                _record_source_health(src_id, ok=False, latency_ms=latency, error=err)
                errors.append(f"source#{src_id}:{err}")
                continue
            _record_source_health(src_id, ok=True, latency_ms=latency)
            return row, resolved_url, ext
        except Exception as e:
            _record_source_health(src_id, ok=False, latency_ms=None, error=str(e))
            errors.append(f"source#{src_id}:{e}")
            continue

    raise HTTPException(503, f"No se pudo conectar a ninguna fuente activa ({'; '.join(errors[:3])})")


def _read_header_meta(path_or_url: str) -> tuple[bytes, bytes, int]:
    if path_or_url.startswith(("http://", "https://")):
        reader = HTTPRangeFile(path_or_url)
        reader.seek(0)
        data = reader.read(_svx.HEADER_BASE)
    else:
        with open(path_or_url, "rb") as f:
            data = f.read(_svx.HEADER_BASE)

    if len(data) < _svx.HEADER_BASE:
        raise ValueError("Archivo SVX incompleto")
    magic = data[:_svx.MAGIC_LEN]
    if magic != _svx.MAGIC:
        raise ValueError("No es un archivo SVX válido")

    salt = data[_svx.MAGIC_LEN:_svx.MAGIC_LEN + _svx.SALT_LEN]
    iv = data[_svx.MAGIC_LEN + _svx.SALT_LEN:_svx.MAGIC_LEN + _svx.SALT_LEN + _svx.IV_LEN]
    index_len = struct.unpack("<I", data[_svx.MAGIC_LEN + _svx.SALT_LEN + _svx.IV_LEN:_svx.HEADER_BASE])[0]
    return salt, iv, index_len


def _load_or_build_index_cache(source_url: str, password: str) -> dict:
    source_hash = _source_fingerprint(source_url)
    password_hash = _sha256_text(password)
    now = _utc_now()
    cache_cutoff = now - timedelta(hours=INDEX_CACHE_MAX_AGE_HOURS)

    row = None
    try:
        with DB_LOCK:
            conn = _db_conn()
            try:
                row = conn.execute(
                    """
                    SELECT * FROM svx_index_cache
                    WHERE source_hash = ? AND password_hash = ?
                    """,
                    (source_hash, password_hash)
                ).fetchone()
            finally:
                conn.close()
    except sqlite3.OperationalError:
        row = None

    if row:
        updated_at = _parse_iso(row["updated_at"])
        if updated_at and updated_at >= cache_cutoff:
            try:
                return {
                    "entries": json.loads(row["index_json"]),
                    "header_size": int(row["header_size"]),
                    "index_len": int(row["index_len"]),
                    "salt_b64": row["salt_b64"],
                    "iv_b64": row["iv_b64"],
                }
            except Exception:
                pass

    is_remote = source_url.startswith(("http://", "https://"))
    if is_remote:
        reader = HTTPRangeFile(source_url)
        index, header_size, _key, _iv = _svx.read_index(None, password, http_range_reader=reader)
    else:
        index, header_size, _key, _iv = _svx.read_index(source_url, password)
    salt, iv, index_len = _read_header_meta(source_url)
    index_json = json.dumps(index, ensure_ascii=False, separators=(",", ":"))
    salt_b64 = base64.b64encode(salt).decode("ascii")
    iv_b64 = base64.b64encode(iv).decode("ascii")
    now_s = _iso_utc(now)

    try:
        with DB_LOCK:
            conn = _db_conn()
            try:
                conn.execute(
                    """
                    INSERT INTO svx_index_cache(source_hash, password_hash, header_size, index_len, index_json, salt_b64, iv_b64, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(source_hash, password_hash) DO UPDATE SET
                        header_size = excluded.header_size,
                        index_len = excluded.index_len,
                        index_json = excluded.index_json,
                        salt_b64 = excluded.salt_b64,
                        iv_b64 = excluded.iv_b64,
                        updated_at = excluded.updated_at
                    """,
                    (source_hash, password_hash, header_size, index_len, index_json, salt_b64, iv_b64, now_s, now_s)
                )
                conn.commit()
            finally:
                conn.close()
    except sqlite3.OperationalError:
        # Si la DB no permite escritura, seguimos sin cache persistente.
        pass

    return {
        "entries": index,
        "header_size": header_size,
        "index_len": index_len,
        "salt_b64": salt_b64,
        "iv_b64": iv_b64,
    }


def _read_range_bytes(path_or_url: str, start: int, end: int) -> bytes:
    if start < 0 or end < start:
        return b""
    if path_or_url.startswith(("http://", "https://")):
        try:
            r = _http_get_with_proxy_fallback(
                path_or_url,
                headers={"Range": f"bytes={start}-{end}", "User-Agent": "SVX-Pro/1.0"},
                timeout=25,
                allow_redirects=True,
            )
            if r.status_code not in (200, 206):
                raise RuntimeError(f"HTTP {r.status_code}")
            return r.content
        finally:
            try:
                r.close()  # type: ignore[name-defined]
            except Exception:
                pass
    with open(path_or_url, "rb") as f:
        f.seek(start)
        return f.read(end - start + 1)


def _load_active_session(session_id: str) -> sqlite3.Row:
    with DB_LOCK:
        conn = _db_conn()
        try:
            row = conn.execute(
                "SELECT * FROM play_sessions WHERE id = ? AND active = 1",
                (session_id,)
            ).fetchone()
        finally:
            conn.close()
    if not row:
        raise HTTPException(404, "Sesión no encontrada")
    expires_at = _parse_iso(row["expires_at"])
    if not expires_at or expires_at <= _utc_now():
        with DB_LOCK:
            conn = _db_conn()
            try:
                conn.execute("UPDATE play_sessions SET active = 0 WHERE id = ?", (session_id,))
                conn.commit()
            finally:
                conn.close()
        raise HTTPException(410, "Sesión expirada")
    return row


def _swap_session_source(session_id: str, source_id: int, resolved_url: str) -> None:
    with DB_LOCK:
        conn = _db_conn()
        try:
            conn.execute(
                "UPDATE play_sessions SET source_id = ?, resolved_url = ? WHERE id = ?",
                (source_id, resolved_url, session_id)
            )
            conn.commit()
        finally:
            conn.close()


_init_svx_core_db()
_migrate_legacy_json_once()


class HTTPRangeFile(io.RawIOBase):
    """
    Simula un archivo local permitiendo acceso aleatorio (seek) mediante peticiones HTTP Range.
    Ideal para listar y extraer archivos de ZIPs remotos sin descargarlos completos.
    """
    def __init__(self, url):
        self.url = url
        self.pos = 0
        self._size = -1
        self._actual_url = url
        self._session = _new_requests_session()
        # Resolver URL final y tamaño
        try:
            r = self._session.head(url, allow_redirects=True, timeout=10)
            self._size = int(r.headers.get('Content-Length', 0))
            self._actual_url = r.url
        except:
            # Fallback a GET si HEAD falla
            r = self._session.get(url, stream=True, timeout=10)
            self._size = int(r.headers.get('Content-Length', 0))
            self._actual_url = r.url
            r.close()

    def seek(self, offset, whence=io.SEEK_SET):
        if whence == io.SEEK_SET: self.pos = offset
        elif whence == io.SEEK_CUR: self.pos += offset
        elif whence == io.SEEK_END: self.pos = self._size + offset
        return self.pos

    def tell(self): return self.pos

    def read(self, n=-1):
        if n == -1: n = self._size - self.pos
        if n <= 0 or self.pos >= self._size: return b''
        
        end = min(self.pos + n - 1, self._size - 1)
        headers = {'Range': f'bytes={self.pos}-{end}'}
        try:
            r = self._session.get(self._actual_url, headers=headers, timeout=15)
            if r.status_code not in (200, 206):
                return b''
            data = r.content
            self.pos += len(data)
            return data
        except:
            return b''

    def readable(self): return True
    def seekable(self): return True
    @property
    def size(self): return self._size

def list_archive_remote_lazy(url, password=""):
    """
    Intenta listar los videos de un ZIP o RAR remoto sin descargarlo todo.
    """
    ext = Path(urlparse(url).path).suffix.lower()
    results = []

    # ZIP: Usar HTTPRangeFile para leer solo el final (Central Directory)
    if ext == '.zip' or 'zip' in url.lower():
        import pyzipper
        try:
            remote_f = HTTPRangeFile(url)
            if remote_f.size < 100: return []
            with pyzipper.AESZipFile(remote_f, 'r') as zf:
                if password: zf.setpassword(password.encode())
                for info in zf.infolist():
                    if Path(info.filename).suffix.lower() in VIDEO_EXTS:
                        results.append({
                            "filename": info.filename,
                            "size_str": f"{info.file_size/1024/1024:.1f} MB",
                            "ext": Path(info.filename).suffix.lower()[1:],
                        })
            return results
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"Lazy ZIP list failed: {e}")
            return []

    # RAR: Descargar solo los primeros 2MB y probar unrar
    elif ext == '.rar' or 'rar' in url.lower():
        try:
            sess = _new_requests_session()
            r = sess.get(url, stream=True, timeout=10)
            chunk = b""
            for data in r.iter_content(chunk_size=1024*1024):
                chunk += data
                if len(chunk) >= 2*1024*1024: break
            r.close()
            
            with tempfile.NamedTemporaryFile(suffix=".rar", delete=False) as tmp:
                tmp.write(chunk)
                tmp_path = tmp.name
            
            import subprocess
            rar_p_flag = f'-p{password}' if password else '-p-'
            try:
                out = subprocess.check_output(
                    [UNRAR_BIN, 'vb', rar_p_flag, tmp_path],
                    text=True, timeout=5, stderr=subprocess.DEVNULL
                )
                for line in out.splitlines():
                    if Path(line.strip()).suffix.lower() in VIDEO_EXTS:
                        results.append({
                            "filename": line.strip(), "size_str": "?",
                            "ext": Path(line.strip()).suffix.lower()[1:],
                        })
            finally:
                Path(tmp_path).unlink(missing_ok=True)
            return results
        except:
            return []
    
    return []

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(content=b"", media_type="image/x-icon", status_code=204)

@app.get("/rar-live")
async def rar_live_page(request: Request):
    tpl_path = Path(__file__).parent / "templates" / "rar_live.html"
    with open(tpl_path, encoding="utf-8") as f:
        return HTMLResponse(f.read())


@app.get("/admin/tokens")
async def admin_tokens_page(request: Request):
    tpl_path = Path(__file__).parent / "templates" / "admin_tokens.html"
    with open(tpl_path, encoding="utf-8") as f:
        return HTMLResponse(f.read())

def normalize_input_url(url: str):
    return (url or "").strip()

def get_mediafire_info(url: str) -> dict:
    """
    Extrae la URL de descarga directa de un enlace de MediaFire.
    Soporta: https://www.mediafire.com/file/{key}/{filename}/file
    """
    from html import unescape
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'es-ES,es;q=0.9,en;q=0.8',
    }
    resp = _http_get_with_proxy_fallback(url, headers=headers, timeout=20, allow_redirects=False, stream=True)
    
    # Manejar redirección automática (Cuentas Premium de MediaFire)
    if resp.status_code in (301, 302, 303, 307, 308) and "Location" in resp.headers:
        download_url = resp.headers["Location"]
        resp.close()
        # Fallback para nombre del archivo
        name_m = re.search(r'mediafire\.com/file/[^/]+/([^/]+)/', url)
        title = name_m.group(1) if name_m else "mediafire_file"
        ext = title.rsplit('.', 1)[-1].lower() if '.' in title else 'bin'
        from urllib.parse import unquote
        title = unquote(title)
        return {
            'title':        title,
            'download_url': download_url,
            'ext':          ext,
        }

    resp.raise_for_status()
    html = resp.text
    resp.close()

    # Buscar URL de descarga directa (download\d+.mediafire.com)
    m = re.search(r'href="(https://download\d+\.mediafire\.com/[^"]+)"', html)
    if not m:
        m = re.search(r"href='(https://download\d+\.mediafire\.com/[^']+)'", html)
    if not m:
        m = re.search(r'"(https://download\d+\.mediafire\.com/[^"]+)"', html)
    if not m:
        raise ValueError("No se encontró URL de descarga directa en la página de MediaFire")

    download_url = unescape(m.group(1))   # convierte &amp; → &

    # Extraer nombre del archivo (intentar desde HTML primero, luego URL)
    title = ""
    title_m = re.search(r'class="filename"[^>]*>\s*([^<]+)<', html)
    if not title_m:
        title_m = re.search(r'"filename"\s*:\s*"([^"]+)"', html)
    if title_m:
        title = title_m.group(1).strip()
    if not title:
        # Fallback: tomar del segmento del URL original
        name_m = re.search(r'mediafire\.com/file/[^/]+/([^/]+)/', url)
        title = name_m.group(1) if name_m else "mediafire_file"

    ext = title.rsplit('.', 1)[-1].lower() if '.' in title else 'bin'

    return {
        'title':        title,
        'download_url': download_url,
        'ext':          ext,
    }

@app.get("/api/extract")
async def extract_direct_url(url: str, quality: str = "audio", bypass: bool = False, password: str = ""):
    try:
        url = normalize_input_url(url)
        if "mediafire.com" not in url.lower():
            # Compatibilidad: permitir token SVX Pro en endpoint legacy.
            with DB_LOCK:
                conn = _db_conn()
                try:
                    token_row = conn.execute(
                        "SELECT token FROM videos WHERE token = ?",
                        (url,)
                    ).fetchone()
                finally:
                    conn.close()
            if token_row:
                return await play_session_create(url, PlaySessionInput(password=password, mode="webcrypto"))
            raise HTTPException(400, "Only mediafire is supported")

        # Buscar en la BD si ya tenemos datos precargados
        db_entry = None
        if DB_FILE.exists():
            with open(DB_FILE) as _f:
                _db = json.load(_f)
            for _v in _db:
                if _v.get("url") == url or _v.get("id") == url:
                    db_entry = _v
                    break

        # Si ya tenemos la URL directa cacheada en la BD, usarla
        direct_url = (db_entry or {}).get("direct_url")
        ext_saved  = (db_entry or {}).get("ext", "")
        password_saved = (db_entry or {}).get("password", "") or password
        if not direct_url:
            mf = await asyncio.get_running_loop().run_in_executor(None, get_mediafire_info, url)
            direct_url = mf["download_url"]
            probe_ok, _probe_lat, _probe_err = await asyncio.get_running_loop().run_in_executor(
                None, lambda: _probe_source(direct_url)
            )
            if not probe_ok:
                candidate = _mediafire_http_fallback_url(direct_url)
                if candidate != direct_url:
                    probe_ok_http, _probe_lat2, _probe_err2 = await asyncio.get_running_loop().run_in_executor(
                        None, lambda: _probe_source(candidate)
                    )
                    if probe_ok_http:
                        direct_url = candidate
            ext_saved  = mf["ext"]

        archive_exts = {"zip", "rar", "7z", "tar", "gz", "bz2"}
        if ext_saved.lower().strip('.') == "svx":
            # ── SVX: Resolver y listar videos encriptados al vuelo ───────────
            index, header_size, key, iv = await asyncio.get_event_loop().run_in_executor(
                None, lambda: _svx.read_index(None, password_saved, http_range_reader=HTTPRangeFile(direct_url))
            )
            from urllib.parse import urlencode as _ue
            videos_out = []
            for e in index:
                v_url = "/api/svx/stream?" + _ue({
                    "path": direct_url, "password": password_saved, "item": e["name"]
                })
                videos_out.append({
                    "filename": e["name"], "name": e["name"],
                    "size_str": f"{e['size']/1024/1024:.1f} MB", "ext": e["ext"],
                    "stream_url": v_url
                })
            return JSONResponse(content={
                "url": videos_out[0]["stream_url"],
                "cached": True,
                "title": Path(urlparse(url).path).name or "Archivo SVX Encriptado",
                "ext": videos_out[0]["ext"],
                "stream_type": "archive_list",
                "archive_files": videos_out
            })

        elif ext_saved.lower().strip('.') in archive_exts:
            # ── Archive: resolver y listar videos on-the-fly ─────────────────
            cache_id, suffix, cached_path = _archive_cache_key(url)
            dl_info = ARCHIVE_DOWNLOADS.get(cache_id, {})

            video_entries = []
            if not cached_path.exists() and dl_info.get("status") != "done":
                video_entries = await asyncio.get_event_loop().run_in_executor(
                    None, list_archive_remote_lazy, direct_url, password_saved
                )
            else:
                file_ext = cached_path.suffix.lower()
                if file_ext == '.zip':
                    import pyzipper
                    try:
                        with pyzipper.AESZipFile(str(cached_path), 'r') as zf:
                            if password_saved: zf.setpassword(password_saved.encode())
                            for info in zf.infolist():
                                if Path(info.filename).suffix.lower() in VIDEO_EXTS:
                                    video_entries.append({
                                        "filename": info.filename,
                                        "size_str": f"{info.file_size/1024/1024:.1f} MB" if info.file_size else "?",
                                        "ext": Path(info.filename).suffix.lower()[1:],
                                    })
                    except Exception as e:
                        raise HTTPException(500, f"Error leyendo ZIP local: {e}")
                elif file_ext == '.rar':
                    import subprocess
                    rar_p_flag = f'-p{password_saved}' if password_saved else '-p-'
                    try:
                        proc = subprocess.run(
                            [UNRAR_BIN, 'vb', rar_p_flag, str(cached_path)],
                            text=True, timeout=30, capture_output=True
                        )
                        out = proc.stdout or ""
                        for line in out.splitlines():
                            if Path(line.strip()).suffix.lower() in VIDEO_EXTS:
                                video_entries.append({
                                    "filename": line.strip(), "size_str": "?",
                                    "ext": Path(line.strip()).suffix.lower()[1:],
                                })
                    except Exception:
                        video_entries = []

                    if not video_entries and cached_path.exists():
                        try:
                            guessed = _guess_video_names_from_partial_rar(str(cached_path))
                            for cand in guessed:
                                if _rar_item_has_extractable_bytes(str(cached_path), cand, password_saved):
                                    video_entries.append({
                                        "filename": cand, "size_str": "?",
                                        "ext": Path(cand).suffix.lower()[1:] if Path(cand).suffix else "mkv",
                                    })
                                    break
                        except Exception:
                            pass

            if not video_entries:
                if dl_info.get('status') != 'downloading':
                    try:
                        _ensure_archive_download_thread(url)
                    except Exception:
                        pass
                return JSONResponse(content={
                    "url": "", "cached": False,
                    "stream_type": "mediafire_archive_downloading",
                    "download_id": cache_id, "direct_url": direct_url,
                    "password": password_saved,
                    "title": f"Cargando indice de {ext_saved.upper()}...",
                })

            from urllib.parse import urlencode as _ue
            def _stream_url_gen(fname):
                return "/api/archive/stream?" + _ue({
                    "path": direct_url if not cached_path.exists() else str(cached_path),
                    "password": password_saved, "item": fname
                })

            videos_out = [{
                "filename": v["filename"], "name": Path(v["filename"]).name,
                "size_str": v["size_str"], "ext": v["ext"],
                "stream_url": _stream_url_gen(v["filename"]),
            } for v in video_entries]

            return JSONResponse(content={
                "url": videos_out[0]["stream_url"],
                "cached": True,
                "title": Path(urlparse(url).path).name or "Archivo Comprimido",
                "ext": videos_out[0]["ext"],
                "stream_type": "archive_list",
                "archive_files": videos_out
            })

        else:
            # Video directo (no comprimido)
            if not direct_url:
                raise HTTPException(400, "No se pudo extraer la URL directa de MediaFire.")
            return JSONResponse(content={
                "url": direct_url,
                "cached": False,
                "ext": ext_saved,
                "is_live": False,
                "stream_type": "direct"
            })

    except HTTPException:
        raise
    except ValueError as e:
        traceback.print_exc()
        raise HTTPException(status_code=400, detail=str(e))
    except requests.exceptions.SSLError as e:
        traceback.print_exc()
        raise HTTPException(
            status_code=502,
            detail=f"Error SSL al conectar con MediaFire/CDN. Revisa proxy/red del servidor. Detalle: {e}"
        )
    except requests.exceptions.RequestException as e:
        traceback.print_exc()
        raise HTTPException(
            status_code=502,
            detail=f"No se pudo conectar a MediaFire/CDN. Detalle: {e}"
        )
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

def _archive_cache_key(url: str) -> tuple[str, str, Path]:
    """
    Devuelve (cache_id, suffix, cached_path) para una URL de archivo.
    Usa el nombre de archivo de la URL como clave de caché (ej: dannan.rar).
    """
    import urllib.parse
    from pathlib import Path as _Path
    url_clean = url.split('?')[0].rstrip('/')
    
    if 'mediafire.com/file/' in url_clean and url_clean.endswith('/file'):
        url_clean = url_clean[:-5].rstrip('/')
        
    url_fname = urllib.parse.unquote(_Path(url_clean).name)
    # Limpiar '+' que MediaFire suele poner en lugar de espacios
    url_fname = url_fname.replace('+', ' ')
    
    # Limpiar caracteres peligrosos y espacios del nombre de archivo
    safe_fname = re.sub(r'[\\/*?:"<>| ]', '_', url_fname)
    if not safe_fname or safe_fname.lower() == 'file':
        # Fallback si no logramos extraer un nombre decente
        safe_fname = hashlib.md5(url.encode()).hexdigest()[:16]

    parts = safe_fname.rsplit('.', 1)
    suffix = f'.{parts[-1].lower()}' if len(parts) > 1 and len(parts[-1]) <= 5 else '.bin'
    
    # Asegurar que el ID y el path sean consistentes
    cache_id = safe_fname
    cached_path = ARCHIVE_CACHE_DIR / safe_fname
    if not cached_path.suffix:
        cached_path = cached_path.with_suffix(suffix)
        cache_id = cached_path.name
        
    return cache_id, suffix, cached_path

def _resolve_archive_path(source: str) -> str:
    """
    Si 'source' es una URL HTTP/HTTPS descarga el archivo al directorio
    de caché y devuelve la ruta local resultante.
    Si ya es una ruta local la devuelve tal cual.
    El archivo se cachea por hash de URL para no descargar dos veces.
    Actualiza ARCHIVE_DOWNLOADS[hash] con progreso en tiempo real.
    """
    if source.startswith(('http://', 'https://')):
        cache_id, suffix, cached_path = _archive_cache_key(source)

        if cached_path.exists():
            # Ya en caché → marcar como done
            ARCHIVE_DOWNLOADS[cache_id] = {
                'status': 'done', 'pct': 100,
                'downloaded': cached_path.stat().st_size,
                'total': cached_path.stat().st_size,
                'local_path': str(cached_path),
                'speed_mb': 0,
            }
            return str(cached_path)

        # Si ya está descargándose en otro hilo, esperar
        import time as _t
        wait_start = _t.time()
        while ARCHIVE_DOWNLOADS.get(cache_id, {}).get('status') == 'downloading':
            _t.sleep(1)
            # Timeout de seguridad (2 horas)
            if _t.time() - wait_start > 7200:
                break
        
        # Re-chequear si terminó mientras esperábamos
        if cached_path.exists():
            return str(cached_path)

        # Crear flag de cancelación para este download
        cancel_event = _threading.Event()
        ARCHIVE_CANCEL_FLAGS[cache_id] = cancel_event

        # Inicializar estado
        ARCHIVE_DOWNLOADS[cache_id] = {
            'status': 'downloading', 'pct': 0,
            'downloaded': 0, 'total': 0,
            'local_path': str(cached_path),
            'speed_mb': 0,
        }
        dl_headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': '*/*',
        }
        import time as _time
        t_start = _time.time()
        cancelled = False
        # Si es MediaFire, necesitamos la URL directa para la descarga real
        actual_download_url = source
        if 'mediafire.com/' in source:
            try:
                # Usar executor para no bloquear si se llama desde sync
                mf = get_mediafire_info(source)
                actual_download_url = mf["download_url"]
                ok_https, _lat, _err = _probe_source(actual_download_url)
                if not ok_https:
                    candidate = _mediafire_http_fallback_url(actual_download_url)
                    if candidate != actual_download_url:
                        ok_http, _lat2, _err2 = _probe_source(candidate)
                        if ok_http:
                            actual_download_url = candidate
            except Exception as e:
                print(f"[archive-dl] Error extrayendo link directo de MediaFire: {e}")
                # Intentamos con la original por si acaso ya era directa

        try:
            sess = _new_requests_session()
            with sess.get(actual_download_url, headers=dl_headers, stream=True, timeout=300) as r:
                r.raise_for_status()
                total_size = int(r.headers.get('Content-Length', 0))
                ARCHIVE_DOWNLOADS[cache_id]['total'] = total_size
                downloaded = 0
                with open(cached_path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=2 * 1024 * 1024):
                        if cancel_event.is_set():
                            cancelled = True
                            break
                        if chunk:
                            f.write(chunk)
                            downloaded += len(chunk)
                            elapsed = max(_time.time() - t_start, 0.001)
                            speed = downloaded / elapsed / 1024 / 1024  # MB/s
                            pct = int(downloaded / total_size * 100) if total_size else 0
                            ARCHIVE_DOWNLOADS[cache_id].update({
                                'downloaded': downloaded,
                                'pct': pct,
                                'speed_mb': round(speed, 1),
                            })
            if cancelled:
                ARCHIVE_DOWNLOADS[cache_id]['status'] = 'cancelled'
                if cached_path.exists():
                    cached_path.unlink(missing_ok=True)
                return str(cached_path)  # return early (won't be used)
            ARCHIVE_DOWNLOADS[cache_id].update({
                'status': 'done', 'pct': 100,
                'local_path': str(cached_path),
            })
        except Exception as e:
            ARCHIVE_DOWNLOADS[cache_id]['status'] = 'error'
            ARCHIVE_DOWNLOADS[cache_id]['error'] = str(e)
            if cached_path.exists():
                cached_path.unlink(missing_ok=True)
            raise
        finally:
            ARCHIVE_CANCEL_FLAGS.pop(cache_id, None)
        return str(cached_path)
    return source

def _ensure_archive_download_thread(url: str):
    """
    Asegura que exista una descarga en background para una URL de archivo.
    Devuelve (cache_id, cached_path, status_actual).
    """
    import threading
    cache_id, _, cached_path = _archive_cache_key(url)
    existing = ARCHIVE_DOWNLOADS.get(cache_id, {})
    status = existing.get("status")

    if cached_path.exists():
        ARCHIVE_DOWNLOADS[cache_id] = {
            'status': 'done', 'pct': 100,
            'downloaded': cached_path.stat().st_size,
            'total': cached_path.stat().st_size,
            'local_path': str(cached_path),
            'speed_mb': 0,
        }
        return cache_id, cached_path, "done"

    if status != "downloading":
        def _do_download():
            try:
                _resolve_archive_path(url)
            except Exception as e:
                print(f"[archive-dl] Error descargando {url[:60]}: {e}")
        threading.Thread(target=_do_download, daemon=True).start()
        status = "downloading"

    return cache_id, cached_path, status

def _refresh_rar_partial_extract_snapshot(local_rar_path: str, item: str, password: str, output_path: Path):
    """
    Intenta extraer (con tolerancia a archivo incompleto) el item de un RAR parcial
    y guarda un snapshot reproducible en output_path.
    Devuelve bytes escritos.
    """
    import subprocess
    tmp_out = output_path.with_suffix(output_path.suffix + ".tmp")
    pw_flag = f"-p{password}" if password else "-p-"
    with open(tmp_out, "wb") as fout:
        proc = subprocess.Popen(
            [UNRAR_BIN, "p", "-inul", "-idq", "-kb", pw_flag, local_rar_path, item],
            stdout=fout, stderr=subprocess.PIPE
        )
        _, _ = proc.communicate(timeout=300)
    size = tmp_out.stat().st_size if tmp_out.exists() else 0
    if size > 0:
        tmp_out.replace(output_path)
        return size
    tmp_out.unlink(missing_ok=True)
    return 0

def _guess_video_names_from_partial_rar(local_rar_path: str, max_bytes: int = 8 * 1024 * 1024):
    """
    Heurística: extrae posibles nombres de video desde bytes parciales del RAR.
    Sirve para intentar stream progresivo antes de que UnRAR liste normalmente.
    """
    p = Path(local_rar_path)
    if not p.exists() or p.stat().st_size == 0:
        return []
    raw = p.read_bytes()[:max_bytes]
    text = raw.decode("latin1", errors="ignore")
    # Buscar rutas/archivos que terminen en extensiones de video comunes.
    patt = re.compile(r"([A-Za-z0-9 _.\-\\/\[\]\(\)]+?\.(?:mp4|mkv|avi|webm|mov|m4v|flv|ts))", re.IGNORECASE)
    found = []
    seen = set()
    for m in patt.finditer(text):
        cand = m.group(1).strip().strip("\x00").replace("/", "\\")
        if len(cand) < 4 or len(cand) > 260:
            continue
        key = cand.lower()
        if key in seen:
            continue
        seen.add(key)
        found.append(cand)
        if len(found) >= 20:
            break
    return found

def _rar_item_has_extractable_bytes(local_rar_path: str, item: str, password: str, min_bytes: int = 4096):
    """
    Comprueba rápidamente si UnRAR ya puede emitir bytes del item desde un RAR parcial.
    """
    import subprocess
    pw_flag = f"-p{password}" if password else "-p-"
    proc = subprocess.Popen(
        [UNRAR_BIN, "p", "-inul", "-idq", "-kb", pw_flag, str(local_rar_path), item],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    try:
        data = proc.stdout.read(min_bytes)
        return len(data) > 0
    finally:
        try:
            proc.kill()
        except Exception:
            pass
        try:
            proc.wait(timeout=1)
        except Exception:
            pass

@app.post("/api/archive/download-start")
async def archive_download_start(request: Request):
    """
    Inicia la descarga de un archivo remoto en background.
    Devuelve { download_id, already_cached } inmediatamente.
    El frontend puede hacer polling a /api/archive/download-status/{id}.
    """
    import threading
    body = await request.json()
    url = body.get("url", "").strip()
    if not url:
        raise HTTPException(400, "URL requerida")

    cache_id, suffix, cached_path = _archive_cache_key(url)

    if cached_path.exists():
        ARCHIVE_DOWNLOADS[cache_id] = {
            'status': 'done', 'pct': 100,
            'downloaded': cached_path.stat().st_size,
            'total': cached_path.stat().st_size,
            'local_path': str(cached_path),
            'speed_mb': 0,
        }
        return JSONResponse({'download_id': cache_id, 'already_cached': True,
                             'local_path': str(cached_path)})

    # Si ya está en progreso no lanzar de nuevo
    existing = ARCHIVE_DOWNLOADS.get(cache_id, {})
    if existing.get('status') == 'downloading':
        return JSONResponse({'download_id': cache_id, 'already_cached': False})

    # Lanzar descarga en thread background
    def _do_download():
        try:
            _resolve_archive_path(url)
        except Exception as e:
            print(f"[archive-dl] Error descargando {url[:60]}: {e}")

    t = threading.Thread(target=_do_download, daemon=True)
    t.start()

    return JSONResponse({'download_id': cache_id, 'already_cached': False})

@app.get("/api/archive/download-status/{download_id}")
async def archive_download_status(download_id: str):
    """
    Devuelve el estado de una descarga iniciada con /api/archive/download-start.
    { status, pct, downloaded_mb, total_mb, speed_mb, local_path?, error? }
    """
    info = ARCHIVE_DOWNLOADS.get(download_id)
    if not info:
        raise HTTPException(404, "Download ID no encontrado")
    total = info.get('total', 0)
    downloaded = info.get('downloaded', 0)
    return JSONResponse({
        'status':        info['status'],
        'pct':           info.get('pct', 0),
        'downloaded_mb': round(downloaded / 1024 / 1024, 1),
        'total_mb':      round(total / 1024 / 1024, 1),
        'speed_mb':      info.get('speed_mb', 0),
        'local_path':    info.get('local_path') if info['status'] == 'done' else None,
        'error':         info.get('error'),
    })

@app.delete("/api/archive/download-cancel/{download_id}")
async def archive_download_cancel(download_id: str):
    """
    Cancela una descarga en progreso.
    Setea el threading.Event para que el hilo de descarga pare en el próximo chunk.
    Borra el archivo parcial y marca status como 'cancelled'.
    """
    info = ARCHIVE_DOWNLOADS.get(download_id)
    if not info:
        raise HTTPException(404, "Download ID no encontrado")
    if info.get('status') != 'downloading':
        return JSONResponse({'ok': False, 'msg': f"Estado actual: {info.get('status')} (no se puede cancelar)"})
    # Señalizar al hilo
    flag = ARCHIVE_CANCEL_FLAGS.get(download_id)
    if flag:
        flag.set()
    # Marcar inmediatamente para que el polling del frontend lo sepa
    ARCHIVE_DOWNLOADS[download_id]['status'] = 'cancelled'
    return JSONResponse({'ok': True, 'msg': 'Cancelación solicitada'})

async def archive_list(path: str, password: str = ""):
    """
    Lista los archivos de video dentro de un ZIP o RAR.
    'path' puede ser ruta local (D:\\...) o URL remota (https://...).
    """
    loop = asyncio.get_running_loop()
    try:
        local_path = await loop.run_in_executor(None, _resolve_archive_path, path)
    except Exception as e:
        raise HTTPException(400, f"No se pudo obtener el archivo: {e}")

    # Determinar qué objeto "File-like" usar: local o remoto
    if local_path and Path(local_path).exists():
        archive_file = local_path
        ext = Path(local_path).suffix.lower()
    elif path.startswith("http") and path.lower().split("?")[0].endswith(".zip"):
        archive_file = HTTPRangeFile(path)
        ext = ".zip"
    else:
        raise HTTPException(404, f"Archivo no encontrado: {path}")
    results = []

    if ext == '.zip':
        import pyzipper
        try:
            with pyzipper.AESZipFile(local_path, 'r') as zf:
                if password:
                    zf.setpassword(password.encode())
                for info in zf.infolist():
                    if Path(info.filename).suffix.lower() in VIDEO_EXTS:
                        results.append({
                            "filename": info.filename,
                            "size": info.file_size,
                            "size_str": f"{info.file_size/1024/1024:.1f} MB",
                            "compress_type": info.compress_type,
                            "storable": info.compress_type == 0,
                        })
        except Exception as e:
            raise HTTPException(500, f"Error leyendo ZIP: {e}")

    elif ext == '.rar':
        import subprocess
        try:
            _rar_p = f'-p{password}' if password else '-p-'
            out = subprocess.check_output(
                [UNRAR_BIN, 'vb', _rar_p, local_path], text=True, timeout=30,
                stderr=subprocess.DEVNULL
            )
            for line in out.splitlines():
                line = line.strip()
                if Path(line).suffix.lower() in VIDEO_EXTS:
                    results.append({
                        "filename": line,
                        "size": None,
                        "storable": False,
                    })
        except Exception as e:
            raise HTTPException(500, f"Error leyendo RAR: {e}")
    else:
        raise HTTPException(400, "Solo se soportan .zip y .rar")

    return JSONResponse({"files": results, "count": len(results)})

@app.get("/api/archive/stream")
async def archive_stream(request: Request, path: str, password: str = "", item: str = ""):
    """
    Streaming progresivo del video contenido en un ZIP/RAR.
    'path' puede ser ruta local o URL remota.
    Soporta Range requests. Para ZIPs sin comprimir usa FastPath (seek directo).
    """
    import time as _t
    loop = asyncio.get_running_loop()

    # Resolver ruta local si existe en caché
    local_path = ""
    cache_id, _, cached_path = _archive_cache_key(path)
    if cached_path.exists():
        local_path = str(cached_path)

    # Determinar si es un ZIP remoto para streaming on-demand
    is_remote_zip = (not local_path) and ('.zip' in path.lower() or 'zip' in path.split('?')[0].lower())
    # RAR remoto: intentar modo progresivo con archivo parcial
    path_lower = path.lower()
    is_remote_rar = (not local_path) and path.startswith(('http://', 'https://')) and ('.rar' in path_lower or 'rar' in path.split('?')[0].lower())

    if not local_path and is_remote_rar:
        _dl_id, _dl_cached_path, _dl_status = _ensure_archive_download_thread(path)
        # Dar una pequeÃ±a ventana para que aparezca el archivo parcial
        for _ in range(20):
            if _dl_cached_path.exists() and _dl_cached_path.stat().st_size > 0:
                local_path = str(_dl_cached_path)
                break
            await asyncio.sleep(0.15)
        if not local_path:
            raise HTTPException(425, "RAR en preparacion. Reintenta en unos segundos.")
    elif not local_path and not is_remote_zip:
        try:
            local_path = await loop.run_in_executor(None, _resolve_archive_path, path)
        except Exception as e:
            raise HTTPException(400, f"No se pudo obtener el archivo: {e}")

    # Determinar qué objeto File-like usar: local o remoto
    if local_path and Path(local_path).exists():
        archive_file = local_path
        ext = Path(local_path).suffix.lower()
        source_label = f"LOCAL:{Path(local_path).name}"
    elif is_remote_zip:
        archive_file = HTTPRangeFile(path)
        ext = ".zip"
        source_label = f"REMOTO:{path.split('/')[-1]}"
    else:
        raise HTTPException(404, f"Archivo no encontrado: {path}")

    print(f"\n[ARCHIVE STREAM] ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"[ARCHIVE STREAM] 📂 Fuente  : {source_label}")
    print(f"[ARCHIVE STREAM] 📋 Formato : {ext.upper()}")
    print(f"[ARCHIVE STREAM] 🎯 Item    : {item or '(primero)'}")

    # ── ZIP ──────────────────────────────────────────────────────────────
    if ext == '.zip':
        import pyzipper, struct

        with pyzipper.AESZipFile(archive_file, 'r') as zf:
            if password:
                zf.setpassword(password.encode())
            videos = [i for i in zf.infolist() if Path(i.filename).suffix.lower() in VIDEO_EXTS]
            if not videos:
                raise HTTPException(404, "No hay video en el ZIP")
            target_info = next((i for i in videos if i.filename == item), videos[0])
            total_size  = target_info.file_size
            mime        = MIME_MAP.get(Path(target_info.filename).suffix.lower()[1:], 'video/mp4')
            
            # Detectar si está cifrado y si podemos usar FastPath
            is_encrypted = bool(target_info.flag_bits & 0x1)
            is_stored    = (target_info.compress_type == 0)
            use_fastpath = is_stored and not is_encrypted and not password

        compress_desc = "STORED (sin compresión)" if is_stored else f"COMPRESSED (método {target_info.compress_type})"
        crypto_desc   = "CIFRADO (Password)" if is_encrypted else "SIN CIFRAR"
        print(f"[ARCHIVE STREAM] 🗜  Zip Info  : {compress_desc} | {crypto_desc}")
        print(f"[ARCHIVE STREAM] ⚡ FastPath  : {'SÍ' if use_fastpath else 'NO'}")
        print(f"[ARCHIVE STREAM] 📏 Tamaño    : {total_size / 1024 / 1024:.1f} MB")

        range_header = request.headers.get('range')
        start = 0
        end   = total_size - 1

        if range_header:
            m = re.match(r'bytes=(\d*)-(\d*)', range_header)
            if m:
                start = int(m.group(1)) if m.group(1) else 0
                end   = int(m.group(2)) if m.group(2) else total_size - 1
                end   = min(end, total_size - 1)

        chunk_size  = 512 * 1024
        content_len = end - start + 1

        _path_ref   = archive_file
        _pwd_ref    = password
        _fname_ref  = target_info.filename
        _hdr_offset = target_info.header_offset

        print(f"[ARCHIVE STREAM] 🔍 Range     : bytes={start}-{end}/{total_size} ({content_len//1024}KB)")
        if use_fastpath:
            print(f"[ARCHIVE STREAM] 🚀 Modo      : FASTPATH (seek directo)")
        else:
            print(f"[ARCHIVE STREAM] 🔄 Modo      : FALLBACK (procesamiento con password/descompresión)")

        def stream_zip(skip: int, length: int):
            import pyzipper as _pz, struct as _st, time as _ts

            t_start = _ts.time()
            sent    = 0

            # ── FastPath: archivo sin compresión y sin cifrar ───────────
            if use_fastpath:
                if isinstance(_path_ref, HTTPRangeFile):
                    file_like   = _path_ref
                    should_close = False
                else:
                    file_like    = open(_path_ref, 'rb')
                    should_close = True

                try:
                    # Leer header local para obtener el offset exacto de los datos.
                    # El header local puede tener campo 'extra' de tamaño diferente
                    # al directorio central, por eso no podemos asumir un offset fijo.
                    file_like.seek(_hdr_offset)
                    hdr = file_like.read(30)

                    if hdr[:4] == b'PK\x03\x04':
                        fname_len  = _st.unpack('<H', hdr[26:28])[0]
                        extra_len  = _st.unpack('<H', hdr[28:30])[0]
                        data_start = _hdr_offset + 30 + fname_len + extra_len
                        seek_pos   = data_start + skip

                        print(f"[ZIP FASTPATH] 📍 data_offset={data_start} | seek_to={seek_pos}")

                        file_like.seek(seek_pos)

                        last_log = 0
                        while sent < length:
                            to_read = min(chunk_size, length - sent)
                            chunk   = file_like.read(to_read)
                            if not chunk:
                                break
                            yield chunk
                            sent += len(chunk)

                            # Log cada 10 MB
                            if sent - last_log >= 10 * 1024 * 1024:
                                last_log   = sent
                                elapsed    = max(_ts.time() - t_start, 0.001)
                                speed_mb   = sent / elapsed / 1024 / 1024
                                pct        = int(sent / length * 100)
                                print(f"[ZIP FASTPATH] 📊 {pct}% | "
                                      f"{sent//1024//1024}MB/{length//1024//1024}MB | "
                                      f"{speed_mb:.1f} MB/s")

                        elapsed  = max(_ts.time() - t_start, 0.001)
                        speed_mb = sent / elapsed / 1024 / 1024
                        print(f"[ZIP FASTPATH] ✅ Completado | {sent//1024//1024}MB | "
                              f"{speed_mb:.1f} MB/s | {elapsed:.2f}s")
                        return

                    # Header inválido → caer al fallback
                    print(f"[ZIP FASTPATH] ⚠ Header inválido, usando fallback")

                finally:
                    if should_close:
                        file_like.close()

            # ── Fallback: descompresión en streaming ─────────────────────
            print(f"[ZIP FALLBACK] 🔄 Descomprimiendo | skip={skip//1024}KB")
            last_log = 0
            with _pz.AESZipFile(_path_ref, 'r') as zf:
                if _pwd_ref:
                    zf.setpassword(_pwd_ref.encode())
                with zf.open(_fname_ref) as f:
                    remaining_skip = skip
                    while remaining_skip > 0:
                        discard = min(remaining_skip, chunk_size)
                        f.read(discard)
                        remaining_skip -= discard

                    while sent < length:
                        to_read = min(chunk_size, length - sent)
                        chunk   = f.read(to_read)
                        if not chunk:
                            break
                        yield chunk
                        sent += len(chunk)

                        if sent - last_log >= 10 * 1024 * 1024:
                            last_log  = sent
                            elapsed   = max(_ts.time() - t_start, 0.001)
                            speed_mb  = sent / elapsed / 1024 / 1024
                            pct       = int(sent / length * 100)
                            print(f"[ZIP FALLBACK] 📊 {pct}% | "
                                  f"{sent//1024//1024}MB/{length//1024//1024}MB | "
                                  f"{speed_mb:.1f} MB/s")

            elapsed  = max(_ts.time() - t_start, 0.001)
            speed_mb = sent / elapsed / 1024 / 1024
            print(f"[ZIP FALLBACK] ✅ Completado | {sent//1024//1024}MB | "
                  f"{speed_mb:.1f} MB/s | {elapsed:.2f}s")

        status  = 206 if range_header else 200
        headers = {
            'Content-Range':  f'bytes {start}-{end}/{total_size}',
            'Accept-Ranges':  'bytes',
            'Content-Length': str(content_len),
            'Content-Type':   mime,
            'Cache-Control':  'no-cache',
        }
        return StreamingResponse(stream_zip(start, content_len),
                                 status_code=status, headers=headers, media_type=mime)

    # ── RAR ──────────────────────────────────────────────────────────────────
    elif ext == '.rar':
        import subprocess, shutil
        from fastapi.responses import FileResponse

        if not Path(UNRAR_BIN).exists():
            raise HTTPException(500, "UnRAR no encontrado en el servidor")

        # Listar y elegir video si no se especificó
        if not item:
            def _try_list_rar(pw_flag: str):
                return subprocess.check_output(
                    [UNRAR_BIN, 'vb', pw_flag, local_path], text=True,
                    stderr=subprocess.DEVNULL
                )
            list_out = None
            if password:
                try: list_out = _try_list_rar(f'-p{password}')
                except: list_out = None
            if list_out is None:
                try: list_out = _try_list_rar('-p-')
                except Exception as e: raise HTTPException(500, f"Error leyendo RAR: {e}")

            videos = [l.strip() for l in list_out.splitlines() if Path(l.strip()).suffix.lower() in VIDEO_EXTS]
            if not videos: raise HTTPException(404, "No hay video en el RAR")
            item = videos[0]

        # Definir MIME tipo basado en el item
        mime = MIME_MAP.get(Path(item).suffix.lower()[1:], 'video/mp4')

        # 1. Obtener información del item (Tamaño real)
        item_size = None
        try:
            _rar_p_info = f'-p{password}' if password else '-p-'
            info_out = subprocess.check_output(
                [UNRAR_BIN, 'vt', '-y', _rar_p_info, local_path, item], 
                text=True, timeout=15, stderr=subprocess.DEVNULL
            )
            m_size = re.search(r'(?:Size|Tamaño):\s+(\d+)', info_out)
            if m_size: item_size = int(m_size.group(1))
        except: pass

        # 2. Gestionar extracción transparente
        safe_item_name = re.sub(r'[^a-zA-Z0-9._-]', '_', item)
        item_hash = hashlib.md5(item.encode()).hexdigest()[:8]
        extracted_path = ARCHIVE_CACHE_DIR / f"ext_{cache_id}_{item_hash}_{safe_item_name}"
        is_partial_source = False
        if path.startswith(('http://', 'https://')) and local_path and Path(local_path).exists():
            dl = ARCHIVE_DOWNLOADS.get(cache_id, {})
            is_partial_source = dl.get('status') == 'downloading'

        if not extracted_path.exists():
            print(f"[RAR STREAM] 🚀 Extrayendo item: {item}")
            _rar_p_ext = f'-p{password}' if password else '-p-'
            try:
                # Extraer archivo (flattens paths con 'e')
                subprocess.run(
                    [UNRAR_BIN, 'e', '-y', '-idq', _rar_p_ext, local_path, item, str(ARCHIVE_CACHE_DIR)],
                    check=True, timeout=300
                )
                original_extracted = ARCHIVE_CACHE_DIR / Path(item).name
                if original_extracted.exists():
                    original_extracted.rename(extracted_path)
                else:
                    raise FileNotFoundError("Error en extracción individual")
            except Exception as e:
                print(f"[RAR STREAM] ❌ Error extrayendo RAR: {e}")
                _local_path_rar = local_path
                def stream_rar_fallback():
                    _p = f'-p{password}' if password else '-p-'
                    proc = subprocess.Popen(
                        # -kb conserva bytes extraibles si el RAR esta incompleto.
                        [UNRAR_BIN, 'p', '-inul', '-idq', '-kb', _p, _local_path_rar, item],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE
                    )
                    try:
                        while True:
                            chunk = proc.stdout.read(512 * 1024)
                            if not chunk: break
                            yield chunk
                    finally:
                        proc.stdout.close(); proc.wait()
                return StreamingResponse(stream_rar_fallback(), media_type=mime)

        if is_partial_source and not extracted_path.exists():
            print(f"[RAR STREAM] Modo progresivo (fuente parcial): {Path(local_path).name}")
            progressive_snapshot = ARCHIVE_CACHE_DIR / f"prg_{cache_id}_{item_hash}_{safe_item_name}"
            try:
                snap_size = await loop.run_in_executor(
                    None,
                    _refresh_rar_partial_extract_snapshot,
                    local_path,
                    item,
                    password,
                    progressive_snapshot
                )
            except Exception as e:
                snap_size = 0
                print(f"[RAR STREAM] Snapshot progresivo fallo: {e}")

            if snap_size > 0 and progressive_snapshot.exists():
                print(f"[RAR STREAM] Snapshot progresivo listo: {progressive_snapshot.name} ({snap_size} bytes)")
                return FileResponse(
                    path=progressive_snapshot,
                    media_type=mime,
                    filename=Path(item).name,
                    content_disposition_type="inline",
                    headers={"X-RAR-Progressive": "1"}
                )

            _local_path_rar = local_path
            def stream_rar_progressive():
                _p = f'-p{password}' if password else '-p-'
                proc = subprocess.Popen(
                    [UNRAR_BIN, 'p', '-inul', '-idq', '-kb', _p, _local_path_rar, item],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE
                )
                try:
                    while True:
                        chunk = proc.stdout.read(512 * 1024)
                        if not chunk:
                            break
                        yield chunk
                finally:
                    proc.stdout.close()
                    proc.wait()
            return StreamingResponse(
                stream_rar_progressive(),
                media_type=mime,
                headers={"X-RAR-Progressive": "1"}
            )

        # 3. Servir el archivo extraído (FileResponse soporta Range nativo)
        print(f"[RAR STREAM] ✅ Sirviendo desde caché: {extracted_path.name}")
        return FileResponse(
            path=extracted_path,
            media_type=mime,
            filename=Path(item).name,
            content_disposition_type="inline"
        )

    else:
        raise HTTPException(400, "Solo se soportan .zip y .rar")

@app.get("/")
async def svx_dashboard(request: Request):
    tpl_path = Path(__file__).parent / "templates" / "index.html"
    with open(tpl_path, encoding="utf-8") as f:
        return HTMLResponse(f.read())

@app.post("/api/svx/create")
async def svx_create(
    files: list[UploadFile] = File(...),
    password: str = Form(""),
):
    """
    Recibe uno o más archivos de video via multipart form-data, los empaqueta
    en un .svx encriptado con AES-256-CTR y lo devuelve como descarga.
    """
    import tempfile, shutil
    tmp_dir = Path(tempfile.mkdtemp(prefix="svx_"))
    saved_paths = []

    try:
        for uf in files:
            if not uf.filename:
                continue
            dest = tmp_dir / uf.filename
            content = await uf.read()
            dest.write_bytes(content)
            saved_paths.append(dest)

        if not saved_paths:
            raise HTTPException(400, "No se recibieron archivos validos")

        out_name = saved_paths[0].stem + ".svx"
        out_path = tmp_dir / out_name

        # Generar .svx real antes de responder
        await asyncio.get_event_loop().run_in_executor(
            None, lambda: _svx.pack([str(p) for p in saved_paths], password, str(out_path))
        )

        # Validar resultado
        if out_path.exists():
            print(f"SVX created successfully: {out_path} ({out_path.stat().st_size} bytes)")
        else:
            print(f"ERROR: SVX file not found at {out_path}")
            raise HTTPException(500, "Archivo .svx no generado")

        async def iter_file():
            with open(out_path, mode="rb") as f:
                while chunk := f.read(1024*1024): # 1MB chunks
                    yield chunk
        
        # Manually close uploaded files
        for uf in files:
            await uf.close()

        print(f"Streaming response for {out_name}...")
        return StreamingResponse(
            iter_file(),
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{quote(out_name)}",
                "Access-Control-Expose-Headers": "Content-Disposition"
            },
            background=BackgroundTask(lambda: __import__("shutil").rmtree(tmp_dir, ignore_errors=True)),
        )

    except HTTPException:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    except Exception as e:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise HTTPException(500, f"Error al crear .svx: {e}")

@app.get("/api/svx/stream")
async def svx_stream(
    request: Request,
    path: str = "",          # URL de MediaFire o path local
    password: str = "",
    item: str = "",          # nombre del archivo dentro del .svx (vacío = primero)
):
    """
    Reproduce un archivo dentro de un .svx de forma de streaming al vuelo.
    Soporta HTTP Range requests para seek del reproductor de video.
    """
    if not path:
        raise HTTPException(400, "Se requiere el parámetro path")

    # ── Determinar si es remoto o local ─────────────────────────────────────
    is_remote = path.startswith("http://") or path.startswith("https://")

    try:
        if is_remote:
            reader = HTTPRangeFile(path)
            index, header_size, key, iv = await asyncio.get_event_loop().run_in_executor(
                None, lambda: _svx.read_index(None, password, http_range_reader=reader)
            )
        else:
            reader = None
            index, header_size, key, iv = await asyncio.get_event_loop().run_in_executor(
                None, lambda: _svx.read_index(path, password)
            )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"Error leyendo .svx: {e}")

    if not index:
        raise HTTPException(404, "El archivo .svx no contiene entradas")

    # Seleccionar entrada
    if item:
        entry = next((e for e in index if e["name"] == item), None)
        if not entry:
            raise HTTPException(404, f"Entrada '{item}' no encontrada en el .svx")
    else:
        entry = index[0]

    file_size = entry["size"]
    ext       = entry.get("ext", "mp4")
    mime_map  = {"mp4": "video/mp4", "mkv": "video/x-matroska",
                 "avi": "video/x-msvideo", "mov": "video/quicktime",
                 "webm": "video/webm", "ts": "video/mp2t"}
    mime_type = mime_map.get(ext, "video/mp4")

    # ── HTTP Range ───────────────────────────────────────────────────────────
    range_header = request.headers.get("Range", "")
    byte_start, byte_end = 0, file_size - 1
    status_code = 200

    if range_header:
        try:
            rng = range_header.strip().replace("bytes=", "")
            parts = rng.split("-")
            byte_start = int(parts[0]) if parts[0] else 0
            byte_end   = int(parts[1]) if parts[1] else file_size - 1
            byte_end   = min(byte_end, file_size - 1)
            status_code = 206
        except Exception:
            pass

    content_length = byte_end - byte_start + 1

    # Generador de stream desencriptado
    svx_ref = reader if is_remote else path
    gen = _svx.stream_entry(
        svx_ref, entry, password,
        header_size, key, iv,
        byte_start=byte_start, byte_end=byte_end
    )

    headers = {
        "Content-Type": mime_type,
        "Accept-Ranges": "bytes",
        "Content-Length": str(content_length),
        "Content-Range": f"bytes {byte_start}-{byte_end}/{file_size}",
        "X-SVX-Progressive": "1",
    }
    return StreamingResponse(gen, status_code=status_code, headers=headers)

@app.get("/api/svx/inspect")
async def svx_inspect(
    path: str = "",
    password: str = "",
):
    """Devuelve el índice de un .svx (lista de archivos dentro)."""
    if not path:
        raise HTTPException(400, "Se requiere el parámetro path")
    
    if "/api/svx/stream" in path:
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(path).query)
        if "path" in qs:
            path = qs["path"][0]
        if "password" in qs and not password:
            password = qs["password"][0]

    try:
        cache_payload = await asyncio.get_event_loop().run_in_executor(
            None, lambda: _load_or_build_index_cache(path, password)
        )
        index = cache_payload["entries"]
    except ValueError as e:
        raise HTTPException(400, str(e))

    base_url = f"/api/svx/stream?path={quote(path)}&password={quote(password)}"
    return JSONResponse(content={
        "entries": [
            {**e, "stream_url": base_url + f"&item={quote(e['name'])}"}
            for e in index
        ]
    })


@app.post("/api/tokens/register")
async def token_register(payload: TokenRegisterInput, request: Request):
    _require_admin(request)
    token = _token_from_input(payload.token or "")
    expires_at = _iso_utc(_utc_now() + timedelta(hours=payload.expires_in_hours))
    now_s = _iso_utc(_utc_now())

    with DB_LOCK:
        conn = _db_conn()
        try:
            exists = conn.execute("SELECT id FROM videos WHERE token = ?", (token,)).fetchone()
            if exists:
                raise HTTPException(409, f"Token ya existe: {token}")
            conn.execute(
                "INSERT INTO videos(token, title, status, created_at, expires_at) VALUES (?, ?, 'active', ?, ?)",
                (token, payload.title.strip(), now_s, expires_at)
            )
            video_id = int(conn.execute("SELECT id FROM videos WHERE token = ?", (token,)).fetchone()["id"])
            for src in payload.sources:
                src_url = src.url.strip()
                provider = (src.provider or urlparse(src_url).netloc or "unknown").lower()
                ext = Path(urlparse(src_url).path).suffix.lstrip(".").lower()
                conn.execute(
                    """
                    INSERT INTO video_sources(video_id, url, provider, priority, active, ext, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (video_id, src_url, provider, int(src.priority), 1 if src.active else 0, ext or None, now_s)
                )
            conn.commit()
        finally:
            conn.close()

    return JSONResponse({
        "ok": True,
        "token": token,
        "title": payload.title.strip(),
        "expires_at": expires_at,
        "sources": len(payload.sources),
    })


@app.post("/api/tokens/{token}/revoke")
async def token_revoke(token: str, request: Request):
    _require_admin(request)
    now_s = _iso_utc(_utc_now())
    with DB_LOCK:
        conn = _db_conn()
        try:
            row = conn.execute("SELECT id FROM videos WHERE token = ?", (token,)).fetchone()
            if not row:
                raise HTTPException(404, "Token no encontrado")
            conn.execute(
                "UPDATE videos SET status = 'revoked', revoked_at = ? WHERE token = ?",
                (now_s, token)
            )
            conn.execute("UPDATE play_sessions SET active = 0 WHERE token = ?", (token,))
            conn.commit()
        finally:
            conn.close()
    return JSONResponse({"ok": True, "token": token, "revoked_at": now_s})


@app.patch("/api/tokens/{token}/sources")
async def token_sources_patch(token: str, payload: TokenSourcesPatchInput, request: Request):
    _require_admin(request)
    now_s = _iso_utc(_utc_now())
    with DB_LOCK:
        conn = _db_conn()
        try:
            video = conn.execute("SELECT id FROM videos WHERE token = ?", (token,)).fetchone()
            if not video:
                raise HTTPException(404, "Token no encontrado")
            video_id = int(video["id"])
            conn.execute("DELETE FROM video_sources WHERE video_id = ?", (video_id,))
            for src in payload.sources:
                src_url = src.url.strip()
                provider = (src.provider or urlparse(src_url).netloc or "unknown").lower()
                ext = Path(urlparse(src_url).path).suffix.lstrip(".").lower()
                conn.execute(
                    """
                    INSERT INTO video_sources(video_id, url, provider, priority, active, ext, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (video_id, src_url, provider, int(src.priority), 1 if src.active else 0, ext or None, now_s)
                )
            conn.commit()
        finally:
            conn.close()
    return JSONResponse({"ok": True, "token": token, "sources": len(payload.sources)})


@app.get("/api/tokens/{token}")
async def token_get(token: str, request: Request):
    _require_admin(request)
    with DB_LOCK:
        conn = _db_conn()
        try:
            video = conn.execute(
                "SELECT token, title, status, created_at, expires_at, revoked_at, id FROM videos WHERE token = ?",
                (token,)
            ).fetchone()
            if not video:
                raise HTTPException(404, "Token no encontrado")
            sources = conn.execute(
                "SELECT id, provider, priority, active, url FROM video_sources WHERE video_id = ? ORDER BY priority ASC, id ASC",
                (int(video["id"]),)
            ).fetchall()
        finally:
            conn.close()

    return JSONResponse({
        "token": video["token"],
        "title": video["title"],
        "status": video["status"],
        "created_at": video["created_at"],
        "expires_at": video["expires_at"],
        "revoked_at": video["revoked_at"],
        "sources": [
            {
                "id": int(s["id"]),
                "provider": s["provider"],
                "priority": int(s["priority"]),
                "active": bool(s["active"]),
                "masked_url": _mask_url(s["url"]),
            }
            for s in sources
        ],
    })


@app.post("/api/play/{token}/session")
async def play_session_create(token: str, payload: PlaySessionInput):
    try:
        with DB_LOCK:
            conn = _db_conn()
            try:
                video = conn.execute(
                    "SELECT id, token, title, status, expires_at, revoked_at FROM videos WHERE token = ?",
                    (token,)
                ).fetchone()
            finally:
                conn.close()

        if not video:
            raise HTTPException(404, "Token no encontrado")
        if (video["status"] or "").lower() != "active":
            raise HTTPException(403, "Token inactivo o revocado")
        token_exp = _parse_iso(video["expires_at"])
        if token_exp and token_exp <= _utc_now():
            raise HTTPException(403, "Token expirado")

        video_id = int(video["id"])
        source_row, resolved_url, resolved_ext = _select_best_source(video_id, payload.preferred_source_id)

        mode = (payload.mode or "").strip().lower() or "webcrypto"
        password = payload.password or ""
        try:
            manifest_cache = _load_or_build_index_cache(resolved_url, password)
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            raise HTTPException(500, f"No se pudo construir el índice SVX: {e}")

        entries = manifest_cache["entries"]
        for e in entries:
            e["chunk_url"] = f"/api/play/session/__SID__/chunk/{quote(e['name'])}"
            e["fallback_stream_url"] = f"/api/play/session/__SID__/stream/{quote(e['name'])}"

        session_id = uuid.uuid4().hex
        created_at = _utc_now()
        expires_at = created_at + timedelta(minutes=SESSION_TTL_MINUTES)
        manifest = {
            "session_id": session_id,
            "token": token,
            "mode": mode,
            "expires_at": _iso_utc(expires_at),
            "fallback_ready": True,
            "kdf_iterations": int(_svx.KDF_ITERATIONS),
            "crypto": {
                "algorithm": "AES-CTR",
                "salt_b64": manifest_cache["salt_b64"],
                "iv_b64": manifest_cache["iv_b64"],
                "index_len": int(manifest_cache["index_len"]),
            },
            "source": {
                "id": int(source_row["id"]),
                "provider": source_row["provider"],
                "ext": resolved_ext,
            },
            "entries": entries,
        }
        manifest_json = json.dumps(manifest, ensure_ascii=False, separators=(",", ":"))
        with DB_LOCK:
            conn = _db_conn()
            try:
                conn.execute(
                    """
                    INSERT INTO play_sessions(id, token, video_id, source_id, resolved_url, password, mode, manifest_json, created_at, expires_at, active)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                    """,
                    (
                        session_id, token, video_id, int(source_row["id"]), resolved_url,
                        password, mode, manifest_json, _iso_utc(created_at), _iso_utc(expires_at)
                    )
                )
                conn.commit()
            finally:
                conn.close()

        # Reemplazar placeholder con session_id sin exponer fuente real.
        manifest["entries"] = [
            {
                **e,
                "chunk_url": e["chunk_url"].replace("__SID__", session_id),
                "fallback_stream_url": e["fallback_stream_url"].replace("__SID__", session_id),
            }
            for e in entries
        ]
        _log_view_event(token, session_id, "session_created", f"mode={mode}")
        return JSONResponse(manifest)
    except Exception as e:
        import traceback
        with open("debug_error.log", "a", encoding="utf-8") as f:
            f.write(f"\n[{datetime.now()}] ERROR IN SESSION CREATE: {e}\n")
            f.write(traceback.format_exc())
            f.write("-" * 40 + "\n")
        raise



@app.get("/api/play/session/{session_id}/manifest")
async def play_session_manifest(session_id: str):
    row = _load_active_session(session_id)
    manifest = json.loads(row["manifest_json"])
    manifest["session_id"] = session_id
    manifest["expires_at"] = row["expires_at"]
    manifest["mode"] = row["mode"]
    manifest["entries"] = [
        {
            **e,
            "chunk_url": f"/api/play/session/{session_id}/chunk/{quote(e['name'])}",
            "fallback_stream_url": f"/api/play/session/{session_id}/stream/{quote(e['name'])}",
        }
        for e in manifest.get("entries", [])
    ]
    return JSONResponse(manifest)


def _try_chunk_from_source(path_or_url: str, header_size: int, entry_obj: dict, start: int, end: int) -> bytes:
    file_start = header_size + int(entry_obj["offset"]) + start
    file_end = header_size + int(entry_obj["offset"]) + end
    return _read_range_bytes(path_or_url, file_start, file_end)


@app.get("/api/play/session/{session_id}/chunk/{entry:path}")
async def play_session_chunk(session_id: str, entry: str, start: int = 0, end: int | None = None):
    row = _load_active_session(session_id)
    manifest = json.loads(row["manifest_json"])
    entries = manifest.get("entries", [])
    entry_obj = next((e for e in entries if e["name"] == entry), None)
    if not entry_obj:
        raise HTTPException(404, f"Entrada no encontrada: {entry}")

    entry_size = int(entry_obj["size"])
    if start < 0:
        start = 0
    if end is None:
        end = min(entry_size - 1, start + PLAY_CHUNK_DEFAULT_SIZE - 1)
    end = min(end, entry_size - 1)
    if start > end:
        raise HTTPException(416, "Rango invalido")

    header_size = int(manifest["crypto"]["index_len"]) + _svx.HEADER_BASE
    expected_len = end - start + 1
    current_source_id = int(row["source_id"])
    current_url = row["resolved_url"]
    source_ok = False
    data = b""

    try:
        data = _try_chunk_from_source(current_url, header_size, entry_obj, start, end)
        source_ok = True
        _record_source_health(current_source_id, ok=True)
    except Exception as e:
        _record_source_health(current_source_id, ok=False, error=str(e))
        data = b""
        source_ok = False

    if not source_ok:
        with DB_LOCK:
            conn = _db_conn()
            try:
                alt_rows = conn.execute(
                    """
                    SELECT vs.*,
                           COALESCE(sh.ok_count, 0) AS ok_count,
                           COALESCE(sh.fail_count, 0) AS fail_count
                    FROM video_sources vs
                    LEFT JOIN source_health sh ON sh.source_id = vs.id
                    WHERE vs.video_id = ? AND vs.active = 1 AND vs.id != ?
                    ORDER BY vs.priority ASC, (COALESCE(sh.fail_count, 0) - COALESCE(sh.ok_count, 0)) ASC
                    """,
                    (int(row["video_id"]), current_source_id)
                ).fetchall()
            finally:
                conn.close()

        failover_error = ""
        for alt in alt_rows:
            alt_id = int(alt["id"])
            try:
                resolved_url, _ext = _resolve_source_url(alt)
                data = _try_chunk_from_source(resolved_url, header_size, entry_obj, start, end)
                _record_source_health(alt_id, ok=True)
                _swap_session_source(session_id, alt_id, resolved_url)
                _log_view_event(row["token"], session_id, "source_failover", f"{current_source_id}->{alt_id}")
                source_ok = True
                break
            except Exception as e:
                _record_source_health(alt_id, ok=False, error=str(e))
                failover_error = str(e)
                continue

        if not source_ok:
            raise HTTPException(503, f"No se pudo leer chunk de ninguna fuente: {failover_error or 'sin detalle'}")

    if len(data) > expected_len:
        data = data[:expected_len]
    if len(data) <= 0:
        raise HTTPException(503, "Chunk vacio")

    ks_offset = int(manifest["crypto"]["index_len"]) + int(entry_obj["offset"]) + start
    headers = {
        "Content-Type": "application/octet-stream",
        "Content-Length": str(len(data)),
        "Accept-Ranges": "bytes",
        "Content-Range": f"bytes {start}-{start + len(data) - 1}/{entry_size}",
        "X-SVX-Encrypted": "1",
        "X-SVX-Keystream-Offset": str(ks_offset),
        "X-SVX-Entry-Size": str(entry_size),
    }
    _log_view_event(row["token"], session_id, "chunk", f"{entry}:{start}-{start + len(data) - 1}")
    return Response(content=data, media_type="application/octet-stream", headers=headers, status_code=206)


@app.get("/api/play/session/{session_id}/stream/{entry:path}")
async def play_session_stream(session_id: str, entry: str, request: Request):
    row = _load_active_session(session_id)
    try:
        return await svx_stream(
            request=request,
            path=row["resolved_url"],
            password=row["password"],
            item=entry,
        )
    except Exception as first_err:
        with DB_LOCK:
            conn = _db_conn()
            try:
                alt_rows = conn.execute(
                    "SELECT * FROM video_sources WHERE video_id = ? AND active = 1 AND id != ? ORDER BY priority ASC, id ASC",
                    (int(row["video_id"]), int(row["source_id"]))
                ).fetchall()
            finally:
                conn.close()
        for alt in alt_rows:
            try:
                resolved_url, _ = _resolve_source_url(alt)
                _swap_session_source(session_id, int(alt["id"]), resolved_url)
                _record_source_health(int(alt["id"]), ok=True)
                _log_view_event(row["token"], session_id, "stream_failover", f"{row['source_id']}->{int(alt['id'])}")
                return await svx_stream(
                    request=request,
                    path=resolved_url,
                    password=row["password"],
                    item=entry,
                )
            except Exception as e:
                _record_source_health(int(alt["id"]), ok=False, error=str(e))
                continue
        raise HTTPException(503, f"Fallback stream falló: {first_err}")

