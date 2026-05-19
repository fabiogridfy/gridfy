"""
migrate_paths.py
Migración puntual: reescribe las rutas absolutas de la BD para que apunten
a la ubicación correcta de redes/ en esta máquina.

Uso:
    python migrate_paths.py            # ejecuta migración (con backup)
    python migrate_paths.py --dry-run  # solo muestra qué haría
"""
import os
import sys
import shutil

from db.session import SessionLocal, init_db
from db.models import Network, Scenario


PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
REDES_DIR   = os.path.join(PROJECT_DIR, "redes")
DB_PATH     = os.path.join(PROJECT_DIR, "gridfy.db")


def safe_name(name: str) -> str:
    return name.replace(" ", "_").replace("/", "_")


def resolve_excel_path(net_name: str) -> str | None:
    p = os.path.join(REDES_DIR, safe_name(net_name), "datos.xlsx")
    return p if os.path.exists(p) else None


def resolve_pickle_path(net_name: str) -> str | None:
    p = os.path.join(REDES_DIR, safe_name(net_name), "modificada.p")
    return p if os.path.exists(p) else None


def resolve_scenario_excel(net_name: str, scenario_name: str) -> str | None:
    safe_sc = scenario_name.replace(" ", "_").replace("/", "_")
    candidates = [
        os.path.join(REDES_DIR, safe_name(net_name), "escenarios", f"{safe_sc}.xlsx"),
        os.path.join(REDES_DIR, safe_name(net_name), "escenarios", f"{safe_sc}.xls"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    folder = os.path.join(REDES_DIR, safe_name(net_name), "escenarios")
    if os.path.isdir(folder):
        for f in os.listdir(folder):
            if f.lower().startswith(safe_sc.lower()) and f.lower().endswith((".xlsx", ".xls")):
                return os.path.join(folder, f)
    return None


def main(dry_run: bool = False):
    print(f"[migrate] Proyecto: {PROJECT_DIR}")
    print(f"[migrate] BD:       {DB_PATH}")
    print(f"[migrate] Modo:     {'DRY-RUN (no escribe)' if dry_run else 'ESCRITURA'}")
    print()

    if not os.path.exists(DB_PATH):
        print("[migrate] ERROR: no se encuentra gridfy.db — arranca la app al menos una vez.")
        sys.exit(1)

    # Backup
    if not dry_run:
        backup = DB_PATH + ".bak"
        shutil.copy2(DB_PATH, backup)
        print(f"[migrate] Backup creado: {backup}")

    init_db()
    db = SessionLocal()
    try:
        networks = db.query(Network).all()
        sc_total = 0
        net_changes  = 0
        sc_changes   = 0
        net_skipped  = 0
        sc_skipped   = 0

        print(f"[migrate] Redes en BD: {len(networks)}")
        print()

        for n in networks:
            new_excel  = resolve_excel_path(n.name)
            new_pickle = resolve_pickle_path(n.name)

            excel_changed  = new_excel  is not None and n.excel_path  != new_excel
            pickle_changed = new_pickle is not None and n.pickle_path != new_pickle

            if not new_excel:
                print(f"  [!] Red '{n.name}' (id={n.id}): carpeta no encontrada en redes/ — SKIP")
                net_skipped += 1
                continue

            if excel_changed or pickle_changed:
                print(f"  [~] Red '{n.name}' (id={n.id})")
                if excel_changed:
                    print(f"        excel:  {n.excel_path}")
                    print(f"        →       {new_excel}")
                if pickle_changed:
                    print(f"        pickle: {n.pickle_path}")
                    print(f"        →       {new_pickle}")
                if not dry_run:
                    n.excel_path = new_excel
                    if new_pickle:
                        n.pickle_path = new_pickle
                net_changes += 1
            else:
                print(f"  [=] Red '{n.name}' (id={n.id}): rutas OK")

            # Escenarios de esta red
            scenarios = db.query(Scenario).filter(Scenario.network_id == n.id).all()
            for s in scenarios:
                sc_total += 1
                new_sc = resolve_scenario_excel(n.name, s.name)
                if not new_sc:
                    print(f"     [!] Escenario '{s.name}' (id={s.id}): archivo no encontrado — SKIP")
                    sc_skipped += 1
                    continue
                if s.excel_path != new_sc:
                    print(f"     [~] Escenario '{s.name}' (id={s.id})")
                    print(f"            {s.excel_path}")
                    print(f"            → {new_sc}")
                    if not dry_run:
                        s.excel_path = new_sc
                    sc_changes += 1

        if not dry_run:
            db.commit()
            print()
            print(f"[migrate] OK. Redes modificadas: {net_changes}/{len(networks)} "
                  f"(skip: {net_skipped})")
            print(f"[migrate] OK. Escenarios modificados: {sc_changes}/{sc_total} "
                  f"(skip: {sc_skipped})")
        else:
            print()
            print(f"[migrate] DRY-RUN. Cambiaría: {net_changes} redes, {sc_changes} escenarios")
            print(f"[migrate] Skip: {net_skipped} redes, {sc_skipped} escenarios sin archivo")
    finally:
        db.close()


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    main(dry_run=dry_run)
