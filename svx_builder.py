"""
svx_builder.py — Empaqueta y encripta videos en formato .svx
Formato diseñado para streaming progresivo al vuelo con contraseña AES-256-CTR.

Estructura del archivo .svx:
  [MAGIC]       8  bytes  — b"SVX1\x00\x00\x00\x00"
  [SALT]       16  bytes  — salt aleatorio para PBKDF2
  [IV]         16  bytes  — IV para AES-256-CTR
  [INDEX_LEN]   4  bytes  — longitud del INDEX JSON (uint32 LE)
  [INDEX_DATA]  N  bytes  — JSON encriptado con info de archivos
  [VIDEO...]         —    — datos de cada video, encriptados en stream continuo

El uso de AES-CTR permite seek aleatorio: para leer el byte N de un video,
basta con hacer seek a (HEADER_BASE + offset_del_video + N) en el archivo
y continuar el keystream desde allí. No se necesita desencriptar desde cero.

Uso:
  python svx_builder.py pack video1.mp4 video2.mp4 -p contraseña -o salida.svx
  python svx_builder.py inspect salida.svx -p contraseña
"""

import argparse
import json
import os
import struct
import sys
from pathlib import Path

# ── Intentar importar pycryptodome ────────────────────────────────────────────
try:
    from Crypto.Cipher import AES
    from Crypto.Hash import SHA256
    from Crypto.Protocol.KDF import PBKDF2
    from Crypto.Random import get_random_bytes
except ImportError:
    print("ERROR: Falta pycryptodome. Instalar con:  pip install pycryptodome")
    sys.exit(1)

# ── Constantes ────────────────────────────────────────────────────────────────
MAGIC          = b"SVX1\x00\x00\x00\x00"   # 8 bytes
MAGIC_LEN      = 8
SALT_LEN       = 16
IV_LEN         = 16
INDEX_LEN_SIZE = 4                          # uint32 LE
HEADER_BASE    = MAGIC_LEN + SALT_LEN + IV_LEN + INDEX_LEN_SIZE  # 44 bytes fijos
KDF_ITERATIONS = 20000
CHUNK_SIZE     = 256 * 1024                 # 256 KB para lectura de archivos

VIDEO_EXTS = {'.mp4', '.mkv', '.mov', '.avi', '.webm', '.ts', '.flv', '.m4v', '.wmv'}


# ── Derivación de clave ───────────────────────────────────────────────────────
def _derive_key(password: str, salt: bytes) -> bytes:
    """Deriva una clave AES-256 desde la contraseña usando PBKDF2-SHA256."""
    return PBKDF2(
        password.encode('utf-8'),
        salt,
        dkLen=32,
        count=KDF_ITERATIONS,
        prf=lambda p, s: SHA256.new(p + s).digest()
    )


# ── Cifrador CTR con seek ─────────────────────────────────────────────────────
def _make_cipher_at(key: bytes, nonce: bytes, offset: int = 0) -> AES:
    """
    Crea un cifrador AES-CTR posicionado en el byte 'offset' del keystream.
    Permite seek: para descifrar a partir de byte N no hay que procesar 0..N-1.
    """
    # AES-CTR usa contador de 128 bits. El offset en bytes / 16 da el bloque inicial.
    initial_value = offset // 16
    cipher = AES.new(key, AES.MODE_CTR, nonce=nonce[:8],
                     initial_value=initial_value)
    # Consumir los bytes parciales del primer bloque si offset no es múltiplo de 16
    leftover = offset % 16
    if leftover:
        cipher.encrypt(bytes(leftover))
    return cipher


