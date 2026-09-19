import json
from pathlib import Path
import shutil
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import test_sync

server = test_sync.server


class TrashTests(unittest.TestCase):
    def setUp(self):
        test_sync.SharedLibrarySyncTests.setUp(self)
        self.photo = self.root / 'Familia' / 'foto.png'
        self.photo.write_bytes(b'original photo')
        self.dad.post(f'/api/resources/{self.rid}/rescan')
        self.tag = self.dad.get('/api/tags').json()[0]['id']
        self.dad.put(f'/api/resources/{self.rid}/file-tags', json={'path': 'Familia/foto.png', 'tag_ids': [self.tag]})

    def ok(self, response):
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def entries(self):
        return self.ok(self.mom.get('/api/trash'))

    def trash_file(self, paths=None):
        return self.ok(self.dad.post(f'/api/resources/{self.rid}/items/delete',
                                    json={'paths': paths or ['Familia/foto.png']}))['trash_ids'][0]

    def restore(self, entry):
        return self.mom.post(f'/api/trash/{entry}/restore')

    def purge(self, entry, confirmed=True):
        return self.mom.request('DELETE', f'/api/trash/{entry}', json={'confirmed': confirmed})

    def collection(self):
        col = self.ok(self.dad.post('/api/collections', json={
            'name': 'AÑO NUEVO', 'category_id': self.resource['category_id']}))
        self.ok(self.dad.post(f"/api/collections/{col['id']}/items", json={
            'items': [{'resource_id': self.rid, 'path': 'Familia/foto.png'}]}))
        return self.ok(self.dad.get(f"/api/collections/{col['id']}"))

    def test_shared_file_trash_restore_keeps_bytes_tags_and_counts(self):
        entry = self.trash_file()
        self.assertFalse(self.photo.exists())
        self.assertEqual(self.entries()[0]['id'], entry)
        self.assertEqual(self.mom.get('/api/assets').json(), [])
        self.ok(self.restore(entry))
        self.assertEqual(self.photo.read_bytes(), b'original photo')
        asset = self.mom.get('/api/assets').json()[0]
        self.assertEqual(asset['tags'][0]['id'], self.tag)
        self.assertEqual(self.entries(), [])
        self.assertEqual(self.mom.get(f'/api/resources/{self.rid}').json()['file_count'], 1)

    def test_folder_including_selected_child_is_trashed_once_and_restored(self):
        self.dad.put(f'/api/resources/{self.rid}/folder-tags', json={'path': 'Familia', 'tag_ids': [self.tag]})
        entry = self.trash_file(['Familia/foto.png', 'Familia', 'Familia'])
        self.assertEqual(len(self.entries()), 1)
        self.assertFalse(self.photo.parent.exists())
        self.ok(self.restore(entry))
        self.assertTrue(self.photo.exists())
        tags = self.mom.get(f'/api/resources/{self.rid}/folder-tags', params={'path': 'Familia'}).json()
        self.assertEqual(tags[0]['id'], self.tag)

    def test_collection_restore_and_purge_never_remove_original(self):
        col = self.collection()
        copied = Path(col['items'][0]['copied_path'])
        entry = self.ok(self.dad.delete(f"/api/collections/{col['id']}"))['trash_id']
        self.assertTrue(self.photo.exists())
        self.assertFalse(copied.exists())
        self.assertEqual(self.mom.get('/api/collections').json(), [])
        self.ok(self.restore(entry))
        self.assertEqual(copied.read_bytes(), b'original photo')
        restored = self.mom.get(f"/api/collections/{col['id']}").json()
        self.assertEqual(restored['items'][0]['id'], col['items'][0]['id'])
        entry = self.ok(self.dad.delete(f"/api/collections/{col['id']}"))['trash_id']
        self.ok(self.purge(entry))
        self.assertTrue(self.photo.exists())
        self.assertFalse(copied.exists())
        self.assertEqual(self.entries(), [])

    def test_removed_collection_copy_is_not_recreated_by_sync(self):
        col = self.collection()
        with server.db() as con:
            con.execute('UPDATE resources SET collection_id=? WHERE id=?', (col['id'], self.rid))
        item = col['items'][0]
        entry = self.ok(self.dad.delete(f"/api/collections/{col['id']}/items/{item['id']}"))['trash_id']
        self.ok(self.mom.post('/api/resources/rescan-all'))
        self.assertEqual(self.mom.get(f"/api/collections/{col['id']}").json()['items'], [])
        self.ok(self.restore(entry))
        self.assertTrue(Path(item['copied_path']).exists())
        self.assertTrue(self.photo.exists())

    def test_resource_restore_keeps_classification_and_children(self):
        self.dad.put(f'/api/resources/{self.rid}/tags', json={'tag_ids': [self.tag]})
        entry = self.ok(self.dad.delete(f'/api/resources/{self.rid}'))['trash_id']
        self.assertFalse(self.root.exists())
        self.assertEqual(self.mom.get('/api/resources').json(), [])
        self.ok(self.restore(entry))
        resource = self.mom.get(f'/api/resources/{self.rid}').json()
        self.assertEqual(resource['category_id'], self.resource['category_id'])
        self.assertEqual(resource['tags'][0]['id'], self.tag)
        self.assertTrue(self.photo.exists())

    def test_purged_collection_copy_stays_removed_after_sync_but_can_be_added_explicitly(self):
        col = self.collection()
        with server.db() as con:
            con.execute('UPDATE resources SET collection_id=? WHERE id=?', (col['id'], self.rid))
        item = col['items'][0]
        entry = self.ok(self.dad.delete(f"/api/collections/{col['id']}/items/{item['id']}"))['trash_id']
        self.ok(self.purge(entry))
        self.ok(self.mom.post('/api/resources/rescan-all'))
        self.assertEqual(self.mom.get(f"/api/collections/{col['id']}").json()['items'], [])
        self.assertTrue(self.photo.exists())
        result = self.ok(self.dad.post(f"/api/collections/{col['id']}/items", json={
            'items': [{'resource_id': self.rid, 'path': 'Familia/foto.png'}]}))
        self.assertEqual(result['added'], 1)

    def test_interrupted_trash_move_restores_active_content_on_recovery(self):
        with patch.object(server.trash_store().__class__, 'recover'):
            with patch('trash_store.shutil.move', side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    server.resource_delete_items(self.rid, server.DeleteItemsIn(paths=['Familia/foto.png']))
        entry = self.entries()[0]
        record = server.trash_store().get(entry['id'])
        stored = server.trash_store().payload(record)
        stored.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(self.photo), str(stored))
        server.trash_store().recover()
        self.assertTrue(self.photo.exists())
        self.assertEqual(self.entries(), [])
        self.assertEqual(len(self.mom.get('/api/assets').json()), 1)

    def test_queued_download_blocks_resource_deletion(self):
        with patch.dict(server.active_downloads, {self.rid: {'progress': 0}}):
            self.assertEqual(self.dad.delete(f'/api/resources/{self.rid}').status_code, 409)
        self.assertTrue(self.photo.exists())
        self.assertEqual(self.entries(), [])

    def test_restore_refuses_overwrite_and_retains_trashed_content(self):
        entry = self.trash_file()
        self.photo.write_bytes(b'new work')
        self.assertEqual(self.restore(entry).status_code, 409)
        self.assertEqual(self.photo.read_bytes(), b'new work')
        self.assertEqual(len(self.entries()), 1)
        stored = server.trash_store().payload(server.trash_store().get(entry))
        self.assertEqual(stored.read_bytes(), b'original photo')

    def test_purge_requires_explicit_confirmation(self):
        entry = self.trash_file()
        self.assertEqual(self.purge(entry, False).status_code, 400)
        self.assertEqual(len(self.entries()), 1)
        self.ok(self.purge(entry))
        self.assertEqual(self.entries(), [])
        self.assertEqual(self.restore(entry).status_code, 404)

    def test_failed_move_preserves_active_files_and_metadata(self):
        with patch('trash_store.shutil.move', side_effect=PermissionError('Archivo en uso')):
            result = self.ok(self.dad.post(f'/api/resources/{self.rid}/items/delete', json={'paths': ['Familia/foto.png']}))
        self.assertFalse(result['ok'])
        self.assertEqual(result['deleted'], 0)
        self.assertTrue(self.photo.exists())
        self.assertEqual(len(self.mom.get('/api/assets').json()), 1)
        self.assertEqual(self.entries(), [])

    def test_restart_preserves_trash_and_rolls_back_interrupted_restore(self):
        entry = self.trash_file()
        record = server.trash_store().get(entry)
        stored = server.trash_store().payload(record)
        with server.db() as con:
            con.execute("UPDATE trash SET state='restoring' WHERE id=?", (entry,))
        shutil.move(str(stored), str(self.photo))
        server.init_db()
        server.trash_store().recover()
        self.assertFalse(self.photo.exists())
        self.assertEqual(self.entries()[0]['state'], 'ready')
        self.ok(self.restore(entry))
        self.assertTrue(self.photo.exists())

    def test_tag_name_conflict_is_safe_and_associations_can_be_restored(self):
        entry = self.ok(self.dad.delete(f'/api/tags/{self.tag}'))['trash_id']
        name = self.entries()[0]['name']
        new_tag = self.ok(self.dad.post('/api/tags', json={'name': name}))['id']
        self.assertEqual(self.restore(entry).status_code, 409)
        self.ok(self.dad.delete(f'/api/tags/{new_tag}'))
        self.ok(self.restore(entry))
        self.assertEqual(self.mom.get('/api/assets').json()[0]['tags'][0]['id'], self.tag)

    def test_two_clients_deleting_same_file_produce_one_trash_entry(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda client: client.post(f'/api/resources/{self.rid}/items/delete',
                                    json={'paths': ['Familia/foto.png']}), [self.dad, self.mom]))
        self.assertEqual(sorted(r.status_code for r in results), [200, 404])
        self.assertEqual(len(self.entries()), 1)

    def test_parent_must_be_restored_before_separately_trashed_child(self):
        child = self.trash_file()
        parent = self.ok(self.dad.delete(f'/api/resources/{self.rid}'))['trash_id']
        self.assertEqual(self.restore(child).status_code, 409)
        self.assertEqual(self.purge(parent).status_code, 409)
        self.ok(self.restore(parent))
        self.ok(self.restore(child))
        self.assertTrue(self.photo.exists())

    def test_path_traversal_cannot_trash_outside_resource(self):
        result = self.dad.post(f'/api/resources/{self.rid}/items/delete', json={'paths': ['../../../../outside.txt']})
        self.assertEqual(result.status_code, 400)
        self.assertEqual(self.entries(), [])


if __name__ == '__main__':
    unittest.main()
