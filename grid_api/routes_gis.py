"""
grid_api/routes_gis.py
Endpoint para importar redes desde formato GIS (Tramo_baja + Transformador + Punto_D-C).

Este es un camino alternativo al Excel de Gridfy — el conversor genera
el Excel Gridfy internamente y luego sigue el mismo flujo de validación.
"""
import os
import shutil

from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Depends
from fastapi.responses import Response
from sqlalchemy.orm import Session

from db.session import get_db
from db import crud
from grid_network.gis_converter import convert_gis_to_gridfy_excel, validate_gis_files
from grid_network.builder import build_network
from grid_api.routes_network import set_net
from grid_network.manager import network_manager
from paths import to_relative, REDES_DIR

router = APIRouter()


@router.post("/gis/import")
async def import_from_gis(
    tramos:      UploadFile = File(..., description="CSV Tramo_baja del GIS"),
    trafo:       UploadFile = File(..., description="CSV Transformador del GIS"),
    loads:       UploadFile = File(None, description="CSV Punto_D-C del GIS (opcional)"),
    gens:        UploadFile = File(None, description="CSV Punto_G-D del GIS (opcional, generadores)"),
    name:        str        = Form(...),
    description: str        = Form(""),
    voltage:     str        = Form("BT"),
    decisions:   str        = Form("{}"),
    utm_zone:    str        = Form("auto"),
    db:          Session    = Depends(get_db),
):
    """
    Importa una red desde CSVs GIS y la registra como red Gridfy.
    Internamente convierte a Excel Gridfy, valida y carga.
    """
    if crud.get_network_by_name(db, name):
        raise HTTPException(409, f"Ya existe una red con el nombre '{name}'")

    # Read file contents
    tramos_bytes = await tramos.read()
    trafo_bytes  = await trafo.read()
    loads_bytes  = (await loads.read()) if loads else None
    gens_bytes   = (await gens.read())  if gens  else None

    # Validate GIS structure
    errors = validate_gis_files(tramos_bytes, trafo_bytes, loads_bytes)
    if errors:
        raise HTTPException(422, {"stage": "gis_validation", "errors": errors})

    # Convert to Gridfy Excel
    try:
        zone = None if utm_zone == "auto" else int(utm_zone)
        excel_bytes = convert_gis_to_gridfy_excel(tramos_bytes, trafo_bytes,
                                                   loads_bytes or b"", utm_zone=zone,
                                                   gens_bytes=gens_bytes)
    except Exception as e:
        raise HTTPException(422, {"stage": "conversion", "errors": [str(e)]})

    # Save Excel to disk
    safe_name  = name.replace(" ", "_").replace("/", "_")
    net_folder = os.path.join(REDES_DIR, safe_name)
    os.makedirs(net_folder, exist_ok=True)
    excel_path = os.path.join(net_folder, "datos.xlsx")
    with open(excel_path, "wb") as f:
        f.write(excel_bytes)

    # Also save original GIS files for reference
    gis_folder = os.path.join(net_folder, "gis_original")
    os.makedirs(gis_folder, exist_ok=True)
    with open(os.path.join(gis_folder, "Tramo_baja.csv"),   "wb") as f: f.write(tramos_bytes)
    with open(os.path.join(gis_folder, "Transformador.csv"), "wb") as f: f.write(trafo_bytes)
    if loads_bytes:
        with open(os.path.join(gis_folder, "Punto_DC.csv"), "wb") as f: f.write(loads_bytes)
    if gens_bytes:
        with open(os.path.join(gis_folder, "Punto_GD.csv"), "wb") as f: f.write(gens_bytes)

    # Parse decisions from frontend
    import json as _json
    try:
        dec = _json.loads(decisions)
    except Exception:
        dec = {}

    # Validate pandapower build
    try:
        net = build_network(excel_path)
    except Exception as e:
        shutil.rmtree(net_folder, ignore_errors=True)
        raise HTTPException(422, {"stage": "build", "errors": [str(e)]})

    # ── Apply user decisions to the network ──────────────────────────────────
    import pandapower as _pp

    # 1. Open loop lines chosen by user
    opened_lines = dec.get("opened_lines", [])
    if opened_lines:
        loop_lines = net.get("_loop_lines", [])
        for line_name in opened_lines:
            # Find line by name
            mask = net.line["name"] == line_name
            if mask.any():
                net.line.loc[mask, "in_service"] = False
                print(f"  [import] Línea abierta: {line_name}")
        # If no specific lines chosen, open all detected loops
    elif net.get("_loop_lines"):
        # Auto-open all detected loops (default behaviour)
        for lid in net.get("_loop_lines", []):
            if lid in net.line.index:
                net.line.at[lid, "in_service"] = False
                print(f"  [import] Auto-abriendo bucle #{lid}: {net.line.at[lid,'name']}")

    # 2. Handle island actions
    island_actions = dec.get("island_actions", [])
    # Islands were already handled at build time (connected to nearest)
    # Here we apply any manual overrides
    for act in island_actions:
        if act.get("action") == "delete":
            # Mark buses of this island as out of service
            # (already removed by builder if unsupplied)
            pass
        elif act.get("action") in ("connect", "move") and act.get("target_bus"):
            from grid_network.conductor_db import get_conductor_params
            target    = act.get("target_bus")
            source    = act.get("connect_from")
            conductor = act.get("conductor", "MANGUERA CU(4X6)")
            dist_m    = float(act.get("dist_m") or 10)
            length_km = max(dist_m / 1000.0, 0.001)

            if target and source:
                t_mask = net.bus["name"] == target
                s_mask = net.bus["name"] == source
                # If source not in network (island bus was removed), find nearest in-model bus
                if not s_mask.any():
                    # Use any bus from the island that survived
                    s_mask = net.bus["name"].str.startswith(source[:10])
                if t_mask.any() and s_mask.any():
                    t_idx = int(net.bus[t_mask].index[0])
                    s_idx = int(net.bus[s_mask].index[0])
                    cond  = get_conductor_params(conductor)
                    new_idx = _pp.create_line_from_parameters(
                        net,
                        from_bus      = s_idx,
                        to_bus        = t_idx,
                        length_km     = length_km,
                        r_ohm_per_km  = cond["r_ohm_per_km"],
                        x_ohm_per_km  = cond["x_ohm_per_km"],
                        c_nf_per_km   = 0.0,
                        max_i_ka      = cond["i_max_ka"],
                        name          = f"CONN_{source[-8:]}_{target[-8:]}",
                        in_service    = True,
                    )
                    # Preservar el nombre del conductor y la sección para el Excel descargable
                    net.line.at[new_idx, "conductor"]   = cond.get("name") or conductor
                    if cond.get("seccion_mm2") is not None:
                        net.line.at[new_idx, "seccion_mm2"] = float(cond["seccion_mm2"])
                    print(f"  [import] Nueva línea: {source[-12:]} → {target[-12:]} "
                          f"| {conductor} | {dist_m:.0f}m")
                else:
                    print(f"  [import] WARNING: no se encontró bus origen/destino para conexión")

    # Save the modified network back to Excel
    # Re-mark loop lines in the modified net
    open_loop_ids = [int(i) for i in net.line[net.line["in_service"]==False].index 
                     if i in (net.get("_loop_lines") or [])]
    net["_loop_lines"] = [i for i in (net.get("_loop_lines") or []) 
                          if i not in open_loop_ids] + open_loop_ids

    # Register in DB (ruta relativa para portabilidad)
    db_net = crud.create_network(
        db, name=name, description=description,
        voltage=voltage, excel_path=to_relative(excel_path),
    )

    # Load the already-built (and decisions-applied) net directly into memory
    network_manager._net        = net
    network_manager._network_id = db_net.id
    network_manager._name       = db_net.name
    set_net(net)

    return {
        "ok":      True,
        "id":      db_net.id,
        "name":    db_net.name,
        "source":  "GIS import",
        "buses":   len(net.bus),
        "lines":   len(net.line),
        "trafos":  len(net.trafo),
        "loads":   len(net.load),
    }


