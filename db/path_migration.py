"""
db/path_migration.py
Migración automática al arrancar: convierte rutas absolutas heredadas en la BD
a rutas relativas al PROJECT_ROOT, para que la BD sea portable entre máquinas.

Reescribe `excel_path` y `pickle_path` de Network y Scenario.
Solo toca filas cuya ruta sea absoluta y resoluble dentro de redes/.
"""
import os

from db.session import SessionLocal
from db.models import Network, Scenario
from paths import PROJECT_ROOT, REDES_DIR, to_relative


def _needs_migration(path: str | None) -> bool:
    return bool(path) and os.path.isabs(path)


def _resolve_legacy(legacy_path: str, net_name: str, kind: str) -> str | None:
    """
    Intenta resolver una ruta absoluta heredada a la equivalente local.
    1. Si la ruta absoluta YA apunta a un archivo existente local → la usa.
    2. Si no, intenta reconstruir por convención: redes/{safe_name}/{archivo}.
    Devuelve la ruta relativa lista para BD, o None si no se encuentra.
    """
    # Si el path absoluto resuelve a un archivo dentro del proyecto, basta con relativizar
    if os.path.exists(legacy_path) and os.path.abspath(legacy_path).startswith(PROJECT_ROOT):
        return to_relative(legacy_path)

    safe = net_name.replace(" ", "_").replace("/", "_")
    # Tomar el nombre de archivo de la ruta legacy
    filename = os.path.basename(legacy_path)

    if kind == "scenario":
        candidate = os.path.join(REDES_DIR, safe, "escenarios", filename)
    else:
        candidate = os.path.join(REDES_DIR, safe, filename)

    if os.path.exists(candidate):
        return to_relative(candidate)
    return None


def migrate_legacy_absolute_paths(verbose: bool = True) -> dict:
    """
    Recorre Network y Scenario. Convierte rutas absolutas a relativas.
    Devuelve {'networks': n_changed, 'scenarios': s_changed, 'skipped': skipped}.
    """
    db = SessionLocal()
    n_changed = s_changed = skipped = 0
    try:
        # Redes
        for n in db.query(Network).all():
            if _needs_migration(n.excel_path):
                new = _resolve_legacy(n.excel_path, n.name, "excel")
                if new:
                    n.excel_path = new
                    n_changed += 1
                else:
                    skipped += 1
                    if verbose:
                        print(f"[path-migration] Red '{n.name}': excel no resoluble — SKIP")

            if _needs_migration(n.pickle_path):
                new = _resolve_legacy(n.pickle_path, n.name, "pickle")
                if new:
                    n.pickle_path = new
                    n_changed += 1
                # No es error si no existe el pickle (red sin versión guardada)

        # Escenarios
        for s in db.query(Scenario).all():
            if _needs_migration(s.excel_path):
                # Necesitamos el nombre de la red para reconstruir la ruta
                net = db.query(Network).filter(Network.id == s.network_id).first()
                net_name = net.name if net else ""
                new = _resolve_legacy(s.excel_path, net_name, "scenario")
                if new:
                    s.excel_path = new
                    s_changed += 1
                else:
                    skipped += 1
                    if verbose:
                        print(f"[path-migration] Escenario '{s.name}': no resoluble — SKIP")

        if n_changed or s_changed:
            db.commit()
            if verbose:
                print(f"[path-migration] Normalizadas {n_changed} rutas de red, "
                      f"{s_changed} de escenarios")
        elif verbose:
            print("[path-migration] BD ya estaba normalizada")
    finally:
        db.close()
    return {"networks": n_changed, "scenarios": s_changed, "skipped": skipped}
