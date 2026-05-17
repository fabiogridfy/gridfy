"""
grid_api/routes_grids.py
Endpoints para gestión de redes:
  GET  /api/grids          → listar redes
  POST /api/grids/upload   → subir Excel y crear red
  POST /api/grids/{id}/load   → cargar red como activa
  POST /api/grids/{id}/save   → guardar estado actual en BD
  DELETE /api/grids/{id}      → eliminar red
  GET  /api/grids/active      → info de la red activa
"""
import os
import shutil
from datetime import datetime

from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Depends
from sqlalchemy.orm import Session

from db.session import get_db
from db import crud
from grid_network.manager import network_manager
from grid_api.routes_network import set_net

router = APIRouter()

REDES_DIR = os.path.join(os.path.dirname(__file__), "..", "redes")


def _pickle_path(network_name: str) -> str:
    safe = network_name.replace(" ", "_").replace("/", "_")
    return os.path.join(REDES_DIR, safe, "modificada.p")


# ── GET /api/grids ────────────────────────────────────────────────────────────
@router.get("/grids")
def list_grids(db: Session = Depends(get_db)):
    networks = crud.list_networks(db)
    return [
        {
            "id":          n.id,
            "name":        n.name,
            "description": n.description,
            "voltage":     n.voltage,
            "has_saved":   bool(n.pickle_path and os.path.exists(n.pickle_path)),
            "created_at":  n.created_at.isoformat() if n.created_at else None,
            "updated_at":  n.updated_at.isoformat() if n.updated_at else None,
            "active":      network_manager.network_id == n.id,
        }
        for n in networks
    ]


# ── GET /api/grids/active ─────────────────────────────────────────────────────
@router.get("/grids/current")
def active_grid():
    if not network_manager.is_loaded():
        return {"loaded": False}
    net = network_manager.net
    return {
        "loaded":     True,
        "network_id": network_manager.network_id,
        "name":       network_manager.name,
        "buses":      len(net.bus),
        "lines":      len(net.line),
        "trafos":     len(net.trafo),
        "loads":      len(net.load),
        "sgens":      len(net.sgen),
    }


# ── Columnas requeridas por nivel de tensión ─────────────────────────────────
REQUIRED_SHEETS_BT = {
    "Terminal":       ["Nombre", "Tensión", "X", "Y"],
    "Lineas":         ["Nombre", "Terminal_i", "Terminal_j", "Longitud (km)", "R/km", "X/km", "I max (kA)"],
    "ExternalGrid":   ["Nombre", "Terminal"],
    "Transformadores":["code", "primary_node_code", "secondary_node_code",
                       "nominal_apparent_power_kVA", "Tension HV", "Tension LV",
                       "vk_percent", "vkr_percent", "pfe_kw", "i0_percent"],
}
OPTIONAL_SHEETS_BT = {
    "Loads_Data": ["Terminal"],
    "Gen_Data":   ["Terminal"],
}


def _validate_excel_structure(excel_path: str, voltage: str) -> list[str]:
    """
    Comprueba que el Excel tiene las hojas y columnas requeridas.
    Devuelve lista de errores (vacía = OK).
    """
    import pandas as pd
    errors = []
    required = REQUIRED_SHEETS_BT if voltage == "BT" else REQUIRED_SHEETS_BT  # MT TBD

    try:
        xl = pd.ExcelFile(excel_path)
    except Exception as e:
        return [f"No se puede leer el Excel: {e}"]

    for sheet, cols in required.items():
        if sheet not in xl.sheet_names:
            errors.append(f"Hoja requerida ausente: '{sheet}'")
            continue
        df = xl.parse(sheet, nrows=1)
        for col in cols:
            if col not in df.columns:
                errors.append(f"Columna '{col}' no encontrada en hoja '{sheet}'")

    return errors


def _validate_powerflow(net) -> tuple[bool, list[str]]:
    """
    Intenta correr un PF y devuelve (converged, log_lines).
    """
    import pandapower as pp
    import pandapower.topology as top
    log = []

    # Topología
    unsupplied = top.unsupplied_buses(net)
    if unsupplied:
        log.append(f"Buses sin suministro: {sorted(unsupplied)}")

    total_load = float(net.load.p_mw.sum()) if len(net.load) > 0 else 0.0
    total_sgen = float(net.sgen.p_mw.sum()) if len(net.sgen) > 0 else 0.0
    log.append(f"Elementos: {len(net.bus)} buses | {len(net.line)} lineas | "
               f"{len(net.trafo)} trafos | {len(net.load)} cargas | {len(net.sgen)} sgens")
    log.append(f"Balance: carga={total_load:.4f} MW | generacion={total_sgen:.4f} MW")

    try:
        pp.runpp(net, algorithm="nr", calculate_voltage_angles=True,
                 max_iteration=50, init="auto")
        vms = net.res_bus["vm_pu"].dropna()
        log.append(f"Power Flow convergido: V_min={vms.min():.4f} V_max={vms.max():.4f} p.u.")
        log.append(f"Cargabilidad max lineas: {net.res_line.loading_percent.max():.2f}%")
        return True, log
    except Exception as e:
        log.append(f"ERROR: Power Flow no convergió — {e}")
        log.append("Posibles causas: red desconectada, parámetros de línea inválidos, "
                   "desequilibrio de potencia severo.")
        return False, log