@router.post("/gis/preview")
async def preview_gis_conversion(
    tramos: UploadFile = File(...),
    trafo:  UploadFile = File(...),
    loads:  UploadFile = File(None),
    gens:   UploadFile = File(None),
):
    """
    Convierte GIS a Excel Gridfy y lo devuelve para descarga/previsualización,
    sin registrar en BD. Útil para revisar antes de importar definitivamente.
    """
    tramos_bytes = await tramos.read()
    trafo_bytes  = await trafo.read()
    loads_bytes  = (await loads.read()) if loads else b""
    gens_bytes   = (await gens.read())  if gens  else None

    errors = validate_gis_files(tramos_bytes, trafo_bytes, loads_bytes or None)
    if errors:
        raise HTTPException(422, {"errors": errors})

    try:
        excel_bytes = convert_gis_to_gridfy_excel(tramos_bytes, trafo_bytes, loads_bytes,
                                                   utm_zone=None, gens_bytes=gens_bytes)
    except Exception as e:
        raise HTTPException(422, {"errors": [str(e)]})

    return Response(
        content=excel_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="GIS_Preview_Gridfy.xlsx"'}
    )


# ── POST /api/gis/diagnose ────────────────────────────────────────────────────
from pydantic import BaseModel as _BM
from typing import List as _L, Dict as _D, Any as _A

