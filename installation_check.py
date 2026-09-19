"""Headless check of the actual frozen application, with disposable data only."""
import json
from pathlib import Path
import socket
import tempfile
import threading
import time
from urllib.request import Request, urlopen

import app as server
import uvicorn


def run():
    # Import and construct the same dynamically loaded Drive client, offline.
    from google.auth.credentials import AnonymousCredentials
    from googleapiclient.discovery import build
    from google_auth_oauthlib.flow import InstalledAppFlow
    import gdown
    drive = build('drive', 'v3', credentials=AnonymousCredentials(), static_discovery=True)
    assert drive.files() is not None
    drive.close()
    assert InstalledAppFlow and gdown.download

    with tempfile.TemporaryDirectory(prefix='sorprezz-install-check-') as temp:
        base = Path(temp)
        server.DATA_DIR = base
        server.DB_PATH = base / 'test.db'
        server.CONFIG_PATH = base / 'config.json'
        server.init_db()
        server.save_config({'library_path': str(base / 'library')})
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            port = listener.getsockname()[1]
            service = uvicorn.Server(uvicorn.Config(server.app, log_config=None, access_log=False))
            thread = threading.Thread(target=service.run, kwargs={'sockets': [listener]}, daemon=True)
            thread.start()

            def request(path, payload=None, method=None):
                data = json.dumps(payload).encode() if payload is not None else None
                req = Request(f'http://127.0.0.1:{port}{path}', data=data, method=method,
                              headers={'Content-Type': 'application/json'})
                with urlopen(req, timeout=15) as response:
                    return json.load(response)

            try:
                deadline = time.monotonic() + 30
                while not service.started:
                    if not thread.is_alive() or time.monotonic() > deadline:
                        raise RuntimeError('El servidor compilado no inició')
                    time.sleep(0.1)
                for path in ('/', '/static/app.js', '/static/styles.css'):
                    with urlopen(f'http://127.0.0.1:{port}{path}', timeout=15) as response:
                        assert response.status == 200 and response.read()
                category = request('/api/categories')[0]['id']
                resource = request('/api/resources/manual', {'name': 'Prueba', 'category_id': category})
                root = Path(resource['local_path'])
                photo = root / 'imagen.png'
                photo.write_bytes(b'image fixture')
                assert request('/api/resources/rescan-all', method='POST')['ok']
                assert request('/api/assets')[0]['name'] == 'imagen.png'
                col = request('/api/collections', {'name': 'Prueba', 'category_id': category})
                (Path(col['physical_path']) / 'windows.png').write_bytes(b'from Windows')
                assert len(request(f"/api/collections/{col['id']}")['items']) == 1
                removed = request(f"/api/resources/{resource['id']}/items/delete", {'paths': ['imagen.png']})
                assert not photo.exists()
                request(f"/api/trash/{removed['trash_ids'][0]}/restore", method='POST')
                assert photo.read_bytes() == b'image fixture'
                assert not request('/api/trash')
                return {'ok': True, 'version': server.APP_VERSION,
                        'checks': ['server', 'web', 'drive_dependencies', 'windows_sync', 'collections', 'trash_restore']}
            finally:
                service.should_exit = True
                thread.join(timeout=15)
                if thread.is_alive():
                    raise RuntimeError('El servidor de prueba no se cerró')