# ── POST /api/grids/upload ────────────────────────────────────────────────────
@router.post("/grids/upload")
async def upload_grid(
    file:        UploadFile = File(...),
    name:        str        = Form(...),
    description: str        = Form(""),
    voltage:     str        = Form("BT"),
    db:          Session    = Depends(get_db),
):
    """
    Sube un Excel con validación completa:
    1. Extensión y nombre único
    2. Estructura de hojas y columnas requeridas
    3. Construcción del modelo pandapower
    4. Power Flow de convergencia
    Si cualquier paso falla, devuelve los errores y NO registra la red.
    """
    if not file.filename.endswith((".xlsx", ".xls")):
        raise HTTPException(400, "Solo se aceptan archivos Excel (.xlsx / .xls)")

    if crud.get_network_by_name(db, name):
        raise HTTPException(409, f"Ya existe una red con el nombre '{name}'")

    # Guardar Excel temporalmente para validar
    safe_name  = name.replace(" ", "_").replace("/", "_")
    net_folder = os.path.join(REDES_DIR, safe_name)
    os.makedirs(net_folder, exist_ok=True)
    excel_path = os.path.join(net_folder, "datos.xlsx")

    content = await file.read()
    with open(excel_path, "wb") as f:
        f.write(content)

    # ── Paso 1: Validar estructura del Excel ──────────────────────────────────
    struct_errors = _validate_excel_structure(excel_path, voltage)
    if struct_errors:
        shutil.rmtree(net_folder, ignore_errors=True)
        raise HTTPException(422, {
            "stage": "estructura",
            "errors": struct_errors,
            "log": struct_errors,
        })

    # ── Paso 2: Construir modelo pandapower ───────────────────────────────────
    try:
        from grid_network.builder import build_network
        net = build_network(excel_path)
    except Exception as e:
        shutil.rmtree(net_folder, ignore_errors=True)
        raise HTTPException(422, {
            "stage": "construccion",
            "errors": [str(e)],
            "log": [f"Error construyendo la red: {e}"],
        })

    # ── Paso 3: Validar Power Flow ────────────────────────────────────────────
    pf_ok, pf_log = _validate_powerflow(net)
    if not pf_ok:
        shutil.rmtree(net_folder, ignore_errors=True)
        raise HTTPException(422, {
            "stage": "powerflow",
            "errors": ["El Power Flow no convergió — revisa los parámetros de la red"],
            "log": pf_log,
        })

    # Guardar en BD
    db_net = crud.create_network(
        db, name=name, description=description,
        voltage=voltage, excel_path=excel_path,
    )

    # Cargar como red activa
    network_manager.load_from_excel(excel_path, db_net.id, name)
    set_net(network_manager.net)

    return {
        "ok":   True,
        "id":   db_net.id,
        "name": db_net.name,
        "buses": len(net.bus), "lines": len(net.line),
        "trafos": len(net.trafo),
    }


class LoadRequest(BaseModel):
    from_original: bool = False   # True = always load from Excel (ignore saved versions)

# ── POST /api/grids/{id}/load ─────────────────────────────────────────────────
@router.post("/grids/{network_id}/load")
def load_grid(network_id: int, body: LoadRequest = None, db: Session = Depends(get_db)):
    """
    Carga una red como activa.
    - from_original=False (default): carga la última versión guardada si existe, si no el Excel
    - from_original=True: siempre carga desde el Excel original, ignorando versiones
    """
    db_net = crud.get_network(db, network_id)
    if not db_net:
        raise HTTPException(404, "Red no encontrada")

    force_original = (body.from_original if body else False)
    pickle = db_net.pickle_path

    if not force_original and pickle and os.path.exists(pickle):
        network_manager.load_from_pickle(pickle, db_net.id, db_net.name)
        source = "ultima version guardada"
    else:
        network_manager.load_from_excel(db_net.excel_path, db_net.id, db_net.name)
        source = "original (Excel)"

    set_net(network_manager.net)
    net = network_manager.net

    return {
        "ok":     True,
        "name":   db_net.name,
        "source": source,
        "buses":  len(net.bus), "lines": len(net.line),
        "trafos": len(net.trafo), "loads": len(net.load), "sgens": len(net.sgen),
    }


