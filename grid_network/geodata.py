"""
geodata.py
Utilidades para extraer coordenadas geográficas de la red pandapower.
Compatible con versiones antiguas (bus_geodata) y nuevas (columna geo GeoJSON).
"""
import json
import math


def get_bus_geo(net, bus_idx: int):
    """
    Devuelve {"x": lon, "y": lat} para un bus, o None si no hay geodata.
    Compatible con pandapower antiguo (bus_geodata) y nuevo (columna geo).
    """
    # Nuevo pandapower: coordenadas como GeoJSON en net.bus["geo"]
    try:
        if "geo" in net.bus.columns:
            raw = net.bus.at[bus_idx, "geo"]
            if raw is not None and str(raw) not in ("", "nan", "None"):
                g = json.loads(raw) if isinstance(raw, str) else raw
                coords = g.get("coordinates", [None, None])
                if coords[0] is not None:
                    return {"x": coords[0], "y": coords[1]}
    except Exception:
        pass

    # Pandapower antiguo: net.bus_geodata DataFrame
    try:
        gd = net.bus_geodata
        if bus_idx in gd.index:
            return {"x": float(gd.at[bus_idx, "x"]), "y": float(gd.at[bus_idx, "y"])}
    except Exception:
        pass

    return None


def safe_float(v):
    if v is None:
        return None
    try:
        f = float(v)
        return None if (math.isnan(f) or math.isinf(f)) else round(f, 6)
    except Exception:
        return None
