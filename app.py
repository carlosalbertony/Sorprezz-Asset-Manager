from __future__ import annotations

import csv
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
import webbrowser
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import uvicorn

APP_NAME = "Sorprezz Asset Manager"
APP_VERSION = "1.4.1"
# PyInstaller extracts bundled resources to sys._MEIPASS. In source mode we use this file's folder.
BASE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
WEB_DIR = BASE_DIR / "web"
DATA_DIR = Path(os.getenv("LOCALAPPDATA", str(Path.home()))) / "SorprezzAssetManager" / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "sorprezz.db"
CONFIG_PATH = DATA_DIR / "config.json"

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

        count = con.execute("SELECT COUNT(*) AS c FROM categories").fetchone()["c"]
        if count == 0:
            con.executemany(
                "INSERT INTO categories(name, created_at) VALUES (?, ?)",
                [(c, now_iso()) for c in DEFAULT_CATEGORIES],
            )
        if con.execute("SELECT COUNT(*) AS c FROM tags").fetchone()["c"] == 0:
            con.executemany(
                "INSERT INTO tags(name, created_at) VALUES (?,?)",
                [(x, now_iso()) for x in ["Por revisar", "Favorito", "Seleccionado", "Listo para catálogo"]],
            )
        if con.execute("SELECT COUNT(*) AS c FROM folder_templates").fetchone()["c"] == 0:
            cur = con.execute(
                "INSERT INTO folder_templates(name,description,created_at,updated_at) VALUES (?,?,?,?)",
                ("Flujo base Sorprezz", "Estructura general para revisar, preparar y seleccionar diseños de sublimación.", now_iso(), now_iso()),
            )
            tid = cur.lastrowid
            starter = ["01 ORIGINALES", "02 EDITABLES", "03 PNG", "04 MOCKUPS", "05 SELECCIONADOS", "06 LISTOS PARA CATÁLOGO"]
            con.executemany(
                "INSERT INTO folder_template_items(template_id,folder_path,sort_order) VALUES (?,?,?)",
                [(tid, x, i) for i, x in enumerate(starter)],
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
    for folder in ["Biblioteca", "Catalogo_Sorprezz", "Exportaciones", "Temporales"]:
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
    file_match = re.search(r"/file/d/([A-Za-z0-9_-]+)", u)
    return {
        "is_drive": is_drive,
        "kind": "folder" if folder_match else ("file" if file_match else "link"),
        "id": (folder_match or file_match).group(1) if (folder_match or file_match) else None,
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
    file_rows = []
    total = 0
    count = 0
    for p in path.rglob("*"):
        if p.is_file():
            try:
                size = p.stat().st_size
            except OSError:
                size = 0
            rel = str(p.relative_to(path))
            ext = p.suffix.lower().lstrip(".") or "sin_extension"
            file_rows.append((rid, rel, ext, size, now_iso()))
            total += size
            count += 1
    with db() as con:
        con.execute("DELETE FROM files WHERE resource_id=?", (rid,))
        con.executemany(
            "INSERT OR REPLACE INTO files(resource_id, rel_path, ext, size_bytes, created_at) VALUES (?,?,?,?,?)",
            file_rows,
        )
        con.execute(
            "UPDATE resources SET file_count=?, total_bytes=?, updated_at=? WHERE id=?",
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


def download_worker(rid: int) -> None:
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


@app.on_event("startup")
def startup():
    init_db()
    load_config()
    repair_existing_downloads()


@app.get("/")
def home():
    return FileResponse(WEB_DIR / "index.html")


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
    return {"valid": True, "info": info, "existing": dict(existing) if existing else None}


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
    try:
        with db() as con:
            cur = con.execute(
                """
                INSERT INTO resources(name,url,category_id,subcategory_id,status,created_at,updated_at)
                VALUES (?,?,?,?, 'pendiente', ?, ?)
                """,
                (name, url, payload.category_id, payload.subcategory_id, now_iso(), now_iso()),
            )
            rid = cur.lastrowid
    except sqlite3.IntegrityError:
        raise HTTPException(409, "Este enlace ya está registrado en la biblioteca")
    return get_resource(rid)


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
    if q.strip():
        sql += " AND (r.name LIKE ? OR r.url LIKE ? OR c.name LIKE ? OR s.name LIKE ? OR EXISTS (SELECT 1 FROM resource_tags rt JOIN tags t ON t.id=rt.tag_id WHERE rt.resource_id=r.id AND t.name LIKE ?))"
        like = f"%{q.strip()}%"
        params.extend([like, like, like, like, like])
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
            row["tags"] = [dict(x) for x in con.execute("SELECT t.id,t.name FROM tags t JOIN resource_tags rt ON rt.tag_id=t.id WHERE rt.resource_id=? ORDER BY t.name COLLATE NOCASE", (row["id"],))]
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
    """Reindexa todas las carpetas locales sin borrar ni mover archivos."""
    with db() as con:
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
    return {"ok": True, "resources": scanned, "files": files, "total_bytes": total_bytes, "missing": missing}


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
    folder_tags_map = {}
    for tr in folder_tag_rows:
        folder_tags_map.setdefault(tr["rel_path"], []).append({"id": tr["id"], "name": tr["name"]})
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
                "previewable": ext in PREVIEW_EXTS, "cataloged": rel in cataloged,
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
        if payload.operation == "copy":
            shutil.copy2(src, dst)
        else:
            shutil.move(str(src), str(dst))
            new_rel = str(dst.relative_to(root)).replace("\\", "/")
            # Mantiene enlazados los elementos ya agregados al catálogo cuando se mueve su original.
            with db() as con:
                con.execute(
                    "UPDATE catalog_items SET source_rel_path=?, updated_at=? WHERE resource_id=? AND source_rel_path=?",
                    (new_rel, now_iso(), rid, rel),
                )
        done.append({"source": rel, "destination": str(dst.relative_to(root)).replace("\\", "/")})
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
        else:
            con.execute("UPDATE catalog_items SET source_rel_path=?,updated_at=? WHERE resource_id=? AND source_rel_path=?", (new_rel,now_iso(),rid,old_rel))
    index_resource(rid, root)
    return {"ok": True, "path": new_rel}


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
    return {"ok": True, "path": str(out), "files": added}


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
    with db() as con:
        rows = [dict(x) for x in con.execute(
            """SELECT t.id,t.name,t.created_at,
                      (SELECT COUNT(*) FROM resource_tags rt WHERE rt.tag_id=t.id) AS resources,
                      (SELECT COUNT(*) FROM folder_tags ft WHERE ft.tag_id=t.id) AS folders
               FROM tags t ORDER BY t.name COLLATE NOCASE"""
        )]
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
    return {"ok": True, "path": archive}


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
        catalog = con.execute("SELECT COUNT(*) c FROM catalog_items").fetchone()["c"]
    return {"resources": total, "completed": completed, "pending": pending, "errors": errors, "files": files, "bytes": bytes_, "catalog": catalog}


if __name__ == "__main__":
    init_db()
    load_config()
    def open_ui():
        time.sleep(1.2)
        webbrowser.open("http://127.0.0.1:8765")
    threading.Thread(target=open_ui, daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="warning")
