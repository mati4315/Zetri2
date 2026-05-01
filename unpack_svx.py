import sys
from pathlib import Path
import svx_builder as _svx

def unpack(svx_file, password):
    _svx.KDF_ITERATIONS = 200000
    index, header_size, key, iv = _svx.read_index(svx_file, password)
    print(f"Index: {index}")
    
    for entry in index:
        out_name = f"extracted_{entry['name']}"
        print(f"Extracting to {out_name}...")
        gen = _svx.stream_entry(
            svx_file, entry, password, 
            header_size, key, iv,
            byte_start=0, byte_end=entry['size'] - 1
        )
        
        with open(out_name, "wb") as out:
            written = 0
            for chunk in gen:
                out.write(chunk)
                written += len(chunk)
                pct = written / entry['size'] * 100
                print(f"\r  {pct:.1f}%", end="")
        print(f"\nDone: {out_name}")

if __name__ == "__main__":
    unpack(sys.argv[1], sys.argv[2])
