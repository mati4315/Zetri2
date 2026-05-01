import sys
import os
import json
import requests
import re
from html import unescape

# Simular entorno de Zetri
# Simular entorno de Zetri
def _http_get_with_proxy_fallback(url, **kwargs):
    return requests.get(url, **kwargs)

def get_mediafire_info(url: str) -> dict:
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36',
    }
    # Forzar HTTP si hay problemas de SSL en el entorno local
    resp = requests.get(url, headers=headers, timeout=20, allow_redirects=True, verify=False)
    resp.raise_for_status()
    html = resp.text
    # Buscar link de descarga
    m = re.search(r'href="(http[s]?://download\d+\.mediafire\.com/[^"]+)"', html)
    if not m:
        m = re.search(r"href='(http[s]?://download\d+\.mediafire\.com/[^']+)'", html)
    if not m:
        raise ValueError("No se encontró URL de descarga directa")
    download_url = unescape(m.group(1)).replace("https://", "http://")
    print(f"DEBUG: download_url resolved to: {download_url}")
    return {'download_url': download_url}

def test_mediafire_svx(url):
    print(f"Probando enlace: {url}")
    try:
        info = get_mediafire_info(url)
        direct_url = info['download_url']
        print(f"URL Directo extraído: {direct_url[:100]}...")
        
        # Probar si el servidor SVX Pro puede indexarlo
        # Hacemos una petición al propio servidor local corriendo
        api_url = f"https://localhost:8098/api/svx/inspect?path={url}&password=1234"
        print(f"Llamando a API local: {api_url}")
        r = requests.get(api_url, verify=False)
        if r.status_code == 200:
            print("✅ Éxito: El servidor pudo indexar el SVX remoto.")
            print(json.dumps(r.json(), indent=2))
        else:
            print(f"❌ Error {r.status_code}: {r.text}")
            
    except Exception as e:
        print(f"❌ Error crítico: {e}")

if __name__ == "__main__":
    url = "https://www.mediafire.com/file/j4op67tsuc644em/El.Guardián.Último.Refugio.2026.1080PDual-Lat.svx/file"
    test_mediafire_svx(url)
