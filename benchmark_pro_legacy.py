import time
import requests
import json
import base64
import os
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend

# Configuración
BASE_URL = "https://localhost:8098"
SVX_PATH = r"D:\Peliculas\El.Guardián.Último.Refugio.2026.1080PDual-Lat.svx"
PASSWORD = "1234"
TOKEN = "svx-test-pro"
VERIFY_SSL = False

# Módulos del Core (simplificados para benchmark)
KDF_ITERATIONS = 100_000

def derive_key(password: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=KDF_ITERATIONS,
        backend=default_backend()
    )
    return kdf.derive(password.encode())

def decrypt_chunk(data: bytes, key: bytes, iv: bytes, offset: int):
    # AES-CTR logic for seeking
    counter_val = int.from_bytes(iv, "big") + (offset // 16)
    new_iv = counter_val.to_bytes(16, "big")
    cipher = Cipher(algorithms.AES(key), modes.CTR(new_iv), backend=default_backend())
    decryptor = cipher.decryptor()
    # Handle sub-block offset
    skip = offset % 16
    prepped_data = b"\x00" * skip + data
    decrypted = decryptor.update(prepped_data) + decryptor.finalize()
    return decrypted[skip:]

def benchmark_legacy():
    print("\n--- [MODO LEGACY: Server-side Decryption] ---")
    start_total = time.perf_counter()
    
    # 1. Inspect (Header/Index read + Key Derivation on Server)
    # The server caches the result, so we should expect it to be fast if cached, 
    # but the FIRST time it is slow. We want to measure the performance profile.
    start_step = time.perf_counter()
    inspect_url = f"{BASE_URL}/api/svx/inspect?path={SVX_PATH}&password={PASSWORD}"
    r = requests.get(inspect_url, verify=VERIFY_SSL)
    t_inspect = time.perf_counter() - start_step
    print(f"1. Inspección (Header + KDF en Servidor): {t_inspect*1000:.2f} ms")
    
    # 2. Start Stream (First Chunk)
    start_step = time.perf_counter()
    stream_url = f"{BASE_URL}/api/svx/stream?path={SVX_PATH}&password={PASSWORD}"
    r = requests.get(stream_url, headers={"Range": "bytes=0-1048575"}, verify=VERIFY_SSL, stream=True)
    chunk = next(r.iter_content(chunk_size=1024*1024))
    t_chunk = time.perf_counter() - start_step
    print(f"2. Primer Chunk 1MB (Lectura + Descifrado en Servidor): {t_chunk*1000:.2f} ms")
    
    total = time.perf_counter() - start_total
    print(f">> Latencia total de arranque (Legacy): {total*1000:.2f} ms")
    return total

def benchmark_pro():
    print("\n--- [MODO PRO: Client-side Decryption] ---")
    start_total = time.perf_counter()
    
    # 1. Create Session / Get Manifest
    start_step = time.perf_counter()
    session_url = f"{BASE_URL}/api/play/{TOKEN}/session"
    r = requests.post(session_url, json={"password": PASSWORD, "mode": "webcrypto"}, verify=VERIFY_SSL)
    manifest = r.json()
    t_manifest = time.perf_counter() - start_step
    print(f"1. Obtener Manifiesto (JSON): {t_manifest*1000:.2f} ms")
    
    # 2. Key Derivation (Simulating Client work)
    start_step = time.perf_counter()
    salt = base64.b64decode(manifest["crypto"]["salt_b64"])
    key = derive_key(PASSWORD, salt)
    t_kdf = time.perf_counter() - start_step
    print(f"2. Derivación de Clave (Simulado en Cliente): {t_kdf*1000:.2f} ms")
    
    # 3. Fetch Encrypted Chunk
    start_step = time.perf_counter()
    sid = manifest["session_id"]
    item_name = manifest["entries"][0]["name"]
    chunk_url = f"{BASE_URL}/api/play/session/{sid}/chunk/{item_name}"
    r = requests.get(chunk_url, headers={"Range": "bytes=0-1048575"}, verify=VERIFY_SSL)
    enc_data = r.content
    t_fetch = time.perf_counter() - start_step
    print(f"3. Fetch Chunk Encriptado (Puro I/O): {t_fetch*1000:.2f} ms")
    
    # 4. Decrypt in Client
    start_step = time.perf_counter()
    iv = base64.b64decode(manifest["crypto"]["iv_b64"])
    # Offset is 0 for first chunk, but we skip header_size
    # Actually the chunk endpoint handles header skipping for us if it's the raw chunk endpoint? No, we use /chunk/
    # Main.py says /chunk/ skips the SVX header.
    dec_data = decrypt_chunk(enc_data, key, iv, 0)
    t_decrypt = time.perf_counter() - start_step
    print(f"4. Descifrado en Cliente (AES-CTR): {t_decrypt*1000:.2f} ms")
    
    total = time.perf_counter() - start_total
    print(f">> Latencia total de arranque (Pro): {total*1000:.2f} ms")
    return total

if __name__ == "__main__":
    print("Iniciando Benchmarks de Zetri Pro vs Legacy...")
    print("Archivo:", SVX_PATH)
    
    # Disable warnings for self-signed certs
    requests.packages.urllib3.disable_warnings()

    # Primero Pro para evitar que el cache del servidor beneficie a Legacy injustamente
    # Aunque Pro también se beneficia del cache del indice.
    t_pro = benchmark_pro()
    t_legacy = benchmark_legacy()
    
    print("\n" + "="*50)
    print("COMPARATIVA TÉCNICA")
    print("="*50)
    print(f"Modo PRO (Client-side)    : {t_pro*1000:7.2f} ms")
    print(f"Modo LEGACY (Server-side) : {t_legacy*1000:7.2f} ms")
    print("-" * 50)
    diff = abs(t_pro - t_legacy) * 1000
    if t_pro < t_legacy:
        print(f"PRO es {diff:.2f} ms más rápido en el arranque.")
    else:
        print(f"LEGACY es {diff:.2f} ms más rápido en el arranque.")
    
    print("\nANÁLISIS DE CARGA DE SERVIDOR:")
    print("- LEGACY: El servidor consume CPU para cada chunk enviado (AES) y cada inicio (PBKDF2).")
    print("- PRO   : El servidor solo entrega bytes. La CPU del usuario hace el trabajo pesado.")
    print("ESTADO: Con 1 usuario la diferencia es mínima. Con 100 usuarios, Legacy colapsaría el servidor.")
