import time
import svx_builder as _svx
from pathlib import Path
import os
import json

def benchmark():
    mkv_path = r"D:\Peliculas\El.Guardián.Último.Refugio.2026.1080PDual-Lat.mkv"
    svx_path = r"D:\Peliculas\El.Guardián.Último.Refugio.2026.1080PDual-Lat.svx"
    password = "1234"

    results = {}

    # 1. MKV Raw Read Speed (10MB)
    print("Midiendo lectura RAW del MKV...")
    t0 = time.perf_counter()
    with open(mkv_path, "rb") as f:
        data = f.read(10 * 1024 * 1024)
    t1 = time.perf_counter()
    results["mkv_raw_read_10mb_ms"] = (t1 - t0) * 1000

    # 2. SVX Index Parsing
    print("Midiendo parseo de índice SVX...")
    t0 = time.perf_counter()
    index, header_size, key, iv = _svx.read_index(svx_path, password)
    t1 = time.perf_counter()
    results["svx_index_parse_ms"] = (t1 - t0) * 1000
    results["svx_header_size"] = header_size

    # 3. SVX Streaming Speed (First 10MB decrypted)
    print("Midiendo stream desencriptado SVX (10MB)...")
    t0 = time.perf_counter()
    stream = _svx.stream_entry(svx_path, index[0], password, header_size, key, iv, byte_start=0, byte_end=10*1024*1024-1)
    bytes_read = 0
    for chunk in stream:
        bytes_read += len(chunk)
    t1 = time.perf_counter()
    results["svx_stream_10mb_ms"] = (t1 - t0) * 1000

    # 4. Seek test (Jump to middle of 2GB file)
    middle_offset = 1024 * 1024 * 1024 # 1GB
    print(f"Midiendo seek a 1GB en SVX...")
    t0 = time.perf_counter()
    stream = _svx.stream_entry(svx_path, index[0], password, header_size, key, iv, byte_start=middle_offset, byte_end=middle_offset + 1024*1024-1)
    for chunk in stream:
        pass
    t1 = time.perf_counter()
    results["svx_seek_1gb_ms"] = (t1 - t0) * 1000

    print("\n--- RESULTADOS ---")
    print(json.dumps(results, indent=2))
    
    with open("benchmark_results.json", "w") as f:
        json.dump(results, f)

if __name__ == "__main__":
    benchmark()