class SaveVersionRequest(BaseModel):
    version_name: str = "version"

# ── POST /api/grids/{id}/save ─────────────────────────────────────────────────
@router.post("/grids/{network_id}/save")
def save_grid(network_id: int, body: SaveVersionRequest = None, db: Session = Depends(get_db)):
    """Guarda el estado actual de la red activa como pickle con nombre de versión."""
    if not network_manager.is_loaded():
        raise HTTPException(503, "No hay red cargada")
    if network_manager.network_id != network_id:
        raise HTTPException(400, "La red solicitada no es la red activa")

    db_net = crud.get_network(db, network_id)
    if not db_net:
        raise HTTPException(404, "Red no encontrada")

    version_name = (body.version_name if body else "version").strip()
    safe_version = version_name.replace(" ", "_").replace("/", "_").replace(":", "-")[:80]

    # Store named version in versions/ subfolder
    safe_name = db_net.name.replace(" ", "_").replace("/", "_")
    versions_folder = os.path.join(REDES_DIR, safe_name, "versiones")
    os.makedirs(versions_folder, exist_ok=True)
    pickle_path = os.path.join(versions_folder, f"{safe_version}.p")

    network_manager.save_to_pickle(pickle_path)
    # Always update the "latest" pointer too
    latest_path = _pickle_path(db_net.name)
    network_manager.save_to_pickle(latest_path)
    crud.update_pickle(db, network_id, latest_path)

    return {"ok": True, "version_name": version_name, "saved_to": pickle_path, "name": db_net.name}


# ── GET /api/grids/{id}/versions ──────────────────────────────────────────────
@router.get("/grids/{network_id}/versions")
def list_versions(network_id: int, db: Session = Depends(get_db)):
    """Lista las versiones guardadas de una red."""
    db_net = crud.get_network(db, network_id)
    if not db_net:
        raise HTTPException(404, "Red no encontrada")

    safe_name = db_net.name.replace(" ", "_").replace("/", "_")
    versions_folder = os.path.join(REDES_DIR, safe_name, "versiones")

    versions = []
    if os.path.exists(versions_folder):
        for f in sorted(os.listdir(versions_folder), key=lambda x: os.path.getmtime(os.path.join(versions_folder, x)), reverse=True):
            if f.endswith(".p"):
                fp = os.path.join(versions_folder, f)
                import datetime
                versions.append({
                    "name": f[:-2].replace("_", " "),
                    "file": f,
                    "saved_at": datetime.datetime.fromtimestamp(os.path.getmtime(fp)).isoformat()
                })
    return versions


# ── POST /api/grids/{id}/load-version ────────────────────────────────────────
class LoadVersionRequest(BaseModel):
    file: str

@router.post("/grids/{network_id}/load-version")
def load_version(network_id: int, body: LoadVersionRequest, db: Session = Depends(get_db)):
    """Carga una versión específica guardada."""
    db_net = crud.get_network(db, network_id)
    if not db_net:
        raise HTTPException(404, "Red no encontrada")

    safe_name = db_net.name.replace(" ", "_").replace("/", "_")
    pickle_path = os.path.join(REDES_DIR, safe_name, "versiones", body.file)

    if not os.path.exists(pickle_path) or not pickle_path.endswith(".p"):
        raise HTTPException(404, "Versión no encontrada")

    network_manager.load_from_pickle(pickle_path, db_net.id, db_net.name)
    set_net(network_manager.net)
    net = network_manager.net

    return {"ok": True, "version": body.file, "buses": len(net.bus),
            "lines": len(net.line), "loads": len(net.load), "sgens": len(net.sgen)}


# ── DELETE /api/grids/{id} ────────────────────────────────────────────────────
@router.delete("/grids/{network_id}")
def delete_grid(network_id: int, db: Session = Depends(get_db)):
    db_net = crud.get_network(db, network_id)
    if not db_net:
        raise HTTPException(404, "Red no encontrada")

    # Si es la activa, descargar
    if network_manager.network_id == network_id:
        network_manager.unload()
        set_net(None)

    # Borrar archivos
    safe = db_net.name.replace(" ", "_").replace("/", "_")
    net_folder = os.path.join(REDES_DIR, safe)
    shutil.rmtree(net_folder, ignore_errors=True)

    crud.delete_network(db, network_id)
    return {"ok": True, "deleted": db_net.name}
