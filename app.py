from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import unicodedata
import uuid
import webbrowser
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import uvicorn

APP_NAME = "Sorprezz Asset Manager"
APP_VERSION = "1.9.1"
# PyInstaller extracts bundled resources to sys._MEIPASS. In source mode we use this file's folder.
BASE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
WEB_DIR = BASE_DIR / "web"
DATA_DIR = Path(os.getenv("LOCALAPPDATA", str(Path.home()))) / "SorprezzAssetManager" / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "sorprezz.db"
CONFIG_PATH = DATA_DIR / "config.json"
GOOGLE_OAUTH_CLIENT_PATH = DATA_DIR / "google_oauth_client.json"
GOOGLE_TOKEN_DIR = DATA_DIR / "google_tokens"
GOOGLE_TOKEN_DIR.mkdir(parents=True, exist_ok=True)
DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
GOOGLE_FOLDER_MIME = "application/vnd.google-apps.folder"
GOOGLE_SHORTCUT_MIME = "application/vnd.google-apps.shortcut"
GOOGLE_EXPORTS = {
    "application/vnd.google-apps.document": ("application/pdf", ".pdf"),
    "application/vnd.google-apps.spreadsheet": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx"),
    "application/vnd.google-apps.presentation": ("application/vnd.openxmlformats-officedocument.presentationml.presentation", ".pptx"),
    "application/vnd.google-apps.drawing": ("application/pdf", ".pdf"),
}

DEFAULT_CATEGORIES = [
    "Camisetas", "Tazas", "Cojines", "Termos y Tumblers", "Stickers", "Mousepad",
    "Lanyards", "Chancletas", "Bolsos", "Cuadros", "Gorras", "Botas", "Llaveros",
    "Botellas", "Pijamas", "Boxers", "Diseños Varios", "Bonos", "Instaladores",
    "Acciones Photoshop"
]

app = FastAPI(title=APP_NAME, version=APP_VERSION)
app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")
executor = ThreadPoolExecutor(max_workers=2)
active_downloads: dict[int, dict] = {}
active_lock = threading.Lock()


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def canonical_rel_path(value: str | None) -> str:
    """Ruta relativa estable para Windows/Linux: siempre usa / y nunca empieza/termina con /."""
    return str(value or "").replace('\\', '/').strip('/')


def fast_file_fingerprint(path: Path) -> str:
    """Huella rápida para conservar etiquetas si un archivo es renombrado o movido manualmente."""
    try:
        size = path.stat().st_size
        h = hashlib.blake2b(digest_size=16)
        h.update(str(size).encode('ascii'))
        with path.open('rb') as fh:
            first = fh.read(65536)
            h.update(first)
            if size > 65536:
                fh.seek(max(0, size - 65536))
                h.update(fh.read(65536))
        return h.hexdigest()
    except Exception:
        return ""


def normalize_metadata_paths(con: sqlite3.Connection) -> None:
    """Normaliza metadatos creados por versiones previas de Windows sin perder etiquetas."""
    # files usa id y puede actualizarse directamente.
    for row in [dict(x) for x in con.execute('SELECT id,resource_id,rel_path FROM files')]:
        clean = canonical_rel_path(row['rel_path'])
        if clean == row['rel_path']:
            continue
        try:
            con.execute('UPDATE files SET rel_path=? WHERE id=?', (clean, row['id']))
        except sqlite3.IntegrityError:
            con.execute('DELETE FROM files WHERE id=?', (row['id'],))
    # Tablas con clave compuesta: insertar forma canónica antes de borrar la antigua.
    for table in ('file_tags','folder_tags'):
        rows = [dict(x) for x in con.execute(f'SELECT resource_id,rel_path,tag_id FROM {table}') ]
        for row in rows:
            clean = canonical_rel_path(row['rel_path'])
            if clean == row['rel_path']:
                continue
            con.execute(f'INSERT OR IGNORE INTO {table}(resource_id,rel_path,tag_id) VALUES (?,?,?)', (row['resource_id'],clean,row['tag_id']))
            con.execute(f'DELETE FROM {table} WHERE resource_id=? AND rel_path=? AND tag_id=?', (row['resource_id'],row['rel_path'],row['tag_id']))
    for table,col in (('collection_items','source_rel_path'),('catalog_items','source_rel_path')):
        try:
            rows=[dict(x) for x in con.execute(f'SELECT id,{col} FROM {table} WHERE {col} IS NOT NULL AND {col}<>""')]
        except sqlite3.OperationalError:
            continue
        for row in rows:
            clean=canonical_rel_path(row[col])
            if clean!=row[col]:
                try:
                    con.execute(f'UPDATE {table} SET {col}=? WHERE id=?',(clean,row['id']))
                except sqlite3.IntegrityError:
                    pass


def backup_metadata_db(prefix: str = 'sync') -> str | None:
    """Copia de seguridad pequeña de la base antes de una sincronización global."""
    try:
        out_dir = DATA_DIR / 'Backups'
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / f"sorprezz_{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        src = db(); dst = sqlite3.connect(target)
        try:
            src.backup(dst)
        finally:
            dst.close(); src.close()
        # Mantener solo las 8 copias más recientes.
        backups = sorted(out_dir.glob('sorprezz_*.db'), key=lambda x: x.stat().st_mtime, reverse=True)
        for old in backups[8:]:
            try: old.unlink()
            except OSError: pass
        return str(target)
    except Exception:
        return None


