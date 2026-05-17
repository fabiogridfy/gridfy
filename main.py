"""
main.py — Gridfy Platform

Estructura:
  main.py
  db/           ← SQLite + SQLAlchemy (models, session, crud)
  grid_network/ ← builder, powerflow, geodata, ac_hosting, manager
  grid_api/     ← routes_network, routes_studies, routes_ac, routes_grids
  static/       ← frontend
  redes/        ← Excels y pickles de las redes subidas
"""
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from db.session import init_db, SessionLocal
from db import crud
from grid_network.manager import network_manager
from grid_api.routes_network import router as network_router, set_net
from grid_api.routes_studies  import router as studies_router
from grid_api.routes_ac       import router as ac_router
from grid_api.routes_scenarios import router as scenarios_router
from grid_api.routes_gis       import router as gis_router
from grid_api.routes_settings   import router as settings_router
from grid_api.routes_grids    import router as grids_router

EXCEL_PATH  = os.path.join(os.path.dirname(__file__), "Dataset_Sta_Ana2.xlsx")
REDES_DIR   = os.path.join(os.path.dirname(__file__), "redes")
SEED_NAME   = "Santa Ana 2"

app = FastAPI(title="Gridfy Platform")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.on_event("startup")
async def startup():
    # 1. Inicializar BD
    init_db()
    print("[Gridfy] Base de datos inicializada")

    db = SessionLocal()
    try:
        # 2. Sembrar red de Santa Ana 2 si no existe
        if os.path.exists(EXCEL_PATH):
            existing = crud.get_network_by_name(db, SEED_NAME)
            if not existing:
                import shutil
                safe = SEED_NAME.replace(" ", "_")
                net_folder = os.path.join(REDES_DIR, safe)
                os.makedirs(net_folder, exist_ok=True)
                dest = os.path.join(net_folder, "datos.xlsx")
                shutil.copy2(EXCEL_PATH, dest)
                existing = crud.create_network(
                    db, name=SEED_NAME, description="Red de BT Santa Ana 2 — red de ejemplo",
                    voltage="BT", excel_path=dest,
                )
                print(f"[Gridfy] Red '{SEED_NAME}' registrada en BD (id={existing.id})")

                print(f"[Gridfy] Red '{SEED_NAME}' disponible en el selector")
        else:
            print("[Gridfy] No se encontró el Excel de Santa Ana 2")
    finally:
        db.close()

    print("[Gridfy] Listo en http://localhost:8000")


# Routers
app.include_router(network_router, prefix="/api")
app.include_router(studies_router, prefix="/api")
app.include_router(ac_router,      prefix="/api")
app.include_router(grids_router,   prefix="/api")
app.include_router(scenarios_router, prefix="/api")
app.include_router(gis_router,       prefix="/api")
app.include_router(settings_router,  prefix="/api")

# Frontend
app.mount("/", StaticFiles(
    directory=os.path.join(os.path.dirname(__file__), "static"), html=True
), name="static")
