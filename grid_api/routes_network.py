"""
routes_network.py
Endpoints GET/PATCH/DELETE para consultar y modificar elementos de la red.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Any
import json, math

from grid_network import get_bus_geo, safe_float

router = APIRouter()

_net = None

def set_net(net):
    global _net
    _net = net

def get_net():
    if _net is None:
        raise HTTPException(503, "No hay red cargada. Selecciona o sube una red.")
    return _net


@router.get("/network")
def get_network():
    net = get_net()
    buses = []
    for idx, row in net.bus.iterrows():
        geo = get_bus_geo(net, idx)
        buses.append({"id": int(idx), "name": row["name"],
                      "vn_kv": safe_float(row["vn_kv"]),
                      "lat": safe_float(geo["y"]) if geo else None,
                      "lon": safe_float(geo["x"]) if geo else None})
    lines = []
    for idx, row in net.line.iterrows():
        fb, tb = int(row["from_bus"]), int(row["to_bus"])
        fg, tg = get_bus_geo(net, fb), get_bus_geo(net, tb)
        lines.append({"id": int(idx), "name": row["name"],
                      "from_bus": fb, "to_bus": tb,
                      "length_km": safe_float(row["length_km"]),
                      "max_i_ka": safe_float(row["max_i_ka"]),
                      "from_lat": safe_float(fg["y"]) if fg else None,
                      "from_lon": safe_float(fg["x"]) if fg else None,
                      "to_lat":   safe_float(tg["y"]) if tg else None,
                      "to_lon":   safe_float(tg["x"]) if tg else None})
    trafos = []
    for idx, row in net.trafo.iterrows():
        hv, lv = int(row["hv_bus"]), int(row["lv_bus"])
        hg, lg = get_bus_geo(net, hv), get_bus_geo(net, lv)
        trafos.append({"id": int(idx), "name": row["name"],
                       "hv_bus": hv, "lv_bus": lv,
                       "sn_mva": safe_float(row["sn_mva"]),
                       "hv_lat": safe_float(hg["y"]) if hg else None,
                       "hv_lon": safe_float(hg["x"]) if hg else None,
                       "lv_lat": safe_float(lg["y"]) if lg else None,
                       "lv_lon": safe_float(lg["x"]) if lg else None})
    loads = []
    for idx, row in net.load.iterrows():
        geo = get_bus_geo(net, int(row["bus"]))
        loads.append({"id": int(idx), "name": row["name"], "bus": int(row["bus"]),
                      "p_mw": safe_float(row["p_mw"]), "q_mvar": safe_float(row["q_mvar"]),
                      "lat": safe_float(geo["y"]) if geo else None,
                      "lon": safe_float(geo["x"]) if geo else None})
    sgens = []
    for idx, row in net.sgen.iterrows():
        geo = get_bus_geo(net, int(row["bus"]))
        sgens.append({"id": int(idx), "name": row["name"], "bus": int(row["bus"]),
                      "p_mw": safe_float(row["p_mw"]), "q_mvar": safe_float(row["q_mvar"]),
                      "lat": safe_float(geo["y"]) if geo else None,
                      "lon": safe_float(geo["x"]) if geo else None})
    # Include loop lines and in_service status
    loop_lines = [int(x) for x in net.get("_loop_lines", [])]
    # Add in_service to each line
    for l in lines:
        l["in_service"] = bool(net.line.at[l["id"], "in_service"]) if l["id"] in net.line.index else True
    return {"buses": buses, "lines": lines, "trafos": trafos,
            "loads": loads, "sgens": sgens, "loop_lines": loop_lines}


@router.get("/extgrid")
def get_extgrid():
    """Devuelve los ext_grid (nodos slack) de la red activa."""
    import math
    net = get_net()
    result = []
    for idx, row in net.ext_grid.iterrows():
        data = {"id": int(idx)}
        for col, v in row.items():
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                data[col] = None
            elif hasattr(v, "item"):
                data[col] = v.item()
            else:
                try:
                    import json; json.dumps(v); data[col] = v
                except: data[col] = str(v)
        result.append(data)
    return result


@router.get("/buses")
def list_buses():
    net = get_net()
    return [{"id": int(i), "name": r["name"], "vn_kv": safe_float(r["vn_kv"])}
            for i, r in net.bus.iterrows()]


@router.get("/element/{t}/{eid}")
def get_element(t: str, eid: int):
    net = get_net()
    tbl = {"bus": net.bus, "line": net.line, "trafo": net.trafo,
           "load": net.load, "sgen": net.sgen, "ext_grid": net.ext_grid}.get(t)
    if tbl is None: raise HTTPException(400, f"Tipo desconocido: {t}")
    if eid not in tbl.index: raise HTTPException(404, "No encontrado")
    data = {}
    for col, v in tbl.loc[eid].items():
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)): data[col] = None
        elif hasattr(v, "item"): data[col] = v.item()
        else:
            try: json.dumps(v); data[col] = v
            except: data[col] = str(v)
    return data


class PatchEl(BaseModel):
    param: str
    value: Any

@router.patch("/element/{t}/{eid}")
def patch_element(t: str, eid: int, body: PatchEl):
    net = get_net()
    tbl = {"bus": net.bus, "line": net.line, "trafo": net.trafo,
           "load": net.load, "sgen": net.sgen, "ext_grid": net.ext_grid}.get(t)
    if tbl is None: raise HTTPException(400, f"Tipo desconocido: {t}")
    if eid not in tbl.index: raise HTTPException(404, "No encontrado")
    if body.param not in tbl.columns: raise HTTPException(400, f"Parámetro desconocido: {body.param}")
    tbl.at[eid, body.param] = body.value
    return {"ok": True}


@router.delete("/element/{t}/{eid}")
def delete_element(t: str, eid: int):
    net = get_net()
    if t == "load":
        if eid not in net.load.index: raise HTTPException(404, "No encontrado")
        net.load.drop(index=eid, inplace=True)
    elif t == "sgen":
        if eid not in net.sgen.index: raise HTTPException(404, "No encontrado")
        net.sgen.drop(index=eid, inplace=True)
    else:
        raise HTTPException(400, "Solo load/sgen se pueden eliminar")
    return {"ok": True}


# ── POST /api/line/{id}/toggle ────────────────────────────────────────────────
@router.post("/line/{line_id}/toggle")
def toggle_line_service(line_id: int):
    """Abre o cierra una línea (toggle in_service)."""
    net = get_net()
    if line_id not in net.line.index:
        raise HTTPException(404, "Línea no encontrada")
    current = bool(net.line.at[line_id, "in_service"])
    net.line.at[line_id, "in_service"] = not current
    return {"line_id": line_id, "in_service": not current}
