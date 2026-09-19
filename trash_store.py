"""Durable, server-side recycle bin for files and their SQLite metadata."""
from datetime import datetime
import json
from pathlib import Path
import shutil
import sqlite3
import uuid

from fastapi import HTTPException


TABLE_KEYS = {
    'resources': ('id',), 'files': ('id',), 'collections': ('id',),
    'collection_items': ('id',), 'catalog_items': ('id',), 'tags': ('id',),
    'resource_tags': ('resource_id', 'tag_id'),
    'file_tags': ('resource_id', 'rel_path', 'tag_id'),
    'folder_tags': ('resource_id', 'rel_path', 'tag_id'),
    'folder_templates': ('id',), 'folder_template_items': ('id',),
}


class TrashStore:
    def __init__(self, db, root):
        self.db = db
        self.root = Path(root).resolve()

    def get(self, entry_id):
        with self.db() as con:
            row = con.execute('SELECT * FROM trash WHERE id=?', (entry_id,)).fetchone()
        if not row:
            raise HTTPException(404, 'El elemento ya no está en la Papelera')
        return dict(row)

    def payload(self, entry):
        # Never build a deletion target directly from a URL or trust an arbitrary DB path.
        entry_id = entry['id']
        if str(uuid.UUID(entry_id)) != entry_id:
            raise HTTPException(400, 'Identificador de Papelera inválido')
        folder = self.root / entry_id
        target = folder / 'content'
        if folder.resolve().parent != self.root or target.resolve().parent != folder.resolve():
            raise HTTPException(409, 'La ubicación de Papelera no es segura')
        if entry.get('stored_path') and Path(entry['stored_path']).resolve() != target.resolve():
            raise HTTPException(409, 'La ubicación de Papelera ha cambiado')
        return target

    def put(self, kind, name, tables, source=None, **extra):
        if not set(tables).issubset(TABLE_KEYS):
            raise ValueError('Unsupported metadata table')
        source = Path(source) if source else None
        if source and (source.is_symlink() or (hasattr(source, 'is_junction') and source.is_junction())):
            raise HTTPException(409, 'Esta ubicación es un enlace a otra carpeta o archivo; no se moverá su destino a la Papelera')
        source = source.resolve() if source else None
        if source and (source == self.root or source in self.root.parents or self.root in source.parents):
            raise HTTPException(409, 'No se puede enviar esa ubicación a la Papelera')
        entry_id = str(uuid.uuid4())
        stored = self.root / entry_id / 'content' if source and source.exists() else None
        metadata = json.dumps({'tables': tables, **extra}, ensure_ascii=False)
        with self.db() as con:
            con.execute('INSERT INTO trash VALUES (?,?,?,?,?,?,?,?)', (
                entry_id, kind, name, str(source) if source else None,
                str(stored) if stored else None, metadata,
                datetime.now().isoformat(timespec='seconds'), 'pending'))
        try:
            if stored:
                stored.parent.mkdir(parents=True)
                shutil.move(str(source), str(stored))
            with self.db() as con:
                for table, rows in reversed(list(tables.items())):
                    keys = TABLE_KEYS[table]
                    for row in rows:
                        where = ' AND '.join(f'{key}=?' for key in keys)
                        con.execute(f'DELETE FROM {table} WHERE {where}', tuple(row[k] for k in keys))
                con.execute("UPDATE trash SET state='ready' WHERE id=?", (entry_id,))
        except Exception as exc:
            self.recover(entry_id)
            raise HTTPException(500, f'No se pudo enviar a la Papelera: {exc}') from exc
        return entry_id

    def restore(self, entry_id, destination=None):
        entry = self.get(entry_id)
        if entry['state'] != 'ready':
            raise HTTPException(409, 'Hay una operación pendiente sobre este elemento. Reinicia el servidor para recuperarla.')
        metadata = json.loads(entry['metadata'])
        stored = self.payload(entry) if entry['stored_path'] else None
        original = Path(destination or entry['original_path']) if entry['original_path'] else None
        if stored:
            if not stored.exists():
                raise HTTPException(409, 'No se encuentran los archivos guardados en la Papelera')
            if original.exists():
                raise HTTPException(409, 'Ya existe un archivo o carpeta en el destino. Renómbralo o muévelo antes de restaurar; no se sobrescribirá.')
        # Persist the destination before moving so interrupted restores can be rolled back.
        with self.db() as con:
            con.execute("UPDATE trash SET state='restoring',original_path=? WHERE id=?",
                        (str(original) if original else None, entry_id))
        try:
            if stored:
                original.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(stored), str(original))
            with self.db() as con:
                for table, rows in metadata['tables'].items():
                    if table not in TABLE_KEYS:
                        raise ValueError('Unsupported metadata table')
                    allowed = {x['name'] for x in con.execute(f'PRAGMA table_info({table})')}
                    for row in rows:
                        if not set(row).issubset(allowed):
                            raise ValueError('Unsupported metadata columns')
                        cols = ','.join(row)
                        values = ','.join('?' for _ in row)
                        # Never REPLACE: a newer item must not be overwritten.
                        con.execute(f'INSERT INTO {table} ({cols}) VALUES ({values})', tuple(row.values()))
                con.execute('DELETE FROM trash WHERE id=?', (entry_id,))
        except Exception as exc:
            self.recover(entry_id)
            if isinstance(exc, sqlite3.IntegrityError):
                raise HTTPException(409, 'Ya existe un elemento con ese nombre o esos datos. El contenido sigue en la Papelera.') from exc
            raise HTTPException(500, f'No se pudo restaurar: {exc}') from exc
        self.remove_empty_folder(entry)
        return metadata

    def purge(self, entry_id):
        entry = self.get(entry_id)
        if entry['state'] not in ('ready', 'purging'):
            raise HTTPException(409, 'Primero debe recuperarse la operación pendiente')
        target = self.payload(entry)
        with self.db() as con:
            con.execute("UPDATE trash SET state='purging' WHERE id=?", (entry_id,))
        try:
            if entry['stored_path'] and target.exists():
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink()
            with self.db() as con:
                con.execute('DELETE FROM trash WHERE id=?', (entry_id,))
            self.remove_empty_folder(entry)
        except OSError as exc:
            raise HTTPException(500, f'No se pudo eliminar definitivamente. Puedes reintentar desde la Papelera: {exc}') from exc

    def remove_empty_folder(self, entry):
        try:
            self.payload(entry).parent.rmdir()
        except OSError:
            pass

    def recover(self, entry_id=None):
        """Roll back interrupted moves; leave ambiguous states intact for recovery."""
        with self.db() as con:
            rows = [dict(r) for r in con.execute(
                "SELECT * FROM trash WHERE state IN ('pending','restoring')" + (' AND id=?' if entry_id else ''),
                (entry_id,) if entry_id else ())]
        for entry in rows:
            try:
                stored = self.payload(entry) if entry['stored_path'] else None
                original = Path(entry['original_path']) if entry['original_path'] else None
                if stored:
                    if stored.exists() and original.exists():
                        continue  # Interrupted cross-volume copy: preserve both copies.
                    if entry['state'] == 'pending' and stored.exists():
                        original.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(stored), str(original))
                    elif entry['state'] == 'restoring' and original.exists():
                        stored.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(original), str(stored))
                    elif not stored.exists() and not original.exists():
                        continue
                with self.db() as con:
                    if entry['state'] == 'pending':
                        con.execute('DELETE FROM trash WHERE id=?', (entry['id'],))
                    else:
                        con.execute("UPDATE trash SET state='ready' WHERE id=?", (entry['id'],))
                if entry['state'] == 'pending':
                    self.remove_empty_folder(entry)
            except (OSError, HTTPException):
                continue