def init_db() -> None:
    with db() as con:
        con.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS subcategories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(category_id, name COLLATE NOCASE),
                FOREIGN KEY(category_id) REFERENCES categories(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS resources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                url TEXT NOT NULL UNIQUE,
                category_id INTEGER,
                subcategory_id INTEGER,
                status TEXT NOT NULL DEFAULT 'pendiente',
                local_path TEXT,
                file_count INTEGER NOT NULL DEFAULT 0,
                total_bytes INTEGER NOT NULL DEFAULT 0,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(category_id) REFERENCES categories(id),
                FOREIGN KEY(subcategory_id) REFERENCES subcategories(id)
            );
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                resource_id INTEGER NOT NULL,
                rel_path TEXT NOT NULL,
                ext TEXT,
                size_bytes INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                UNIQUE(resource_id, rel_path),
                FOREIGN KEY(resource_id) REFERENCES resources(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS catalog_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                resource_id INTEGER,
                source_rel_path TEXT,
                title TEXT NOT NULL,
                product_type TEXT NOT NULL DEFAULT 'Otro',
                technique TEXT NOT NULL DEFAULT 'Sublimación',
                print_size TEXT,
                variants TEXT,
                status TEXT NOT NULL DEFAULT 'seleccionado',
                sku TEXT,
                price TEXT,
                tags TEXT,
                notes TEXT,
                catalog_path TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(resource_id) REFERENCES resources(id) ON DELETE SET NULL
            );
            CREATE TABLE IF NOT EXISTS tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS resource_tags (
                resource_id INTEGER NOT NULL,
                tag_id INTEGER NOT NULL,
                PRIMARY KEY(resource_id, tag_id),
                FOREIGN KEY(resource_id) REFERENCES resources(id) ON DELETE CASCADE,
                FOREIGN KEY(tag_id) REFERENCES tags(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS folder_tags (
                resource_id INTEGER NOT NULL,
                rel_path TEXT NOT NULL,
                tag_id INTEGER NOT NULL,
                PRIMARY KEY(resource_id, rel_path, tag_id),
                FOREIGN KEY(resource_id) REFERENCES resources(id) ON DELETE CASCADE,
                FOREIGN KEY(tag_id) REFERENCES tags(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS folder_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                description TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS folder_template_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                template_id INTEGER NOT NULL,
                folder_path TEXT NOT NULL,
                sort_order INTEGER NOT NULL DEFAULT 0,
                UNIQUE(template_id, folder_path COLLATE NOCASE),
                FOREIGN KEY(template_id) REFERENCES folder_templates(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS file_tags (
                resource_id INTEGER NOT NULL,
                rel_path TEXT NOT NULL,
                tag_id INTEGER NOT NULL,
                PRIMARY KEY(resource_id, rel_path, tag_id),
                FOREIGN KEY(resource_id) REFERENCES resources(id) ON DELETE CASCADE,
                FOREIGN KEY(tag_id) REFERENCES tags(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS collections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'General',
                description TEXT,
                physical_path TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(category, name COLLATE NOCASE)
            );
            CREATE TABLE IF NOT EXISTS collection_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                collection_id INTEGER NOT NULL,
                resource_id INTEGER,
                source_rel_path TEXT,
                copied_path TEXT NOT NULL,
                title TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(collection_id, resource_id, source_rel_path),
                FOREIGN KEY(collection_id) REFERENCES collections(id) ON DELETE CASCADE,
                FOREIGN KEY(resource_id) REFERENCES resources(id) ON DELETE SET NULL
            );
            """
        )
        # Migraciones no destructivas para versiones futuras del catálogo.
        catalog_columns = {x["name"] for x in con.execute("PRAGMA table_info(catalog_items)")}
        for col, ddl in {
            "technique": "TEXT NOT NULL DEFAULT 'Sublimación'",
            "print_size": "TEXT",
            "variants": "TEXT",
        }.items():
            if col not in catalog_columns:
                con.execute(f"ALTER TABLE catalog_items ADD COLUMN {col} {ddl}")

        resource_columns = {x["name"] for x in con.execute("PRAGMA table_info(resources)")}
        for col, ddl in {
            "collection_id": "INTEGER",
            "avoid_duplicates": "INTEGER NOT NULL DEFAULT 1",
            "source_type": "TEXT NOT NULL DEFAULT 'drive'",
            "source_detail": "TEXT",
            "drive_account_id": "INTEGER",
            "drive_item_id": "TEXT",
            "drive_item_kind": "TEXT",
            "remote_file_count": "INTEGER NOT NULL DEFAULT 0",
            "remote_folder_count": "INTEGER NOT NULL DEFAULT 0",
            "remote_total_bytes": "INTEGER NOT NULL DEFAULT 0",
            "downloaded_file_count": "INTEGER NOT NULL DEFAULT 0",
            "skipped_file_count": "INTEGER NOT NULL DEFAULT 0",
            "download_engine": "TEXT",
        }.items():
            if col not in resource_columns:
                con.execute(f"ALTER TABLE resources ADD COLUMN {col} {ddl}")

        con.execute(
            """CREATE TABLE IF NOT EXISTS drive_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                display_name TEXT,
                token_path TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )

        file_columns = {x["name"] for x in con.execute("PRAGMA table_info(files)")}
        for col, ddl in {
            "fingerprint": "TEXT",
            "mtime_ns": "INTEGER",
        }.items():
            if col not in file_columns:
                con.execute(f"ALTER TABLE files ADD COLUMN {col} {ddl}")

        # V1.8.1: Colecciones usa las mismas categorías globales que Biblioteca.
        collection_columns = {x["name"] for x in con.execute("PRAGMA table_info(collections)")}
        for col, ddl in {
            "category_id": "INTEGER",
            "subcategory_id": "INTEGER",
        }.items():
            if col not in collection_columns:
                con.execute(f"ALTER TABLE collections ADD COLUMN {col} {ddl}")

        # Migra las categorías de texto antiguas a categorías globales sin perder colecciones existentes.
        old_collection_categories = [
            (row["id"], (row["category"] or "General").strip() or "General")
            for row in con.execute("SELECT id, category FROM collections WHERE category_id IS NULL")
        ]
        for collection_id, category_name in old_collection_categories:
            cat = con.execute("SELECT id,name FROM categories WHERE name=? COLLATE NOCASE", (category_name,)).fetchone()
            if not cat:
                try:
                    cur = con.execute("INSERT INTO categories(name, created_at) VALUES (?,?)", (category_name, now_iso()))
                    category_id = cur.lastrowid
                except sqlite3.IntegrityError:
                    category_id = con.execute("SELECT id FROM categories WHERE name=? COLLATE NOCASE", (category_name,)).fetchone()["id"]
            else:
                category_id = cat["id"]
            con.execute("UPDATE collections SET category_id=? WHERE id=?", (category_id, collection_id))

        # V1.7.1: evita que Windows (\) y la interfaz web (/) representen la misma ruta de forma distinta.
        normalize_metadata_paths(con)

        count = con.execute("SELECT COUNT(*) AS c FROM categories").fetchone()["c"]
        if count == 0:
            con.executemany(
                "INSERT INTO categories(name, created_at) VALUES (?, ?)",
                [(c, now_iso()) for c in DEFAULT_CATEGORIES],
            )
        if con.execute("SELECT COUNT(*) AS c FROM tags").fetchone()["c"] == 0:
            con.executemany(
                "INSERT INTO tags(name, created_at) VALUES (?,?)",
                [(x, now_iso()) for x in ["Por revisar", "Favorito", "Seleccionado", "Para colección"]],
            )
        con.execute("UPDATE resources SET status='error', error='La aplicación se cerró durante la descarga.' WHERE status='descargando'")


def default_library() -> Path:
    docs = Path.home() / "Documents"
    if not docs.exists():
        docs = Path.home()
    return docs / "SorprezzLibrary"


def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if "library_path" in cfg:
                return cfg
        except Exception:
            pass
    cfg = {"library_path": str(default_library())}
    save_config(cfg)
    return cfg


def save_config(cfg: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    ensure_library(Path(cfg["library_path"]))


def ensure_library(root: Path) -> None:
    for folder in ["Biblioteca", "Colecciones_Web", "Exportaciones", "Temporales"]:
        (root / folder).mkdir(parents=True, exist_ok=True)


def library_root() -> Path:
    cfg = load_config()
    root = Path(cfg["library_path"]).expanduser().resolve()
    ensure_library(root)
    return root


def slug_folder(text: str) -> str:
    text = re.sub(r'[<>:"/\\|?*]+', " ", text).strip()
    text = re.sub(r"\s+", " ", text)
    return text[:100] or "Sin_nombre"


def safe_upload_relative_path(raw: str | None, fallback_name: str) -> Path:
    """Convierte la ruta enviada por el selector web en una ruta relativa segura.

    Permite conservar subcarpetas al importar una carpeta completa, pero bloquea
    rutas absolutas, ``..`` y caracteres no válidos en Windows.
    """
    value = str(raw or fallback_name or "archivo").replace("\\", "/").strip("/")
    parts: list[str] = []
    for part in PurePosixPath(value).parts:
        part = str(part).strip()
        if not part or part in {".", ".."}:
            continue
        clean = re.sub(r'[<>:"\\|?*]+', "_", part).strip().rstrip(".")
        if clean:
            parts.append(clean[:180])
    if not parts:
        parts = [re.sub(r'[<>:"/\\|?*]+', "_", Path(fallback_name or "archivo").name) or "archivo"]
    return Path(*parts)


PREVIEW_EXTS = {"jpg", "jpeg", "png", "webp", "gif", "bmp", "svg"}

def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except Exception:
        return False

def resource_root_path(rid: int) -> tuple[dict, Path]:
    r = get_resource(rid)
    if not r or not r.get("local_path"):
        raise HTTPException(404, "Este recurso todavía no tiene carpeta local")
    root = Path(r["local_path"]).resolve()
    if not root.exists():
        raise HTTPException(404, "La carpeta local ya no existe")
    return r, root

def safe_resource_path(rid: int, rel_path: str = "") -> tuple[dict, Path, Path]:
    r, root = resource_root_path(rid)
    clean = (rel_path or "").replace("\\", "/").strip("/")
    target = (root / clean).resolve() if clean else root
    if not _is_within(target, root):
        raise HTTPException(400, "Ruta no válida")
    return r, root, target

def unique_destination(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    for i in range(2, 10000):
        candidate = path.with_name(f"{stem} ({i}){suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError("No se pudo generar un nombre disponible")

def open_os_path(path: Path) -> None:
    if sys.platform.startswith("win"):
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])

def build_folder_tree(root: Path, current: Path, depth: int = 0, max_depth: int = 5) -> list[dict]:
    if depth > max_depth:
        return []
    result = []
    try:
        dirs = sorted([p for p in current.iterdir() if p.is_dir()], key=lambda p: p.name.lower())
    except OSError:
        return []
    for d in dirs:
        rel = str(d.relative_to(root)).replace("\\", "/")
        node = {"name": d.name, "path": rel, "children": []}
        if depth < max_depth:
            node["children"] = build_folder_tree(root, d, depth + 1, max_depth)
        result.append(node)
    return result


def extract_drive_info(url: str) -> dict:
    u = url.strip()
    if not u.startswith(("http://", "https://")):
        raise ValueError("El enlace debe comenzar con http:// o https://")
    is_drive = "drive.google.com" in u or "docs.google.com" in u
    folder_match = re.search(r"/folders/([A-Za-z0-9_-]+)", u)
    file_match = re.search(r"/(?:file/)?d/([A-Za-z0-9_-]+)", u)
    id_match = re.search(r"[?&]id=([A-Za-z0-9_-]+)", u)
    match = folder_match or file_match or id_match
    return {
        "is_drive": is_drive,
        "kind": "folder" if folder_match else ("file" if match else "link"),
        "id": match.group(1) if match else None,
    }


def get_resource(rid: int):
    with db() as con:
        row = con.execute(
            """
            SELECT r.*, c.name AS category_name, s.name AS subcategory_name
            FROM resources r
            LEFT JOIN categories c ON c.id=r.category_id
            LEFT JOIN subcategories s ON s.id=r.subcategory_id
            WHERE r.id=?
            """,
            (rid,),
        ).fetchone()
        return dict(row) if row else None


def index_resource(rid: int, path: Path) -> tuple[int, int]:
    """Reindexa sin destruir etiquetas.

    Las versiones anteriores reconstruían `files` usando \\ en Windows mientras las etiquetas
    guardaban /. Al sincronizar, parecía que los archivos etiquetados ya no existían y se borraban
    sus relaciones. Esta versión usa rutas canónicas y conserva metadatos incluso si un archivo fue
    renombrado o movido manualmente (por huella rápida).
    """
    with db() as con:
        old_files = [dict(x) for x in con.execute(
            'SELECT rel_path,fingerprint,size_bytes,mtime_ns FROM files WHERE resource_id=?', (rid,)
        )]
        old_tag_rows = [dict(x) for x in con.execute(
            'SELECT rel_path,tag_id FROM file_tags WHERE resource_id=?', (rid,)
        )]

    tags_by_path: dict[str, set[int]] = {}
    for tr in old_tag_rows:
        tags_by_path.setdefault(canonical_rel_path(tr['rel_path']), set()).add(int(tr['tag_id']))

    old_fp_by_path = {canonical_rel_path(x['rel_path']): (x.get('fingerprint') or '') for x in old_files}
    tags_by_fp: dict[str, set[int]] = {}
    for rel, tids in tags_by_path.items():
        fp = old_fp_by_path.get(rel)
        if fp:
            tags_by_fp.setdefault(fp, set()).update(tids)

    file_rows = []
    remapped_tags: set[tuple[str,int]] = set()
    total = 0
    count = 0
    for p in path.rglob('*'):
        if not p.is_file():
            continue
        try:
            st = p.stat(); size = st.st_size; mtime_ns = int(getattr(st, 'st_mtime_ns', 0) or 0)
        except OSError:
            size = 0; mtime_ns = 0
        rel = canonical_rel_path(str(p.relative_to(path)))
        ext = p.suffix.lower().lstrip('.') or 'sin_extension'
        fp = fast_file_fingerprint(p)
        file_rows.append((rid, rel, ext, size, now_iso(), fp, mtime_ns))
        total += size; count += 1
        tids = tags_by_path.get(rel) or (tags_by_fp.get(fp) if fp else None) or set()
        for tid in tids:
            remapped_tags.add((rel, int(tid)))

    with db() as con:
        # No se eliminan file_tags: si un archivo se desconecta temporalmente, su clasificación queda
        # guardada y reaparece cuando vuelve. Los conteos solo consideran archivos existentes.
        normalize_metadata_paths(con)
        con.execute('DELETE FROM files WHERE resource_id=?', (rid,))
        con.executemany(
            'INSERT OR REPLACE INTO files(resource_id,rel_path,ext,size_bytes,created_at,fingerprint,mtime_ns) VALUES (?,?,?,?,?,?,?)',
            file_rows,
        )
        for rel, tid in remapped_tags:
            con.execute('INSERT OR IGNORE INTO file_tags(resource_id,rel_path,tag_id) VALUES (?,?,?)', (rid, rel, tid))
        con.execute(
            'UPDATE resources SET file_count=?, total_bytes=?, updated_at=? WHERE id=?',
            (count, total, now_iso(), rid),
        )
    return count, total


def repair_existing_downloads() -> int:
    """Reindexa descargas locales que sí existen aunque una versión anterior las marcara con error.

    Esto corrige el caso en que Google Drive/gdown logra descargar parte o todo el contenido y
    luego lanza una excepción al encontrar un archivo no público. En versiones anteriores ese
    recurso quedaba en 0 archivos aunque la carpeta local tuviera contenido.
    """
    repaired = 0
    with db() as con:
        rows = [dict(r) for r in con.execute(
            "SELECT id, status, local_path, error FROM resources WHERE local_path IS NOT NULL AND local_path <> ''"
        )]
    for r in rows:
        path = Path(r["local_path"])
        if not path.exists():
            continue
        try:
            count, total = index_resource(r["id"], path)
        except Exception:
            continue
        if count <= 0:
            continue
        # V1.4.1: el estado principal refleja el contenido utilizable que existe localmente.
        # Si hay archivos en disco, el recurso se considera completado. Cualquier incidencia
        # de Google Drive se conserva únicamente como nota técnica dentro de Detalles.
        if r["status"] in ("error", "parcial", "pendiente", "descargando"):
            with db() as con:
                con.execute(
                    "UPDATE resources SET status='completado', file_count=?, total_bytes=?, updated_at=? WHERE id=?",
                    (count, total, now_iso(), r["id"]),
                )
            repaired += 1
    return repaired


def sync_resource_to_selected_collection(rid: int) -> dict:
    """Copia las imágenes indexadas de un recurso a la colección elegida al registrarlo."""
    with db() as con:
        row = con.execute("SELECT collection_id FROM resources WHERE id=?", (rid,)).fetchone()
        if not row or not row["collection_id"]:
            return {"added": 0, "skipped": 0}
        placeholders = ",".join("?" for _ in PREVIEW_EXTS)
        refs = [dict(x) for x in con.execute(
            f"SELECT resource_id, rel_path AS path FROM files "
            f"WHERE resource_id=? AND LOWER(ext) IN ({placeholders}) ORDER BY rel_path COLLATE NOCASE",
            (rid, *sorted(PREVIEW_EXTS)),
        )]
    if not refs:
        return {"added": 0, "skipped": 0}
    try:
        return add_assets_to_collection(int(row["collection_id"]), refs)
    except Exception:
        # La descarga nunca se marca como fallida solo porque la colección no pudo sincronizarse.
        return {"added": 0, "skipped": len(refs)}

def google_client_configured() -> bool:
    if not GOOGLE_OAUTH_CLIENT_PATH.exists():
        return False
    try:
        data = json.loads(GOOGLE_OAUTH_CLIENT_PATH.read_text(encoding="utf-8"))
        installed = data.get("installed") or {}
        return bool(installed.get("client_id") and installed.get("client_secret") and installed.get("token_uri"))
    except Exception:
        return False


def validate_google_client_json(raw: bytes) -> dict:
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except Exception as e:
        raise HTTPException(400, "El archivo no es un JSON válido de Google OAuth") from e
    installed = data.get("installed")
    if not isinstance(installed, dict):
        raise HTTPException(400, "Las credenciales deben ser de tipo Aplicación de escritorio")
    required = ["client_id", "client_secret", "auth_uri", "token_uri"]
    if any(not installed.get(k) for k in required):
        raise HTTPException(400, "El JSON de OAuth está incompleto")
    return data


def drive_accounts_rows() -> list[dict]:
    with db() as con:
        return [dict(x) for x in con.execute(
            "SELECT id,email,display_name,is_active,created_at,updated_at FROM drive_accounts ORDER BY is_active DESC,email COLLATE NOCASE"
        )]


def drive_account_row(account_id: int) -> dict | None:
    with db() as con:
        row = con.execute("SELECT * FROM drive_accounts WHERE id=?", (account_id,)).fetchone()
        return dict(row) if row else None


def google_credentials_for_account(account_id: int):
    row = drive_account_row(account_id)
    if not row:
        raise RuntimeError("La cuenta de Google Drive seleccionada no existe")
    token_path = Path(row["token_path"])
    if not token_path.exists():
        raise RuntimeError("La autorización de Google Drive ya no existe. Vuelve a conectar la cuenta.")
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
    except ImportError as e:
        raise RuntimeError("Faltan componentes de Google Drive API. Reinstala Sorprezz Asset Manager.") from e
    creds = Credentials.from_authorized_user_file(str(token_path), DRIVE_SCOPES)
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            token_path.write_text(creds.to_json(), encoding="utf-8")
        except Exception as e:
            raise RuntimeError("La sesión de Google Drive venció. Vuelve a conectar esta cuenta.") from e
    if not creds.valid:
        raise RuntimeError("La sesión de Google Drive no es válida. Vuelve a conectar esta cuenta.")
    return creds


def drive_service_for_account(account_id: int):
    try:
        from googleapiclient.discovery import build
    except ImportError as e:
        raise RuntimeError("Faltan componentes de Google Drive API. Reinstala Sorprezz Asset Manager.") from e
    creds = google_credentials_for_account(account_id)
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def drive_account_candidates(preferred_id: int | None = None) -> list[dict]:
    rows = drive_accounts_rows()
    if preferred_id:
        return [x for x in rows if int(x["id"]) == int(preferred_id)]
    return rows


def drive_get_item(service, file_id: str) -> dict:
    return service.files().get(
        fileId=file_id,
        fields="id,name,mimeType,size,modifiedTime,shortcutDetails",
        supportsAllDrives=True,
    ).execute()


def find_drive_account_with_access(file_id: str, preferred_id: int | None = None) -> tuple[dict, object, dict]:
    accounts = drive_account_candidates(preferred_id)
    if not accounts:
        raise RuntimeError("Conecta al menos una cuenta de Google Drive en Configuración")
    last_error = None
    for acc in accounts:
        try:
            service = drive_service_for_account(int(acc["id"]))
            item = drive_get_item(service, file_id)
            return acc, service, item
        except Exception as e:
            last_error = e
    raise RuntimeError("Ninguna cuenta conectada tiene acceso a este enlace de Google Drive") from last_error


def drive_list_children(service, folder_id: str) -> list[dict]:
    rows: list[dict] = []
    token = None
    while True:
        resp = service.files().list(
            q=f"'{folder_id}' in parents and trashed=false",
            spaces="drive",
            pageSize=1000,
            pageToken=token,
            fields="nextPageToken,files(id,name,mimeType,size,modifiedTime,shortcutDetails)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()
        rows.extend(resp.get("files", []))
        token = resp.get("nextPageToken")
        if not token:
            break
    return rows


def safe_drive_name(name: str) -> str:
    clean = re.sub(r'[<>:"/\\|?*]+', "_", str(name or "Sin_nombre")).strip().rstrip(".")
    return (clean[:180] or "Sin_nombre")


def drive_manifest(service, root_item: dict) -> dict:
    files: list[dict] = []
    skipped: list[dict] = []
    folder_count = 0
    total_bytes = 0
    visited_folders: set[str] = set()
    used_rel_paths: set[str] = set()

    def unique_manifest_rel(rel_dir: PurePosixPath, name: str) -> str:
        candidate = str(rel_dir / name) if str(rel_dir) != "." else name
        if candidate.lower() not in used_rel_paths:
            used_rel_paths.add(candidate.lower())
            return candidate
        stem, suffix = Path(name).stem, Path(name).suffix
        i = 2
        while True:
            alt = f"{stem} ({i}){suffix}"
            candidate = str(rel_dir / alt) if str(rel_dir) != "." else alt
            if candidate.lower() not in used_rel_paths:
                used_rel_paths.add(candidate.lower())
                return candidate
            i += 1

    def add_file(item: dict, rel_dir: PurePosixPath, display_name: str | None = None, source_id: str | None = None):
        nonlocal total_bytes
        mime = item.get("mimeType") or "application/octet-stream"
        name = safe_drive_name(display_name or item.get("name") or "archivo")
        export_mime = None
        export_ext = ""
        if mime.startswith("application/vnd.google-apps."):
            export = GOOGLE_EXPORTS.get(mime)
            if not export:
                skipped.append({"id": item.get("id"), "name": name, "reason": "Tipo de archivo de Google no exportable automáticamente"})
                return
            export_mime, export_ext = export
            if export_ext and not name.lower().endswith(export_ext):
                name += export_ext
        size = int(item.get("size") or 0)
        total_bytes += size
        rel_path = unique_manifest_rel(rel_dir, name)
        files.append({
            "id": source_id or item.get("id"),
            "name": Path(rel_path).name,
            "rel_path": rel_path,
            "mime_type": mime,
            "size": size,
            "export_mime": export_mime,
        })

    def walk_folder(folder_id: str, rel_dir: PurePosixPath):
        nonlocal folder_count
        if folder_id in visited_folders:
            skipped.append({"id": folder_id, "name": str(rel_dir), "reason": "Carpeta repetida o acceso directo circular"})
            return
        visited_folders.add(folder_id)
        for item in drive_list_children(service, folder_id):
            mime = item.get("mimeType")
            name = safe_drive_name(item.get("name") or "Sin_nombre")
            if mime == GOOGLE_FOLDER_MIME:
                folder_count += 1
                walk_folder(item["id"], rel_dir / name)
            elif mime == GOOGLE_SHORTCUT_MIME:
                target = (item.get("shortcutDetails") or {}).get("targetId")
                if not target:
                    skipped.append({"id": item.get("id"), "name": name, "reason": "Acceso directo sin destino"})
                    continue
                try:
                    target_item = drive_get_item(service, target)
                    if target_item.get("mimeType") == GOOGLE_FOLDER_MIME:
                        folder_count += 1
                        walk_folder(target, rel_dir / name)
                    else:
                        add_file(target_item, rel_dir, display_name=name, source_id=target)
                except Exception as e:
                    skipped.append({"id": item.get("id"), "name": name, "reason": f"No se pudo resolver el acceso directo: {e}"})
            else:
                add_file(item, rel_dir)

    if root_item.get("mimeType") == GOOGLE_FOLDER_MIME:
        walk_folder(root_item["id"], PurePosixPath("."))
        kind = "folder"
    else:
        add_file(root_item, PurePosixPath("."))
        kind = "file"
    return {
        "kind": kind,
        "name": root_item.get("name") or "Sin nombre",
        "files": files,
        "file_count": len(files),
        "folder_count": folder_count,
        "total_bytes": total_bytes,
        "skipped": skipped,
    }


def drive_analyze_url(url: str, preferred_account_id: int | None = None) -> dict:
    info = extract_drive_info(url)
    if not info.get("is_drive") or not info.get("id"):
        raise RuntimeError("No se pudo obtener el ID del enlace de Google Drive")
    acc, service, item = find_drive_account_with_access(info["id"], preferred_account_id)
    manifest = drive_manifest(service, item)
    return {
        "account": {"id": acc["id"], "email": acc["email"], "display_name": acc.get("display_name")},
        "item": {"id": item["id"], "name": item.get("name"), "mime_type": item.get("mimeType"), "kind": manifest["kind"]},
        "file_count": manifest["file_count"],
        "folder_count": manifest["folder_count"],
        "total_bytes": manifest["total_bytes"],
        "skipped_count": len(manifest["skipped"]),
        "skipped": manifest["skipped"][:20],
    }


def download_worker_drive_api(rid: int) -> None:
    resource = get_resource(rid)
    if not resource:
        return
    with active_lock:
        active_downloads[rid] = {"progress": 4, "message": "Analizando Google Drive con la API oficial..."}
    try:
        info = extract_drive_info(resource["url"])
        if not info.get("id"):
            raise RuntimeError("No se pudo obtener el ID del enlace de Google Drive")
        acc, service, root_item = find_drive_account_with_access(info["id"], resource.get("drive_account_id"))
        manifest = drive_manifest(service, root_item)
        cat = slug_folder(resource.get("category_name") or "Sin_categoria")
        sub = slug_folder(resource.get("subcategory_name") or "General")
        name = slug_folder(resource["name"])
        dest = library_root() / "Biblioteca" / cat / sub / name
        dest.mkdir(parents=True, exist_ok=True)
        with db() as con:
            con.execute(
                """UPDATE resources SET status='descargando',local_path=?,error=NULL,drive_account_id=?,drive_item_id=?,drive_item_kind=?,
                   remote_file_count=?,remote_folder_count=?,remote_total_bytes=?,downloaded_file_count=0,skipped_file_count=?,download_engine='drive_api',updated_at=? WHERE id=?""",
                (str(dest), int(acc["id"]), root_item.get("id"), manifest["kind"], manifest["file_count"], manifest["folder_count"],
                 manifest["total_bytes"], len(manifest["skipped"]), now_iso(), rid),
            )
        try:
            from googleapiclient.http import MediaIoBaseDownload
        except ImportError as e:
            raise RuntimeError("Faltan componentes de Google Drive API. Reinstala Sorprezz Asset Manager.") from e

        total_items = max(1, manifest["file_count"])
        completed = 0
        failed: list[str] = []
        for idx, item in enumerate(manifest["files"], start=1):
            rel = PurePosixPath(item["rel_path"])
            target = dest.joinpath(*rel.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and item.get("size") and target.stat().st_size == int(item["size"]):
                completed += 1
            else:
                temp_target = target.with_name(target.name + ".sorprezz.part")
                try:
                    if temp_target.exists():
                        temp_target.unlink()
                    if item.get("export_mime"):
                        request = service.files().export_media(fileId=item["id"], mimeType=item["export_mime"])
                    else:
                        request = service.files().get_media(fileId=item["id"])
                    with temp_target.open("wb") as fh:
                        downloader = MediaIoBaseDownload(fh, request, chunksize=8 * 1024 * 1024)
                        done = False
                        while not done:
                            _, done = downloader.next_chunk()
                    os.replace(temp_target, target)
                    completed += 1
                except Exception as e:
                    failed.append(f"{item['rel_path']}: {e}")
                    try:
                        if temp_target.exists():
                            temp_target.unlink()
                    except OSError:
                        pass
            with active_lock:
                pct = 18 + int((idx / total_items) * 72)
                active_downloads[rid] = {"progress": min(90, pct), "message": f"Descargando {idx} de {total_items} archivos · {acc['email']}"}

        with active_lock:
            active_downloads[rid] = {"progress": 94, "message": "Verificando e indexando la descarga..."}
        local_count, local_bytes = index_resource(rid, dest)
        skipped_count = len(manifest["skipped"]) + len(failed)
        status = "completado" if completed == manifest["file_count"] and not failed and not manifest["skipped"] else "incompleto"
        note_parts = []
        if manifest["skipped"]:
            note_parts.append(f"{len(manifest['skipped'])} elementos no descargables automáticamente")
        if failed:
            note_parts.append(f"{len(failed)} archivos fallaron")
        error = "; ".join(note_parts) if note_parts else None
        with db() as con:
            con.execute(
                """UPDATE resources SET status=?,error=?,file_count=?,total_bytes=?,downloaded_file_count=?,skipped_file_count=?,updated_at=? WHERE id=?""",
                (status, error, local_count, local_bytes, completed, skipped_count, now_iso(), rid),
            )
        sync_resource_to_selected_collection(rid)
        with active_lock:
            active_downloads[rid] = {
                "progress": 100,
                "message": (f"Verificado: {completed} de {manifest['file_count']} archivos" if status == "completado" else f"Incompleto: {completed} de {manifest['file_count']} archivos"),
            }
        time.sleep(1)
    except Exception as e:
        current = get_resource(rid)
        local_path = current.get("local_path") if current else None
        if local_path and Path(local_path).exists():
            try:
                count, total = index_resource(rid, Path(local_path))
            except Exception:
                count, total = 0, 0
        else:
            count, total = 0, 0
        with db() as con:
            con.execute(
                "UPDATE resources SET status='error',error=?,file_count=?,total_bytes=?,download_engine='drive_api',updated_at=? WHERE id=?",
                (str(e), count, total, now_iso(), rid),
            )
        with active_lock:
            active_downloads[rid] = {"progress": 0, "message": str(e), "error": True}
        time.sleep(1)
    finally:
        with active_lock:
            active_downloads.pop(rid, None)


def download_worker(rid: int) -> None:
    resource = get_resource(rid)
    if not resource:
        return
    info = extract_drive_info(resource.get("url") or "")
    has_accounts = bool(drive_accounts_rows())
    if google_client_configured() and has_accounts and info.get("is_drive") and info.get("id"):
        if resource.get("drive_account_id"):
            return download_worker_drive_api(rid)
        # En modo Automático, usa la API si alguna cuenta puede acceder. Si ninguna puede,
        # se conserva el modo de enlace público como respaldo para enlaces realmente públicos.
        try:
            find_drive_account_with_access(info["id"], None)
            return download_worker_drive_api(rid)
        except Exception:
            return download_worker_public(rid)
    return download_worker_public(rid)


def download_worker_public(rid: int) -> None:
    resource = get_resource(rid)
    if not resource:
        return
    with active_lock:
        active_downloads[rid] = {"progress": 5, "message": "Preparando descarga..."}

    try:
        info = extract_drive_info(resource["url"])
        cat = slug_folder(resource.get("category_name") or "Sin_categoria")
        sub = slug_folder(resource.get("subcategory_name") or "General")
        name = slug_folder(resource["name"])
        dest = library_root() / "Biblioteca" / cat / sub / name
        dest.mkdir(parents=True, exist_ok=True)

        with db() as con:
            con.execute(
                "UPDATE resources SET status='descargando', local_path=?, error=NULL, updated_at=? WHERE id=?",
                (str(dest), now_iso(), rid),
            )

        with active_lock:
            active_downloads[rid] = {"progress": 12, "message": "Conectando con Google Drive..."}

        try:
            import gdown
        except ImportError as e:
            raise RuntimeError(
                "Falta el componente de descarga de Google Drive. Reinstala Sorprezz Asset Manager."
            ) from e

        download_warning = None
        before_files = {str(p) for p in dest.rglob("*") if p.is_file()}

        # IMPORTANTE: gdown puede descargar muchos archivos y después lanzar una excepción
        # por un único elemento sin enlace público. No descartamos lo ya descargado.
        try:
            if info["kind"] == "folder":
                with active_lock:
                    active_downloads[rid] = {"progress": 25, "message": "Descargando carpeta y subcarpetas..."}
                result = gdown.download_folder(
                    url=resource["url"],
                    output=str(dest),
                    quiet=True,
                    use_cookies=False,
                    remaining_ok=True,
                )
                if result is None and not any(dest.rglob("*")):
                    raise RuntimeError(
                        "Google Drive no permitió descargar la carpeta. Verifica que el enlace esté compartido para lectura."
                    )
            else:
                with active_lock:
                    active_downloads[rid] = {"progress": 25, "message": "Descargando archivo..."}
                result = gdown.download(
                    resource["url"],
                    output=str(dest) + os.sep,
                    quiet=True,
                    fuzzy=True,
                    use_cookies=False,
                )
                if not result:
                    raise RuntimeError("No se pudo descargar el archivo desde el enlace proporcionado.")
        except Exception as e:
            download_warning = str(e)

        with active_lock:
            active_downloads[rid] = {"progress": 86, "message": "Verificando e indexando archivos locales..."}

        # Siempre indexamos el destino, incluso si Drive/gdown reportó una excepción.
        count, total = index_resource(rid, dest)
        after_files = {str(p) for p in dest.rglob("*") if p.is_file()}

        if count <= 0:
            if download_warning:
                raise RuntimeError(download_warning)
            if not (after_files - before_files):
                raise RuntimeError("La descarga terminó sin archivos. Revisa los permisos del enlace.")

        if download_warning:
            # El contenido descargado es utilizable: no mostramos un estado de alarma en Inicio.
            # Guardamos la incidencia como nota técnica consultable desde Detalles.
            technical_note = f"Nota técnica de Google Drive: {download_warning}"
            with db() as con:
                con.execute(
                    "UPDATE resources SET status='completado', error=?, file_count=?, total_bytes=?, updated_at=? WHERE id=?",
                    (technical_note, count, total, now_iso(), rid),
                )
            with active_lock:
                active_downloads[rid] = {
                    "progress": 100,
                    "message": f"Completado: {count} archivos indexados",
                }
        else:
            with db() as con:
                con.execute(
                    "UPDATE resources SET status='completado', error=NULL, file_count=?, total_bytes=?, updated_at=? WHERE id=?",
                    (count, total, now_iso(), rid),
                )
            with active_lock:
                active_downloads[rid] = {"progress": 100, "message": f"Completado: {count} archivos"}

        sync_resource_to_selected_collection(rid)
        time.sleep(1)

    except Exception as e:
        # Última oportunidad: si hay contenido local, lo indexamos y evitamos el falso 0 archivos.
        current = get_resource(rid)
        local_path = current.get("local_path") if current else None
        recovered = False
        if local_path:
            path = Path(local_path)
            if path.exists():
                try:
                    count, total = index_resource(rid, path)
                    if count > 0:
                        with db() as con:
                            con.execute(
                                "UPDATE resources SET status='completado', error=?, file_count=?, total_bytes=?, updated_at=? WHERE id=?",
                                (f"Nota técnica de Google Drive: {e}", count, total, now_iso(), rid),
                            )
                        with active_lock:
                            active_downloads[rid] = {
                                "progress": 100,
                                "message": f"Completado: {count} archivos encontrados",
                            }
                        sync_resource_to_selected_collection(rid)
                        recovered = True
                except Exception:
                    pass
        if not recovered:
            with db() as con:
                con.execute(
                    "UPDATE resources SET status='error', error=?, updated_at=? WHERE id=?",
                    (str(e), now_iso(), rid),
                )
            with active_lock:
                active_downloads[rid] = {"progress": 0, "message": str(e), "error": True}
    finally:
        time.sleep(2)
        with active_lock:
            active_downloads.pop(rid, None)


class CategoryIn(BaseModel):
    name: str


class SubcategoryIn(BaseModel):
    category_id: int
    name: str


class ResourceIn(BaseModel):
    name: str
    url: str
    category_id: int
    subcategory_id: Optional[int] = None
    tag_ids: list[int] = []
    collection_id: Optional[int] = None
    avoid_duplicates: bool = True
    drive_account_id: Optional[int] = None


class ManualResourceIn(BaseModel):
    name: str
    category_id: int
    subcategory_id: Optional[int] = None
    tag_ids: list[int] = []
    collection_id: Optional[int] = None


class ResourceMetadataIn(BaseModel):
    name: str
    category_id: int
    subcategory_id: Optional[int] = None
    tag_ids: list[int] = []


class ImportFilesIn(BaseModel):
    paths: list[str] = []
    target_path: str = ""


class DeleteItemsIn(BaseModel):
    paths: list[str] = []


class ConfigIn(BaseModel):
    library_path: str


class FolderCreateIn(BaseModel):
    parent_path: str = ""
    name: str


class FileOrganizeIn(BaseModel):
    paths: list[str]
    target_path: str = ""
    operation: str = "copy"


class RenameIn(BaseModel):
    path: str
    new_name: str


class SelectionZipIn(BaseModel):
    paths: list[str]
    name: str = "Seleccion_Sorprezz"


class TagIn(BaseModel):
    name: str


class TagAssignIn(BaseModel):
    tag_ids: list[int] = []


class FolderTagAssignIn(BaseModel):
    path: str = ""
    tag_ids: list[int] = []


class FileTagAssignIn(BaseModel):
    path: str
    tag_ids: list[int] = []


class FileTagBulkIn(BaseModel):
    paths: list[str] = []
    tag_ids: list[int] = []
    mode: str = "add"


class TagApplyFilesIn(BaseModel):
    path: str = ""
    tag_ids: list[int] = []
    mode: str = "add"
    images_only: bool = True


class SelectionExportIn(BaseModel):
    paths: list[str] = []
    destination_dir: str
    folder_name: str = ""


class CollectionIn(BaseModel):
    name: str
    category_id: int
    subcategory_id: Optional[int] = None
    description: str = ""


class CollectionAssetRef(BaseModel):
    resource_id: int
    path: str


class CollectionAddItemsIn(BaseModel):
    items: list[CollectionAssetRef] = []


class CollectionFromTagIn(BaseModel):
    name: str
    category_id: int
    subcategory_id: Optional[int] = None
    description: str = ""
    tag_id: int


class FolderTemplateIn(BaseModel):
    name: str
    description: str = ""
    folders: list[str] = []


class FolderTemplateApplyIn(BaseModel):
    parent_path: str = ""


class CatalogCreateIn(BaseModel):
    resource_id: int
    source_rel_path: str
    title: str
    product_type: str = "Otro"
    technique: str = "Sublimación"
    print_size: str = ""
    variants: str = ""
    status: str = "seleccionado"
    sku: str = ""
    price: str = ""
    tags: str = ""
    notes: str = ""


class CatalogUpdateIn(BaseModel):
    title: str
    product_type: str = "Otro"
    technique: str = "Sublimación"
    print_size: str = ""
    variants: str = ""
    status: str = "seleccionado"
    sku: str = ""
    price: str = ""
    tags: str = ""
    notes: str = ""


def _background_repair_existing_downloads() -> None:
    """Repara/indexa recursos existentes sin bloquear el arranque de la interfaz.

    En bibliotecas grandes o ubicadas en discos lentos/OneDrive, recorrer y calcular
    huellas puede tardar bastante. La app debe quedar disponible primero.
    """
    try:
        repair_existing_downloads()
    except Exception:
        # Es una tarea de mantenimiento: un fallo aquí nunca debe impedir abrir Sorprezz.
        pass


@app.on_event("startup")
def startup():
    init_db()
    load_config()
    threading.Thread(
        target=_background_repair_existing_downloads,
        daemon=True,
        name="SorprezzStartupRepair",
    ).start()


@app.get("/")
def home():
    return FileResponse(WEB_DIR / "index.html")


@app.get("/api/google/status")
def google_status():
    accounts = drive_accounts_rows()
    return {
        "client_configured": google_client_configured(),
        "client_path": str(GOOGLE_OAUTH_CLIENT_PATH) if GOOGLE_OAUTH_CLIENT_PATH.exists() else None,
        "accounts": accounts,
        "scope": DRIVE_SCOPES[0],
    }


@app.post("/api/google/client")
async def google_client_upload(file: UploadFile = File(...)):
    raw = await file.read()
    validate_google_client_json(raw)
    GOOGLE_OAUTH_CLIENT_PATH.write_bytes(raw)
    try:
        await file.close()
    except Exception:
        pass
    return {"ok": True, "client_configured": True}


@app.delete("/api/google/client")
def google_client_delete():
    if GOOGLE_OAUTH_CLIENT_PATH.exists():
        GOOGLE_OAUTH_CLIENT_PATH.unlink()
    return {"ok": True}


@app.post("/api/google/accounts/connect")
def google_account_connect():
    if not google_client_configured():
        raise HTTPException(400, "Primero selecciona el archivo JSON del cliente OAuth")
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as e:
        raise HTTPException(500, "Faltan componentes de Google Drive API. Reinstala Sorprezz Asset Manager.") from e
    try:
        flow = InstalledAppFlow.from_client_secrets_file(str(GOOGLE_OAUTH_CLIENT_PATH), scopes=DRIVE_SCOPES)
        creds = flow.run_local_server(
            host="127.0.0.1", port=0, open_browser=True,
            authorization_prompt_message="Se abrirá Google para autorizar Sorprezz Asset Manager.",
            success_message="Google Drive quedó conectado. Puedes cerrar esta ventana y volver a Sorprezz Asset Manager.",
            prompt="consent", access_type="offline",
        )
        service = build("drive", "v3", credentials=creds, cache_discovery=False)
        about = service.about().get(fields="user").execute().get("user") or {}
        email = (about.get("emailAddress") or "").strip()
        if not email:
            raise RuntimeError("Google no devolvió el correo de la cuenta autorizada")
        display_name = (about.get("displayName") or email).strip()
        token_name = hashlib.sha256(email.lower().encode("utf-8")).hexdigest()[:20] + ".json"
        token_path = GOOGLE_TOKEN_DIR / token_name
        token_path.write_text(creds.to_json(), encoding="utf-8")
        with db() as con:
            existing = con.execute("SELECT id FROM drive_accounts WHERE email=? COLLATE NOCASE", (email,)).fetchone()
            if existing:
                account_id = int(existing["id"])
                con.execute("UPDATE drive_accounts SET display_name=?,token_path=?,updated_at=? WHERE id=?", (display_name, str(token_path), now_iso(), account_id))
            else:
                active_count = int(con.execute("SELECT COUNT(*) AS c FROM drive_accounts WHERE is_active=1").fetchone()["c"] or 0)
                cur = con.execute(
                    "INSERT INTO drive_accounts(email,display_name,token_path,is_active,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                    (email, display_name, str(token_path), 1 if active_count == 0 else 0, now_iso(), now_iso()),
                )
                account_id = int(cur.lastrowid)
        return {"ok": True, "account": next(x for x in drive_accounts_rows() if int(x["id"]) == account_id)}
    except Exception as e:
        raise HTTPException(400, f"No se pudo conectar Google Drive: {e}")


@app.post("/api/google/accounts/{account_id}/activate")
def google_account_activate(account_id: int):
    if not drive_account_row(account_id):
        raise HTTPException(404, "Cuenta no encontrada")
    with db() as con:
        con.execute("UPDATE drive_accounts SET is_active=0")
        con.execute("UPDATE drive_accounts SET is_active=1,updated_at=? WHERE id=?", (now_iso(), account_id))
    return {"ok": True, "accounts": drive_accounts_rows()}


@app.delete("/api/google/accounts/{account_id}")
def google_account_delete(account_id: int):
    row = drive_account_row(account_id)
    if not row:
        raise HTTPException(404, "Cuenta no encontrada")
    try:
        token_path = Path(row["token_path"])
        if token_path.exists():
            token_path.unlink()
    except OSError:
        pass
    with db() as con:
        was_active = bool(row.get("is_active"))
        con.execute("UPDATE resources SET drive_account_id=NULL WHERE drive_account_id=?", (account_id,))
        con.execute("DELETE FROM drive_accounts WHERE id=?", (account_id,))
        if was_active:
            first = con.execute("SELECT id FROM drive_accounts ORDER BY id LIMIT 1").fetchone()
            if first:
                con.execute("UPDATE drive_accounts SET is_active=1 WHERE id=?", (first["id"],))
    return {"ok": True, "accounts": drive_accounts_rows()}


@app.post("/api/google/analyze")
def google_analyze(payload: ResourceIn):
    try:
        return drive_analyze_url(payload.url, payload.drive_account_id)
    except Exception as e:
        raise HTTPException(400, str(e))


@app.get("/api/config")
def api_config():
    cfg = load_config()
    return {**cfg, "db_path": str(DB_PATH), "version": APP_VERSION}


@app.put("/api/config")
def api_config_put(payload: ConfigIn):
    p = Path(payload.library_path).expanduser()
    try:
        p.mkdir(parents=True, exist_ok=True)
        test = p / ".sorprezz_write_test"
        test.write_text("ok", encoding="utf-8")
        test.unlink()
    except Exception as e:
        raise HTTPException(400, f"No se puede escribir en esa ubicación: {e}")
    save_config({"library_path": str(p.resolve())})
    return {"ok": True, "library_path": str(p.resolve())}


@app.get("/api/categories")
def categories():
    with db() as con:
        cats = [dict(r) for r in con.execute("SELECT * FROM categories ORDER BY name COLLATE NOCASE")]
        subs = [dict(r) for r in con.execute("SELECT * FROM subcategories ORDER BY name COLLATE NOCASE")]
    by_cat = {}
    for s in subs:
        by_cat.setdefault(s["category_id"], []).append(s)
    for c in cats:
        c["subcategories"] = by_cat.get(c["id"], [])
    return cats


@app.post("/api/categories")
def category_create(payload: CategoryIn):
    name = payload.name.strip()
    if not name:
        raise HTTPException(400, "Nombre requerido")
    try:
        with db() as con:
            cur = con.execute("INSERT INTO categories(name, created_at) VALUES (?,?)", (name, now_iso()))
            return {"id": cur.lastrowid, "name": name}
    except sqlite3.IntegrityError:
        raise HTTPException(409, "La categoría ya existe")


@app.post("/api/subcategories")
def subcategory_create(payload: SubcategoryIn):
    name = payload.name.strip()
    if not name:
        raise HTTPException(400, "Nombre requerido")
    try:
        with db() as con:
            cur = con.execute(
                "INSERT INTO subcategories(category_id, name, created_at) VALUES (?,?,?)",
                (payload.category_id, name, now_iso()),
            )
            return {"id": cur.lastrowid, "name": name}
    except sqlite3.IntegrityError:
        raise HTTPException(409, "La subcategoría ya existe")


@app.post("/api/analyze")
def analyze(payload: ResourceIn):
    try:
        info = extract_drive_info(payload.url)
    except ValueError as e:
        raise HTTPException(400, str(e))
    with db() as con:
        existing = con.execute("SELECT id, name, status, local_path FROM resources WHERE url=?", (payload.url.strip(),)).fetchone()
    result = {"valid": True, "info": info, "existing": dict(existing) if existing else None, "engine": "public_link"}
    if info.get("is_drive") and info.get("id") and google_client_configured() and drive_accounts_rows():
        try:
            remote = drive_analyze_url(payload.url, payload.drive_account_id)
            result.update({"engine": "drive_api", "remote": remote})
        except Exception as e:
            result["drive_api_error"] = str(e)
    return result


@app.post("/api/resources")
def resource_create(payload: ResourceIn):
    name = payload.name.strip()
    url = payload.url.strip()
    if not name or not url:
        raise HTTPException(400, "Nombre y enlace son obligatorios")
    try:
        extract_drive_info(url)
    except ValueError as e:
        raise HTTPException(400, str(e))

    if payload.collection_id:
        with db() as con:
            if not con.execute("SELECT id FROM collections WHERE id=?", (payload.collection_id,)).fetchone():
                raise HTTPException(404, "La colección seleccionada no existe")
    if payload.drive_account_id and not drive_account_row(int(payload.drive_account_id)):
        raise HTTPException(404, "La cuenta de Google Drive seleccionada no existe")

    with db() as con:
        existing = con.execute("SELECT id FROM resources WHERE url=?", (url,)).fetchone()
        if existing:
            if payload.avoid_duplicates:
                raise HTTPException(409, "Este enlace ya está registrado en la biblioteca")
            rid = int(existing["id"])
            con.execute(
                """UPDATE resources
                   SET name=?,category_id=?,subcategory_id=?,collection_id=?,avoid_duplicates=?,drive_account_id=?,source_type='drive',source_detail=?,updated_at=?
                   WHERE id=?""",
                (name, payload.category_id, payload.subcategory_id, payload.collection_id,
                 1 if payload.avoid_duplicates else 0, payload.drive_account_id, url, now_iso(), rid),
            )
        else:
            cur = con.execute(
                """
                INSERT INTO resources(
                    name,url,category_id,subcategory_id,status,collection_id,avoid_duplicates,drive_account_id,source_type,source_detail,created_at,updated_at
                )
                VALUES (?,?,?,?, 'pendiente', ?, ?, ?, 'drive', ?, ?, ?)
                """,
                (name, url, payload.category_id, payload.subcategory_id, payload.collection_id,
                 1 if payload.avoid_duplicates else 0, payload.drive_account_id, url, now_iso(), now_iso()),
            )
            rid = int(cur.lastrowid)

        # Las etiquetas elegidas en Descargas se asignan al recurso completo.
        con.execute("DELETE FROM resource_tags WHERE resource_id=?", (rid,))
        valid_tag_ids = []
        for tid in payload.tag_ids:
            if con.execute("SELECT id FROM tags WHERE id=?", (int(tid),)).fetchone():
                valid_tag_ids.append(int(tid))
        con.executemany(
            "INSERT OR IGNORE INTO resource_tags(resource_id,tag_id) VALUES (?,?)",
            [(rid, tid) for tid in valid_tag_ids],
        )
    return get_resource(rid)


@app.post("/api/resources/manual")
def resource_create_manual(payload: ManualResourceIn):
    name = payload.name.strip()
    if not name:
        raise HTTPException(400, "Escribe un nombre para el material")
    with db() as con:
        cat = con.execute("SELECT id,name FROM categories WHERE id=?", (payload.category_id,)).fetchone()
        if not cat:
            raise HTTPException(404, "La categoría seleccionada no existe")
        sub_name = "General"
        if payload.subcategory_id:
            sub = con.execute(
                "SELECT id,name FROM subcategories WHERE id=? AND category_id=?",
                (payload.subcategory_id, payload.category_id),
            ).fetchone()
            if not sub:
                raise HTTPException(400, "La subcategoría no corresponde a la categoría")
            sub_name = sub["name"]
        if payload.collection_id and not con.execute("SELECT id FROM collections WHERE id=?", (payload.collection_id,)).fetchone():
            raise HTTPException(404, "La colección seleccionada no existe")

    dest = library_root() / "Biblioteca" / slug_folder(cat["name"]) / slug_folder(sub_name) / slug_folder(name)
    if dest.exists():
        raise HTTPException(409, "Ya existe una carpeta con ese nombre en esa categoría y subcategoría")
    dest.mkdir(parents=True, exist_ok=False)
    manual_url = f"manual://{uuid.uuid4()}"
    try:
        with db() as con:
            cur = con.execute(
                """INSERT INTO resources(
                       name,url,category_id,subcategory_id,status,local_path,file_count,total_bytes,error,
                       collection_id,avoid_duplicates,source_type,source_detail,created_at,updated_at
                   ) VALUES (?,?,?,?, 'completado', ?,0,0,NULL, ?,1,'manual','Creado manualmente',?,?)""",
                (name, manual_url, payload.category_id, payload.subcategory_id, str(dest), payload.collection_id, now_iso(), now_iso()),
            )
            rid = int(cur.lastrowid)
            for tid in payload.tag_ids:
                if con.execute("SELECT id FROM tags WHERE id=?", (int(tid),)).fetchone():
                    con.execute("INSERT OR IGNORE INTO resource_tags(resource_id,tag_id) VALUES (?,?)", (rid, int(tid)))
    except Exception:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    return get_resource(rid)


@app.put("/api/resources/{rid}/metadata")
def resource_update_metadata(rid: int, payload: ResourceMetadataIn):
    r = get_resource(rid)
    if not r:
        raise HTTPException(404, "Recurso no encontrado")
    name = payload.name.strip()
    if not name:
        raise HTTPException(400, "Escribe un nombre")
    with db() as con:
        cat = con.execute("SELECT id,name FROM categories WHERE id=?", (payload.category_id,)).fetchone()
        if not cat:
            raise HTTPException(404, "La categoría no existe")
        sub_name = "General"
        if payload.subcategory_id:
            sub = con.execute("SELECT id,name FROM subcategories WHERE id=? AND category_id=?", (payload.subcategory_id, payload.category_id)).fetchone()
            if not sub:
                raise HTTPException(400, "La subcategoría no corresponde a la categoría")
            sub_name = sub["name"]

    new_local_path = r.get("local_path")
    if r.get("local_path"):
        current = Path(r["local_path"])
        target = library_root() / "Biblioteca" / slug_folder(cat["name"]) / slug_folder(sub_name) / slug_folder(name)
        try:
            if current.exists() and current.resolve() != target.resolve():
                if target.exists():
                    raise HTTPException(409, "Ya existe una carpeta con ese nombre en la clasificación seleccionada")
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(current), str(target))
                new_local_path = str(target)
        except HTTPException:
            raise
        except OSError as e:
            raise HTTPException(500, f"No se pudo mover la carpeta física: {e}")

    with db() as con:
        con.execute(
            "UPDATE resources SET name=?,category_id=?,subcategory_id=?,local_path=?,updated_at=? WHERE id=?",
            (name, payload.category_id, payload.subcategory_id, new_local_path, now_iso(), rid),
        )
        con.execute("DELETE FROM resource_tags WHERE resource_id=?", (rid,))
        for tid in payload.tag_ids:
            if con.execute("SELECT id FROM tags WHERE id=?", (int(tid),)).fetchone():
                con.execute("INSERT OR IGNORE INTO resource_tags(resource_id,tag_id) VALUES (?,?)", (rid, int(tid)))
    return get_resource(rid)


def normalize_search_text(value: str) -> str:
    """Normaliza mayúsculas, acentos y espacios para búsquedas tolerantes."""
    value = unicodedata.normalize("NFKD", str(value or ""))
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"\\s+", " ", value).strip().lower()
    return value


@app.get("/api/resources")
def resources(q: str = "", category_id: Optional[int] = None, status: str = "", tag_id: Optional[int] = None):
    sql = """
        SELECT r.*, c.name AS category_name, s.name AS subcategory_name
        FROM resources r
        LEFT JOIN categories c ON c.id=r.category_id
        LEFT JOIN subcategories s ON s.id=r.subcategory_id
        WHERE 1=1
    """
    params = []
    if category_id:
        sql += " AND r.category_id=?"
        params.append(category_id)
    if status:
        sql += " AND r.status=?"
        params.append(status)
    if tag_id:
        sql += " AND EXISTS (SELECT 1 FROM resource_tags rt WHERE rt.resource_id=r.id AND rt.tag_id=?)"
        params.append(tag_id)
    sql += " ORDER BY r.updated_at DESC"

    with db() as con:
        rows = [dict(r) for r in con.execute(sql, params)]
        for row in rows:
            row["tags"] = [dict(x) for x in con.execute(
                "SELECT t.id,t.name FROM tags t JOIN resource_tags rt ON rt.tag_id=t.id WHERE rt.resource_id=? ORDER BY t.name COLLATE NOCASE",
                (row["id"],),
            )]

    # Búsqueda en memoria: permite buscar sin acentos y por varias palabras.
    # Ej.: "dia mujer" encuentra "8m día de la mujer".
    query = normalize_search_text(q)
    if query:
        terms = [x for x in query.split(" ") if x]
        filtered_rows = []
        for row in rows:
            searchable = " ".join([
                str(row.get("name") or ""),
                str(row.get("url") or ""),
                str(row.get("category_name") or ""),
                str(row.get("subcategory_name") or ""),
                "manual" if row.get("source_type") == "manual" else "google drive descarga",
                " ".join(str(t.get("name") or "") for t in row.get("tags", [])),
            ])
            haystack = normalize_search_text(searchable)
            if all(term in haystack for term in terms):
                filtered_rows.append(row)
        rows = filtered_rows

    with active_lock:
        for r in rows:
            if r["id"] in active_downloads:
                r["live"] = active_downloads[r["id"]]
    return rows


@app.get("/api/resources/{rid}")
def resource_detail(rid: int):
    r = get_resource(rid)
    if not r:
        raise HTTPException(404, "Recurso no encontrado")
    with db() as con:
        types = [dict(x) for x in con.execute(
            "SELECT ext, COUNT(*) count, SUM(size_bytes) bytes FROM files WHERE resource_id=? GROUP BY ext ORDER BY count DESC",
            (rid,),
        )]
        files = [dict(x) for x in con.execute(
            "SELECT rel_path, ext, size_bytes FROM files WHERE resource_id=? ORDER BY rel_path LIMIT 500",
            (rid,),
        )]
        resource_tags = [dict(x) for x in con.execute("SELECT t.id,t.name FROM tags t JOIN resource_tags rt ON rt.tag_id=t.id WHERE rt.resource_id=? ORDER BY t.name COLLATE NOCASE", (rid,))]
    r["types"] = types
    r["files"] = files
    r["tags"] = resource_tags
    with active_lock:
        r["live"] = active_downloads.get(rid)
    return r


@app.post("/api/resources/{rid}/download")
def resource_download(rid: int):
    r = get_resource(rid)
    if not r:
        raise HTTPException(404, "Recurso no encontrado")
    with active_lock:
        if rid in active_downloads:
            return {"ok": True, "message": "La descarga ya está activa"}
    executor.submit(download_worker, rid)
    return {"ok": True, "message": "Descarga iniciada"}


@app.post("/api/resources/{rid}/rescan")
def resource_rescan(rid: int):
    r = get_resource(rid)
    if not r or not r.get("local_path"):
        raise HTTPException(404, "No existe carpeta local para indexar")
    path = Path(r["local_path"])
    if not path.exists():
        raise HTTPException(404, "La carpeta local ya no existe")
    count, total = index_resource(rid, path)
    try:
        sync_resource_to_selected_collection(rid)
    except Exception:
        pass
    if count > 0:
        new_status = "completado"
        with db() as con:
            con.execute(
                "UPDATE resources SET status=?, file_count=?, total_bytes=?, updated_at=? WHERE id=?",
                (new_status, count, total, now_iso(), rid),
            )
    return {"ok": True, "file_count": count, "total_bytes": total, "status": (new_status if count > 0 else r.get("status"))}


@app.post("/api/resources/rescan-all")
def resources_rescan_all():
    """Reindexa toda la Biblioteca preservando categorías, etiquetas y colecciones."""
    backup_path = backup_metadata_db('sync')
    with db() as con:
        tag_links_before = int(con.execute("SELECT COUNT(*) AS c FROM file_tags").fetchone()["c"] or 0)
        rows = [dict(x) for x in con.execute(
            "SELECT id, local_path FROM resources WHERE local_path IS NOT NULL AND local_path <> '' ORDER BY id"
        )]
    scanned = 0
    files = 0
    total_bytes = 0
    missing = 0
    for row in rows:
        path = Path(row["local_path"])
        if not path.exists() or not path.is_dir():
            missing += 1
            continue
        try:
            count, total = index_resource(row["id"], path)
            try:
                sync_resource_to_selected_collection(row["id"])
            except Exception:
                pass
            files += count
            total_bytes += total
            scanned += 1
            if count > 0:
                with db() as con:
                    con.execute(
                        "UPDATE resources SET status='completado', file_count=?, total_bytes=?, updated_at=? WHERE id=?",
                        (count, total, now_iso(), row["id"]),
                    )
        except Exception:
            continue
    with db() as con:
        tag_links_after = int(con.execute("SELECT COUNT(*) AS c FROM file_tags").fetchone()["c"] or 0)
    return {
        "ok": True, "resources": scanned, "files": files, "total_bytes": total_bytes,
        "missing": missing, "backup": backup_path,
        "tag_links_before": tag_links_before, "tag_links_after": tag_links_after,
        "tags_preserved": tag_links_after >= tag_links_before,
    }


@app.post("/api/resources/{rid}/open")
def resource_open(rid: int):
    r = get_resource(rid)
    if not r or not r.get("local_path"):
        raise HTTPException(404, "Este recurso todavía no tiene carpeta local")
    p = Path(r["local_path"])
    if not p.exists():
        raise HTTPException(404, "La carpeta ya no existe")
    # Mantiene sincronizados conteo/tamaño aunque una descarga anterior haya terminado con aviso.
    try:
        count, total = index_resource(rid, p)
        if count > 0 and r.get("status") in ("error", "parcial"):
            with db() as con:
                con.execute(
                    "UPDATE resources SET status='completado', file_count=?, total_bytes=?, updated_at=? WHERE id=?",
                    (count, total, now_iso(), rid),
                )
    except Exception:
        pass
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(p))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(p)])
        else:
            subprocess.Popen(["xdg-open", str(p)])
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/resources/{rid}/tree")
def resource_tree(rid: int):
    r, root = resource_root_path(rid)
    return {"resource": r, "tree": build_folder_tree(root, root), "root_name": root.name}


@app.get("/api/resources/{rid}/browse")
def resource_browse(rid: int, path: str = ""):
    r, root, current = safe_resource_path(rid, path)
    if not current.exists() or not current.is_dir():
        raise HTTPException(404, "La carpeta no existe")
    items = []
    try:
        entries = sorted(current.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except OSError as e:
        raise HTTPException(500, f"No se pudo leer la carpeta: {e}")
    with db() as con:
        cataloged = {x["source_rel_path"] for x in con.execute(
            "SELECT source_rel_path FROM catalog_items WHERE resource_id=? AND source_rel_path IS NOT NULL", (rid,)
        )}
        folder_tag_rows = [dict(x) for x in con.execute(
            "SELECT ft.rel_path,t.id,t.name FROM folder_tags ft JOIN tags t ON t.id=ft.tag_id WHERE ft.resource_id=?", (rid,)
        )]
        file_tag_rows = [dict(x) for x in con.execute(
            "SELECT ft.rel_path,t.id,t.name FROM file_tags ft JOIN tags t ON t.id=ft.tag_id WHERE ft.resource_id=?", (rid,)
        )]
    folder_tags_map = {}
    for tr in folder_tag_rows:
        folder_tags_map.setdefault(tr["rel_path"], []).append({"id": tr["id"], "name": tr["name"]})
    file_tags_map = {}
    for tr in file_tag_rows:
        file_tags_map.setdefault(tr["rel_path"], []).append({"id": tr["id"], "name": tr["name"]})
    for p in entries:
        rel = str(p.relative_to(root)).replace("\\", "/")
        if p.is_dir():
            try:
                child_count = sum(1 for _ in p.iterdir())
            except OSError:
                child_count = 0
            items.append({"kind": "folder", "name": p.name, "path": rel, "child_count": child_count, "tags": folder_tags_map.get(rel, [])})
        elif p.is_file():
            try:
                size = p.stat().st_size
            except OSError:
                size = 0
            ext = p.suffix.lower().lstrip(".") or "sin_extension"
            items.append({
                "kind": "file", "name": p.name, "path": rel, "ext": ext, "size_bytes": size,
                "previewable": ext in PREVIEW_EXTS, "cataloged": rel in cataloged, "tags": file_tags_map.get(rel, []),
            })
    current_rel = str(current.relative_to(root)).replace("\\", "/") if current != root else ""
    parent_rel = ""
    if current != root:
        parent_rel = str(current.parent.relative_to(root)).replace("\\", "/") if current.parent != root else ""
    return {"resource": r, "current_path": current_rel, "parent_path": parent_rel, "items": items}


@app.get("/api/resources/{rid}/preview")
def resource_preview(rid: int, path: str):
    _, _, target = safe_resource_path(rid, path)
    if not target.exists() or not target.is_file():
        raise HTTPException(404, "Archivo no encontrado")
    ext = target.suffix.lower().lstrip(".")
    if ext not in PREVIEW_EXTS:
        raise HTTPException(415, "Este tipo de archivo no tiene vista previa")
    return FileResponse(target)


@app.get("/api/resources/{rid}/files/download")
def resource_file_download(rid: int, path: str):
    """Descarga directa por HTTP, para usar el programa desde otra PC/celular en la red."""
    _, _, target = safe_resource_path(rid, path)
    if not target.exists() or not target.is_file():
        raise HTTPException(404, "Archivo no encontrado")
    return FileResponse(target, filename=target.name)


def safe_library_path(rel_path: str) -> Path:
    root = library_root().resolve()
    clean = (rel_path or "").replace("\\", "/").strip("/")
    target = (root / clean).resolve() if clean else root
    if not _is_within(target, root):
        raise HTTPException(400, "Ruta no válida")
    return target


def library_relative_path(absolute: str | Path) -> str:
    return str(Path(absolute).resolve().relative_to(library_root().resolve())).replace("\\", "/")


def is_local_request(request: Request) -> bool:
    return bool(request.client) and request.client.host in ("127.0.0.1", "::1")


@app.get("/api/library/download")
def library_file_download(path: str):
    """Descarga por HTTP cualquier archivo dentro de la Biblioteca (ZIPs exportados, colecciones, etc.),
    para usar el programa desde otra PC/celular en la red."""
    target = safe_library_path(path)
    if not target.exists() or not target.is_file():
        raise HTTPException(404, "Archivo no encontrado")
    return FileResponse(target, filename=target.name)


@app.post("/api/resources/{rid}/folders")
def resource_create_folder(rid: int, payload: FolderCreateIn):
    name = slug_folder(payload.name)
    if not name or name == "Sin_nombre":
        raise HTTPException(400, "Escribe un nombre de carpeta")
    _, root, parent = safe_resource_path(rid, payload.parent_path)
    if not parent.exists() or not parent.is_dir():
        raise HTTPException(404, "La carpeta padre no existe")
    new_folder = parent / name
    if new_folder.exists():
        raise HTTPException(409, "Ya existe una carpeta con ese nombre")
    new_folder.mkdir(parents=False)
    index_resource(rid, root)
    return {"ok": True, "path": str(new_folder.relative_to(root)).replace("\\", "/")}


@app.post("/api/resources/{rid}/files/organize")
def resource_organize_files(rid: int, payload: FileOrganizeIn):
    if payload.operation not in {"copy", "move"}:
        raise HTTPException(400, "Operación no válida")
    if not payload.paths:
        raise HTTPException(400, "Selecciona al menos un archivo")
    _, root, target_dir = safe_resource_path(rid, payload.target_path)
    target_dir.mkdir(parents=True, exist_ok=True)
    done = []
    for rel in payload.paths:
        _, _, src = safe_resource_path(rid, rel)
        if not src.exists() or not src.is_file():
            continue
        dst = unique_destination(target_dir / src.name)
        new_rel = str(dst.relative_to(root)).replace("\\", "/")
        if payload.operation == "copy":
            shutil.copy2(src, dst)
            # Copia también las etiquetas del archivo original a la nueva copia.
            with db() as con:
                tag_rows = [x["tag_id"] for x in con.execute(
                    "SELECT tag_id FROM file_tags WHERE resource_id=? AND rel_path=?", (rid, rel)
                )]
                for tid in tag_rows:
                    con.execute(
                        "INSERT OR IGNORE INTO file_tags(resource_id,rel_path,tag_id) VALUES (?,?,?)",
                        (rid, new_rel, tid),
                    )
        else:
            shutil.move(str(src), str(dst))
            # Mantiene enlazados catálogo y etiquetas cuando se mueve el original.
            with db() as con:
                con.execute(
                    "UPDATE catalog_items SET source_rel_path=?, updated_at=? WHERE resource_id=? AND source_rel_path=?",
                    (new_rel, now_iso(), rid, rel),
                )
                con.execute(
                    "UPDATE collection_items SET source_rel_path=? WHERE resource_id=? AND source_rel_path=?",
                    (new_rel, rid, rel),
                )
                con.execute(
                    "UPDATE OR IGNORE file_tags SET rel_path=? WHERE resource_id=? AND rel_path=?",
                    (new_rel, rid, rel),
                )
                con.execute(
                    "DELETE FROM file_tags WHERE resource_id=? AND rel_path=?",
                    (rid, rel),
                )
        done.append({"source": rel, "destination": new_rel})
    count, total = index_resource(rid, root)
    return {"ok": True, "processed": len(done), "items": done, "file_count": count, "total_bytes": total}


@app.post("/api/resources/{rid}/rename")
def resource_rename(rid: int, payload: RenameIn):
    _, root, src = safe_resource_path(rid, payload.path)
    if not src.exists():
        raise HTTPException(404, "El elemento ya no existe")
    raw = payload.new_name.strip()
    if not raw:
        raise HTTPException(400, "Escribe un nuevo nombre")
    if any(ch in raw for ch in '<>:"/\\|?*'):
        raise HTTPException(400, "El nombre contiene caracteres no permitidos")
    if src.is_file() and not Path(raw).suffix and src.suffix:
        raw += src.suffix
    old_rel = str(src.relative_to(root)).replace("\\", "/")
    was_dir = src.is_dir()
    dst = src.with_name(raw)
    if dst.exists():
        raise HTTPException(409, "Ya existe un elemento con ese nombre")
    src.rename(dst)
    new_rel = str(dst.relative_to(root)).replace("\\", "/")
    # Actualiza metadatos que apuntaban a la ruta anterior.
    with db() as con:
        if was_dir:
            ft_rows = [dict(x) for x in con.execute("SELECT rel_path,tag_id FROM folder_tags WHERE resource_id=?", (rid,))]
            for row in ft_rows:
                rp = row["rel_path"]
                if rp == old_rel or rp.startswith(old_rel + "/"):
                    changed = new_rel + rp[len(old_rel):]
                    con.execute("DELETE FROM folder_tags WHERE resource_id=? AND rel_path=? AND tag_id=?", (rid, rp, row["tag_id"]))
                    con.execute("INSERT OR IGNORE INTO folder_tags(resource_id,rel_path,tag_id) VALUES (?,?,?)", (rid, changed, row["tag_id"]))
            ci_rows = [dict(x) for x in con.execute("SELECT id,source_rel_path FROM catalog_items WHERE resource_id=? AND source_rel_path IS NOT NULL", (rid,))]
            for row in ci_rows:
                rp = row["source_rel_path"]
                if rp == old_rel or rp.startswith(old_rel + "/"):
                    changed = new_rel + rp[len(old_rel):]
                    con.execute("UPDATE catalog_items SET source_rel_path=?,updated_at=? WHERE id=?", (changed,now_iso(),row["id"]))
            col_rows = [dict(x) for x in con.execute("SELECT id,source_rel_path FROM collection_items WHERE resource_id=? AND source_rel_path IS NOT NULL", (rid,))]
            for row in col_rows:
                rp = row["source_rel_path"]
                if rp == old_rel or rp.startswith(old_rel + "/"):
                    changed = new_rel + rp[len(old_rel):]
                    con.execute("UPDATE collection_items SET source_rel_path=? WHERE id=?", (changed,row["id"]))
            ftag_rows = [dict(x) for x in con.execute("SELECT rel_path,tag_id FROM file_tags WHERE resource_id=?", (rid,))]
            for row in ftag_rows:
                rp = row["rel_path"]
                if rp == old_rel or rp.startswith(old_rel + "/"):
                    changed = new_rel + rp[len(old_rel):]
                    con.execute("DELETE FROM file_tags WHERE resource_id=? AND rel_path=? AND tag_id=?", (rid,rp,row["tag_id"]))
                    con.execute("INSERT OR IGNORE INTO file_tags(resource_id,rel_path,tag_id) VALUES (?,?,?)", (rid,changed,row["tag_id"]))
        else:
            con.execute("UPDATE catalog_items SET source_rel_path=?,updated_at=? WHERE resource_id=? AND source_rel_path=?", (new_rel,now_iso(),rid,old_rel))
            con.execute("UPDATE collection_items SET source_rel_path=? WHERE resource_id=? AND source_rel_path=?", (new_rel,rid,old_rel))
            tag_rows = [x["tag_id"] for x in con.execute("SELECT tag_id FROM file_tags WHERE resource_id=? AND rel_path=?", (rid, old_rel))]
            con.execute("DELETE FROM file_tags WHERE resource_id=? AND rel_path=?", (rid, old_rel))
            for tid in tag_rows:
                con.execute("INSERT OR IGNORE INTO file_tags(resource_id,rel_path,tag_id) VALUES (?,?,?)", (rid,new_rel,tid))
    index_resource(rid, root)
    return {"ok": True, "path": new_rel}



@app.post("/api/resources/{rid}/files/upload")
async def resource_upload_files(
    rid: int,
    target_path: str = Form(""),
    rel_paths_json: str = Form("[]"),
    files: list[UploadFile] = File(...),
):
    """Importa archivos usando el selector estándar del navegador/Windows.

    No depende del puente pywebview, por lo que funciona tanto en la aplicación
    instalada como en la interfaz local. Con ``webkitRelativePath`` conserva la
    estructura al importar una carpeta completa.
    """
    if not files:
        raise HTTPException(400, "Selecciona al menos un archivo")
    _, root, target_dir = safe_resource_path(rid, target_path)
    if not target_dir.exists() or not target_dir.is_dir():
        raise HTTPException(404, "La carpeta de destino no existe")
    try:
        rel_paths = json.loads(rel_paths_json or "[]")
        if not isinstance(rel_paths, list):
            rel_paths = []
    except Exception:
        rel_paths = []

    copied: list[str] = []
    skipped = 0
    for idx, upload in enumerate(files):
        fallback = Path(upload.filename or f"archivo_{idx+1}").name
        raw_rel = rel_paths[idx] if idx < len(rel_paths) else fallback
        relative = safe_upload_relative_path(raw_rel, fallback)
        destination = target_dir / relative
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination = unique_destination(destination)
            with destination.open("wb") as out:
                while True:
                    chunk = await upload.read(1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
            copied.append(canonical_rel_path(str(destination.relative_to(root))))
        except Exception:
            skipped += 1
        finally:
            try:
                await upload.close()
            except Exception:
                pass

    count, total = index_resource(rid, root)
    try:
        sync_resource_to_selected_collection(rid)
    except Exception:
        pass
    return {
        "ok": True, "copied": len(copied), "skipped": skipped, "items": copied,
        "file_count": count, "total_bytes": total, "target_path": canonical_rel_path(target_path),
    }


@app.post("/api/resources/{rid}/files/import")
def resource_import_files(rid: int, payload: ImportFilesIn):
    if not payload.paths:
        raise HTTPException(400, "Selecciona al menos un archivo")
    _, root, target_dir = safe_resource_path(rid, payload.target_path)
    if not target_dir.exists() or not target_dir.is_dir():
        raise HTTPException(404, "La carpeta de destino no existe")
    copied = []
    skipped = 0
    for raw in payload.paths:
        src = Path(raw).expanduser()
        if not src.exists() or not src.is_file():
            skipped += 1
            continue
        try:
            dst = unique_destination(target_dir / src.name)
            shutil.copy2(src, dst)
            copied.append(str(dst.relative_to(root)).replace("\\", "/"))
        except OSError:
            skipped += 1
    count, total = index_resource(rid, root)
    try:
        sync_resource_to_selected_collection(rid)
    except Exception:
        pass
    return {"ok": True, "copied": len(copied), "skipped": skipped, "items": copied, "file_count": count, "total_bytes": total}


@app.post("/api/resources/{rid}/items/delete")
def resource_delete_items(rid: int, payload: DeleteItemsIn):
    if not payload.paths:
        raise HTTPException(400, "Selecciona al menos un elemento")
    _, root = resource_root_path(rid)
    deleted = 0
    normalized = []
    for rel in payload.paths:
        _, _, target = safe_resource_path(rid, rel)
        if target == root:
            continue
        if not target.exists():
            continue
        rel_norm = str(target.relative_to(root)).replace("\\", "/")
        normalized.append(rel_norm)
        try:
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
            deleted += 1
        except OSError:
            continue
    if normalized:
        with db() as con:
            for rel in normalized:
                con.execute("DELETE FROM file_tags WHERE resource_id=? AND (rel_path=? OR rel_path LIKE ?)", (rid, rel, rel + "/%"))
                con.execute("DELETE FROM folder_tags WHERE resource_id=? AND (rel_path=? OR rel_path LIKE ?)", (rid, rel, rel + "/%"))
    count, total = index_resource(rid, root)
    return {"ok": True, "deleted": deleted, "file_count": count, "total_bytes": total}

@app.post("/api/resources/{rid}/files/zip")
def resource_zip_selection(rid: int, payload: SelectionZipIn):
    if not payload.paths:
        raise HTTPException(400, "Selecciona al menos un archivo")
    _, root = resource_root_path(rid)
    out_dir = library_root() / "Exportaciones"
    out_dir.mkdir(parents=True, exist_ok=True)
    name = slug_folder(payload.name) or "Seleccion_Sorprezz"
    out = unique_destination(out_dir / f"{name}.zip")
    added = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in payload.paths:
            _, _, src = safe_resource_path(rid, rel)
            if src.exists() and src.is_file():
                zf.write(src, arcname=src.name)
                added += 1
    if added == 0:
        try:
            out.unlink()
        except OSError:
            pass
        raise HTTPException(400, "No se encontraron archivos válidos para comprimir")
    return {"ok": True, "path": str(out), "download_path": library_relative_path(out), "files": added}


@app.post("/api/resources/{rid}/file/open")
def resource_open_file(rid: int, payload: dict):
    rel = str(payload.get("path", ""))
    _, _, target = safe_resource_path(rid, rel)
    if not target.exists():
        raise HTTPException(404, "Archivo no encontrado")
    try:
        open_os_path(target)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/api/resources/{rid}/folder/open")
def resource_open_subfolder(rid: int, payload: dict):
    rel = str(payload.get("path", ""))
    _, _, target = safe_resource_path(rid, rel)
    if not target.exists() or not target.is_dir():
        raise HTTPException(404, "Carpeta no encontrada")
    try:
        open_os_path(target)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/tags")
def tags_list():
    """Lista etiquetas con conteos reales y una pequeña vista previa de imágenes."""
    with db() as con:
        rows = [dict(x) for x in con.execute(
            """SELECT t.id,t.name,t.created_at,
                      (SELECT COUNT(*) FROM resource_tags rt WHERE rt.tag_id=t.id) AS resources,
                      (SELECT COUNT(*) FROM folder_tags ft WHERE ft.tag_id=t.id) AS folders,
                      (SELECT COUNT(*) FROM file_tags fit
                         JOIN files f ON f.resource_id=fit.resource_id AND f.rel_path=fit.rel_path
                       WHERE fit.tag_id=t.id) AS files
               FROM tags t ORDER BY t.name COLLATE NOCASE"""
        )]
        image_exts = tuple(sorted(PREVIEW_EXTS))
        placeholders = ",".join("?" for _ in image_exts)
        for row in rows:
            sample_sql = f"""
                SELECT f.resource_id,f.rel_path,r.name AS resource_name
                FROM file_tags ft
                JOIN files f ON f.resource_id=ft.resource_id AND f.rel_path=ft.rel_path
                JOIN resources r ON r.id=f.resource_id
                WHERE ft.tag_id=? AND LOWER(f.ext) IN ({placeholders})
                ORDER BY r.name COLLATE NOCASE,f.rel_path COLLATE NOCASE
                LIMIT 4
            """
            samples = [dict(x) for x in con.execute(sample_sql, (row["id"], *image_exts))]
            row["samples"] = samples
    return rows


@app.post("/api/tags")
def tag_create(payload: TagIn):
    name = payload.name.strip()
    if not name:
        raise HTTPException(400, "Escribe el nombre de la etiqueta")
    try:
        with db() as con:
            cur = con.execute("INSERT INTO tags(name,created_at) VALUES (?,?)", (name, now_iso()))
            return {"id": cur.lastrowid, "name": name}
    except sqlite3.IntegrityError:
        raise HTTPException(409, "La etiqueta ya existe")


@app.delete("/api/tags/{tag_id}")
def tag_delete(tag_id: int):
    with db() as con:
        exists = con.execute("SELECT id FROM tags WHERE id=?", (tag_id,)).fetchone()
        if not exists:
            raise HTTPException(404, "Etiqueta no encontrada")
        con.execute("DELETE FROM resource_tags WHERE tag_id=?", (tag_id,))
        con.execute("DELETE FROM folder_tags WHERE tag_id=?", (tag_id,))
        con.execute("DELETE FROM file_tags WHERE tag_id=?", (tag_id,))
        con.execute("DELETE FROM tags WHERE id=?", (tag_id,))
    return {"ok": True}


@app.get("/api/resources/{rid}/tags")
def resource_tags_get(rid: int):
    if not get_resource(rid):
        raise HTTPException(404, "Recurso no encontrado")
    with db() as con:
        return [dict(x) for x in con.execute("SELECT t.id,t.name FROM tags t JOIN resource_tags rt ON rt.tag_id=t.id WHERE rt.resource_id=? ORDER BY t.name COLLATE NOCASE", (rid,))]


@app.put("/api/resources/{rid}/tags")
def resource_tags_set(rid: int, payload: TagAssignIn):
    if not get_resource(rid):
        raise HTTPException(404, "Recurso no encontrado")
    tag_ids = sorted(set(int(x) for x in payload.tag_ids))
    with db() as con:
        con.execute("DELETE FROM resource_tags WHERE resource_id=?", (rid,))
        for tid in tag_ids:
            if con.execute("SELECT id FROM tags WHERE id=?", (tid,)).fetchone():
                con.execute("INSERT OR IGNORE INTO resource_tags(resource_id,tag_id) VALUES (?,?)", (rid, tid))
    return {"ok": True}


@app.get("/api/resources/{rid}/folder-tags")
def folder_tags_get(rid: int, path: str = ""):
    _, root, target = safe_resource_path(rid, path)
    if not target.exists() or not target.is_dir():
        raise HTTPException(404, "Carpeta no encontrada")
    rel = str(target.relative_to(root)).replace("\\", "/") if target != root else ""
    with db() as con:
        return [dict(x) for x in con.execute("SELECT t.id,t.name FROM tags t JOIN folder_tags ft ON ft.tag_id=t.id WHERE ft.resource_id=? AND ft.rel_path=? ORDER BY t.name COLLATE NOCASE", (rid, rel))]


@app.put("/api/resources/{rid}/folder-tags")
def folder_tags_set(rid: int, payload: FolderTagAssignIn):
    _, root, target = safe_resource_path(rid, payload.path)
    if not target.exists() or not target.is_dir():
        raise HTTPException(404, "Carpeta no encontrada")
    rel = str(target.relative_to(root)).replace("\\", "/") if target != root else ""
    tag_ids = sorted(set(int(x) for x in payload.tag_ids))
    with db() as con:
        con.execute("DELETE FROM folder_tags WHERE resource_id=? AND rel_path=?", (rid, rel))
        for tid in tag_ids:
            if con.execute("SELECT id FROM tags WHERE id=?", (tid,)).fetchone():
                con.execute("INSERT OR IGNORE INTO folder_tags(resource_id,rel_path,tag_id) VALUES (?,?,?)", (rid, rel, tid))
    return {"ok": True, "path": rel}




@app.get("/api/resources/{rid}/file-tags")
def file_tags_get(rid: int, path: str):
    _, _, target = safe_resource_path(rid, path)
    if not target.exists() or not target.is_file():
        raise HTTPException(404, "Archivo no encontrado")
    with db() as con:
        return [dict(x) for x in con.execute(
            "SELECT t.id,t.name FROM tags t JOIN file_tags ft ON ft.tag_id=t.id WHERE ft.resource_id=? AND ft.rel_path=? ORDER BY t.name COLLATE NOCASE",
            (rid, path),
        )]


@app.put("/api/resources/{rid}/file-tags")
def file_tags_set(rid: int, payload: FileTagAssignIn):
    _, _, target = safe_resource_path(rid, payload.path)
    if not target.exists() or not target.is_file():
        raise HTTPException(404, "Archivo no encontrado")
    tag_ids = sorted(set(int(x) for x in payload.tag_ids))
    with db() as con:
        con.execute("DELETE FROM file_tags WHERE resource_id=? AND rel_path=?", (rid, payload.path))
        for tid in tag_ids:
            if con.execute("SELECT id FROM tags WHERE id=?", (tid,)).fetchone():
                con.execute("INSERT OR IGNORE INTO file_tags(resource_id,rel_path,tag_id) VALUES (?,?,?)", (rid, payload.path, tid))
    return {"ok": True, "path": payload.path}


@app.put("/api/resources/{rid}/file-tags/bulk")
def file_tags_bulk(rid: int, payload: FileTagBulkIn):
    if payload.mode not in {"add", "remove", "replace"}:
        raise HTTPException(400, "Modo de etiquetas no válido")
    paths = list(dict.fromkeys(x for x in payload.paths if str(x).strip()))
    tag_ids = sorted(set(int(x) for x in payload.tag_ids))
    if not paths:
        raise HTTPException(400, "Selecciona al menos un archivo")
    valid_paths = []
    for rel in paths:
        try:
            _, _, target = safe_resource_path(rid, rel)
        except HTTPException:
            continue
        if target.exists() and target.is_file():
            valid_paths.append(rel)
    if not valid_paths:
        raise HTTPException(400, "No hay archivos válidos en la selección")
    with db() as con:
        valid_tag_ids = [tid for tid in tag_ids if con.execute("SELECT id FROM tags WHERE id=?", (tid,)).fetchone()]
        for rel in valid_paths:
            if payload.mode == "replace":
                con.execute("DELETE FROM file_tags WHERE resource_id=? AND rel_path=?", (rid, rel))
            if payload.mode in {"add", "replace"}:
                for tid in valid_tag_ids:
                    con.execute("INSERT OR IGNORE INTO file_tags(resource_id,rel_path,tag_id) VALUES (?,?,?)", (rid, rel, tid))
            elif payload.mode == "remove":
                for tid in valid_tag_ids:
                    con.execute("DELETE FROM file_tags WHERE resource_id=? AND rel_path=? AND tag_id=?", (rid, rel, tid))
    return {"ok": True, "files": len(valid_paths), "tags": len(tag_ids), "mode": payload.mode}


@app.post("/api/resources/{rid}/tags/apply-to-files")
def tag_apply_to_files(rid: int, payload: TagApplyFilesIn):
    r = get_resource(rid)
    if not r:
        raise HTTPException(404, "Recurso no encontrado")
    prefix = canonical_rel_path(payload.path)
    tag_ids = sorted(set(int(x) for x in payload.tag_ids))
    if payload.mode not in {"add", "remove"}:
        raise HTTPException(400, "Modo no válido")
    with db() as con:
        valid_tag_ids = [tid for tid in tag_ids if con.execute('SELECT id FROM tags WHERE id=?', (tid,)).fetchone()]
        sql = 'SELECT rel_path,ext FROM files WHERE resource_id=?'
        rows = [dict(x) for x in con.execute(sql, (rid,))]
        paths = []
        for row in rows:
            rel = canonical_rel_path(row['rel_path'])
            if prefix and not (rel == prefix or rel.startswith(prefix + '/')):
                continue
            if payload.images_only and str(row.get('ext') or '').lower() not in PREVIEW_EXTS:
                continue
            paths.append(rel)
        for rel in paths:
            for tid in valid_tag_ids:
                if payload.mode == 'add':
                    con.execute('INSERT OR IGNORE INTO file_tags(resource_id,rel_path,tag_id) VALUES (?,?,?)', (rid,rel,tid))
                else:
                    con.execute('DELETE FROM file_tags WHERE resource_id=? AND rel_path=? AND tag_id=?', (rid,rel,tid))
    return {"ok": True, "files": len(paths), "tags": len(valid_tag_ids), "mode": payload.mode}


@app.post("/api/resources/{rid}/files/export")
def resource_export_selection(rid: int, payload: SelectionExportIn):
    if not payload.paths:
        raise HTTPException(400, "Selecciona al menos un archivo")
    destination = Path(payload.destination_dir).expanduser().resolve()
    try:
        destination.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        raise HTTPException(400, f"No se puede usar la carpeta de destino: {e}")
    if payload.folder_name.strip():
        destination = destination / slug_folder(payload.folder_name)
        destination.mkdir(parents=True, exist_ok=True)
    copied = []
    for rel in payload.paths:
        _, _, src = safe_resource_path(rid, rel)
        if not src.exists() or not src.is_file():
            continue
        dst = unique_destination(destination / src.name)
        try:
            if src.resolve() == dst.resolve():
                continue
        except Exception:
            pass
        shutil.copy2(src, dst)
        copied.append(str(dst))
    if not copied:
        raise HTTPException(400, "No se copiaron archivos")
    return {"ok": True, "files": len(copied), "destination": str(destination), "items": copied}


@app.get("/api/assets")
def global_assets(q: str = "", tag_id: Optional[int] = None, category_id: Optional[int] = None,
                  resource_id: Optional[int] = None, images_only: bool = True, limit: int = 500):
    limit = max(1, min(int(limit or 500), 1500))
    sql = """
        SELECT f.resource_id,f.rel_path,f.ext,f.size_bytes,r.name AS resource_name,
               c.id AS category_id,c.name AS category_name,s.name AS subcategory_name
        FROM files f
        JOIN resources r ON r.id=f.resource_id
        LEFT JOIN categories c ON c.id=r.category_id
        LEFT JOIN subcategories s ON s.id=r.subcategory_id
        WHERE r.local_path IS NOT NULL AND r.local_path<>''
    """
    params = []
    if tag_id:
        sql += " AND EXISTS (SELECT 1 FROM file_tags ft WHERE ft.resource_id=f.resource_id AND ft.rel_path=f.rel_path AND ft.tag_id=?)"
        params.append(tag_id)
    if category_id:
        sql += " AND r.category_id=?"
        params.append(category_id)
    if resource_id:
        sql += " AND r.id=?"
        params.append(resource_id)
    if images_only:
        placeholders = ",".join("?" for _ in PREVIEW_EXTS)
        sql += f" AND LOWER(f.ext) IN ({placeholders})"
        params.extend(sorted(PREVIEW_EXTS))
    sql += " ORDER BY r.name COLLATE NOCASE,f.rel_path COLLATE NOCASE LIMIT ?"
    params.append(limit)
    with db() as con:
        rows = [dict(x) for x in con.execute(sql, params)]
        tag_rows = [dict(x) for x in con.execute(
            "SELECT ft.resource_id,ft.rel_path,t.id,t.name FROM file_tags ft JOIN tags t ON t.id=ft.tag_id ORDER BY t.name COLLATE NOCASE"
        )]
    tag_map = {}
    for tr in tag_rows:
        tag_map.setdefault((tr["resource_id"], tr["rel_path"]), []).append({"id": tr["id"], "name": tr["name"]})
    query = normalize_search_text(q)
    terms = [x for x in query.split(" ") if x]
    result = []
    for row in rows:
        row["tags"] = tag_map.get((row["resource_id"], row["rel_path"]), [])
        if terms:
            searchable = " ".join([
                row.get("rel_path") or "", row.get("resource_name") or "", row.get("category_name") or "",
                row.get("subcategory_name") or "", " ".join(t["name"] for t in row["tags"]),
            ])
            haystack = normalize_search_text(searchable)
            if not all(term in haystack for term in terms):
                continue
        row["name"] = Path(row["rel_path"]).name
        row["previewable"] = str(row.get("ext") or "").lower() in PREVIEW_EXTS
        result.append(row)
    return result


def unique_directory(path: Path) -> Path:
    if not path.exists():
        return path
    for i in range(2, 10000):
        candidate = path.with_name(f"{path.name} ({i})")
        if not candidate.exists():
            return candidate
    raise RuntimeError("No se pudo generar una carpeta disponible")


def create_collection_record(name: str, category_id: int, subcategory_id: Optional[int] = None, description: str = "") -> dict:
    name = name.strip()
    if not name:
        raise HTTPException(400, "Escribe un nombre para la colección")
    with db() as con:
        cat = con.execute("SELECT id,name FROM categories WHERE id=?", (category_id,)).fetchone()
        if not cat:
            raise HTTPException(400, "Selecciona una categoría global válida")
        sub = None
        if subcategory_id:
            sub = con.execute(
                "SELECT id,name FROM subcategories WHERE id=? AND category_id=?",
                (subcategory_id, category_id),
            ).fetchone()
            if not sub:
                raise HTTPException(400, "La subcategoría no pertenece a la categoría seleccionada")
        category_name = cat["name"]
        subcategory_name = sub["name"] if sub else ""
        duplicate = con.execute(
            "SELECT id FROM collections WHERE category_id=? AND COALESCE(subcategory_id,0)=? AND name=? COLLATE NOCASE",
            (category_id, subcategory_id or 0, name),
        ).fetchone()
        if duplicate:
            raise HTTPException(409, "Ya existe una colección con ese nombre dentro de esa clasificación")

    base = library_root() / "Colecciones_Web" / slug_folder(category_name)
    if subcategory_name:
        base = base / slug_folder(subcategory_name)
    base.mkdir(parents=True, exist_ok=True)
    physical = unique_directory(base / slug_folder(name))
    physical.mkdir(parents=True, exist_ok=False)
    with db() as con:
        cur = con.execute(
            "INSERT INTO collections(name,category,category_id,subcategory_id,description,physical_path,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (name, category_name, category_id, subcategory_id, description.strip(), str(physical), now_iso(), now_iso()),
        )
        cid = cur.lastrowid
    return {
        "id": cid, "name": name, "category": category_name, "category_id": category_id,
        "subcategory": subcategory_name, "subcategory_id": subcategory_id,
        "description": description.strip(), "physical_path": str(physical),
    }

def add_assets_to_collection(collection_id: int, items: list[dict]) -> dict:
    with db() as con:
        col = con.execute("SELECT * FROM collections WHERE id=?", (collection_id,)).fetchone()
    if not col:
        raise HTTPException(404, "Colección no encontrada")
    dest_dir = Path(col["physical_path"])
    dest_dir.mkdir(parents=True, exist_ok=True)
    added = []
    skipped = 0
    for ref in items:
        rid = int(ref.get("resource_id") or 0)
        rel = str(ref.get("path") or "")
        if not rid or not rel:
            skipped += 1
            continue
        try:
            _, _, src = safe_resource_path(rid, rel)
        except HTTPException:
            skipped += 1
            continue
        if not src.exists() or not src.is_file():
            skipped += 1
            continue
        with db() as con:
            existing = con.execute(
                "SELECT id FROM collection_items WHERE collection_id=? AND resource_id=? AND source_rel_path=?",
                (collection_id, rid, rel),
            ).fetchone()
        if existing:
            skipped += 1
            continue
        dst = unique_destination(dest_dir / src.name)
        shutil.copy2(src, dst)
        with db() as con:
            cur = con.execute(
                "INSERT INTO collection_items(collection_id,resource_id,source_rel_path,copied_path,title,created_at) VALUES (?,?,?,?,?,?)",
                (collection_id, rid, rel, str(dst), src.stem, now_iso()),
            )
            item_id = cur.lastrowid
        added.append({"id": item_id, "resource_id": rid, "source_rel_path": rel, "copied_path": str(dst)})
    with db() as con:
        con.execute("UPDATE collections SET updated_at=? WHERE id=?", (now_iso(), collection_id))
    return {"ok": True, "added": len(added), "skipped": skipped, "items": added}


@app.get("/api/collections")
def collections_list(q: str = ""):
    with db() as con:
        rows = [dict(x) for x in con.execute(
            """SELECT c.*,
                      cat.name AS category_name, sub.name AS subcategory_name,
                      (SELECT COUNT(*) FROM collection_items ci WHERE ci.collection_id=c.id) AS item_count
               FROM collections c
               LEFT JOIN categories cat ON cat.id=c.category_id
               LEFT JOIN subcategories sub ON sub.id=c.subcategory_id
               ORDER BY c.updated_at DESC,c.name COLLATE NOCASE"""
        )]
    query = normalize_search_text(q)
    if query:
        rows = [r for r in rows if query in normalize_search_text(
            f"{r['name']} {r.get('category_name') or r.get('category') or ''} {r.get('subcategory_name') or ''} {r.get('description') or ''}"
        )]
    for r in rows:
        r["category"] = r.get("category_name") or r.get("category") or "General"
        r["subcategory"] = r.get("subcategory_name") or ""
        total = 0
        p = Path(r["physical_path"])
        if p.exists():
            for f in p.iterdir():
                if f.is_file():
                    try: total += f.stat().st_size
                    except OSError: pass
        r["total_bytes"] = total
    return rows


@app.post("/api/collections")
def collection_create(payload: CollectionIn):
    return create_collection_record(payload.name, payload.category_id, payload.subcategory_id, payload.description)


@app.get("/api/collections/{cid}")
def collection_detail(cid: int):
    with db() as con:
        c = con.execute(
            """SELECT c.*,cat.name AS category_name,sub.name AS subcategory_name
               FROM collections c
               LEFT JOIN categories cat ON cat.id=c.category_id
               LEFT JOIN subcategories sub ON sub.id=c.subcategory_id
               WHERE c.id=?""", (cid,)
        ).fetchone()
        if not c:
            raise HTTPException(404, "Colección no encontrada")
        items = [dict(x) for x in con.execute(
            """SELECT ci.*,r.name AS resource_name,c.name AS source_category,s.name AS source_subcategory
               FROM collection_items ci
               LEFT JOIN resources r ON r.id=ci.resource_id
               LEFT JOIN categories c ON c.id=r.category_id
               LEFT JOIN subcategories s ON s.id=r.subcategory_id
               WHERE ci.collection_id=? ORDER BY ci.id DESC""", (cid,)
        )]
    result = dict(c)
    result["category"] = result.get("category_name") or result.get("category") or "General"
    result["subcategory"] = result.get("subcategory_name") or ""
    for item in items:
        p = Path(item["copied_path"])
        item["exists"] = p.exists()
        item["name"] = p.name if p.name else (item.get("title") or "Archivo")
        item["ext"] = p.suffix.lower().lstrip(".")
        item["previewable"] = item["ext"] in PREVIEW_EXTS
        try: item["size_bytes"] = p.stat().st_size if p.exists() else 0
        except OSError: item["size_bytes"] = 0
    result["items"] = items
    return result


@app.post("/api/collections/{cid}/items")
def collection_add_items(cid: int, payload: CollectionAddItemsIn):
    refs = [{"resource_id": x.resource_id, "path": x.path} for x in payload.items]
    return add_assets_to_collection(cid, refs)


@app.post("/api/collections/from-tag")
def collection_create_from_tag(payload: CollectionFromTagIn):
    with db() as con:
        tag = con.execute("SELECT id,name FROM tags WHERE id=?", (payload.tag_id,)).fetchone()
        if not tag:
            raise HTTPException(404, "Etiqueta no encontrada")
        refs = [dict(x) for x in con.execute(
            """SELECT ft.resource_id,ft.rel_path AS path
               FROM file_tags ft JOIN files f ON f.resource_id=ft.resource_id AND f.rel_path=ft.rel_path
               WHERE ft.tag_id=? ORDER BY ft.resource_id,ft.rel_path""", (payload.tag_id,)
        )]
    if not refs:
        raise HTTPException(400, "No hay archivos etiquetados con esa etiqueta")
    col = create_collection_record(payload.name, payload.category_id, payload.subcategory_id, payload.description)
    result = add_assets_to_collection(col["id"], refs)
    if result["added"] == 0:
        with db() as con:
            con.execute("DELETE FROM collections WHERE id=?", (col["id"],))
        shutil.rmtree(Path(col["physical_path"]), ignore_errors=True)
        raise HTTPException(400, "Los archivos etiquetados ya no están disponibles en la biblioteca")
    return {**col, **result, "tag": tag["name"]}


@app.get("/api/collections/{cid}/items/{item_id}/preview")
def collection_item_preview(cid: int, item_id: int):
    with db() as con:
        row = con.execute("SELECT copied_path FROM collection_items WHERE id=? AND collection_id=?", (item_id, cid)).fetchone()
    if not row:
        raise HTTPException(404, "Elemento no encontrado")
    p = Path(row["copied_path"])
    if not p.exists() or p.suffix.lower().lstrip(".") not in PREVIEW_EXTS:
        raise HTTPException(404, "Vista previa no disponible")
    return FileResponse(p)


@app.post("/api/collections/{cid}/open")
def collection_open(cid: int):
    with db() as con:
        row = con.execute("SELECT physical_path FROM collections WHERE id=?", (cid,)).fetchone()
    if not row:
        raise HTTPException(404, "Colección no encontrada")
    p = Path(row["physical_path"])
    if not p.exists():
        raise HTTPException(404, "La carpeta física de la colección no existe")
    try:
        open_os_path(p)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/api/collections/{cid}/zip")
def collection_zip(cid: int, request: Request):
    with db() as con:
        c = con.execute("SELECT * FROM collections WHERE id=?", (cid,)).fetchone()
    if not c:
        raise HTTPException(404, "Colección no encontrada")
    src = Path(c["physical_path"])
    if not src.exists():
        raise HTTPException(404, "La carpeta física de la colección no existe")
    out_dir = library_root() / "Exportaciones"
    out_dir.mkdir(parents=True, exist_ok=True)
    base = out_dir / f"{slug_folder(c['category'])}_{slug_folder(c['name'])}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    archive = shutil.make_archive(str(base), "zip", root_dir=str(src))
    if is_local_request(request):
        try:
            open_os_path(out_dir)
        except Exception:
            pass
    return {"ok": True, "path": archive, "download_path": library_relative_path(archive)}


@app.delete("/api/collections/{cid}/items/{item_id}")
def collection_remove_item(cid: int, item_id: int, delete_copy: bool = True):
    with db() as con:
        row = con.execute("SELECT copied_path FROM collection_items WHERE id=? AND collection_id=?", (item_id, cid)).fetchone()
        if not row:
            raise HTTPException(404, "Elemento no encontrado")
        con.execute("DELETE FROM collection_items WHERE id=?", (item_id,))
        con.execute("UPDATE collections SET updated_at=? WHERE id=?", (now_iso(), cid))
    if delete_copy:
        p = Path(row["copied_path"])
        if p.exists() and _is_within(p, library_root() / "Colecciones_Web"):
            try: p.unlink()
            except OSError: pass
    return {"ok": True}


@app.delete("/api/collections/{cid}")
def collection_delete(cid: int, delete_files: bool = True):
    with db() as con:
        row = con.execute("SELECT physical_path FROM collections WHERE id=?", (cid,)).fetchone()
        if not row:
            raise HTTPException(404, "Colección no encontrada")
        con.execute("DELETE FROM collection_items WHERE collection_id=?", (cid,))
        con.execute("DELETE FROM collections WHERE id=?", (cid,))
    if delete_files:
        p = Path(row["physical_path"])
        if p.exists() and _is_within(p, library_root() / "Colecciones_Web"):
            shutil.rmtree(p, ignore_errors=True)
    return {"ok": True}


def normalize_template_folder_path(raw: str) -> str:
    raw = raw.strip().replace("\\", "/").strip("/")
    if not raw:
        return ""
    parts = []
    for part in raw.split("/"):
        part = part.strip()
        if not part or part in {".", ".."}:
            continue
        clean = slug_folder(part)
        if clean and clean != "Sin_nombre":
            parts.append(clean)
    return "/".join(parts)


@app.get("/api/folder-templates")
def folder_templates_list():
    with db() as con:
        templates = [dict(x) for x in con.execute("SELECT * FROM folder_templates ORDER BY name COLLATE NOCASE")]
        for t in templates:
            t["folders"] = [x["folder_path"] for x in con.execute("SELECT folder_path FROM folder_template_items WHERE template_id=? ORDER BY sort_order,id", (t["id"],))]
    return templates


@app.post("/api/folder-templates")
def folder_template_create(payload: FolderTemplateIn):
    name = payload.name.strip()
    if not name:
        raise HTTPException(400, "Escribe un nombre para la lista")
    folders = []
    seen = set()
    for raw in payload.folders:
        f = normalize_template_folder_path(raw)
        if f and f.lower() not in seen:
            seen.add(f.lower())
            folders.append(f)
    if not folders:
        raise HTTPException(400, "Agrega al menos una carpeta a la lista")
    try:
        with db() as con:
            cur = con.execute("INSERT INTO folder_templates(name,description,created_at,updated_at) VALUES (?,?,?,?)", (name, payload.description.strip(), now_iso(), now_iso()))
            tid = cur.lastrowid
            con.executemany("INSERT INTO folder_template_items(template_id,folder_path,sort_order) VALUES (?,?,?)", [(tid, f, i) for i,f in enumerate(folders)])
        return {"ok": True, "id": tid}
    except sqlite3.IntegrityError:
        raise HTTPException(409, "Ya existe una lista con ese nombre")


@app.put("/api/folder-templates/{template_id}")
def folder_template_update(template_id: int, payload: FolderTemplateIn):
    name = payload.name.strip()
    folders = []
    seen = set()
    for raw in payload.folders:
        f = normalize_template_folder_path(raw)
        if f and f.lower() not in seen:
            seen.add(f.lower()); folders.append(f)
    if not name or not folders:
        raise HTTPException(400, "La lista necesita nombre y al menos una carpeta")
    try:
        with db() as con:
            if not con.execute("SELECT id FROM folder_templates WHERE id=?", (template_id,)).fetchone():
                raise HTTPException(404, "Lista no encontrada")
            con.execute("UPDATE folder_templates SET name=?,description=?,updated_at=? WHERE id=?", (name,payload.description.strip(),now_iso(),template_id))
            con.execute("DELETE FROM folder_template_items WHERE template_id=?", (template_id,))
            con.executemany("INSERT INTO folder_template_items(template_id,folder_path,sort_order) VALUES (?,?,?)", [(template_id,f,i) for i,f in enumerate(folders)])
        return {"ok": True}
    except sqlite3.IntegrityError:
        raise HTTPException(409, "Ya existe otra lista con ese nombre")


@app.delete("/api/folder-templates/{template_id}")
def folder_template_delete(template_id: int):
    with db() as con:
        con.execute("DELETE FROM folder_template_items WHERE template_id=?", (template_id,))
        cur = con.execute("DELETE FROM folder_templates WHERE id=?", (template_id,))
    if cur.rowcount == 0:
        raise HTTPException(404, "Lista no encontrada")
    return {"ok": True}


@app.post("/api/resources/{rid}/folder-templates/{template_id}/apply")
def folder_template_apply(rid: int, template_id: int, payload: FolderTemplateApplyIn):
    _, root, parent = safe_resource_path(rid, payload.parent_path)
    if not parent.exists() or not parent.is_dir():
        raise HTTPException(404, "La carpeta de destino no existe")
    with db() as con:
        t = con.execute("SELECT * FROM folder_templates WHERE id=?", (template_id,)).fetchone()
        if not t:
            raise HTTPException(404, "Lista de carpetas no encontrada")
        folders = [x["folder_path"] for x in con.execute("SELECT folder_path FROM folder_template_items WHERE template_id=? ORDER BY sort_order,id", (template_id,))]
    created=[]; existing=[]
    for rel in folders:
        cur = parent
        for part in rel.split('/'):
            cur = cur / part
            if cur.exists():
                if cur.is_dir():
                    continue
                raise HTTPException(409, f"No se puede crear {rel}: existe un archivo con ese nombre")
            cur.mkdir()
            created.append(str(cur.relative_to(root)).replace("\\", "/"))
        if (parent / rel).exists() and str((parent / rel).relative_to(root)).replace("\\", "/") not in created:
            existing.append(str((parent / rel).relative_to(root)).replace("\\", "/"))
    index_resource(rid, root)
    return {"ok": True, "template": t["name"], "created": created, "existing": existing, "created_count": len(created)}


@app.get("/api/catalog")
def catalog_items(q: str = "", status: str = "", product_type: str = ""):
    sql = """
        SELECT ci.*, r.name AS resource_name, c.name AS category_name
        FROM catalog_items ci
        LEFT JOIN resources r ON r.id=ci.resource_id
        LEFT JOIN categories c ON c.id=r.category_id
        WHERE 1=1
    """
    params = []
    if q.strip():
        like = f"%{q.strip()}%"
        sql += " AND (ci.title LIKE ? OR ci.tags LIKE ? OR ci.sku LIKE ? OR r.name LIKE ?)"
        params.extend([like, like, like, like])
    if status:
        sql += " AND ci.status=?"
        params.append(status)
    if product_type:
        sql += " AND ci.product_type=?"
        params.append(product_type)
    sql += " ORDER BY ci.updated_at DESC"
    with db() as con:
        return [dict(x) for x in con.execute(sql, params)]


@app.post("/api/catalog")
def catalog_create(payload: CatalogCreateIn):
    r, root, src = safe_resource_path(payload.resource_id, payload.source_rel_path)
    if not src.exists() or not src.is_file():
        raise HTTPException(404, "El archivo seleccionado ya no existe")
    title = payload.title.strip() or src.stem
    product_type = payload.product_type.strip() or (r.get("category_name") or "Otro")
    with db() as con:
        existing = con.execute(
            "SELECT id FROM catalog_items WHERE resource_id=? AND source_rel_path=?",
            (payload.resource_id, payload.source_rel_path),
        ).fetchone()
    if existing:
        raise HTTPException(409, "Este archivo ya está agregado al catálogo")
    dest_dir = library_root() / "Catalogo_Sorprezz" / slug_folder(product_type) / slug_folder(title)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = unique_destination(dest_dir / src.name)
    shutil.copy2(src, dest)
    with db() as con:
        cur = con.execute(
            """
            INSERT INTO catalog_items(resource_id,source_rel_path,title,product_type,technique,print_size,variants,status,sku,price,tags,notes,catalog_path,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (payload.resource_id, payload.source_rel_path, title, product_type, payload.technique.strip() or "Sublimación",
             payload.print_size.strip(), payload.variants.strip(), payload.status, payload.sku.strip(), payload.price.strip(),
             payload.tags.strip(), payload.notes.strip(), str(dest), now_iso(), now_iso()),
        )
        cid = cur.lastrowid
    return {"ok": True, "id": cid, "catalog_path": str(dest)}


@app.get("/api/catalog/{cid}/preview")
def catalog_preview(cid: int):
    with db() as con:
        row = con.execute("SELECT catalog_path FROM catalog_items WHERE id=?", (cid,)).fetchone()
    if not row or not row["catalog_path"]:
        raise HTTPException(404, "Vista previa no disponible")
    p = Path(row["catalog_path"])
    if not p.exists() or p.suffix.lower().lstrip(".") not in PREVIEW_EXTS:
        raise HTTPException(404, "Vista previa no disponible")
    return FileResponse(p)


@app.get("/api/catalog/{cid}")
def catalog_detail(cid: int):
    with db() as con:
        row = con.execute(
            """SELECT ci.*, r.name AS resource_name, c.name AS category_name, s.name AS subcategory_name
               FROM catalog_items ci LEFT JOIN resources r ON r.id=ci.resource_id
               LEFT JOIN categories c ON c.id=r.category_id LEFT JOIN subcategories s ON s.id=r.subcategory_id
               WHERE ci.id=?""", (cid,)
        ).fetchone()
    if not row:
        raise HTTPException(404, "Elemento de catálogo no encontrado")
    return dict(row)


@app.put("/api/catalog/{cid}")
def catalog_update(cid: int, payload: CatalogUpdateIn):
    with db() as con:
        exists = con.execute("SELECT id FROM catalog_items WHERE id=?", (cid,)).fetchone()
        if not exists:
            raise HTTPException(404, "Elemento de catálogo no encontrado")
        con.execute(
            """UPDATE catalog_items SET title=?, product_type=?, technique=?, print_size=?, variants=?, status=?, sku=?, price=?, tags=?, notes=?, updated_at=? WHERE id=?""",
            (payload.title.strip(), payload.product_type.strip() or "Otro", payload.technique.strip() or "Sublimación",
             payload.print_size.strip(), payload.variants.strip(), payload.status, payload.sku.strip(), payload.price.strip(),
             payload.tags.strip(), payload.notes.strip(), now_iso(), cid),
        )
    return {"ok": True}


@app.post("/api/catalog/{cid}/open")
def catalog_open(cid: int):
    with db() as con:
        row = con.execute("SELECT catalog_path FROM catalog_items WHERE id=?", (cid,)).fetchone()
    if not row or not row["catalog_path"]:
        raise HTTPException(404, "Archivo de catálogo no encontrado")
    p = Path(row["catalog_path"])
    if not p.exists():
        raise HTTPException(404, "El archivo de catálogo ya no existe")
    try:
        open_os_path(p.parent)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.delete("/api/catalog/{cid}")
def catalog_delete(cid: int, delete_copy: bool = False):
    with db() as con:
        row = con.execute("SELECT catalog_path FROM catalog_items WHERE id=?", (cid,)).fetchone()
        if not row:
            raise HTTPException(404, "Elemento de catálogo no encontrado")
        con.execute("DELETE FROM catalog_items WHERE id=?", (cid,))
    if delete_copy and row["catalog_path"]:
        p = Path(row["catalog_path"])
        if p.exists() and _is_within(p, library_root() / "Catalogo_Sorprezz"):
            try:
                p.unlink()
            except OSError:
                pass
    return {"ok": True}


@app.post("/api/catalog/export")
def catalog_export_file():
    with db() as con:
        rows = [dict(x) for x in con.execute(
            """SELECT ci.id,ci.title,ci.product_type,ci.technique,ci.print_size,ci.variants,ci.status,ci.sku,ci.price,ci.tags,ci.notes,ci.source_rel_path,
                      r.name AS recurso,c.name AS categoria,s.name AS subcategoria,ci.catalog_path
               FROM catalog_items ci LEFT JOIN resources r ON r.id=ci.resource_id
               LEFT JOIN categories c ON c.id=r.category_id LEFT JOIN subcategories s ON s.id=r.subcategory_id
               ORDER BY ci.id"""
        )]
    fields = ["id","title","product_type","technique","print_size","variants","status","sku","price","tags","notes","source_rel_path","recurso","categoria","subcategoria","catalog_path"]
    out_dir = library_root() / "Exportaciones"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"Catalogo_Sorprezz_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    with out_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    try:
        open_os_path(out_dir)
    except Exception:
        pass
    return {"ok": True, "path": str(out_path), "items": len(rows)}


@app.get("/api/catalog-export.csv")
def catalog_export_csv():
    with db() as con:
        rows = [dict(x) for x in con.execute(
            """SELECT ci.id,ci.title,ci.product_type,ci.technique,ci.print_size,ci.variants,ci.status,ci.sku,ci.price,ci.tags,ci.notes,ci.source_rel_path,
                      r.name AS recurso,c.name AS categoria,s.name AS subcategoria,ci.catalog_path
               FROM catalog_items ci LEFT JOIN resources r ON r.id=ci.resource_id
               LEFT JOIN categories c ON c.id=r.category_id LEFT JOIN subcategories s ON s.id=r.subcategory_id
               ORDER BY ci.id"""
        )]
    out = io.StringIO()
    fields = ["id","title","product_type","technique","print_size","variants","status","sku","price","tags","notes","source_rel_path","recurso","categoria","subcategoria","catalog_path"]
    writer = csv.DictWriter(out, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    content = "\ufeff" + out.getvalue()
    return StreamingResponse(iter([content.encode("utf-8")]), media_type="text/csv; charset=utf-8", headers={"Content-Disposition":"attachment; filename=Catalogo_Sorprezz.csv"})


@app.post("/api/resources/{rid}/zip")
def resource_zip(rid: int):
    r = get_resource(rid)
    if not r or not r.get("local_path"):
        raise HTTPException(404, "No existe carpeta local")
    src = Path(r["local_path"])
    if not src.exists():
        raise HTTPException(404, "La carpeta local ya no existe")
    out_dir = library_root() / "Exportaciones"
    out_dir.mkdir(parents=True, exist_ok=True)
    base = out_dir / f"{slug_folder(r['name'])}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    archive = shutil.make_archive(str(base), "zip", root_dir=str(src))
    return {"ok": True, "path": archive, "download_path": library_relative_path(archive)}


@app.delete("/api/resources/{rid}")
def resource_delete(rid: int, delete_files: bool = False):
    r = get_resource(rid)
    if not r:
        raise HTTPException(404, "Recurso no encontrado")
    if delete_files and r.get("local_path"):
        p = Path(r["local_path"])
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)
    with db() as con:
        con.execute("DELETE FROM files WHERE resource_id=?", (rid,))
        con.execute("DELETE FROM resources WHERE id=?", (rid,))
    return {"ok": True}


@app.get("/api/stats")
def stats():
    with db() as con:
        total = con.execute("SELECT COUNT(*) c FROM resources").fetchone()["c"]
        completed = con.execute("SELECT COUNT(*) c FROM resources WHERE status IN ('completado','parcial')").fetchone()["c"]
        pending = con.execute("SELECT COUNT(*) c FROM resources WHERE status IN ('pendiente','descargando')").fetchone()["c"]
        errors = con.execute("SELECT COUNT(*) c FROM resources WHERE status='error'").fetchone()["c"]
        files = con.execute("SELECT COALESCE(SUM(file_count),0) c FROM resources").fetchone()["c"]
        bytes_ = con.execute("SELECT COALESCE(SUM(total_bytes),0) c FROM resources").fetchone()["c"]
        collections = con.execute("SELECT COUNT(*) c FROM collections").fetchone()["c"]
    return {"resources": total, "completed": completed, "pending": pending, "errors": errors, "files": files, "bytes": bytes_, "collections": collections}


if __name__ == "__main__":
    init_db()
    load_config()
    def open_ui():
        time.sleep(1.2)
        webbrowser.open("http://127.0.0.1:8765")
    threading.Thread(target=open_ui, daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="warning")
