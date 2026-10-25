"""Request bodies. Blank text becomes None so optional fields stay optional."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SITE_KINDS = ("自建", "托管", "云", "分支", "门店", "合作方", "其他")
SPACE_KINDS = ("生产", "办公", "管理", "互联与 DMZ", "云", "其他")
ZONE_KINDS = ("生产", "测试", "开发", "灾备", "DMZ", "管理", "办公", "互联", "其他")
PREFIX_STATES = ("规划", "使用中", "保留", "待回收")
ADDRESS_KINDS = ("主机", "VIP", "设备管理口", "专线互联", "其他")
DIMENSIONS = ("region", "site", "zone", "system", "subsystem", "custom")
IMPORTANCE = ("核心", "重要", "一般")
ENVIRONMENTS = ("生产", "灾备", "测试", "开发", "其他")
HOST_STATES = ("规划中", "建设中", "待投产", "已投产", "待下线", "已下线")
HOST_KINDS = ("物理机", "虚拟机", "容器节点", "其他")
HOST_ROLES = ("Web", "应用", "数据库", "中间件", "缓存", "负载均衡", "其他")
PROTOCOLS = ("TCP", "UDP", "ICMP")
NETWORK_KINDS = ("MPLS VPN", "互联网", "其他")
RISK_KINDS = ("光缆", "管道", "入户", "电源", "设备", "其他")
CIRCUIT_STATES = ("规划", "施工中", "在用", "离线", "拆除中", "已拆除")
ROUTING = ("静态", "BGP", "OSPF", "其他")
DATE = r"^\d{4}-\d{2}-\d{2}$"


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @field_validator("*", mode="before")
    @classmethod
    def blank_is_none(cls, value):
        return None if isinstance(value, str) and not value.strip() else value


class RegionIn(Body):
    name: str = Field(min_length=1, max_length=64)
    note: str | None = Field(None, max_length=500)


class SiteIn(Body):
    region_id: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=64)
    kind: Literal[SITE_KINDS] = "自建"
    address: str | None = Field(None, max_length=200)
    note: str | None = Field(None, max_length=500)
    core: bool = False


class SpaceIn(Body):
    name: str = Field(min_length=1, max_length=64)
    kind: Literal[SPACE_KINDS] = "其他"
    note: str | None = Field(None, max_length=500)


class ZoneIn(Body):
    site_id: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=64)
    title: str | None = Field(None, max_length=64)
    kind: Literal[ZONE_KINDS] = "生产"
    security_level: str | None = Field(None, max_length=20)
    note: str | None = Field(None, max_length=500)


class LevelIn(Body):
    id: str | None = Field(None, max_length=32)
    dimension: Literal[DIMENSIONS]
    title: str = Field(min_length=1, max_length=32)
    default_length: int | None = Field(None, ge=1, le=32)


class LevelsIn(Body):
    levels: list[LevelIn] = Field(max_length=12)


class PrefixFields(Body):
    # A block binds the object of its level only; the rest is inherited from above.
    level_id: str | None = Field(None, max_length=32)
    region_id: str | None = Field(None, max_length=32)
    site_id: str | None = Field(None, max_length=32)
    zone_id: str | None = Field(None, max_length=32)
    system_id: str | None = Field(None, max_length=32)
    subsystem_id: str | None = Field(None, max_length=32)
    label: str | None = Field(None, max_length=64)
    purpose: str | None = Field(None, max_length=200)
    vlan: int | None = Field(None, ge=1, le=4094)
    gateway: str | None = Field(None, max_length=15)
    status: Literal[PREFIX_STATES] = "使用中"
    owner: str | None = Field(None, max_length=64)
    ticket: str | None = Field(None, max_length=80)
    note: str | None = Field(None, max_length=500)


class PrefixIn(PrefixFields):
    # Either a network as written, or a parent and a size to take its next free block.
    space_id: str | None = Field(None, max_length=32)
    parent_id: str | None = Field(None, max_length=32)
    network: str | None = Field(None, max_length=18)
    length: int | None = Field(None, ge=1, le=32)

    @model_validator(mode="after")
    def network_or_size(self):
        if not self.network and not (self.parent_id and self.length):
            raise ValueError("请填写网段，或选择上级网段和大小")
        return self


class AddressFields(Body):
    kind: Literal[ADDRESS_KINDS] = "主机"
    hostname: str | None = Field(None, max_length=128)
    dns_name: str | None = Field(None, max_length=255)
    note: str | None = Field(None, max_length=500)


class AllocateIn(AddressFields):
    # Empty: the first free address of the prefix.
    ip: str | None = Field(None, max_length=15)


class ReservationIn(Body):
    first: str = Field(min_length=1, max_length=15)
    last: str = Field(min_length=1, max_length=15)
    purpose: str = Field(min_length=1, max_length=200)


class SystemIn(Body):
    name: str = Field(min_length=1, max_length=64)
    short_name: str | None = Field(None, max_length=32)
    importance: Literal[IMPORTANCE] = "一般"
    department: str | None = Field(None, max_length=64)
    business_owner: str | None = Field(None, max_length=64)
    ops_owner: str | None = Field(None, max_length=64)
    contact: str | None = Field(None, max_length=64)
    note: str | None = Field(None, max_length=500)


class SubsystemIn(Body):
    system_id: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=64)
    owner: str | None = Field(None, max_length=64)
    note: str | None = Field(None, max_length=500)


class ServiceIn(Body):
    protocol: Literal[PROTOCOLS]
    # "80" or "8000-8010"; empty for ICMP.
    ports: str | None = Field(None, max_length=11)
    name: str | None = Field(None, max_length=64)
    note: str | None = Field(None, max_length=200)

    @field_validator("protocol", mode="before")
    @classmethod
    def upper(cls, value):
        return value.strip().upper() if isinstance(value, str) else value


class HostIn(Body):
    hostname: str = Field(min_length=1, max_length=128)
    system_id: str = Field(min_length=1, max_length=32)
    subsystem_id: str | None = Field(None, max_length=32)
    environment: Literal[ENVIRONMENTS] = "生产"
    status: Literal[HOST_STATES] = "已投产"
    kind: Literal[HOST_KINDS] = "虚拟机"
    role: Literal[HOST_ROLES] = "应用"
    os: str | None = Field(None, max_length=64)
    owner: str | None = Field(None, max_length=64)
    online_date: str | None = Field(None, pattern=DATE)
    offline_date: str | None = Field(None, pattern=DATE)
    tags: str | None = Field(None, max_length=200)
    note: str | None = Field(None, max_length=500)
    # The host's addresses: ones already allocated are linked, new ones are allocated.
    ips: list[str] = Field(default_factory=list, max_length=16)
    services: list[ServiceIn] = Field(default_factory=list, max_length=64)


class ProviderIn(Body):
    name: str = Field(min_length=1, max_length=64)
    support_phone: str | None = Field(None, max_length=80)
    account: str | None = Field(None, max_length=80)
    contact: str | None = Field(None, max_length=120)
    note: str | None = Field(None, max_length=500)


class NetworkIn(Body):
    provider_id: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=80)
    kind: Literal[NETWORK_KINDS] = "MPLS VPN"
    service_id: str | None = Field(None, max_length=80)
    interconnects: bool = True
    note: str | None = Field(None, max_length=500)


class PopIn(Body):
    provider_id: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=120)
    note: str | None = Field(None, max_length=500)


class CircuitTypeIn(Body):
    name: str = Field(min_length=1, max_length=40)
    note: str | None = Field(None, max_length=200)


class RiskIn(Body):
    name: str = Field(min_length=1, max_length=80)
    kind: Literal[RISK_KINDS] = "光缆"
    note: str | None = Field(None, max_length=500)


class TerminationIn(Body):
    # A site of ours, or a provider network; the A end is always a site.
    site_id: str | None = Field(None, max_length=32)
    network_id: str | None = Field(None, max_length=32)
    device: str | None = Field(None, max_length=80)
    interface: str | None = Field(None, max_length=80)
    port_speed: int | None = Field(None, ge=1, le=10_000_000)
    xconnect: str | None = Field(None, max_length=120)
    pop_id: str | None = Field(None, max_length=32)
    ip: str | None = Field(None, max_length=15)
    note: str | None = Field(None, max_length=200)


class CircuitIn(Body):
    provider_id: str = Field(min_length=1, max_length=32)
    cid: str = Field(min_length=1, max_length=80)
    type_id: str = Field(min_length=1, max_length=32)
    status: Literal[CIRCUIT_STATES] = "在用"
    bandwidth: int | None = Field(None, ge=0, le=10_000_000)
    install_date: str | None = Field(None, pattern=DATE)
    description: str | None = Field(None, max_length=200)
    a: TerminationIn
    z: TerminationIn | None = None
    # The interconnect block: an existing block as written, or the next free one carved from a block.
    space_id: str | None = Field(None, max_length=32)
    interconnect: str | None = Field(None, max_length=18)
    carve_from: str | None = Field(None, max_length=18)
    carve_length: int = Field(30, ge=24, le=31)
    vlan: int | None = Field(None, ge=1, le=4094)
    routing: Literal[ROUTING] | None = None
    local_as: str | None = Field(None, max_length=20)
    remote_as: str | None = Field(None, max_length=20)
    risks: list[str] = Field(default_factory=list, max_length=20)
    note: str | None = Field(None, max_length=500)
