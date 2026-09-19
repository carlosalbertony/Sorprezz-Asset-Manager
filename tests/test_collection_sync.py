import os
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

from starlette.requests import Request
import test_trash

server = test_trash.server


class CollectionSyncTests(unittest.TestCase):
    def setUp(self):
        test_trash.TrashTests.setUp(self)

    ok = test_trash.TrashTests.ok
    collection = test_trash.TrashTests.collection

    def make_collection(self, name):
        return self.ok(self.dad.post('/api/collections', json={
            'name': name, 'category_id': self.resource['category_id']}))

    def test_windows_copy_between_collections_is_visible_without_manual_sync(self):
        source = self.collection()
        dest = self.make_collection('ANIME & MANGA')
        folder = Path(dest['physical_path']) / 'Subcarpeta'
        folder.mkdir()
        shutil.copy2(source['items'][0]['copied_path'], folder / 'nueva.png')
        details = self.ok(self.mom.get(f"/api/collections/{dest['id']}"))
        self.assertEqual(len(details['items']), 1)
        item = details['items'][0]
        self.assertEqual(item['rel_path'], 'Subcarpeta/nueva.png')
        self.assertIsNone(item['resource_id'])
        self.assertEqual(self.mom.get(f"/api/collections/{dest['id']}/items/{item['id']}/preview").content, b'original photo')
        listing = self.ok(self.mom.get('/api/collections'))
        self.assertEqual(next(c for c in listing if c['id'] == dest['id'])['item_count'], 1)
        self.assertEqual(next(c for c in listing if c['id'] == dest['id'])['total_bytes'], len(b'original photo'))

    def test_sync_all_and_collection_button_scan_physical_collection_folders(self):
        col = self.make_collection('Colección local')
        path = Path(col['physical_path']) / 'Windows.png'
        path.write_bytes(b'from Windows')
        result = self.ok(self.mom.post('/api/resources/rescan-all'))
        self.assertEqual(result['collections'], 1)
        self.assertEqual(result['collection_files'], 1)
        path.with_name('Otra.png').write_bytes(b'other image')
        result = self.ok(self.dad.post(f"/api/collections/{col['id']}/rescan"))
        self.assertEqual(result['file_count'], 2)

    def test_rename_preserves_copy_identity_and_move_removes_old_location(self):
        col = self.collection()
        item = col['items'][0]
        photo = Path(item['copied_path'])
        renamed = photo.with_name('renombrada.png')
        photo.rename(renamed)
        details = self.ok(self.mom.get(f"/api/collections/{col['id']}"))
        self.assertEqual(details['items'][0]['id'], item['id'])
        self.assertEqual(details['items'][0]['resource_id'], self.rid)
        other = self.make_collection('Destino')
        shutil.move(str(renamed), str(Path(other['physical_path']) / renamed.name))
        self.assertEqual(self.mom.get(f"/api/collections/{col['id']}").json()['items'], [])
        self.assertEqual(len(self.mom.get(f"/api/collections/{other['id']}").json()['items']), 1)

    def test_deleted_in_windows_is_not_recreated_by_automatic_collection_copy(self):
        col = self.collection()
        with server.db() as con:
            con.execute('UPDATE resources SET collection_id=? WHERE id=?', (col['id'], self.rid))
        copy = Path(col['items'][0]['copied_path'])
        copy.unlink()
        self.ok(self.mom.post('/api/resources/rescan-all'))
        self.assertFalse(copy.exists())
        self.assertTrue(self.photo.exists())
        self.assertEqual(self.mom.get(f"/api/collections/{col['id']}").json()['items'], [])

    def test_overwrite_updates_preview_revision_and_unchanged_poll_reuses_fingerprint(self):
        col = self.collection()
        item = col['items'][0]
        photo = Path(item['copied_path'])
        with patch.object(server, 'fast_file_fingerprint', wraps=server.fast_file_fingerprint) as fingerprint:
            self.ok(self.mom.get(f"/api/collections/{col['id']}"))
            fingerprint.assert_not_called()
        photo.write_bytes(b'updated photo!')
        os.utime(photo, ns=(int(item['revision']) + 1000000000, int(item['revision']) + 1000000000))
        updated = self.ok(self.mom.get(f"/api/collections/{col['id']}"))['items'][0]
        self.assertNotEqual(updated['revision'], item['revision'])
        self.assertEqual(updated['id'], item['id'])

    def test_browser_explorer_lists_empty_and_nested_folders_and_downloads_files(self):
        col = self.make_collection('Navegable')
        root = Path(col['physical_path'])
        (root / 'Vacía').mkdir()
        (root / 'Diseños').mkdir()
        (root / 'Diseños' / 'foto.png').write_bytes(b'local')
        response = self.ok(self.mom.get(f"/api/collections/{col['id']}/browse"))
        self.assertEqual({f['name'] for f in response['folders']}, {'Vacía', 'Diseños'})
        self.assertEqual(response['items'], [])
        nested = self.ok(self.mom.get(f"/api/collections/{col['id']}/browse", params={'path': 'Diseños'}))
        self.assertEqual(nested['current_path'], 'Diseños')
        item_id = nested['items'][0]['id']
        download = self.mom.get(f"/api/collections/{col['id']}/items/{item_id}/download")
        self.assertEqual(download.content, b'local')
        self.assertIn('attachment', download.headers['content-disposition'])
        self.assertEqual(self.mom.get(f"/api/collections/{col['id']}/browse", params={'path': '../../..'}).status_code, 400)

    def test_inaccessible_collection_reports_error_without_erasing_metadata(self):
        col = self.collection()
        with patch.object(server.os, 'walk', side_effect=PermissionError('No se puede leer')):
            result = self.ok(self.mom.get(f"/api/collections/{col['id']}"))
            self.assertIn('No se puede leer', result['sync_error'])
            self.assertEqual(len(result['items']), 1)
        Path(col['items'][0]['copied_path']).unlink()
        Path(col['physical_path']).rmdir()
        result = self.ok(self.mom.post('/api/resources/rescan-all'))
        self.assertFalse(result['ok'])
        self.assertEqual(result['errors'][0]['collection_id'], col['id'])

    def test_open_folder_uses_real_client_address_and_never_opens_server_for_remote(self):
        col = self.collection()
        def request(host):
            return Request({'type': 'http', 'client': (host, 5000), 'headers': [(b'x-forwarded-for', b'127.0.0.1')]})
        addresses = [(None, None, None, None, ('192.168.1.20', 0))]
        with patch.object(server.socket, 'getaddrinfo', return_value=addresses), patch.object(server, 'open_os_path') as open_path:
            remote = server.collection_open(col['id'], request('192.168.1.21'))
            self.assertEqual(remote['mode'], 'browser')
            open_path.assert_not_called()
            local = server.collection_open(col['id'], request('127.0.0.1'))
            self.assertEqual(local['mode'], 'windows')
            open_path.assert_called_once_with(Path(col['physical_path']))
            server.collection_open(col['id'], request('192.168.1.20'))
            self.assertEqual(open_path.call_count, 2)

    def test_trash_entries_from_previous_version_restore_after_metadata_migration(self):
        col = self.collection()
        item = col['items'][0]
        entry = self.ok(self.dad.delete(f"/api/collections/{col['id']}/items/{item['id']}"))['trash_id']
        with server.db() as con:
            data = json.loads(con.execute('SELECT metadata FROM trash WHERE id=?', (entry,)).fetchone()['metadata'])
            for row in data['tables']['collection_items']:
                for column in ('size_bytes', 'mtime_ns', 'fingerprint'):
                    row.pop(column, None)
            con.execute('UPDATE trash SET metadata=? WHERE id=?', (json.dumps(data), entry))
        self.ok(self.mom.post(f'/api/trash/{entry}/restore'))
        restored = self.ok(self.mom.get(f"/api/collections/{col['id']}"))['items'][0]
        self.assertEqual(restored['id'], item['id'])
        self.assertEqual(Path(restored['copied_path']).read_bytes(), b'original photo')
        self.assertTrue(restored['revision'])


if __name__ == '__main__':
    unittest.main()
