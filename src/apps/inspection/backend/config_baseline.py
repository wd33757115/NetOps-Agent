"""Authorized internal reading of one baseline snapshot's selected configuration block."""

import hashlib
from datetime import timezone
from ipaddress import ip_address

from fastapi import HTTPException
from pydantic import Field, field_validator
from sqlalchemy import select

from src.infrastructure.db.models import CommandSnapshot, NetworkDevice, PatrolRun
from src.infrastructure.db.postgres import get_db_session
from src.platform.device_access.config_sections import Scope, extract_section, redact_config

from .diagnostics import iso


class ReadConfigSection(Scope):
    snapshot_id: str = Field(min_length=1, max_length=64)
    host: str = Field(min_length=1, max_length=64)

    @field_validator("host")
    @classmethod
    def literal_address(cls, value):
        return str(ip_address(value))


def read_config_section(user, body: ReadConfigSection):
    """The registry supplies user after inspection App authorization; no collection."""
    scope = Scope.model_validate(
        {key: getattr(body, key) for key in ("platform", "section", "target")}
    )
    with get_db_session() as db:
        snapshot = db.get(CommandSnapshot, body.snapshot_id)
        if snapshot is None:
            raise HTTPException(404, "基线快照不存在或不属于目标资产")
        run = db.get(PatrolRun, snapshot.run_id)
        if run is None:
            raise HTTPException(404, "基线快照所属巡检不存在")
        try:
            recorded_host = (
                str(ip_address(snapshot.management_ip)) if snapshot.management_ip else None
            )
        except ValueError:
            recorded_host = None
        if snapshot.inventory_device_id:
            # A conflicting/missing current inventory binding must not fall back
            # to historical IP matching or device-name inference.
            assets = db.scalars(
                select(NetworkDevice.id)
                .where(
                    NetworkDevice.management_ip == body.host,
                    NetworkDevice.status != "deleted",
                )
                .limit(2)
            ).all()
            if (
                len(assets) != 1
                or snapshot.inventory_device_id != assets[0]
                or (snapshot.management_ip and recorded_host != body.host)
            ):
                raise HTTPException(404, "基线快照不存在或不属于目标资产")
            device_id, identity_source = assets[0], "inventory_device_id"
        else:
            if run.mode != "offline" or recorded_host != body.host:
                raise HTTPException(404, "基线快照不存在或不属于目标地址")
            device_id, identity_source = snapshot.device_id, "snapshot_management_ip"
        raw = snapshot.raw_text or ""
        hash_valid = hashlib.sha256(raw.encode("utf-8")).hexdigest() == snapshot.raw_text_hash
        # Parsing need not succeed for a text configuration comparison. Transport
        # completeness, retained full text and independently checked block borders do.
        complete = (
            hash_valid and snapshot.snapshot_kind == "full" and snapshot.output_status == "data"
        )
        fragment = extract_section(
            raw, scope, source_complete=complete, command=snapshot.canonical_command
        )
        if not hash_valid:
            fragment["quality_gaps"].append("snapshot_hash_mismatch")
        if snapshot.snapshot_kind != "full":
            fragment["quality_gaps"].append("snapshot_not_full")
        if snapshot.output_status != "data":
            fragment["quality_gaps"].append(
                "snapshot_output_quality:" + str(snapshot.output_status)
            )
        return {
            "scope": scope.model_dump(),
            "fragment": fragment,
            "baseline": {
                "snapshot_id": snapshot.id,
                "run_id": snapshot.run_id,
                "device_id": device_id,
                "identity_source": identity_source,
                "identity_limitation": "仅历史地址匹配，不能证明地址未复用或当前物理身份"
                if identity_source == "snapshot_management_ip"
                else None,
                "host": body.host,
                "command": redact_config(snapshot.command),
                "observed_at": iso(
                    snapshot.observed_at
                    if snapshot.observed_at.tzinfo
                    else snapshot.observed_at.replace(tzinfo=timezone.utc)
                ),
                "source_sha256": snapshot.raw_text_hash,
                "parse_status": snapshot.parse_status,
                "output_status": snapshot.output_status,
                "snapshot_kind": snapshot.snapshot_kind,
                "run_mode": run.mode,
                "run_source": run.source,
                "run_purpose": run.run_purpose,
                "run_status": run.status,
                "baseline_eligible": run.baseline_eligible,
            },
            "meaning": "按指定 snapshot_id 读取原始基线中的同范围配置块；未重选基线。"
            "基线可用不等于健康，离线或历史配置不代表当前状态，隐藏字段不参与比较。",
        }
