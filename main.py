"""
main.py — Gridfy Platform
"""
import os
import math as _math
import json as _json
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse as _JSONResponse

from db.session import init_db, SessionLocal
from db import crud
from grid_network.manager import network_manager
from grid_api.routes_network import router as network_router, set_net
from paths import to_relative
from db.path_migration import migrate_legacy_absolute_paths
from grid_api.routes_studies  import router as studies_router
from grid_api.routes_ac       import router as ac_router
from grid_api.routes_scenarios import router as scenarios_router
from grid_api.routes_gis       import router as gis_router
from grid_api.routes_settings   import router as settings_router
from grid_api.routes_grids    import router as grids_router

EXCEL_PATH = os.path.join(os.path.dirname(__file__), "Dataset_Sta_Ana2.xlsx")
REDES_DIR  = os.path.join(os.path.dirname(__file__), "redes")
SEED_NAME  = "Santa Ana 2"


class _NaNSafeEncoder(_json.JSONEncoder):
    def iterencode(self, o, _one_shot=False):
        return super().iterencode(self._sanitize(o), _one_shot)
    def _sanitize(self, obj):
        if isinstance(obj, float):
            if _math.isnan(obj) or _math.isinf(obj): return None
            return obj
        if isinstance(obj, dict): return {k: self._sanitize(v) for k,v in obj.items()}
        if isinstance(obj, (list, tuple)): return [self._sanitize(v) for v in obj]
        return obj

class _SafeJSONResponse(_JSONResponse):
    def render(self, content):
        return _json.dumps(content, cls=_NaNSafeEncoder,
                           ensure_ascii=False, allow_nan=False).encode("utf-8")


app = FastAPI(title="Gridfy Platform", default_response_class=_SafeJSONResponse)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.on_event("startup")
async def startup():
    init_db()
    print("[Gridfy] Base de datos inicializada")
    # Normaliza rutas absolutas heredadas (compatibilidad pre-portable-paths)
    migrate_legacy_absolute_paths()
    db = SessionLocal()
    try:
        if os.path.exists(EXCEL_PATH):
            existing = crud.get_network_by_name(db, SEED_NAME)
            if not existing:
                import shutil
                safe = SEED_NAME.replace(" ", "_")
                net_folder = os.path.join(REDES_DIR, safe)
                os.makedirs(net_folder, exist_ok=True)
                dest = os.path.join(net_folder, "datos.xlsx")
                shutil.copy2(EXCEL_PATH, dest)
                crud.create_network(db, name=SEED_NAME,
                    description="Red de BT Santa Ana 2",
                    voltage="BT", excel_path=to_relative(dest))
                print(f"[Gridfy] Red '{SEED_NAME}' registrada")
        else:
            print("[Gridfy] Sin Excel de ejemplo")
    finally:
        db.close()
    print("[Gridfy] Listo en http://localhost:8000")


app.include_router(network_router,   prefix="/api")
app.include_router(studies_router,   prefix="/api")
app.include_router(ac_router,        prefix="/api")
app.include_router(grids_router,     prefix="/api")
app.include_router(scenarios_router, prefix="/api")
app.include_router(gis_router,       prefix="/api")
app.include_router(settings_router,  prefix="/api")

app.mount("/", StaticFiles(
    directory=os.path.join(os.path.dirname(__file__), "static"), html=True
), name="static")
