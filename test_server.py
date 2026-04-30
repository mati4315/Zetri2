
import subprocess
import time
import requests

with open("error.log", "w") as err, open("output.log", "w") as out:
    p = subprocess.Popen(["python", "-m", "uvicorn", "main:app", "--port", "8102"], 
                         stdout=out, stderr=err)
    time.sleep(5)
    try:
        requests.get("http://localhost:8102/")
    except:
        pass
    time.sleep(2)
    p.terminate()
