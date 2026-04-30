import re
file_path = r'D:\WEB MATER\API N OFC YOUTUBE\Zetri\main.py'
with open(file_path, 'r', encoding='utf-8') as f:
    text = f.read()

decorators = {
    r'^async def rar_live_page': '@app.get("/rar-live")\nasync def rar_live_page',
    r'^async def extract_direct_url': '@app.get("/api/extract")\nasync def extract_direct_url',
    r'^async def dict_archive_remote_lazy': '@app.get("/api/archive/list")\nasync def dict_archive_remote_lazy',
    r'^async def archive_stream_mediafire': '@app.get("/api/archive/stream")\nasync def archive_stream_mediafire',
    r'^async def cancel_archive_download': '@app.get("/api/archive/download-cancel/{download_id}")\nasync def cancel_archive_download',
    r'^async def get_archive_download_status': '@app.get("/api/archive/download-status/{download_id}")\nasync def get_archive_download_status',
    r'^async def svx_dashboard': '@app.get("/")\nasync def svx_dashboard',
    r'^async def svx_create': '@app.post("/api/svx/create")\nasync def svx_create',
    r'^async def svx_stream': '@app.get("/api/svx/stream")\nasync def svx_stream',
    r'^async def svx_inspect': '@app.get("/api/svx/inspect")\nasync def svx_inspect'
}

for pattern, repl in decorators.items():
    text = re.sub(pattern, repl, text, flags=re.MULTILINE)

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(text)
print('Routes added!')
