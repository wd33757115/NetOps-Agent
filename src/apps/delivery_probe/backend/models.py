from sqlalchemy import Column, DateTime, String, UniqueConstraint
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class Run(Base):
    __tablename__ = "app_delivery_probe_runs"
    id = Column(String(32), primary_key=True)
    owner_id = Column(String(64), nullable=False)
    request_id = Column(String(100), nullable=False)
    text = Column(String(500), nullable=False)
    status = Column(String(20), nullable=False)
    result = Column(String(500))
    lease_until = Column(DateTime(timezone=True))
    lease_token = Column(String(32))
    __table_args__ = (UniqueConstraint("owner_id", "request_id", name="uq_delivery_probe_request"),)
