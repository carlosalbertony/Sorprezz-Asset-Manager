from __future__ import annotations

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
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import uvicorn

APP_NAME = "Sorprezz Asset Manager"
APP_VERSION = "1.2.0"
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
            """
        )
        count = con.execute("SELECT COUNT(*) AS c FROM categories").fetchone()["c"]
        if count == 0:
            con.executemany(
                "INSERT INTO categories(name, created_at) VALUES (?, ?)",
                [(c, now_iso()) for c in DEFAULT_CATEGORIES],
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
        # Si antes constaba como error, no afirmamos que Drive entregó absolutamente todo:
        # lo dejamos como descargado con aviso. Los recursos sanos permanecen completados.
        if r["status"] == "error":
            with db() as con:
                con.execute(
                    "UPDATE resources SET status='parcial', file_count=?, total_bytes=?, updated_at=? WHERE id=?",
                    (count, total, now_iso(), r["id"]),
                )
            repaired += 1
        elif r["status"] in ("pendiente", "descargando"):
            with db() as con:
                con.execute(
                    "UPDATE resources SET status='completado', error=NULL, file_count=?, total_bytes=?, updated_at=? WHERE id=?",
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
            status = "parcial"
            friendly_warning = (
                f"Se descargaron e indexaron {count} archivos, pero Google Drive reportó un aviso: "
                f"{download_warning}"
            )
            with db() as con:
                con.execute(
                    "UPDATE resources SET status=?, error=?, file_count=?, total_bytes=?, updated_at=? WHERE id=?",
                    (status, friendly_warning, count, total, now_iso(), rid),
                )
            with active_lock:
                active_downloads[rid] = {
                    "progress": 100,
                    "message": f"Descargado con aviso: {count} archivos · revisa el contenido local",
                    "warning": True,
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
                                "UPDATE resources SET status='parcial', error=?, file_count=?, total_bytes=?, updated_at=? WHERE id=?",
                                (f"La descarga dejó contenido utilizable, pero terminó con un aviso: {e}", count, total, now_iso(), rid),
                            )
                        with active_lock:
                            active_downloads[rid] = {
                                "progress": 100,
                                "message": f"Descargado con aviso: {count} archivos encontrados",
                                "warning": True,
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
def resources(q: str = "", category_id: Optional[int] = None, status: str = ""):
    sql = """
        SELECT r.*, c.name AS category_name, s.name AS subcategory_name
        FROM resources r
        LEFT JOIN categories c ON c.id=r.category_id
        LEFT JOIN subcategories s ON s.id=r.subcategory_id
        WHERE 1=1
    """
    params = []
    if q.strip():
        sql += " AND (r.name LIKE ? OR r.url LIKE ? OR c.name LIKE ? OR s.name LIKE ?)"
        like = f"%{q.strip()}%"
        params.extend([like, like, like, like])
    if category_id:
        sql += " AND r.category_id=?"
        params.append(category_id)
    if status:
        sql += " AND r.status=?"
        params.append(status)
    sql += " ORDER BY r.updated_at DESC"
    with db() as con:
        rows = [dict(r) for r in con.execute(sql, params)]
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
    r["types"] = types
    r["files"] = files
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
        new_status = "parcial" if r.get("status") == "error" and r.get("error") else (r.get("status") if r.get("status") == "parcial" else "completado")
        with db() as con:
            con.execute(
                "UPDATE resources SET status=?, file_count=?, total_bytes=?, updated_at=? WHERE id=?",
                (new_status, count, total, now_iso(), rid),
            )
    return {"ok": True, "file_count": count, "total_bytes": total, "status": (new_status if count > 0 else r.get("status"))}


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
        if count > 0 and r.get("status") == "error":
            with db() as con:
                con.execute(
                    "UPDATE resources SET status='parcial', file_count=?, total_bytes=?, updated_at=? WHERE id=?",
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
    return {"resources": total, "completed": completed, "pending": pending, "errors": errors, "files": files, "bytes": bytes_}


if __name__ == "__main__":
    init_db()
    load_config()
    def open_ui():
        time.sleep(1.2)
        webbrowser.open("http://127.0.0.1:8765")
    threading.Thread(target=open_ui, daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="warning")