@router.post("/gis/diagnose")
async def diagnose_gis(
    tramos: UploadFile = File(...),
    trafo:  UploadFile = File(...),
    loads:  UploadFile = File(None),
    gens:   UploadFile = File(None),
):
    """
    Analiza los CSVs GIS y devuelve un informe de problemas SIN importar la red.
    El usuario puede revisar y decidir qué hacer antes de importar.
    """
    import networkx as nx
    import pandapower.topology as top

    tramos_bytes = await tramos.read()
    trafo_bytes  = await trafo.read()
    loads_bytes  = (await loads.read()) if loads else b""
    gens_bytes   = (await gens.read())  if gens  else None

    errors = validate_gis_files(tramos_bytes, trafo_bytes, loads_bytes or None)
    if errors:
        raise HTTPException(422, {"errors": errors})

    # Convert to excel and build network for diagnosis
    try:
        excel_bytes = convert_gis_to_gridfy_excel(tramos_bytes, trafo_bytes, loads_bytes,
                                                   utm_zone=None, gens_bytes=gens_bytes)
    except Exception as e:
        raise HTTPException(422, {"errors": [str(e)]})

    import tempfile, os
    with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as tmp:
        tmp.write(excel_bytes)
        tmp_path = tmp.name

    try:
        from grid_network.conductor_db import get_conductor_params, _load_db, _conductor_db
        import pandas as pd

        # ── 1. Island detection (groups of disconnected buses) ───────────────
        df_term = pd.read_excel(tmp_path, sheet_name='Terminal')
        df_lin  = pd.read_excel(tmp_path, sheet_name='Lineas')
        df_tr   = pd.read_excel(tmp_path, sheet_name='Transformadores')

        # Build bus coordinate lookup
        bus_xy = {str(r['Nombre']): (float(r.get('X',0)), float(r.get('Y',0)))
                  for _, r in df_term.iterrows()}

        # Build graph
        G_full = nx.Graph()
        for _, row in df_term.iterrows():
            G_full.add_node(str(row['Nombre']))
        for _, row in df_lin.iterrows():
            G_full.add_edge(str(row['Terminal_i']), str(row['Terminal_j']),
                            length=float(row.get('Longitud (km)', 0.001)))

        trafo_lv = str(df_tr['secondary_node_code'].iloc[0]) if len(df_tr) > 0 else None
        trafo_hv = str(df_tr['primary_node_code'].iloc[0]) if len(df_tr) > 0 else None

        # Find main component (connected to trafo)
        main_comp = set()
        if trafo_lv and trafo_lv in G_full:
            main_comp = nx.node_connected_component(G_full, trafo_lv)

        # Find all disconnected islands (excluding trafo HV which connects via trafo element)
        trafo_nodes = {trafo_lv, trafo_hv} - {None}
        all_comps   = list(nx.connected_components(G_full))
        islands_raw = [c for c in all_comps if not (c & main_comp) and not (c <= trafo_nodes)]

        # For each island find nearest main-comp bus and centroid
        unsupplied_names = []  # renamed to islands for clarity but keep key for compat
        for island in islands_raw:
            buses_list = list(island)
            # Centroid of island
            xs = [bus_xy[b][0] for b in buses_list if b in bus_xy and bus_xy[b][0]]
            ys = [bus_xy[b][1] for b in buses_list if b in bus_xy and bus_xy[b][1]]
            cx = sum(xs)/len(xs) if xs else 0
            cy = sum(ys)/len(ys) if ys else 0

            # Find nearest bus in main component to any island bus
            nearest_main = None
            nearest_island_bus = None
            min_dist = float('inf')
            for ib in buses_list:
                ix, iy = bus_xy.get(ib, (0,0))
                if not ix: continue
                for mb in main_comp:
                    mx, my = bus_xy.get(mb, (0,0))
                    if not mx: continue
                    d = ((ix-mx)**2 + (iy-my)**2)**0.5
                    if d < min_dist:
                        min_dist = d
                        nearest_main = mb
                        nearest_island_bus = ib

            # Find suggested conductor: look at lines connected to nearest_main bus
            suggested_conductor = 'MANGUERA CU(4X6)'  # default
            try:
                clean_ids = df_tramos_raw['Identificador'].str.strip().str.strip("'")
                # Find lines where nearest_main is Nudo inicio or Nudo fin
                ni_clean = df_tramos_raw['Nudo inicio'].str.strip().str.strip("'")
                nf_clean = df_tramos_raw['Nudo fin'].str.strip().str.strip("'")
                conn_mask = (ni_clean == nearest_main) | (nf_clean == nearest_main)
                if conn_mask.any():
                    cond_val = df_tramos_raw[conn_mask].iloc[0].get('Conductor', '')
                    if cond_val and str(cond_val).strip().strip("'") not in ('', 'nan'):
                        suggested_conductor = str(cond_val).strip().strip("'")
            except Exception:
                pass

            unsupplied_names.append({
                'island_id':           len(unsupplied_names),
                'bus_count':           len(buses_list),
                'buses':               buses_list,
                'centroid_x':          cx, 'centroid_y': cy,
                'connect_from':        nearest_island_bus,
                'connect_from_x':      bus_xy.get(nearest_island_bus,(0,0))[0],
                'connect_from_y':      bus_xy.get(nearest_island_bus,(0,0))[1],
                'nearest_bus':         nearest_main,
                'nearest_bus_x':       bus_xy.get(nearest_main,(0,0))[0],
                'nearest_bus_y':       bus_xy.get(nearest_main,(0,0))[1],
                'nearest_dist_m':      round(min_dist, 1),
                'suggested_conductor': suggested_conductor,
                'bus':                 nearest_island_bus or (buses_list[0] if buses_list else ''),
                'x': bus_xy.get(nearest_island_bus,(cx,cy))[0],
                'y': bus_xy.get(nearest_island_bus,(cx,cy))[1],
            })

        # ── 2. Loop lines ─────────────────────────────────────────────────────
        loop_lines = []
        sg = nx.Graph()
        for _, row in df_lin.iterrows():
            sg.add_edge(str(row['Terminal_i']), str(row['Terminal_j']),
                       line_name=str(row['Nombre']), length=float(row.get('Longitud (km)',0.001)))
        cycles = nx.cycle_basis(sg)
        seen_lines = set()
        for cycle in cycles:
            cycle_lines = []
            for i in range(len(cycle)):
                u, v = cycle[i], cycle[(i+1)%len(cycle)]
                if sg.has_edge(u,v):
                    ln = sg[u][v].get('line_name','')
                    if ln not in seen_lines:
                        cycle_lines.append({'line': ln, 'from': u, 'to': v,
                                           'length_m': round(sg[u][v].get('length',0)*1000,1)})
                        seen_lines.add(ln)
            if cycle_lines:
                loop_lines.append({'cycle': [c for c in cycle], 'lines': cycle_lines})

        # ── 3. Parallel lines ─────────────────────────────────────────────────
        parallel = []
        pair_count: dict = {}
        for _, row in df_lin.iterrows():
            u,v = str(row['Terminal_i']), str(row['Terminal_j'])
            key = tuple(sorted([u,v]))
            pair_count[key] = pair_count.get(key,[])
            pair_count[key].append(str(row['Nombre']))
        for pair, lines in pair_count.items():
            if len(lines) > 1:
                parallel.append({'from': pair[0], 'to': pair[1], 'lines': lines})

        # ── 4. Unknown conductors ─────────────────────────────────────────────
        _load_db()
        from grid_network.conductor_db import _normalize
        df_tramos_raw = pd.read_csv(
            __import__('io').BytesIO(tramos_bytes), sep=None, engine='python',
            dtype=str, encoding='utf-8-sig'
        )
        df_tramos_raw.columns = [c.lstrip('\ufeff') for c in df_tramos_raw.columns]
        unknown_conductors = []
        seen_cond = set()
        for _, row in df_tramos_raw.iterrows():
            cond = str(row.get('Conductor','')).strip().strip("'")
            if not cond or cond in seen_cond or cond=='nan': continue
            seen_cond.add(cond)
            norm = _normalize(cond)
            found = norm in _conductor_db or any(norm in k or k in norm for k in _conductor_db)
            if not found:
                unknown_conductors.append(cond)

        # ── Build network data for map rendering ─────────────────────────────
        # All buses with coordinates
        buses_map = []
        bus_coords = {}  # name -> (x, y)
        for _, row in df_term.iterrows():
            name = str(row['Nombre'])
            x, y = float(row.get('X',0)), float(row.get('Y',0))
            bus_coords[name] = (x, y)
            island_bus_set = set(b for u in unsupplied_names for b in u.get('buses',[]))
            status = 'unsupplied' if name in island_bus_set else 'ok'
            buses_map.append({'name': name, 'x': x, 'y': y, 'status': status})

        # All lines with from/to coords
        loop_line_names = set(l['line'] for loop in loop_lines for l in loop['lines'])
        lines_map = []
        for _, row in df_lin.iterrows():
            name   = str(row['Nombre'])
            fi, ti = str(row['Terminal_i']), str(row['Terminal_j'])
            fc     = bus_coords.get(fi, (0,0))
            tc     = bus_coords.get(ti, (0,0))
            if fc == (0,0) or tc == (0,0): continue
            # Get conductor from original tramos CSV
            conductor = ''
            length_m  = 0.0
            try:
                # Strip apostrophes from both sides for matching
                clean_ids = df_tramos_raw['Identificador'].str.strip().str.strip("'")
                # Name may have suffix _N for parallel lines — try base name first
                base_name = name.rsplit('_', 1)[0] if '_' in name else name
                mask = (clean_ids == name) | (clean_ids == base_name)
                if mask.any():
                    row_t = df_tramos_raw[mask].iloc[0]
                    conductor = str(row_t.get('Conductor', '')).strip().strip("'")
                    raw_len = str(row_t.get('Longitud real (m)',
                                  row_t.get('Longitud calculada (m)', 0)))
                    length_m = float(raw_len.replace(',','.').lstrip("'"))
            except Exception:
                pass
            lines_map.append({
                'name':        name,
                'from_x':      fc[0], 'from_y': fc[1],
                'to_x':        tc[0], 'to_y':   tc[1],
                'is_loop':     name in loop_line_names,
                'is_parallel': any(name in p['lines'] for p in parallel),
                'conductor':   conductor,
                'length_m':    round(length_m, 1),
            })

        # ── Summary ───────────────────────────────────────────────────────────
        return {
            "ok": True,
            "summary": {
                "buses":             len(df_term),
                "lines":             len(df_lin),
                "unsupplied_count":  len(unsupplied_names),
                "unsupplied_buses":   sum(i["bus_count"] for i in unsupplied_names),
                "loop_count":        len(loop_lines),
                "parallel_count":    len(parallel),
                "unknown_conductors_count": len(unknown_conductors),
            },
            "unsupplied":         unsupplied_names,
            "loops":              loop_lines,
            "parallel":           parallel,
            "unknown_conductors": unknown_conductors,
            "buses_map":          buses_map,
            "lines_map":          lines_map,
            "excel_ready":        True,
        }
    finally:
        os.unlink(tmp_path)
