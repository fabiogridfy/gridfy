"""
grid_network/hc_map.py
Calcula el Hosting Capacity de todos los buses de la red usando busqueda binaria.
Emite progreso via SSE (generador).
"""
import json
from grid_network.ac_hosting import (
    get_limits, get_voltage_level, get_feeder_scope,
    _search_generator, P_MAX_KW
)


def hc_map_generator(net, mode: str = "demand", voltage: str = "BT"):
    """
    Calcula HC para todos los buses (excepto slack y buses HV de trafo).
    mode: 'demand' | 'generation'
    Yields SSE strings: "progress: msg" | "result: {json}" | "error: msg"
    """
    is_generation = (mode == "generation")
    limits        = get_limits(net.bus["vn_kv"].min() if voltage == "BT" else 20)
    p_max_kw      = P_MAX_KW.get(voltage, 100_000)
    element_type  = "generacion" if is_generation else "demanda"

    # Buses a analizar: excluir slack y buses HV (>1 kV para BT)
    slack_bus = int(net.ext_grid["bus"].iloc[0])
    hv_buses  = set(net.trafo["hv_bus"].tolist()) if len(net.trafo) > 0 else set()
    target_buses = [
        (int(idx), str(row["name"]))
        for idx, row in net.bus.iterrows()
        if int(idx) != slack_bus and int(idx) not in hv_buses
        and float(row["vn_kv"]) < 1.0  # solo BT por ahora
    ]

    total = len(target_buses)
    yield f"progress: Analizando {total} buses | modo: {element_type} | limite: {p_max_kw:,} kW"

    results = []
    for i, (bus_idx, bus_name) in enumerate(target_buses):
        yield f"progress: [{i+1}/{total}] {bus_name[:50]}"

        feeder_lines_index, buses_to = get_feeder_scope(net, bus_idx)

        hosting_kw     = 0
        saturated_base = False
        violated       = []

        for msg in _search_generator(net, bus_idx, feeder_lines_index, buses_to,
                                      limits, is_generation, p_max_kw):
            if msg[0] == "result":
                hosting_kw     = msg[1]
                violated       = msg[2]
                saturated_base = msg[3]

        # Determine limiting factor text
        if saturated_base:
            limit_text = "Red ya saturada"
        elif hosting_kw >= p_max_kw:
            limit_text = f">=  {p_max_kw:,} kW (limite estudio)"
        elif hosting_kw == 0:
            limit_text = "Sin capacidad"
        else:
            limit_text = "Limite tecnico (tension/corriente/trafo)"

        results.append({
            "bus_id":          bus_idx,
            "bus_name":        bus_name,
            "hosting_kw":      hosting_kw,
            "saturated":       saturated_base,
            "at_limit":        hosting_kw >= p_max_kw,
            "limits_violated": limit_text,
            "violated_detail": violated[:2],  # first 2 violations for detail
        })

    yield f"progress: Calculo completado — {total} buses analizados"
    yield "result: " + json.dumps({
        "mode":     element_type,
        "voltage":  voltage,
        "p_max_kw": p_max_kw,
        "buses":    results,
    }, ensure_ascii=False)
