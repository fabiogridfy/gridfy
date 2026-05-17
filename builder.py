"""
builder.py
Construye la red pandapower a partir del Excel.
Responsabilidad única: leer datos → crear net.
"""
import utm
import pandas as pd
import pandapower as pp


def find_bus(df_Terminal: pd.DataFrame, name: str):
    """Busca un bus por nombre exacto; si no lo encuentra, por coincidencia parcial."""
    exact = df_Terminal[df_Terminal["Nombre"] == name]
    if not exact.empty:
        return int(exact.index[0])
    for idx, row in df_Terminal.iterrows():
        if str(name) in str(row["Nombre"]) or str(row["Nombre"]) in str(name):
            return int(idx)
    return None


def build_network(excel_path: str, timezone: int = 30, geozone: str = "N") -> pp.pandapowerNet:
    """
    Lee el Excel y devuelve una red pandapower lista para simular.

    Hojas usadas: Terminal, ExternalGrid, Lineas, Transformadores, Loads_Data, Gen_Data
    """
    # ── Leer hojas ────────────────────────────────────────────────────────────
    df_Terminal = pd.read_excel(excel_path, sheet_name="Terminal")
    df_Lines    = pd.read_excel(excel_path, sheet_name="Lineas")
    df_ext_grid = pd.read_excel(excel_path, sheet_name="ExternalGrid")
    df_trafo    = pd.read_excel(excel_path, sheet_name="Transformadores")

    net = pp.create_empty_network()

    # ── Buses ─────────────────────────────────────────────────────────────────
    for _, x in df_Terminal.iterrows():
        lat, lon = utm.to_latlon(x["X"], x["Y"], timezone, geozone)
        pp.create_bus(net, vn_kv=x["Tensión"], name=x["Nombre"], geodata=(lon, lat))

    # helper con df_Terminal capturado
    def fb(name):
        return find_bus(df_Terminal, name)

    # ── External grid ─────────────────────────────────────────────────────────
    for _, x in df_ext_grid.iterrows():
        b = fb(x["Terminal"])
        if b is not None:
            pp.create_ext_grid(net, bus=b, vm_pu=1.0, name=x["Nombre"],
                               s_sc_max_mva=1000, rx_max=0.1, x0x_max=1, r0x0_max=0.1)

    # ── Líneas ────────────────────────────────────────────────────────────────
    for _, x in df_Lines.iterrows():
        try:
            pp.create_line_from_parameters(
                net,
                from_bus=fb(x["Terminal_i"]),
                to_bus=fb(x["Terminal_j"]),
                length_km=x["Longitud (km)"],
                r_ohm_per_km=x["R/km"],
                x_ohm_per_km=x["X/km"],
                r0_ohm_per_km=3 * x["R/km"],
                x0_ohm_per_km=3 * x["X/km"],
                c_nf_per_km=0,
                c0_nf_per_km=0,
                max_i_ka=x["I max (kA)"],
                name=x["Nombre"],
            )
        except Exception as e:
            print(f"  [builder] Línea {_} error: {e}")

    # ── Transformadores ───────────────────────────────────────────────────────
    for _, x in df_trafo.iterrows():
        try:
            pp.create_transformer_from_parameters(
                net,
                hv_bus=fb(x["primary_node_code"]),
                lv_bus=fb(x["secondary_node_code"]),
                sn_mva=x["nominal_apparent_power_kVA"] * 1e-3,
                vn_hv_kv=x["Tension HV"],
                vn_lv_kv=x["Tension LV"],
                vkr_percent=x["vkr_percent"],
                vk_percent=x["vk_percent"],
                pfe_kw=x["pfe_kw"],
                i0_percent=x["i0_percent"],
                name=x["code"],
                vector_group="Dyn",
                vk0_percent=4, vkr0_percent=1.03174,
                tap_neutral=0, tap_pos=0, tap_min=-2, tap_max=2,
                 tap_step_percent=2.5, tap_side="hv",
                 mag0_percent=100, mag0_rx=0, si0_hv_partial=0.9,
            )
        except Exception as e:
            print(f"  [builder] Trafo {_} error: {e}")

    # ── Cargas (Loads_Data) ───────────────────────────────────────────────────
    try:
        df_loads = pd.read_excel(excel_path, sheet_name="Loads_Data")
        for _, x in df_loads.iterrows():
            b = fb(x["Terminal"])
            if b is None:
                print(f"  [builder] Load '{x.get('CUPS', _)}': bus no encontrado")
                continue
            # P(MW) tiene prioridad; si vacío usa Pot.contratada como nominal (no operacional)
            p_mw   = float(x["P (MW)"])   if pd.notna(x.get("P (MW)"))   else 0.0
            q_mvar = float(x["Q (MVAR)"]) if pd.notna(x.get("Q (MVAR)")) else 0.0
            nom_p  = float(x.get("Pot. contratada (kW)", 0)) * 1e-3
            pp.create_load(net, bus=b, name=str(x.get("CUPS", f"load_{_}")),
                           p_mw=p_mw, q_mvar=q_mvar,
                           max_p_mw=nom_p)   # guardamos la potencia contratada como límite
        print(f"  [builder] Cargas: {len(net.load)}")
    except Exception as e:
        print(f"  [builder] Loads_Data error: {e}")

    # ── Generadores (Gen_Data) ────────────────────────────────────────────────
    try:
        df_gen = pd.read_excel(excel_path, sheet_name="Gen_Data")
        for _, x in df_gen.iterrows():
            b = fb(x["Terminal"])
            if b is None:
                print(f"  [builder] Gen '{x.get('CUPS', _)}': bus no encontrado")
                continue
            p_mw   = float(x["P (MW)"])   if pd.notna(x.get("P (MW)"))   else 0.0
            q_mvar = float(x["Q (MVAR)"]) if pd.notna(x.get("Q (MVAR)")) else 0.0
            nom_p  = float(x.get("Pot. contratada (kW)", 0)) * 1e-3
            pp.create_sgen(net, bus=b, name=str(x.get("CUPS", f"gen_{_}")),
                           p_mw=p_mw, q_mvar=q_mvar,
                           max_p_mw=nom_p)
        print(f"  [builder] Generadores: {len(net.sgen)}")
    except Exception as e:
        print(f"  [builder] Gen_Data error: {e}")

    # ── Detect and tag isolated buses (no supply) ───────────────────────────
    import pandapower.topology as _top
    import networkx as _nx

    unsupplied = _top.unsupplied_buses(net)
    if unsupplied:
        print(f"  [builder] {len(unsupplied)} buses sin suministro — se eliminan del modelo")
        isolated = set(int(b) for b in unsupplied)
        # Remove elements on isolated buses
        net.load = net.load[~net.load["bus"].isin(isolated)].reset_index(drop=True)
        if len(net.sgen) > 0:
            net.sgen = net.sgen[~net.sgen["bus"].isin(isolated)].reset_index(drop=True)
        # Remove lines touching isolated buses
        net.line = net.line[
            ~net.line["from_bus"].isin(isolated) & ~net.line["to_bus"].isin(isolated)
        ].reset_index(drop=True)
        # Drop buses and reindex
        net.bus = net.bus.drop(index=list(isolated))
        old_to_new = {old: new for new, old in enumerate(net.bus.index)}
        net.bus = net.bus.reset_index(drop=True)
        for tbl, cols in [
            (net.line,    ["from_bus","to_bus"]),
            (net.load,    ["bus"]),
            (net.sgen,    ["bus"]),
            (net.trafo,   ["hv_bus","lv_bus"]),
            (net.ext_grid,["bus"]),
        ]:
            for col in cols:
                if col in tbl.columns:
                    tbl[col] = tbl[col].map(old_to_new)

    # ── Detect loops (more lines than buses-1) ────────────────────────────────
    n_buses = len(net.bus)
    n_lines = len(net.line)
    loop_lines = []
    if n_lines >= n_buses:
        mg = _top.create_nxgraph(net, respect_switches=False)
        sg = _nx.Graph()
        for u, v, data in mg.edges(data=True):
            if not sg.has_edge(u, v):
                sg.add_edge(u, v, **data)
        cycles = _nx.cycle_basis(sg)
        seen = set()
        for cycle in cycles:
            for i in range(len(cycle)):
                u, v = cycle[i], cycle[(i+1) % len(cycle)]
                mask = (
                    ((net.line["from_bus"]==u)&(net.line["to_bus"]==v)) |
                    ((net.line["from_bus"]==v)&(net.line["to_bus"]==u))
                )
                for idx in net.line[mask].index:
                    if idx not in seen:
                        loop_lines.append(int(idx))
                        seen.add(idx)
                        break  # one per cycle edge

    if loop_lines:
        print(f"  [builder] {len(loop_lines)} lineas forman bucles — marcadas para visualizacion")
        # Store in net for frontend to read
        net["_loop_lines"] = loop_lines
    else:
        net["_loop_lines"] = []

    # Remove self-loops (from_bus == to_bus after reindexing)
    self_loops = net.line[net.line["from_bus"] == net.line["to_bus"]]
    if len(self_loops) > 0:
        print(f"  [builder] Eliminando {len(self_loops)} self-loops")
        net.line = net.line[net.line["from_bus"] != net.line["to_bus"]].reset_index(drop=True)

    # Remove exact parallel duplicates keeping the one with lower resistance
    # (same from/to bus pair — causes singular Jacobian)
    net.line["_pair"] = net.line.apply(
        lambda r: tuple(sorted([int(r["from_bus"]), int(r["to_bus"])])), axis=1
    )
    dup_mask = net.line.duplicated(subset=["_pair"], keep=False)
    if dup_mask.any():
        n_dup = dup_mask.sum()
        # Keep lowest R per pair, mark rest as out of service
        idx_to_keep = net.line[dup_mask].groupby("_pair")["r_ohm_per_km"].idxmin()
        idx_to_open = set(net.line[dup_mask].index) - set(idx_to_keep)
        if idx_to_open:
            print(f"  [builder] {len(idx_to_open)} lineas paralelas exactas marcadas como bucles")
            existing = net.get("_loop_lines", [])
            net["_loop_lines"] = existing + [int(i) for i in idx_to_open]
    net.line = net.line.drop(columns=["_pair"])

    # Force tap to neutral — no tap adjustment during base model
    for tidx in net.trafo.index:
        net.trafo.at[tidx, 'tap_pos']     = 0
        net.trafo.at[tidx, 'tap_neutral']  = 0
        # Ensure LV voltage matches the BT bus nominal voltage exactly
        lv_bus = int(net.trafo.at[tidx, 'lv_bus'])
        if lv_bus in net.bus.index:
            net.trafo.at[tidx, 'vn_lv_kv'] = float(net.bus.at[lv_bus, 'vn_kv'])

    print(f"  [builder] Red lista: {len(net.bus)} buses | {len(net.line)} líneas | "
          f"{len(net.trafo)} trafos | {len(net.load)} cargas | {len(net.sgen)} sgens")
    return net
