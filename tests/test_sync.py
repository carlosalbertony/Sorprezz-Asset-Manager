"""Run with: python -m unittest discover -s tests -v (requires httpx)."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor

# Importing the app must never access the user's real database or library.
_sandbox = tempfile.TemporaryDirectory()
with patch.dict(os.environ, {"LOCALAPPDATA": _sandbox.name}):
    import app as server
from fastapi.testclient import TestClient


class SharedLibrarySyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        for name, value in {
            "DATA_DIR": base, "DB_PATH": base / "test.db",
            "CONFIG_PATH": base / "config.json",
        }.items():
            p = patch.object(server, name, value)
            p.start()
            self.addCleanup(p.stop)
        server.init_db()
        server.save_config({"library_path": str(base / "library")})
        # Separate clients represent the desktop and the remote browser.
        # No lifespan: initialization is isolated above, with no background repair.
        self.dad = TestClient(server.app)
        self.mom = TestClient(server.app)
        self.addCleanup(self.dad.close)
        self.addCleanup(self.mom.close)
        category = self.dad.get('/api/categories').json()[0]['id']
        response = self.dad.post('/api/resources/manual', json={
            'name': 'Compartido', 'category_id': category,
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.resource = response.json()
        self.rid = self.resource['id']
        self.root = Path(self.resource['local_path'])
        (self.root / 'Familia').mkdir()

    def assets(self):
        return self.mom.get('/api/assets').json()

    def test_upload_visible_to_other_client_and_both_sync_buttons(self):
        self.assertEqual(self.assets(), [])
        response = self.dad.post(f'/api/resources/{self.rid}/files/upload',
                                 data={'target_path': 'Familia'},
                                 files={'files': ('foto.png', b'photo bytes', 'image/png')})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['copied'], 1)
        self.assertEqual(self.assets()[0]['rel_path'], 'Familia/foto.png')
        for client, endpoint in [(self.mom, '/api/resources/rescan-all'),
                                 (self.dad, f'/api/resources/{self.rid}/rescan')]:
            response = client.post(endpoint)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(response.json()['ok'])
        listing = self.mom.get(f'/api/resources/{self.rid}/browse', params={'path': 'Familia'}).json()
        self.assertEqual(listing['items'][0]['name'], 'foto.png')
        preview = self.mom.get(f'/api/resources/{self.rid}/preview', params={'path': 'Familia/foto.png'})
        self.assertEqual(preview.content, b'photo bytes')

    def test_windows_changes_are_indexed_and_tags_survive_rename(self):
        old = self.root / 'Familia' / 'foto.png'
        old.write_bytes(b'original image')
        self.mom.post('/api/resources/rescan-all')
        tag = self.dad.get('/api/tags').json()[0]['id']
        tagged = self.dad.put(f'/api/resources/{self.rid}/file-tags', json={
            'path': 'Familia/foto.png', 'tag_ids': [tag],
        })
        self.assertEqual(tagged.status_code, 200, tagged.text)
        old.rename(old.with_name('renombrada.png'))
        (self.root / 'Familia' / 'nueva.png').write_bytes(b'new image')
        result = self.mom.post('/api/resources/rescan-all').json()
        self.assertTrue(result['ok'])
        self.assertEqual(result['files'], 2)
        self.assertTrue(result['tags_preserved'])
        assets = {a['name']: a for a in self.assets()}
        self.assertEqual(set(assets), {'renombrada.png', 'nueva.png'})
        self.assertEqual(assets['renombrada.png']['tags'][0]['id'], tag)

    def test_missing_folder_and_scan_failure_are_reported(self):
        (self.root / 'Familia').rmdir()
        self.root.rmdir()
        result = self.mom.post('/api/resources/rescan-all').json()
        self.assertFalse(result['ok'])
        self.assertEqual(result['missing'], 1)
        self.assertEqual(result['errors'][0]['resource_id'], self.rid)
        self.root.mkdir()
        with patch.object(server, 'index_resource', side_effect=OSError('Disco inaccesible')):
            result = self.mom.post('/api/resources/rescan-all').json()
            self.assertFalse(result['ok'])
            self.assertIn('Disco inaccesible', result['errors'][0]['detail'])
            single = self.dad.post(f'/api/resources/{self.rid}/rescan')
            self.assertEqual(single.status_code, 500)
            self.assertIn('Disco inaccesible', single.json()['detail'])

    def test_unchanged_files_reuse_fingerprint_and_changed_previews_get_revision(self):
        photo = self.root / 'Familia' / 'foto.png'
        photo.write_bytes(b'first image')
        self.dad.post(f'/api/resources/{self.rid}/rescan')
        first = self.assets()[0]['revision']
        with patch.object(server, 'fast_file_fingerprint', wraps=server.fast_file_fingerprint) as fingerprint:
            self.mom.post('/api/resources/rescan-all')
            fingerprint.assert_not_called()
            photo.write_bytes(b'changed image')
            os.utime(photo, ns=(int(first) + 1000000000, int(first) + 1000000000))
            self.mom.post('/api/resources/rescan-all')
            fingerprint.assert_called_once()
        self.assertNotEqual(first, self.assets()[0]['revision'])

    def test_simultaneous_clients_keep_complete_index(self):
        for i in range(12):
            (self.root / f'{i}.png').write_bytes(str(i).encode())
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda client: client.post(f'/api/resources/{self.rid}/rescan'),
                                    [self.dad, self.mom]))
        self.assertTrue(all(r.status_code == 200 for r in results))
        self.assertEqual(len(self.assets()), 12)


if __name__ == '__main__':
    unittest.main()
