"""
db/models.py
Modelos de base de datos para Gridfy.
"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, Text, ForeignKey, Float, Boolean
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class Network(Base):
    __tablename__ = "networks"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    name        = Column(String(120), nullable=False, unique=True)
    description = Column(Text, default="")
    voltage     = Column(String(10), default="BT")   # BT / MT
    excel_path  = Column(String(500), nullable=False)
    pickle_path = Column(String(500), nullable=True)
    created_at  = Column(DateTime, default=datetime.utcnow)
    updated_at  = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    scenarios   = relationship("Scenario", back_populates="network", cascade="all, delete-orphan")


class Scenario(Base):
    __tablename__ = "scenarios"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    network_id  = Column(Integer, ForeignKey("networks.id"), nullable=False)
    name        = Column(String(120), nullable=False)
    description = Column(Text, default="")
    excel_path  = Column(String(500), nullable=False)  # ruta al Excel del escenario
    is_active   = Column(Boolean, default=False)        # escenario activo en esta sesión
    created_at  = Column(DateTime, default=datetime.utcnow)

    network     = relationship("Network", back_populates="scenarios")
