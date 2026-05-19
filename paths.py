"""
paths.py
Resolución de rutas portable: la BD almacena rutas relativas al PROJECT_ROOT,
pero el código sigue trabajando con rutas absolutas. Estas dos funciones
median entre ambos mundos.
"""
import os


PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
REDES_DIR    = os.path.join(PROJECT_ROOT, "redes")


def resolve(path: str | None) -> str | None:
    """
    Devuelve una ruta absoluta válida desde un valor almacenado en BD.
    Acepta tanto rutas relativas (formato nuevo) como absolutas (formato legacy).
    """
    if not path:
        return path
    if os.path.isabs(path):
        return path
    return os.path.join(PROJECT_ROOT, path)


def to_relative(path: str | None) -> str | None:
    """
    Convierte una ruta a relativa desde PROJECT_ROOT si está dentro del proyecto.
    Si está fuera, la deja como está (caso raro, no debería ocurrir en flujos normales).
    Usa siempre separador '/' para portabilidad entre SO.
    """
    if not path:
        return path
    abs_path = os.path.abspath(path)
    try:
        rel = os.path.relpath(abs_path, PROJECT_ROOT)
    except ValueError:
        # Diferentes drives en Windows: imposible relativizar
        return path
    if rel.startswith(".."):
        # Fuera del proyecto: dejar como estaba
        return path
    return rel.replace(os.sep, "/")
