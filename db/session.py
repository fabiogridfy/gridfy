"""
db/session.py
Conexión a SQLite y creación de tablas.
"""
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from db.models import Base

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "gridfy.db")
DB_URL  = f"sqlite:///{os.path.abspath(DB_PATH)}"

engine        = create_engine(DB_URL, connect_args={"check_same_thread": False})
SessionLocal  = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def init_db():
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
