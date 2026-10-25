from pydantic import BaseModel, ConfigDict, Field

from src.platform.tools.registry import Tool

from . import circuit_tools, queries


class FindSystems(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=64, description="业务系统或子系统名称，可以是简称或其中几个字")


class SystemHosts(BaseModel):
    model_config = ConfigDict(extra="forbid")
    system: str = Field(min_length=1, max_length=64, description="业务系统名称或简称")
    subsystem: str | None = Field(default=None, max_length=64, description="子系统名称；不填时列出整个系统")
    status: str | None = Field(default=None, max_length=10, description="主机状态，如 已投产、待投产；不填时全部列出")


class LocateIp(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ip: str = Field(min_length=1, max_length=15, description="IPv4 地址")


class PlanAccess(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str = Field(min_length=1, max_length=500,
                        description="源：IP、网段（10.1.1.0/24）、范围（10.1.1.3-10.1.1.6）、逗号分隔的多个地址，或业务系统名称；地名不要写进来")
    destination: str = Field(min_length=1, max_length=500, description="目的：写法同 source")
    source_subsystem: str | None = Field(default=None, max_length=64, description="源是业务系统时，只取这个子系统")
    destination_subsystem: str | None = Field(default=None, max_length=64, description="目的是业务系统时，只取这个子系统")
    ports: str | None = Field(default=None, max_length=300,
                              description="要开通的端口，如 tcp/443,udp/53,icmp；不填时用台账里目的主机登记的服务端口（目的是 IP 时必填）")


class FindCircuits(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=100,
                       description="电路编号、站点名、设备名、接口、互联 IP、局端或运营商网络名称中的任意一个")


class SiteResilience(BaseModel):
    model_config = ConfigDict(extra="forbid")
    site: str | None = Field(default=None, max_length=64, description="站点名称或其中几个字；不填时列出全部站点")


class FailureImpact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(min_length=1, max_length=10, description="故障对象类型：专线、运营商、运营商网络、局端、设备、共享风险")
    name: str = Field(min_length=1, max_length=120,
                      description="对象名称，如电路编号、运营商名、局端名、“站点 设备名”或光缆名称，可以只写其中几个字")


TOOLS = [
    Tool(
        "find_systems",
        "在资源台账里按名称找业务系统：返回子系统、划给它的网段及所在地域/机房/网络区域、"
        "各状态主机数；只读",
        FindSystems,
        lambda user, body: queries.find_systems(user, body.query),
        evidence=True,
    ),
    Tool(
        "list_hosts",
        "列出资源台账里一个业务系统（或子系统）的主机：状态、环境、角色、IP（含所在网段、地域、机房、网络区域）"
        "和登记的服务端口；最多 200 台；只读",
        SystemHosts,
        lambda user, body: queries.system_hosts(user, body.system, body.subsystem, body.status),
        evidence=True,
    ),
    Tool(
        "locate_ip",
        "查一个 IP 在资源台账里的位置：所在网段及其层级归属（地域、机房、网络区域、业务系统、子系统）、"
        "是否已分配、属于哪台主机及其服务端口；found=false 表示台账里没有这个地址，"
        "不代表地址不存在；只读",
        LocateIp,
        lambda user, body: queries.locate_ip(user, body.ip),
        evidence=True,
    ),
    Tool(
        "plan_access",
        "按资源台账拼出“源到目的”的防火墙策略申请行：源或目的是业务系统时取它在用（未下线）主机的 IP，"
        "目的端口取目的主机登记的服务端口（也可用 ports 指定）；台账里没有的地址照常放行并在 notes 里说明；"
        "返回 rows（源业务系统、src_ip、目的业务系统、dst_ip、port、proto）和 notes；"
        "策略下发到哪台防火墙、哪个安全域由防火墙策略应用按拓扑计算，台账不给出；只读，不生成文件",
        PlanAccess,
        lambda user, body: queries.plan_access(
            user, body.source, body.destination, body.source_subsystem, body.destination_subsystem, body.ports),
        evidence=True,
    ),
    Tool(
        "find_circuits",
        "在资源台账里查运营商专线：按电路编号、站点、设备、接口、互联 IP、局端或运营商网络查找，返回运营商、类型、状态、带宽、"
        "A/Z 两端（站点或运营商网络、设备接口、局端、地址）、互联网段、路由与 AS、共享风险，以及这条线一断就会断开的站点；"
        "报障电话和客户编号在运营商上；只读",
        FindCircuits,
        lambda user, body: circuit_tools.find_circuits(user, body.query),
        evidence=True,
    ),
    Tool(
        "site_resilience",
        "按资源台账里在用的专线，列出站点到核心站点的路径和单点故障（运营商、运营商网络、局端、设备、单条专线、共享风险）；"
        "只陈述台账里的事实，不判断是否达标；设备上实际配置的路由看不到；只读",
        SiteResilience,
        lambda user, body: circuit_tools.site_resilience(user, body.site),
        evidence=True,
    ),
    Tool(
        "failure_impact",
        "假设某一处出故障或割接（一条专线、一家运营商、一个运营商网络、一个局端、一台设备或一条共享光缆/管道），"
        "按资源台账算出哪些站点会断开、哪些只剩一条路，涉及哪些专线及报障电话，受影响站点上有哪些业务系统；只读",
        FailureImpact,
        lambda user, body: circuit_tools.failure_impact(user, body.kind, body.name),
        evidence=True,
    ),
]
