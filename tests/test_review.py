"""Regression cases from the shared-server workflow review."""
import io
from pathlib import Path
import unittest
from unittest.mock import patch
import zipfile
from concurrent.futures import ThreadPoolExecutor
from fastapi import UploadFile

import test_trash

server = test_trash.server


class SharedWorkflowReviewTests(unittest.TestCase):
    setUp = test_trash.TrashTests.setUp
    ok = test_trash.TrashTests.ok
    collection = test_trash.TrashTests.collection

    def test_search_filters_before_limiting_including_accented_tags(self):
        (self.root / 'aaa.png').write_bytes(b'first')
        self.ok(self.dad.post(f'/api/resources/{self.rid}/rescan'))
        rows = self.ok(self.mom.get('/api/assets', params={'q': 'foto', 'limit': 1}))
        self.assertEqual([r['name'] for r in rows], ['foto.png'])
        tag = self.ok(self.dad.post('/api/tags', json={'name': 'Celebración especial'}))
        self.ok(self.dad.put(f'/api/resources/{self.rid}/file-tags', json={
            'path': 'Familia/foto.png', 'tag_ids': [tag['id']]}))
        rows = self.ok(self.mom.get('/api/assets', params={'q': 'celebracion especial', 'limit': 1}))
        self.assertEqual([r['name'] for r in rows], ['foto.png'])

    def test_unreadable_library_subfolder_does_not_publish_partial_index(self):
        def interrupted_walk(*args, **kwargs):
            yield str(self.root), [], []
            kwargs['onerror'](PermissionError('Carpeta sin acceso'))
        with patch.object(server.os, 'walk', side_effect=interrupted_walk):
            response = self.mom.post(f'/api/resources/{self.rid}/rescan')
        self.assertEqual(response.status_code, 500)
        self.assertEqual(len(self.ok(self.mom.get('/api/assets'))), 1)

    def test_collection_copy_failure_is_reported_by_both_sync_buttons(self):
        col = self.collection()
        with server.db() as con:
            con.execute('UPDATE resources SET collection_id=? WHERE id=?', (col['id'], self.rid))
        (self.root / 'nueva.png').write_bytes(b'new')
        with patch.object(server.shutil, 'copy2', side_effect=PermissionError('Destino sin permiso')):
            single = self.ok(self.mom.post(f'/api/resources/{self.rid}/rescan'))
            all_ = self.ok(self.dad.post('/api/resources/rescan-all'))
        for result in (single, all_):
            self.assertFalse(result['ok'])
            self.assertIn('Destino sin permiso', result['errors'][0]['detail'])
        self.assertEqual(len(self.ok(self.mom.get('/api/assets'))), 2)

    def test_zip_keeps_files_with_same_name_in_different_folders(self):
        (self.root / 'foto.png').write_bytes(b'other photo')
        result = self.ok(self.mom.post(f'/api/resources/{self.rid}/files/zip', json={
            'paths': ['Familia/foto.png', 'foto.png', 'foto.png'], 'name': 'Familia'}))
        download = self.mom.get('/api/library/download', params={'path': result['download_path']})
        with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
            self.assertEqual(set(archive.namelist()), {'Familia/foto.png', 'foto.png'})
            self.assertEqual(len(archive.namelist()), 2)
            self.assertEqual(archive.read('Familia/foto.png'), b'original photo')
            self.assertEqual(archive.read('foto.png'), b'other photo')

    def test_remote_folder_and_file_open_do_not_launch_apps_on_server(self):
        with patch.object(server, 'open_os_path') as opener:
            for route, payload in [('open', None), ('folder/open', {'path': 'Familia'}),
                                   ('file/open', {'path': 'Familia/foto.png'})]:
                response = self.ok(self.mom.post(f'/api/resources/{self.rid}/{route}', json=payload))
                self.assertEqual(response['mode'], 'browser')
            opener.assert_not_called()

    def test_simultaneous_uploads_with_the_same_name_preserve_both_files(self):
        def upload(pair):
            client, content = pair
            return self.ok(client.post(f'/api/resources/{self.rid}/files/upload',
                                      files={'files': ('nueva.png', content, 'image/png')}))
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(upload, [(self.dad, b'from dad'), (self.mom, b'from mom')]))
        paths = [self.root / result['items'][0] for result in results]
        self.assertNotEqual(paths[0], paths[1])
        self.assertEqual({p.read_bytes() for p in paths}, {b'from dad', b'from mom'})
        self.assertEqual(len(self.ok(self.mom.get('/api/assets'))), 3)

    def test_interrupted_upload_does_not_leave_a_corrupt_image(self):
        class InterruptedFile(io.BytesIO):
            def read(self, *args):
                if self.tell():
                    raise OSError('Lectura interrumpida')
                return super().read(*args)
        upload = UploadFile(filename='incompleta.png', file=InterruptedFile(b'partial'))
        result = server.resource_upload_files(self.rid, target_path='', rel_paths_json='[]', files=[upload])
        self.assertFalse(result['ok'])
        self.assertEqual(result['skipped'], 1)
        self.assertFalse((self.root / 'incompleta.png').exists())
        self.assertEqual(list(self.root.glob('.sorprezz-upload-*')), [])
        self.assertEqual(len(self.ok(self.mom.get('/api/assets'))), 1)

    def test_renaming_the_root_cannot_move_the_whole_library_out_of_its_registered_path(self):
        response = self.mom.post(f'/api/resources/{self.rid}/rename', json={'path': '', 'new_name': 'Cambiado'})
        self.assertEqual(response.status_code, 400)
        self.assertTrue(self.photo.exists())

    def test_simultaneous_zip_downloads_do_not_overwrite_each_other(self):
        col = self.collection()
        for endpoint in (f'/api/resources/{self.rid}/zip', f"/api/collections/{col['id']}/zip"):
            with patch.object(server, 'datetime') as clock:
                clock.now.return_value.strftime.return_value = '20260918_120000'
                with ThreadPoolExecutor(max_workers=2) as pool:
                    responses = list(pool.map(lambda c: self.ok(c.post(endpoint)), [self.dad, self.mom]))
            self.assertNotEqual(responses[0]['download_path'], responses[1]['download_path'])
            for response in responses:
                self.assertTrue(zipfile.is_zipfile(response['path']))


if __name__ == '__main__':
    unittest.main()