# ── PACK ──────────────────────────────────────────────────────────────────────
def pack(input_files: list, password: str, output_path: str):
    """
    Empaqueta uno o más archivos de video en un .svx encriptado.
    """
    output_path = Path(output_path)
    if not output_path.suffix:
        output_path = output_path.with_suffix('.svx')

    salt   = get_random_bytes(SALT_LEN)
    iv     = get_random_bytes(IV_LEN)
    key    = _derive_key(password, salt)

    # ── Construir índice ─────────────────────────────────────────────────────
    index_entries = []
    # El offset de payload empieza después de HEADER_BASE + INDEX_LEN + INDEX_DATA.
    # Lo calculamos en dos pasadas.

    total_payload = 0
    entries_pre = []
    for fp in input_files:
        fp = Path(fp)
        size = fp.stat().st_size
        entries_pre.append({'name': fp.name, 'size': size, 'ext': fp.suffix.lower().lstrip('.')})
        total_payload += size

    # Primera pasada: calcular offsets dentro del payload
    cursor = 0
    for e in entries_pre:
        index_entries.append({
            'name':   e['name'],
            'offset': cursor,
            'size':   e['size'],
            'ext':    e['ext'],
        })
        cursor += e['size']

    # JSON del índice (sin encriptar aún) para calcular su tamaño
    index_json = json.dumps(index_entries, ensure_ascii=False, separators=(',', ':')).encode('utf-8')

    # El offset REAL dentro del keystream para el payload es: len(index_json)
    # (el índice también va encriptado con el mismo stream, desde posición 0)
    payload_keystream_start = len(index_json)

    # ── Cifrar índice ────────────────────────────────────────────────────────
    cipher_index = _make_cipher_at(key, iv, offset=0)
    encrypted_index = cipher_index.encrypt(index_json)

    # ── Escribir archivo ─────────────────────────────────────────────────────
    print(f"Creando {output_path}  ({len(input_files)} archivo(s), {total_payload/1024/1024:.1f} MB)")

    with open(output_path, 'wb') as out:
        out.write(MAGIC)
        out.write(salt)
        out.write(iv)
        out.write(struct.pack('<I', len(encrypted_index)))
        out.write(encrypted_index)

        # Cifrar y escribir payload de cada video
        keystream_pos = payload_keystream_start
        for fp, entry in zip(input_files, index_entries):
            fp = Path(fp)
            print(f"  Agregando {fp.name}  ({fp.stat().st_size/1024/1024:.1f} MB)")
            cipher = _make_cipher_at(key, iv, offset=keystream_pos)
            with open(fp, 'rb') as f:
                written = 0
                while True:
                    chunk = f.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    out.write(cipher.encrypt(chunk))
                    written += len(chunk)
                    pct = written / entry['size'] * 100 if entry['size'] else 100
                    print(f"\r    {pct:5.1f}%", end='', flush=True)
            print()
            keystream_pos += entry['size']

    final_size = output_path.stat().st_size
    print(f"\nSVX creado: {output_path}  ({final_size/1024/1024:.1f} MB)")
    return str(output_path)


# ── INSPECT ───────────────────────────────────────────────────────────────────
def inspect(svx_path: str, password: str):
    """
    Muestra el índice de un .svx sin extraer los videos.
    """
    svx_path = Path(svx_path)
    with open(svx_path, 'rb') as f:
        magic = f.read(MAGIC_LEN)
        if magic != MAGIC:
            raise ValueError(f"No es un archivo SVX válido: {magic!r}")

        salt       = f.read(SALT_LEN)
        iv         = f.read(IV_LEN)
        index_len  = struct.unpack('<I', f.read(INDEX_LEN_SIZE))[0]
        enc_index  = f.read(index_len)

    key    = _derive_key(password, salt)
    cipher = _make_cipher_at(key, iv, offset=0)
    index_json = cipher.decrypt(enc_index)
    index = json.loads(index_json)

    print(f"Archivo: {svx_path.name}  ({svx_path.stat().st_size/1024/1024:.1f} MB)")
    print(f"Indice ({len(index)} entradas):")
    for e in index:
        print(f"   [{e['ext']}] {e['name']}  offset={e['offset']}  size={e['size']/1024/1024:.1f} MB")
    return index


# ── EXTRACT (para el backend) ────────────────────────────────────────────────
def read_index(svx_file_or_url, password: str, http_range_reader=None):
    """
    Lee y desencripta el índice de un .svx.
    Soporta archivo local (Path/str) o lector HTTP personalizado.
    Devuelve (index: list[dict], header_size: int, key: bytes, iv: bytes)
    """
    if http_range_reader:
        reader = http_range_reader
        reader.seek(0)
        data = reader.read(MAGIC_LEN + SALT_LEN + IV_LEN + INDEX_LEN_SIZE)
    else:
        with open(svx_file_or_url, 'rb') as f:
            data = f.read(MAGIC_LEN + SALT_LEN + IV_LEN + INDEX_LEN_SIZE)

    magic      = data[:MAGIC_LEN]
    if magic != MAGIC:
        raise ValueError(f"No es un archivo SVX válido: {magic!r}")

    salt       = data[MAGIC_LEN:MAGIC_LEN + SALT_LEN]
    iv         = data[MAGIC_LEN + SALT_LEN:MAGIC_LEN + SALT_LEN + IV_LEN]
    index_len  = struct.unpack('<I', data[MAGIC_LEN + SALT_LEN + IV_LEN:])[0]

    if http_range_reader:
        enc_index = http_range_reader.read(index_len)
    else:
        with open(svx_file_or_url, 'rb') as f:
            f.seek(HEADER_BASE)
            enc_index = f.read(index_len)

    key    = _derive_key(password, salt)
    cipher = _make_cipher_at(key, iv, offset=0)
    try:
        index_json_str = cipher.decrypt(enc_index).decode('utf-8')
        index  = json.loads(index_json_str)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ValueError("Contraseña incorrecta o archivo SVX dañado.")

    header_size = HEADER_BASE + index_len
    return index, header_size, key, iv


def stream_entry(svx_path_or_reader, entry: dict, password: str,
                 header_size: int, key: bytes, iv: bytes,
                 byte_start: int = 0, byte_end: int = None):
    """
    Generador que produce los bytes desencriptados de una entrada del archivo .svx.
    Soporta HTTP Range requests (byte_start / byte_end).
    Ideal para FastAPI StreamingResponse.

    svx_path_or_reader: str/Path (local) o HTTPRangeFile (remoto)
    entry: dict con {offset, size, name, ext}
    """
    entry_offset   = entry['offset']   # offset dentro del payload (keystream)
    entry_size     = entry['size']
    index_len      = header_size - HEADER_BASE

    # El keystream para el payload comienza en index_len (después del índice encriptado)
    ks_payload_start = index_len

    if byte_end is None or byte_end >= entry_size:
        byte_end = entry_size - 1

    if byte_start > byte_end:
        return

    read_len    = byte_end - byte_start + 1
    # Posición en el keystream global
    ks_pos      = ks_payload_start + entry_offset + byte_start
    # Posición en el archivo .svx
    file_pos    = header_size + entry_offset + byte_start

    use_http = hasattr(svx_path_or_reader, 'seek') and hasattr(svx_path_or_reader, 'read')

    if use_http:
        reader = svx_path_or_reader
        reader.seek(file_pos)
    else:
        reader = open(svx_path_or_reader, 'rb')
        reader.seek(file_pos)

    cipher    = _make_cipher_at(key, iv, offset=ks_pos)
    remaining = read_len

    try:
        while remaining > 0:
            chunk = reader.read(min(CHUNK_SIZE, remaining))
            if not chunk:
                break
            yield cipher.encrypt(chunk)
            remaining -= len(chunk)
    finally:
        if not use_http:
            reader.close()


# ── CLI ───────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description='svx_builder — Empaquetador de video encriptado para streaming progresivo')
    sub = parser.add_subparsers(dest='cmd', required=True)

    # pack
    p_pack = sub.add_parser('pack', help='Crear un archivo .svx')
    p_pack.add_argument('files', nargs='+', help='Archivos de video a empaquetar')
    p_pack.add_argument('-p', '--password', required=True, help='Contraseña de encriptación')
    p_pack.add_argument('-o', '--output', default=None, help='Archivo .svx de salida')

    # inspect
    p_inspect = sub.add_parser('inspect', help='Ver contenido de un .svx')
    p_inspect.add_argument('file', help='Archivo .svx')
    p_inspect.add_argument('-p', '--password', required=True, help='Contraseña')

    args = parser.parse_args()

    if args.cmd == 'pack':
        out = args.output or (Path(args.files[0]).stem + '.svx')
        pack(args.files, args.password, out)
    elif args.cmd == 'inspect':
        inspect(args.file, args.password)


if __name__ == '__main__':
    main()
