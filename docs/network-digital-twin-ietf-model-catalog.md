# IETF 拓扑建模相关文档逐项目录

核对日期：2026-10-03。配套阅读：[分析主文](network-digital-twin-ietf-model-landscape.md)。本目录列出文档状态、模块、能力、边界、关键路径及研读深度；不表示全部已实现。

共 357 份文档：264 份 RFC（含架构、协议、历史版本及非 Standards Track 文档）与 93 份草案。文档数量不是拓扑模型数量。

路径用于说明对象层次，省略前缀或含 `...` 的路径不是可以直接执行的 XPath。模块列可能包含类型模块、兼容模块及子模块；MIB 一节列出的是 SMI/MIB 模块，不能当作 YANG 模块。

## ACTN/VN 与切片

### RFC8453 Framework for Abstraction and Control of TE Networks (ACTN)

[官方原文](https://www.rfc-editor.org/rfc/rfc8453.html) · INFORMATIONAL · 类型：architecture

模块：无独立 YANG 模块（见文档类型与范围）。

支持：ACTN 的 CNC/MDSC/PNC、CMI/MPI/SBI、多域多层抽象、虚拟网络与资源协调架构。。

边界：Informational 架构，无 YANG 模块或具体 wire protocol；信息隔离允许隐藏内部节点，客户视图不等同物理全图。。

核对章节：§2–7。研读深度：abstract, document status and selected architecture/protocol sections; not exhaustive protocol review。

### RFC8454 Information Model for Abstraction and Control of TE Networks (ACTN)

[官方原文](https://www.rfc-editor.org/rfc/rfc8454.html) · INFORMATIONAL · 类型：information-model

模块：无独立 YANG 模块（见文档类型与范围）。

支持：ACTN 的抽象信息模型、VN/访问点/连接、客户与协调器信息需求。。

边界：Informational 信息模型，非 YANG schema；与 RFC9731 的正式 VN YANG 应区分。。

核对章节：§3–8。研读深度：abstract, document status and selected architecture/protocol sections; not exhaustive protocol review。

### draft-ietf-ccamp-yang-otn-slicing-12 Framework and Data Model for OTN Network Slicing

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-ccamp-yang-otn-slicing-12) · Internet-Draft / Active · 类型：service-and-topology-yang

模块：`ietf-otn-slice-mpi`、`ietf-otn-slice`。

支持：OTN slice framework + northbound service 和 MPI topology augments，增加 OTN SLO、资源/隔离与时隙表达。。

边界：-12 活跃草案；依赖 OTN topo/Layer1/Network Slice Service/NS Topo，文档说明尚有增补待未来版本，不能宣称完整硬隔离实现。。

核对章节：§3–7。研读深度：abstract, model tree, module import/augment and selected semantic sections; not compiled。

### draft-ietf-teas-applicability-actn-slicing-10 Applicability of Abstraction and Control of Traffic Engineered Networks (ACTN) to IETF Network Slicing

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-teas-applicability-actn-slicing-10) · Internet-Draft / RFC Editor queue (Blocked) · 类型：applicability

模块：无独立 YANG 模块（见文档类型与范围）。

支持：ACTN VN 与 RFC9543 slicing 的资源分区、定制拓扑、隔离和编排关系以及 ACTN 覆盖边界。。

边界：-10 在 RFC Editor queue（Blocked）；无新 YANG，不能将 ACTN VN 与所有 slice 属性天然等同。。

核对章节：§2–5。研读深度：abstract, document status and selected architecture/protocol sections; not exhaustive protocol review。

### draft-ietf-teas-network-slice-topology-yang-04 IETF Network Slice Topology YANG Data Model

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-teas-network-slice-topology-yang-04) · Internet-Draft / Active · 类型：topology-and-service-yang

模块：`ietf-ns-topo`、`ietf-ns-underlay-path`。

支持：客户 intent topology 与 underlay path 的 Network Slice Service 扩展，基于 RFC8345 图，支持 wholesale/多租户定制资源意图。。

边界：-04 活跃草案；intended 图和 path 约束不是已观测的物理图，不能取代 realization/状态证据。。

关键对象/关联：`/ietf-network:networks/network`。

核对章节：§2–6。研读深度：abstract, model tree, module import/augment and selected semantic sections; not compiled。

## ACTN/packet-optical

### draft-ietf-ccamp-actn-optical-transport-mgmt-05 Integrating YANG Configuration and Management into an Abstraction and Control of TE Networks (ACTN) System for Optical Networks

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-ccamp-actn-optical-transport-mgmt-05) · Internet-Draft / Active · 类型：architecture

模块：无独立 YANG 模块（见文档类型与范围）。

支持：ACTN 加入 Resource/Device/Network Management 与 YANG/FCAPS，讨论 MPI 的抽象控制与详细配置/管理并存。。

边界：-05 活跃草案，无新 YANG；架构扩展非完成光设备 inventory/性能采集的现成 schema。。

核对章节：§2–6。研读深度：abstract, document status and selected architecture/protocol sections; not exhaustive protocol review。

### draft-ietf-teas-actn-poi-applicability-20 Applicability of Abstraction and Control of Traffic Engineered Networks (ACTN) to Packet Optical Integration (POI)

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-teas-actn-poi-applicability-20) · Internet-Draft / Active · 类型：applicability

模块：无独立 YANG 模块（见文档类型与范围）。

支持：ACTN 在 packet over optical 中的域控制器/MDSC、MPI YANG 与协议、跨域多层路径计算和协调。。

边界：-20 活跃草案，Waiting for AD Go-Ahead，无新模块；梳理既有模型，不使所有所引草案自动成为标准。。

核对章节：§2–6。研读深度：abstract, document status and selected architecture/protocol sections; not exhaustive protocol review。

### draft-ietf-teas-actn-poi-assurance-05 Applicability of Abstraction and Control of Traffic Engineered Networks (ACTN) for Packet Optical Integration (POI) service assurance

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-teas-actn-poi-assurance-05) · Internet-Draft / Active · 类型：applicability

模块：无独立 YANG 模块（见文档类型与范围）。

支持：packet/optical 多层故障关联、性能阈值、服务路径健康/SLA 验证与 PNC/MDSC 处理。。

边界：-05 活跃草案，无新模块；跨层根因须有 supporting/realization 映射，不能仅凭节点同名关联。。

核对章节：§3–7。研读深度：abstract, document status and selected architecture/protocol sections; not exhaustive protocol review。

## ALTO/信息交换

### RFC7285 Application-Layer Traffic Optimization (ALTO) Protocol

[官方原文](https://www.rfc-editor.org/rfc/rfc7285.html) · PROPOSED STANDARD · 类型：protocol

模块：无独立 YANG 模块（见文档类型与范围）。

支持：ALTO HTTP/JSON 的 information resource directory、network map（PID/IP 集合）、cost map、endpoint property/cost；将运营者抽象网络信息提供给应用。。

边界：非 YANG；PID 与 cost 是抽象/策略视图，不是 RFC8345 node/TP，也不保证暴露路径内部或物理邻接。。

核对章节：§5–11。研读深度：abstract, document status and selected architecture/protocol sections; not exhaustive protocol review。

### RFC8189 Multi-Cost Application-Layer Traffic Optimization (ALTO)

[官方原文](https://www.rfc-editor.org/rfc/rfc8189.html) · PROPOSED STANDARD · 类型：protocol

模块：无独立 YANG 模块（见文档类型与范围）。

支持：多 cost metrics 和逻辑过滤的 filtered cost/endpoint cost map。。

边界：非 YANG；若未提供上下文，数值代价不能任意解释为 IGP cost/时延。。

核对章节：§3–5。研读深度：abstract, document status and selected architecture/protocol sections; not exhaustive protocol review。

### RFC8686 Application-Layer Traffic Optimization (ALTO) Cross-Domain Server Discovery

[官方原文](https://www.rfc-editor.org/rfc/rfc8686.html) · PROPOSED STANDARD · 类型：protocol

模块：无独立 YANG 模块（见文档类型与范围）。

支持：通过反向 DNS U-NAPTR 发现与 IP/prefix 相关的跨域 ALTO server/resource。。

边界：非 YANG；发现服务位置，不构造/合并跨域物理全图。。

核对章节：§2–4。研读深度：abstract, document status and selected architecture/protocol sections; not exhaustive protocol review。

### RFC8895 Application-Layer Traffic Optimization (ALTO) Incremental Updates Using Server-Sent Events (SSE)

[官方原文](https://www.rfc-editor.org/rfc/rfc8895.html) · PROPOSED STANDARD · 类型：protocol

模块：无独立 YANG 模块（见文档类型与范围）。

支持：SSE 对 ALTO information resources 推送增量更新。。

边界：非 YANG；是资源更新传输机制，非底层 topology discovery。。

核对章节：§3–7。研读深度：abstract, document status and selected architecture/protocol sections; not exhaustive protocol review。

### RFC8896 Application-Layer Traffic Optimization (ALTO) Cost Calendar

[官方原文](https://www.rfc-editor.org/rfc/rfc8896.html) · PROPOSED STANDARD · 类型：protocol

模块：无独立 YANG 模块（见文档类型与范围）。

支持：ALTO Cost Calendar 给 cost 随未来时间变化的序列与 interval 表达，适合计划网络资源。。

边界：非 YANG；是应用可见的代价日历，不是精确设备拓扑历史/计划执行保证。。

核对章节：§3–5。研读深度：abstract, document status and selected architecture/protocol sections; not exhaustive protocol review。

### RFC9240 An Extension for Application-Layer Traffic Optimization (ALTO): Entity Property Maps

[官方原文](https://www.rfc-editor.org/rfc/rfc9240.html) · PROPOSED STANDARD · 类型：protocol

模块：无独立 YANG 模块（见文档类型与范围）。

支持：ALTO entity property map 扩展，资源域与 entity/property 可扩展。。

边界：非 YANG；entity 是协议抽象，需要定义域及属性语义，不能默认等于物理设备。。

核对章节：§3–6。研读深度：abstract, document status and selected architecture/protocol sections; not exhaustive protocol review。

### RFC9275 An Extension for Application-Layer Traffic Optimization (ALTO): Path Vector

[官方原文](https://www.rfc-editor.org/rfc/rfc9275.html) · EXPERIMENTAL · 类型：protocol

模块：无独立 YANG 模块（见文档类型与范围）。

支持：Path Vector 通过 abstract network elements 及相关属性暴露路径资源、瓶颈/共享关系，比纯 pairwise cost 更丰富。。

边界：Experimental，不是 Standards Track；仍是抽象路径，非原始物理 hop/线缆保证。。

核对章节：§3–7。研读深度：abstract, document status and selected architecture/protocol sections; not exhaustive protocol review。

### RFC9439 Application-Layer Traffic Optimization (ALTO) Performance Cost Metrics

[官方原文](https://www.rfc-editor.org/rfc/rfc9439.html) · PROPOSED STANDARD · 类型：protocol

模块：无独立 YANG 模块（见文档类型与范围）。

支持：ALTO 延迟、抖动、丢包、hop count、带宽 metrics 与 cost-context，区分量测估计、SLA 等来源。。

边界：非 YANG；上下文和统计语义不能丢失，不能把 SLA 值认作测量值。。

核对章节：§3–6。研读深度：abstract, document status and selected architecture/protocol sections; not exhaustive protocol review。

### RFC9569 The Application-Layer Traffic Optimization (ALTO) Transport Information Publication Service (TIPS)

[官方原文](https://www.rfc-editor.org/rfc/rfc9569.html) · PROPOSED STANDARD · 类型：protocol

模块：无独立 YANG 模块（见文档类型与范围）。

支持：TIPS 的 updates graph/RESTful pull，利用 HTTP2/3 并发获取 ALTO 信息资源增量。。

边界：非 YANG；更新图是版本/patch 依赖图，而非物理网络图。。

核对章节：§2–5。研读深度：abstract, document status and selected architecture/protocol sections; not exhaustive protocol review。

### draft-ietf-alto-oam-yang-18 YANG Data Models for the Application-Layer Traffic Optimization (ALTO) Protocol

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-alto-oam-yang-18) · Internet-Draft / RFC Editor queue (In Progress) · 类型：device-management-yang

模块：`ietf-alto`、`ietf-alto-stats`。

支持：ietf-alto 管理 server/client、discovery/auth/data-source/information resources，ietf-alto-stats 加服务器/资源统计。。

边界：-18 在 RFC Editor queue；这是 ALTO OAM 管理模型，不把 ALTO network/cost map 协议格式替换成 RFC8345 图，也不证明 physical topology。。

关键对象/关联：`/ietf-alto:alto/alto-server`；`/ietf-alto:alto/alto-client`；`/ietf-alto:alto/alto-server/resource`。

核对章节：§4–7。研读深度：abstract, model tree, module import/augment and selected semantic sections; not compiled。

## BGP-LS/信息交换

### RFC8571 BGP - Link State (BGP-LS) Advertisement of IGP Traffic Engineering Performance Metric Extensions

[官方原文](https://www.rfc-editor.org/rfc/rfc8571.html) · PROPOSED STANDARD · 类型：protocol

模块：无独立 YANG 模块（见文档类型与范围）。

支持：BGP-LS 发布 IGP TE 延迟/抖动/损失/带宽性能度量扩展。。

边界：非 YANG；只补协议 TLV，不定义全新图，也不代表度量已由平台采到。。

核对章节：§2–3。研读深度：abstract, document status and selected architecture/protocol sections; not exhaustive protocol review。

### RFC9085 Border Gateway Protocol - Link State (BGP-LS) Extensions for Segment Routing

[官方原文](https://www.rfc-editor.org/rfc/rfc9085.html) · PROPOSED STANDARD · 类型：protocol

模块：无独立 YANG 模块（见文档类型与范围）。

支持：BGP-LS 发布 SR-MPLS 能力、SID/前缀/邻接等 SR 信息。。

边界：非 YANG；RFC9020 是设备管理模型，二者职责不同。。

核对章节：§2–3。研读深度：abstract, document status and selected architecture/protocol sections; not exhaustive protocol review。

### RFC9351 Border Gateway Protocol - Link State (BGP-LS) Extensions for Flexible Algorithm Advertisement

[官方原文](https://www.rfc-editor.org/rfc/rfc9351.html) · PROPOSED STANDARD · 类型：protocol

模块：无独立 YANG 模块（见文档类型与范围）。

支持：BGP-LS 发布 Flexible Algorithm 定义、约束和相关拓扑属性。。

边界：非 YANG；algorithm 的逻辑视图不等于物理图或某接口已被实际选用。。

核对章节：§2–4。研读深度：abstract, document status and selected architecture/protocol sections; not exhaustive protocol review。

### RFC9514 Border Gateway Protocol - Link State (BGP-LS) Extensions for Segment Routing over IPv6 (SRv6)

[官方原文](https://www.rfc-editor.org/rfc/rfc9514.html) · PROPOSED STANDARD · 类型：protocol

模块：无独立 YANG 模块（见文档类型与范围）。

支持：BGP-LS 发布 SRv6 capabilities/locator/End SID 等 SRv6 信息。。

边界：非 YANG；不是 SRv6 base 管理模型，来源和策略决定可见范围。。

核对章节：§3–5。研读深度：abstract, document status and selected architecture/protocol sections; not exhaustive protocol review。

### RFC9552 Distribution of Link-State and Traffic Engineering Information Using BGP

[官方原文](https://www.rfc-editor.org/rfc/rfc9552.html) · PROPOSED STANDARD · 类型：protocol

模块：无独立 YANG 模块（见文档类型与范围）。

支持：BGP-LS NLRI 与属性发布 node/link/prefix、IGP link-state/TE；应用包括 PCE/ALTO，正式 obsoletes RFC7752 和 RFC9029。。

边界：非 YANG；受策略控制，包含物理或虚拟 IGP 链路。输出的是协议/资源身份，不能单独证明物理机箱归并或完整布线。。

核对章节：§3–5。研读深度：abstract, document status and selected architecture/protocol sections; not exhaustive protocol review。

### RFC9815 BGP Link State (BGP-LS) Shortest Path First (SPF) Routing

[官方原文](https://www.rfc-editor.org/rfc/rfc9815.html) · PROPOSED STANDARD · 类型：protocol

模块：无独立 YANG 模块（见文档类型与范围）。

支持：BGP-LS SPF 在单一管理域以 BGP 分发 LS 数据、以 SPF 计算转发，实现与传统 BGP 决策不同的路由机制。。

边界：2025-07 已发布 Proposed Standard，非草案、非 YANG。不是 RFC9552 所有 BGP-LS 部署都必须启用的 SPF。。

核对章节：§3–7。研读深度：abstract, document status and selected architecture/protocol sections; not exhaustive protocol review。

### RFC9816 Usage and Applicability of BGP Link State (BGP-LS) Shortest Path First (SPF) Routing in Data Centers

[官方原文](https://www.rfc-editor.org/rfc/rfc9816.html) · INFORMATIONAL · 类型：applicability

模块：无独立 YANG 模块（见文档类型与范围）。

支持：BGP-LS SPF 在数据中心的拓扑/运维用法与适用性。。

边界：2025-07 已发布 Informational，非 YANG；只提供 BGP-LS SPF 用例说明。。

核对章节：§2–5。研读深度：abstract, document status and selected architecture/protocol sections; not exhaustive protocol review。

### RFC9857 Advertisement of Segment Routing Policies Using BGP - Link State

[官方原文](https://www.rfc-editor.org/rfc/rfc9857.html) · PROPOSED STANDARD · 类型：protocol

模块：无独立 YANG 模块（见文档类型与范围）。

支持：使用 BGP-LS 发布 SR Policy 的候选路径、segment list 及策略相关信息。。

边界：非 YANG；逻辑 policy/path 是控制平面信息，不能据此保证当前数据流确实走该路径。。

核对章节：§3–5。研读深度：abstract, document status and selected architecture/protocol sections; not exhaustive protocol review。

## DetNet

### RFC8655 Deterministic Networking Architecture

[官方原文](https://www.rfc-editor.org/rfc/rfc8655.html) · PROPOSED STANDARD · 类型：architecture

模块：无独立 YANG 模块（见文档类型与范围）。

支持：DetNet 服务/转发子层、显式路径与冗余/资源保障的架构。。

边界：Informational，不定义 topology YANG 或即取即用的延迟保证实现。。

核对章节：§3–5。研读深度：abstract, document status and selected architecture/protocol sections; not exhaustive protocol review。

### RFC8938 Deterministic Networking (DetNet) Data Plane Framework

[官方原文](https://www.rfc-editor.org/rfc/rfc8938.html) · INFORMATIONAL · 类型：architecture

模块：无独立 YANG 模块（见文档类型与范围）。

支持：DetNet 数据平面各子层、IP/MPLS 与下层 IEEE TSN 的框架。。

边界：Informational，非 YANG；数据面承载与全网图并非同一个模型。。

核对章节：§3–5。研读深度：abstract, document status and selected architecture/protocol sections; not exhaustive protocol review。

### RFC9016 Flow and Service Information Model for Deterministic Networking (DetNet)

[官方原文](https://www.rfc-editor.org/rfc/rfc9016.html) · INFORMATIONAL · 类型：information-model

模块：无独立 YANG 模块（见文档类型与范围）。

支持：DetNet flow/service、traffic specification、所需带宽/延迟/损失等信息模型，RFC9633 引用其参数。。

边界：Informational，非 YANG；需求值不等于测得值，不能据此声称实际满足确定性。。

核对章节：§4–5。研读深度：abstract, document status and selected architecture/protocol sections; not exhaustive protocol review。

### RFC9633 Deterministic Networking (DetNet) YANG Data Model

[官方原文](https://www.rfc-editor.org/rfc/rfc9633.html) · PROPOSED STANDARD · 类型：device-yang

模块：`ietf-detnet`。

支持：DetNet 设备流配置与运行状态：application/service/forwarding 子层、traffic profile、流关联与复制/消除等服务属性；与 IP/MPLS 数据平面配合。。

边界：顶层 /detnet，而非 RFC8345 的全网拓扑 augment；不自动保证 bounded latency/loss，能力与资源预留、调度和路径须有外部事实。。

关键对象/关联：`/ietf-detnet:detnet`。

核对章节：§4–8。研读深度：abstract, model tree, module import/augment and selected semantic sections; not compiled。

### RFC9938 A Framework for the Deterministic Networking (DetNet) Controller Plane

[官方原文](https://www.rfc-editor.org/rfc/rfc9938.html) · INFORMATIONAL · 类型：architecture

模块：无独立 YANG 模块（见文档类型与范围）。

支持：DetNet controller plane 的功能、信息需求、路径计算/资源与管理协调框架。。

边界：Informational，2026-03 已发布；不定义新的控制器协议或拓扑 YANG，后续方案仍需独立标准。。

核对章节：§3–7。研读深度：abstract, document status and selected architecture/protocol sections; not exhaustive protocol review。

### draft-ietf-detnet-multi-domain-framework-02 A Control Plane Framework for Multi-Domain Deterministic Networking (DetNet)

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-detnet-multi-domain-framework-02) · Internet-Draft / Active · 类型：architecture

模块：无独立 YANG 模块（见文档类型与范围）。

支持：多域 DetNet 的 domain 定义、分层协调与 peer stitching、跨域路径计算及资源预留框架。。

边界：-02 活跃草案，无新 YANG；PCE 为示例实现，不是所有场景的强制控制技术。。

核对章节：§3–6。研读深度：abstract, document status and selected architecture/protocol sections; not exhaustive protocol review。

## OAM与测量

### RFC4656 A One-way Active Measurement Protocol (OWAMP)

[官方原文](https://www.rfc-editor.org/rfc/rfc4656.html) · PROPOSED STANDARD · 类型：OWAMP测量协议（无YANG模块）

模块：无独立 YANG 模块（见文档类型与范围）。

支持：主动单向测量控制/测试协议，结果可作为拓扑方向性能证据。。

边界：协议规范不是YANG；单向延迟依赖时间条件，测量包发送需显式动作授权。。

核对章节：§3；§4。研读深度：RFC目录状态、原文摘要/关键协议章节核对；未逐条协议审计。

### RFC5357 A Two-Way Active Measurement Protocol (TWAMP)

[官方原文](https://www.rfc-editor.org/rfc/rfc5357.html) · PROPOSED STANDARD · 类型：TWAMP测量协议（无YANG模块）

模块：无独立 YANG 模块（见文档类型与范围）。

支持：定义双向主动测量控制/测试协议，为8913提供协议语义。。

边界：协议不是模型；往返测量不能无证据拆成两个单向时延。。

核对章节：§3；§4。研读深度：RFC目录状态、摘要/协议角色核对；未逐条协议审计。

### RFC6728 Configuration Data Model for the IP Flow Information Export (IPFIX) and Packet Sampling (PSAMP) Protocols

[官方原文](https://www.rfc-editor.org/rfc/rfc6728.html) · PROPOSED STANDARD · 类型：YANG IPFIX/PSAMP配置及监控模型

模块：`ietf-ipfix-psamp`。

支持：描述观察点、抽样/筛选、cache、exporter/collector和状态，以ifName/ifIndex、物理组件和observationDomainId定位流量观察上下文。。

边界：是监控过程配置模型，不是IPFIX流记录本身或物理邻接证明；使用2012年的pre-NMDA树，不能把整个观察点/抽样范围自动视为全网覆盖。示例my-ipfix-psamp-deviation不是正式IETF模块。。

关键对象/关联：`/ietf-ipfix-psamp:ipfix/observationPoint[name]`；`/ietf-ipfix-psamp:ipfix/selectionProcess[name]`；`/ietf-ipfix-psamp:ipfix/cache[name]`；`/ietf-ipfix-psamp:ipfix/exportingProcess[name]`；`/ietf-ipfix-psamp:ipfix/collectingProcess[name]`。

核对章节：§3；§4；§6。研读深度：旁支模型目录核对：官方范围、模块、关键路径、RFC目录状态及IANA登记；未逐叶审计/编译/验证实例。

### RFC8194 A YANG Data Model for LMAP Measurement Agents

[官方原文](https://www.rfc-editor.org/rfc/rfc8194.html) · PROPOSED STANDARD · 类型：YANG LMAP测量平台模型

模块：`ietf-lmap-common`、`ietf-lmap-control`、`ietf-lmap-report`。

支持：配置Measurement Agent任务/计划/抑制，报告测量点、时间与结果，支持统一测量来源管理。。

边界：任务registry URI决定测量含义，不能只用通用结果列推测时延/丢包；不是完整网络拓扑模块。。

关键对象/关联：`/ietf-lmap-control:lmap/tasks/task[name]`；`/ietf-lmap-control:lmap/schedules/schedule[name]`；`RPC /ietf-lmap-report:report`。

核对章节：§2；§3；§4.1；§4.2；§4.3。研读深度：原文范围、关键树/schema路径、RFC目录状态及IANA登记核对；未编译或验证实例。

### RFC8531 Generic YANG Data Model for Connection-Oriented Operations, Administration, and Maintenance (OAM) Protocols

[官方原文](https://www.rfc-editor.org/rfc/rfc8531.html) · PROPOSED STANDARD · 类型：YANG连接型OAM公共模型

模块：`ietf-connection-oriented-oam`。

支持：技术无关MD/MA/MEP、连续性/连通性/路径发现及统计，供具体CFM/MPLS-TP等增补。。

边界：借鉴IEEE CFM概念不使IEEE YANG成为IETF模型；应保留technology/维护域作用域，不能拿ARP推断替代OAM会话。。

关键对象/关联：`/ietf-connection-oriented-oam:domains/domain[technology md-name-string]/mas/ma[ma-name-string]/mep[mep-name]`；`RPC continuity-check/connectivity-verification/path-discovery`。

核对章节：§3；§4；§5；§7。研读深度：原文范围、关键树/schema路径、RFC目录状态及IANA登记核对；未编译或验证实例。

### RFC8532 Generic YANG Data Model for the Management of Operations, Administration, and Maintenance (OAM) Protocols That Use Connectionless Communications

[官方原文](https://www.rfc-editor.org/rfc/rfc8532.html) · PROPOSED STANDARD · 类型：RFC8345节点增补/连接无关OAM YANG

模块：`ietf-connectionless-oam`、`ietf-lime-time-types`。

支持：在network/node绑定测试点IP与network-instance；用技术无关OAM工具/会话统计定位故障。。

边界：TP在这里可指Test Point，不应与RFC8345 termination-point无条件等同；具体工具/技术仍需扩展与真实数据。。

关键对象/关联：`/ietf-network:networks/network/node/ietf-connectionless-oam:ipv4-location-type/test-point-ipv4-location-list/test-point-locations[ipv4-location ni]`；`/ietf-connectionless-oam:cc-session-statistics-data`。

核对章节：§3；§4；§5；§6。研读深度：原文范围、关键树/schema路径、RFC目录状态及IANA登记核对；未编译或验证实例。

### RFC8533 A YANG Data Model for Retrieval Methods for the Management of Operations, Administration, and Maintenance (OAM) Protocols That Use Connectionless Communications

[官方原文](https://www.rfc-editor.org/rfc/rfc8533.html) · PROPOSED STANDARD · 类型：YANG OAM RPC检索模型

模块：`ietf-connectionless-oam-methods`。

支持：独立OAM调用/检索过程与8532的数据模型分离；可按测试点查询连通与路径结果。。

边界：RPC接口存在不证明实际测量成功；触发测量是动作，不能由读取配置自动执行。。

关键对象/关联：`RPC /ietf-connectionless-oam-methods:continuity-check`；`RPC connectivity-verification/path-discovery`。

核对章节：§3；§3.1；§4。研读深度：原文范围、关键树/schema路径、RFC目录状态及IANA登记核对；未编译或验证实例。

### RFC8762 Simple Two-Way Active Measurement Protocol

[官方原文](https://www.rfc-editor.org/rfc/rfc8762.html) · PROPOSED STANDARD · 类型：STAMP测量协议（无YANG模块）

模块：无独立 YANG 模块（见文档类型与范围）。

支持：简化双向主动测量的sender/reflector及测试格式，可为链路/服务PM供源。。

边界：STAMP协议已发布不意味着STAMP YANG也已发布；当前对应YANG草案仍过期。。

核对章节：§3；§4。研读深度：RFC目录状态、摘要/角色核对；未逐条协议审计。

### RFC8911 Registry for Performance Metrics

[官方原文](https://www.rfc-editor.org/rfc/rfc8911.html) · PROPOSED STANDARD · 类型：性能指标登记规范（无YANG模块）

模块：无独立 YANG 模块（见文档类型与范围）。

支持：定义Performance Metrics Registry规则，使LMAP/PM指标身份、单位和方法可解释。。

边界：不是YANG模块；注册指标不能替代实际测量结果，RFC4148旧IPPM registry已退出不应继续用作当前基线。。

核对章节：§1；§3。研读深度：RFC目录状态、原文摘要/registry范围核对。

### RFC8912 Initial Performance Metrics Registry Entries

[官方原文](https://www.rfc-editor.org/rfc/rfc8912.html) · PROPOSED STANDARD · 类型：初始性能指标登记条目（无YANG模块）

模块：无独立 YANG 模块（见文档类型与范围）。

支持：提供具体性能指标的登记/方法语义，与测量结果ID可关联。。

边界：不是拓扑或YANG模型；不能因为名称相似就把厂商计数器映射到不同采样方法指标。。

核对章节：§1；§4。研读深度：RFC目录状态、原文摘要/registry条目范围核对。

### RFC8913 Two-Way Active Measurement Protocol (TWAMP) YANG Data Model

[官方原文](https://www.rfc-editor.org/rfc/rfc8913.html) · PROPOSED STANDARD · 类型：YANG TWAMP测量控制模型

模块：`ietf-twamp`。

支持：控制连接和测试会话角色/端点、状态与参数可关联所测源/目的及服务路径。。

边界：TWAMP不等于所有PM输出的通用归档；模型支持受feature限制，不能补未观测会话ID/密钥/时间。。

关键对象/关联：`/ietf-twamp:twamp/client`；`/ietf-twamp:twamp/server`；`/ietf-twamp:twamp/session-sender`；`/ietf-twamp:twamp/session-reflector`。

核对章节：§2；§3；§4；§5.2。研读深度：原文范围、关键树/schema路径、RFC目录状态及IANA登记核对；未编译或验证实例。

### RFC9196 YANG Modules Describing Capabilities for Systems and Datastore Update Notifications

[官方原文](https://www.rfc-editor.org/rfc/rfc9196.html) · PROPOSED STANDARD · 类型：YANG系统/推送能力模型

模块：`ietf-system-capabilities`、`ietf-notification-capabilities`。

支持：描述datastore/node可支持的周期/on-change推送、最小周期/阻尼等，为采样覆盖/时效计划提供证据。。

边界：是服务器能力，不是产品功能开关或已执行成功的测量；不替代RFC8525模块/schema支持清单。。

关键对象/关联：`/ietf-system-capabilities:system-capabilities/datastore-capabilities`；`ietf-notification-capabilities augmentation`；`/ietf-system-capabilities:system-capabilities/datastore-capabilities[datastore]/per-node-capabilities`；`其ietf-notification-capabilities:subscription-capabilities增补`。

核对章节：§2；§3；§4；§5。研读深度：原文范围、关键树/schema路径、RFC目录状态及IANA登记核对；未编译或验证实例。

### RFC9197 Data Fields for In Situ Operations, Administration, and Maintenance (IOAM)

[官方原文](https://www.rfc-editor.org/rfc/rfc9197.html) · PROPOSED STANDARD · 类型：IOAM数据字段协议（无YANG模块）

模块：无独立 YANG 模块（见文档类型与范围）。

支持：IOAM节点/接口/时间等trace字段可记录实际经过路径。。

边界：报文格式不是配置/状态YANG；不能只读profile就制造实际过路径trace。。

核对章节：§4。研读深度：RFC目录状态与字段范围核对；未逐条字段审计。

### RFC9232 Network Telemetry Framework

[官方原文](https://www.rfc-editor.org/rfc/rfc9232.html) · INFORMATIONAL · 类型：网络遥测框架（无YANG模块）

模块：无独立 YANG 模块（见文档类型与范围）。

支持：区分管理/控制/转发面数据源、采集和分析，指导拓扑观察证据来源。。

边界：不是特定遥测YANG模型，也没有自动保障数据完整/因果准确。。

核对章节：§3；§4。研读深度：原文范围、关键树/schema路径、RFC目录状态及IANA登记核对；未编译或验证实例。

### RFC9314 YANG Data Model for Bidirectional Forwarding Detection (BFD)

[官方原文](https://www.rfc-editor.org/rfc/rfc9314.html) · PROPOSED STANDARD · 类型：YANG BFD设备/协议模型

模块：`ietf-bfd-types`、`ietf-bfd`、`ietf-bfd-ip-sh`、`ietf-bfd-ip-mh`、`ietf-bfd-lag`、`ietf-bfd-mpls`。

支持：BFD single/multihop、LAG/MPLS会话与接口引用/会话状态，为逻辑链路可达性提供证据。。

边界：BFD会话不是物理线缆；ietf-bfd-types公共模型及IANA types依赖应完整冻结，不混用协议RFC5880和模型RFC9314。。

关键对象/关联：`/ietf-routing:routing/control-plane-protocols/control-plane-protocol/ietf-bfd:bfd/ietf-bfd-ip-sh:ip-sh/sessions/session[interface dest-addr]`；`/ietf-routing:routing/control-plane-protocols/control-plane-protocol/ietf-bfd:bfd/ietf-bfd-ip-mh:ip-mh/session-groups/session-group`；`ietf-bfd-types:bfdv1`；`/ietf-routing:routing/control-plane-protocols/control-plane-protocol/ietf-bfd:bfd`。

核对章节：§3；§4。研读深度：原文范围、关键树/schema路径、RFC目录状态及IANA登记核对；未编译或验证实例。

### RFC9326 In Situ Operations, Administration, and Maintenance (IOAM) Direct Exporting

[官方原文](https://www.rfc-editor.org/rfc/rfc9326.html) · PROPOSED STANDARD · 类型：IOAM直接导出机制（无YANG模块）

模块：无独立 YANG 模块（见文档类型与范围）。

支持：规定IOAM direct-export方式，补充实际路径观测来源。。

边界：不是完整通用拓扑模型或时序数据库；需真实导出事件。。

核对章节：§3；§4。研读深度：RFC目录状态、摘要及机制范围核对。

### RFC9375 A YANG Data Model for Network and VPN Service Performance Monitoring

[官方原文](https://www.rfc-editor.org/rfc/rfc9375.html) · PROPOSED STANDARD · 类型：RFC8345 network/node/link/TP性能增补YANG

模块：`ietf-network-vpn-pm`。

支持：把underlay与overlay VPN性能映射到分层拓扑，包含方向性loss/delay、测量周期/来源及节点统计。。

边界：模型复用已有测量而不定义新测量机制；单向指标不得伪装双向，必须保留方向/区间/来源与单位。。

关键对象/关联：`/ietf-network:networks/network/network-types/ietf-network-vpn-pm:service`；`/ietf-network:networks/network/ietf-network-topology:link/ietf-network-vpn-pm:perf-mon`；`/ietf-network:networks/network/node/ietf-network-topology:termination-point/ietf-network-vpn-pm:pm-statistics`；`/ietf-network:networks/network/node/ietf-network-topology:termination-point/ietf-network-vpn-pm:perf-mon`。

核对章节：§3；§4.1；§4.2；§4.3；§4.4；§5。研读深度：原文范围、关键树/schema路径、RFC目录状态及IANA登记核对；未编译或验证实例。

### RFC9617 A YANG Data Model for In Situ Operations, Administration, and Maintenance (IOAM)

[官方原文](https://www.rfc-editor.org/rfc/rfc9617.html) · PROPOSED STANDARD · 类型：YANG IOAM配置模型

模块：`ietf-ioam`。

支持：IOAM profile/过滤、实际支持接口与trace/direct-export/POT/e2e配置，可与节点/路径telemetry关联。。

边界：配置模型不等于完整IOAM trace导出格式；报文字段规范在9197/9326等，能力不能默认开启。。

关键对象/关联：`/ietf-ioam:ioam/info/available-interface[if-name]`；`/ietf-ioam:ioam/profiles/profile[profile-name]`。

核对章节：§3；§4。研读深度：原文范围、关键树/schema路径、RFC目录状态及IANA登记核对；未编译或验证实例。

### draft-ietf-bmwg-network-tester-cfg-17 A YANG Data Model for Network Tester Management

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-bmwg-network-tester-cfg-17) · WG Internet-Draft; RFC Ed Queue（固定 -17 文本 2026-07-03；尚未发布 RFC） · 类型：YANG Internet-Draft

模块：`ietf-traffic-generator`、`ietf-traffic-analyzer`。

支持：管理测试流量的frame、burst、gap、modifier及分析/capture功能，关联测试接口。。

边界：明确面向laboratory-isolated环境；进入批准流程仍不是已发布RFC，不能自动在实验网/生产网发包。。

关键对象/关联：`/ietf-interfaces:interfaces/interface/ietf-traffic-generator:traffic-generator`；`/ietf-interfaces:interfaces/interface/ietf-traffic-analyzer:traffic-analyzer`。

核对章节：固定-17：generator/analyzer模型与tree。研读深度：Datatracker状态与固定版本原文、模块名/树路径核对；不作为已发布RFC或正式登记证明；未编译/验证实例。

### draft-ietf-ippm-ioam-integrity-yang-08 A YANG Data Model for In Situ Operations, Administration, and Maintenance (IOAM) Integrity-Protected Options

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-ippm-ioam-integrity-yang-08) · 工作组草案；RFC Ed Queue；尚未发布RFC · 类型：model

模块：`ietf-ioam-integrity`、`iana-ioam-integrity-protection-methods`。

支持：IOAM完整性保护选项与方法。

边界：观测保护不等于物理拓扑完备。

核对章节：Abstract；YANG module declarations。研读深度：官方摘要、模块声明、Datatracker发布状态及IANA登记核对；未逐字段审查。

### draft-ietf-ippm-stamp-yang-12 Simple Two-way Active Measurement Protocol (STAMP) Data Model

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-ippm-stamp-yang-12) · Expired WG Internet-Draft（最新文本2023-11-05） · 类型：YANG Internet-Draft

模块：`ietf-stamp`。

支持：STAMP sender/reflector候选配置及状态树，可供研究参考。。

边界：Datatracker明确Expired，不是已发布模型；不能与正式TWAMP8913模型或STAMP8762协议混为一谈。。

关键对象/关联：`/ietf-stamp:stamp/stamp-session-sender`；`/ietf-stamp:stamp-state`。

核对章节：固定-12：model/tree/module。研读深度：Datatracker状态与固定版本原文、模块名/树路径核对；不作为已发布RFC或正式登记证明；未编译/验证实例。

### draft-ietf-opsawg-discardmodel-16 Information and Data Models for Packet Discard Reporting

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-opsawg-discardmodel-16) · 工作组草案；RFC Ed Queue；尚未发布RFC · 类型：model

模块：`ietf-packet-discard-reporting-common`、`ietf-packet-discard-reporting-sx`、`ietf-packet-discard-reporting`。

支持：丢包原因信息模型及接口/设备/控制平面报告数据模型。

边界：需要真实丢包证据，不是自动根因算法。

核对章节：Abstract；YANG module declarations。研读深度：官方摘要、模块声明、Datatracker发布状态及IANA登记核对；未逐字段审查。

## SIMAP与资源清单

### draft-havel-nmop-simap-yang-03 A YANG Data Model for SIMAP

[官方原文](https://datatracker.ietf.org/doc/html/draft-havel-nmop-simap-yang-03) · 个人草案 · 类型：model

模块：`ietf-simap-topology`、`ietf-simap-topology`。

支持：通用拓扑补充、导航与生命周期。

边界：个人草案；需逐项比对树图与代码。

核对章节：7；8；9。研读深度：范围/概览/关键定义核对；未对本目录整体编译。

### draft-ietf-green-power-and-energy-yang-04 Power and Energy YANG Module

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-green-power-and-energy-yang-04) · 工作组草案 · 类型：model

模块：`ietf-power-and-energy`、`iana-power-and-energy`。

支持：能源对象、功率和能量状态及关系。

边界：不是链路发现。

核对章节：3；4；5。研读深度：范围/概览/关键定义核对；未对本目录整体编译。

### draft-ietf-ivy-entitlement-inventory-05 A YANG Module for Entitlement Inventory

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-ivy-entitlement-inventory-05) · 工作组草案 · 类型：model

模块：`ietf-entitlement-inventory`。

支持：能力、许可及资源关联。

边界：许可不证明邻接。

核对章节：3；4；5；6。研读深度：范围/概览/关键定义核对；未对本目录整体编译。

### draft-ietf-ivy-network-inventory-location-06 A YANG Data Model for Network Inventory Location

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-ivy-network-inventory-location-06) · 工作组草案 · 类型：model

模块：`ietf-ni-location`。

支持：层级位置、机架与机箱位置。

边界：不等于UI坐标。

核对章节：2；3；4；5。研读深度：范围/概览/关键定义核对；未对本目录整体编译。

### draft-ietf-ivy-network-inventory-software-04 A YANG Network Data Model of Network Inventory Software Extensions

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-ivy-network-inventory-software-04) · 工作组草案 · 类型：model

模块：`ietf-network-inventory-sw-ext`。

支持：虚拟、容器、软件NE与软件状态。

边界：不是物理端口连通模型。

核对章节：2；3；4；5；6。研读深度：范围/概览/关键定义核对；未对本目录整体编译。

### draft-ietf-ivy-network-inventory-topology-11 A YANG Network Data Model for Inventory Topology Mapping

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-ivy-network-inventory-topology-11) · 工作组草案 · 类型：model

模块：`ietf-network-inventory-topology`。

支持：node/TP到NE及端口映射、链路介质、breakout能力。

边界：channel到具体子接口映射不在范围内。

核对章节：3；4；5。研读深度：范围/概览/关键定义核对；未对本目录整体编译。

### draft-ietf-ivy-network-inventory-yang-20 A Base YANG Data Model for Network Inventory

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-ivy-network-inventory-yang-20) · 工作组草案 · 类型：model

模块：`ietf-network-inventory`。

支持：网络范围NE与组件清单。

边界：发现与稳定实体识别机制由实现负责。

核对章节：3；4；5。研读深度：范围/概览/关键定义核对；未对本目录整体编译。

### draft-ietf-ivy-passive-network-inventory-01 A YANG Data Model for Passive Network Inventory

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-ivy-passive-network-inventory-01) · 工作组草案 · 类型：model

模块：`ietf-nwi-passive-inventory`。

支持：被动设备、端口、缆线、端点与位置。

边界：WG已替代个人草案；接线须独立证据。

核对章节：4；5；6。研读深度：范围/概览/关键定义核对；未对本目录整体编译。

### draft-ietf-nmop-simap-concept-14 SIMAP: Concept, Requirements, and Use Cases

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-nmop-simap-concept-14) · 工作组草案 · 类型：architecture

模块：无独立 YANG 模块（见文档类型与范围）。

支持：服务基础设施映射需求与用例。

边界：WG概念草案，无完整YANG Schema。

核对章节：3；4；5。研读深度：范围/概览/关键定义核对；未对本目录整体编译。

### draft-irtf-nmrg-network-digital-twin-arch-13 Network Digital Twin (NDT): Concepts and Reference Architecture

[官方原文](https://datatracker.ietf.org/doc/html/draft-irtf-nmrg-network-digital-twin-arch-13) · IRTF RG草案 · 类型：architecture

模块：无独立 YANG 模块（见文档类型与范围）。

支持：数字孪生概念与参考架构。

边界：IRTF研究草案，不是IETF标准模型。

核对章节：3；4；5。研读深度：范围/概览/关键定义核对；未对本目录整体编译。

## SR/MPLS 与 SRv6

### RFC8402 Segment Routing Architecture

[官方原文](https://www.rfc-editor.org/rfc/rfc8402.html) · PROPOSED STANDARD · 类型：architecture

模块：无独立 YANG 模块（见文档类型与范围）。

支持：Segment Routing 的 SID/segment list、SR domain 与 MPLS/IPv6 数据平面架构。。

边界：Standards Track 架构，非 YANG；SRGBP/SID 实例需要事实，不能由普通路由表推造。。

核对章节：§2–4。研读深度：abstract, document status and selected architecture/protocol sections; not exhaustive protocol review。

### RFC8986 Segment Routing over IPv6 (SRv6) Network Programming

[官方原文](https://www.rfc-editor.org/rfc/rfc8986.html) · PROPOSED STANDARD · 类型：protocol

模块：无独立 YANG 模块（见文档类型与范围）。

支持：SRv6 Network Programming 的 endpoint behaviors、SID 与服务处理行为。。

边界：Standards Track 数据面行为规范，非 YANG；不能代替 SRv6 base/SR policy 管理模型。。

核对章节：§3–5。研读深度：abstract, document status and selected architecture/protocol sections; not exhaustive protocol review。

### RFC9020 YANG Data Model for Segment Routing

[官方原文](https://www.rfc-editor.org/rfc/rfc9020.html) · PROPOSED STANDARD · 类型：device-yang

模块：`ietf-segment-routing`、`ietf-segment-routing-common`、`ietf-segment-routing-mpls`。

支持：SR 基础、公共 SID 类型与 MPLS 数据平面；SRGB/SRLB、prefix/adjacency SID、label 资源及状态。。

边界：核心设备模型，未独立构造网络 SR 拓扑；SRv6 基础和 SR Policy 另有草案，不能把本 RFC 写成完整 SRv6/策略标准模型。。

关键对象/关联：`/ietf-routing:routing/ietf-segment-routing:segment-routing/ietf-segment-routing-mpls:sr-mpls`；`ietf-segment-routing-common algorithm identities`；`/ietf-routing:routing/ietf-segment-routing:segment-routing`。

核对章节：§3–8。研读深度：abstract, model tree, module import/augment and selected semantic sections; not compiled。

### RFC9256 Segment Routing Policy Architecture

[官方原文](https://www.rfc-editor.org/rfc/rfc9256.html) · PROPOSED STANDARD · 类型：architecture

模块：无独立 YANG 模块（见文档类型与范围）。

支持：SR Policy 的 headend/color/endpoint、candidate path、segment list、binding SID、策略选择架构。。

边界：Standards Track 架构，非 YANG；管理模型是独立 sr-policy-yang 草案。。

核对章节：§2–5；§8–9。研读深度：abstract, document status and selected architecture/protocol sections; not exhaustive protocol review。

### RFC9702 YANG Data Model for Maximum Segment Identifier (SID) Depth (MSD) Types and MPLS MSD

[官方原文](https://www.rfc-editor.org/rfc/rfc9702.html) · PROPOSED STANDARD · 类型：types-and-device-yang

模块：`iana-msd-types`、`ietf-mpls-msd`。

支持：iana-msd-types 和 MPLS node/link 最大 SID 深度；扩展 MPLS base 的设备与接口能力。。

边界：能力上限而非已使用 SID 数或最优路径；不能由能 ping 通/普通 IP 接口推出 MSD。。

关键对象/关联：`/ietf-routing:routing/ietf-mpls:mpls/ietf-mpls-msd:node-msds`；`/ietf-routing:routing/ietf-mpls:mpls/interfaces/interface/ietf-mpls-msd:link-msds`；`iana-msd-types:msd-type`。

核对章节：§2–4。研读深度：abstract, model tree, module import/augment and selected semantic sections; not compiled。

### RFC9902 A YANG Data Model for IS-IS Segment Routing over the MPLS Data Plane

[官方原文](https://www.rfc-editor.org/rfc/rfc9902.html) · PROPOSED STANDARD · 类型：device-yang

模块：`ietf-isis-sr-mpls`。

支持：IS-IS SR-MPLS activation、mapping server、adjacency/prefix SID、FRR 及 LSDB SR 状态；扩展 RFC9130 IS-IS 和 RFC9020 SR。。

边界：协议设备模型与 LSDB，不等于独立网络 topology augment；LSDB/adjacency 身份和物理设备身份需另证。。

关键对象/关联：`/ietf-routing:routing/control-plane-protocols/control-plane-protocol/ietf-isis:isis/ietf-isis-sr-mpls:segment-routing`；`/ietf-routing:routing/control-plane-protocols/control-plane-protocol/ietf-isis:isis/interfaces/interface`；`IS-IS LSP SR capability/prefix-SID/adj-SID augmentations`。

核对章节：§2–3。研读深度：abstract, model tree, module import/augment and selected semantic sections; not compiled。

### RFC9903 A YANG Data Model for OSPF Segment Routing over the MPLS Data Plane

[官方原文](https://www.rfc-editor.org/rfc/rfc9903.html) · PROPOSED STANDARD · 类型：device-yang

模块：`ietf-ospf-sr-mpls`。

支持：OSPF SR-MPLS、区域/接口 SID、FRR、邻居/LSA 状态；扩展 RFC9129 OSPF 与 RFC9020 SR。。

边界：同样是协议树而非物理图；SRv6 或完整 SR Policy 不由本模块提供。。

关键对象/关联：`/ietf-routing:routing/control-plane-protocols/control-plane-protocol/ietf-ospf:ospf/ietf-ospf-sr-mpls:segment-routing`；`/ietf-routing:routing/control-plane-protocols/control-plane-protocol/ietf-ospf:ospf/areas/area/interfaces/interface`；`OSPFv2/v3 LSDB SR-MPLS augmentations`。

核对章节：§2–3。研读深度：abstract, model tree, module import/augment and selected semantic sections; not compiled。

### draft-ietf-spring-sr-policy-yang-08 YANG Data Model for Segment Routing Policy

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-spring-sr-policy-yang-08) · Internet-Draft / Active · 类型：device-and-types-yang

模块：`ietf-sr-policy-types`、`ietf-sr-policy`。

支持：SR Policy（MPLS/SRv6）candidate path、segment list、binding SID、约束和状态；ietf-sr-policy-types 为类型库。。

边界：-08 活跃草案，文档仍列 Pending Items；RFC9256 为策略架构，不能称其为 YANG 模块或已完成此草案全部字段。。

关键对象/关联：`/ietf-routing:routing/ietf-sr-policy:segment-routing/traffic-engineering/policies/policy`。

核对章节：§3–7。研读深度：abstract, model tree, module import/augment and selected semantic sections; not compiled。

### draft-ietf-spring-srv6-yang-base-02 YANG Data Model for SRv6 Base

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-spring-srv6-yang-base-02) · Internet-Draft / Active · 类型：device-and-types-yang

模块：`ietf-srv6-types`、`ietf-srv6-base`。

支持：SRv6 locator、SID 格式/压缩、分配、local SID 功能与资源；ietf-srv6-base 扩展 RFC9020 SR，ietf-srv6-types 可复用。。

边界：-02 活跃草案，尚无正式 SRv6 base YANG RFC；SRv6 转发行为 RFC8986 不是此管理模型，亦不直接提供网络 SRv6 拓扑。。

关键对象/关联：`/ietf-routing:routing/ietf-segment-routing:segment-routing/ietf-srv6-base:srv6`。

核对章节：§3–7。研读深度：abstract, model tree, module import/augment and selected semantic sections; not compiled。

## TE/包交换

### draft-ietf-teas-yang-l3-te-topo-19 YANG Data Models for Layer 3 and Packet TE Topologies

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-teas-yang-l3-te-topo-19) · Internet-Draft / Active · 类型：topology-yang

模块：`ietf-l3-te-topology`、`ietf-te-topology-packet`、`ietf-l3-te-topology-state`、`ietf-te-topology-packet-state`。

支持：L3 unicast↔TE topology/node/TP/link 引用；packet TE 的带宽、标签和性能属性，组合 RFC8346 与 RFC8795；另列旧 -state 兼容模块。。

边界：-19 活跃草案，WG Consensus: Waiting for Write-Up；不是 RFC8346 已有字段的任意改义许可。多个视图关联须有真实资源映射。。

核对章节：§2–4。研读深度：abstract, model tree, module import/augment and selected semantic sections; not compiled。

### draft-ietf-teas-yang-te-mpls-topology-05 A YANG Data Model for MPLS-TE Topology

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-teas-yang-te-mpls-topology-05) · Internet-Draft / Active · 类型：topology-and-types-yang

模块：`ietf-mpls-te-types`、`ietf-te-mpls-topology`。

支持：ietf-mpls-te-types 提供标签/类型，ietf-te-mpls-topology 为 packet TE 拓扑增加 MPLS 资源、TP/链接标签；也可用于 MPLS-TP。。

边界：-05 活跃草案；依赖 packet TE 草案，不能以 MPLS base RFC8960 代替该全网拓扑扩展。。

核对章节：§2–5。研读深度：abstract, model tree, module import/augment and selected semantic sections; not compiled。

## TE/历史与缺口

### draft-ietf-ccamp-flexigrid-media-channel-yang-04 A YANG Data Model for Flexi-Grid Media Channels

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-ccamp-flexigrid-media-channel-yang-04) · Internet-Draft / Replaced · 类型：device-and-network-yang

模块：`ietf-flexi-grid-media-channel`。

支持：旧 ietf-flexi-grid-media-channel。。

边界：最新 -04，Replaced by flexigrid-tunnel-yang，再由 wdm-tunnel-yang 替代；只作历史索引。。

核对章节：模型概览/模块。研读深度：abstract, model tree, module import/augment and selected semantic sections; not compiled。

### draft-ietf-ccamp-flexigrid-tunnel-yang-03 A YANG Data Model for Flexi-Grid Tunnels

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-ccamp-flexigrid-tunnel-yang-03) · Internet-Draft / Replaced · 类型：device-and-network-yang

模块：`ietf-flexi-grid-tunnel`。

支持：旧 ietf-flexi-grid-tunnel 的光 media channel/tunnel 扩展。。

边界：最新 -03，Replaced by draft-ietf-ccamp-wdm-tunnel-yang。。

核对章节：模型概览/模块。研读深度：abstract, model tree, module import/augment and selected semantic sections; not compiled。

### draft-ietf-ccamp-wson-tunnel-model-09 A Yang Data Model for WSON Tunnel

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-ccamp-wson-tunnel-model-09) · Internet-Draft / Replaced · 类型：device-and-network-yang

模块：`ietf-wson-tunnel`。

支持：旧 ietf-wson-tunnel 的 WSON 路径/tunnel 扩展。。

边界：最新 -09，Replaced by draft-ietf-ccamp-wdm-tunnel-yang，不能作为当前优先方案。。

核对章节：模型概览/模块。研读深度：abstract, model tree, module import/augment and selected semantic sections; not compiled。

### draft-ietf-detnet-topology-yang-02 Deterministic Networking (DetNet) Topology YANG Model

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-detnet-topology-yang-02) · Internet-Draft / Expired / WG Dead · 类型：topology-yang

模块：`ietf-detnet-topology`。

支持：DetNet node/link/TP 能力在 RFC8795 图上的拓扑扩展。。

边界：最新 -02，Expired + WG Dead；不是活跃草案或已发布模型。RFC9633 只覆盖设备流，因此这里仍有独立全网模型缺口。。

核对章节：模型概览/模块。研读深度：abstract, model tree, module import/augment and selected semantic sections; not compiled。

### draft-ietf-mpls-static-yang-14 A YANG Data Model for MPLS Static LSPs

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-mpls-static-yang-14) · Internet-Draft / Expired · 类型：device-yang

模块：`ietf-mpls-static`。

支持：基于 RFC8960 MPLS base 配置/管理逐设备静态 LSP。。

边界：最新 -14，Expired，未发布正式 RFC；静态路径不能由普通 route entry 假设生成。。

核对章节：模型概览/模块。研读深度：abstract, model tree, module import/augment and selected semantic sections; not compiled。

### draft-ietf-teas-te-topo-and-tunnel-modeling-06 TE Topology and Tunnel Modeling for Transport Networks

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-teas-te-topo-and-tunnel-modeling-06) · Internet-Draft / Expired · 类型：applicability

模块：无独立 YANG 模块（见文档类型与范围）。

支持：transport TE topology/tunnel 的节点、链路、TTP/LTP、抽象与多层建模讨论。。

边界：最新 -06，Expired，自 2020 文本以后没有新版，不是正式 RFC 或活跃规范；历史解释不得越过 RFC8795 与当前模型原文。。

核对章节：模型概览/模块。研读深度：abstract, document status and selected architecture/protocol sections; not exhaustive protocol review。

### draft-ietf-teas-yang-rsvp-te-10 A YANG Data Model for RSVP-TE Protocol

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-teas-yang-rsvp-te-10) · Internet-Draft / Expired · 类型：device-yang

模块：`ietf-rsvp-te`、`ietf-rsvp-te-mpls`。

支持：RSVP-TE 通用与 MPLS 扩展，在 RSVP/routing 和 TE tunnel/interface/LSP 上 augment。。

边界：最新 -10，Expired（文本 2026-01），截至截止日未发布正式 RFC；基础 RSVP-21 仍活跃，状态不同。。

核对章节：模型概览/模块。研读深度：abstract, model tree, module import/augment and selected semantic sections; not compiled。

### draft-ietf-teas-yang-sr-te-topo-19 YANG Data Model for SR and SR TE Topologies on MPLS Data Plane

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-teas-yang-sr-te-topo-19) · Internet-Draft / Expired · 类型：topology-yang

模块：`ietf-sr-mpls-topology`、`ietf-sr-mpls-topology-state`。

支持：SR-MPLS topology、SID/prefix/adjacency 属性对 RFC8346 的扩展，附旧 -state 模块。。

边界：最新 -19，Expired；RFC9902/9903 是 IGP 原生 SR-MPLS 设备树，不应写成替代这份全网 topology 模块的 RFC。。

核对章节：模型概览/模块。研读深度：abstract, model tree, module import/augment and selected semantic sections; not compiled。

### draft-ietf-teas-yang-te-mpls-04 A YANG Data Model for MPLS Traffic Engineering Tunnels

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-teas-yang-te-mpls-04) · Internet-Draft / Expired · 类型：device-and-network-yang

模块：`ietf-te-mpls`。

支持：在通用 ietf-te 上扩展 MPLS tunnel/path bandwidth/label/backup 数据。。

边界：最新 -04，Expired，尚无正式 MPLS-TE tunnel YANG RFC；不等于活跃 MPLS-TE topology-05。。

核对章节：模型概览/模块。研读深度：abstract, model tree, module import/augment and selected semantic sections; not compiled。

## TE/基础与多层

### RFC8776 Common YANG Data Types for Traffic Engineering

[官方原文](https://www.rfc-editor.org/rfc/rfc8776.html) · PROPOSED STANDARD · 类型：types-yang

模块：`ietf-te-types`、`ietf-te-packet-types`。

支持：TE 类型、identity、路由显式 hop、标签/带宽、路径约束与性能度量 grouping；packet 模块提供包交换专用类型。。

边界：没有独立实例树；不能把类型库当成拓扑或隧道模型。RFC8776-update-24 拟替代本 RFC，但截至截止日尚未正式发布。。

核对章节：§3–5。研读深度：abstract, model tree, module import/augment and selected semantic sections; not compiled。

### RFC8795 YANG Data Model for Traffic Engineering (TE) Topologies

[官方原文](https://www.rfc-editor.org/rfc/rfc8795.html) · PROPOSED STANDARD · 类型：topology-yang

模块：`ietf-te-topology`、`ietf-te-topology-state`。

支持：技术无关 TE 图：provider/client/topology 作用域、node/link/LTP/TTP、可用带宽、SRLG、管理/运行状态和 TED 信息来源。；支持抽象 overlay/underlay、supporting 关系、connectivity matrix、跨层 transition 和 inter-layer-lock；一个 TE link 可代表多条物理链路或链路的一部分。。

边界：TE 节点/链路是资源抽象，不能据此断定一节点一机箱或一链路一根线。；拓扑实例与技术参数需外部事实；模型不生成路径计算算法或物理发现证据。Appendix B 的 -state 模块为旧架构兼容，NMDA 主模块是 ietf-te-topology。。

关键对象/关联：`/ietf-network:networks/network/ietf-te-topology:te`；`/ietf-network:networks/network/node/ietf-te-topology:te`；`/ietf-network:networks/network/ietf-network-topology:link/ietf-te-topology:te`。

核对章节：§2–5；§6；§7；Appendix B。研读深度：abstract, model tree, module import/augment and selected semantic sections; not compiled。

### RFC9522 Overview and Principles of Internet Traffic Engineering

[官方原文](https://www.rfc-editor.org/rfc/rfc9522.html) · INFORMATIONAL · 类型：architecture

模块：无独立 YANG 模块（见文档类型与范围）。

支持：当前 Internet TE 原则、流量需求、控制、优化、资源与部署概念；正式替代 RFC3272。。

边界：Informational，背景术语而非 schema；容量/流量/度量必须量测或明确配置。。

核对章节：§2–6。研读深度：abstract, document status and selected architecture/protocol sections; not exhaustive protocol review。

### draft-ietf-teas-rfc8776-update-24 Common YANG Data Types for Traffic Engineering

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-teas-rfc8776-update-24) · Internet-Draft / RFC Editor queue (Blocked) · 类型：types-yang

模块：`ietf-te-types`、`ietf-te-packet-types`。

支持：扩展 TE 通用类型、identity、grouping，供 topology/tunnel/path 使用，拟 obsoletes RFC8776。。

边界：-24 在 RFC Editor queue（Blocked）；尚未发生正式 RFC 替代，不能默默将同名模块新版混入旧冻结依赖。。

核对章节：§3–5。研读深度：abstract, model tree, module import/augment and selected semantic sections; not compiled。

### draft-ietf-teas-te-topology-profiles-06 Profiles for Traffic Engineering (TE) Topology Data Model and Applicability to non-TE-centric Use Cases

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-teas-te-topology-profiles-06) · Internet-Draft / Active · 类型：applicability

模块：无独立 YANG 模块（见文档类型与范围）。

支持：RFC8795 的裁剪 profile 与非 TE 中心用法，讨论跨域链路、状态、overlay/underlay、交换限制与 multipoint。。

边界：-06 活跃草案，无新 YANG 模块；用例/profile 不能充当新的正式 schema，也不解除具体叶子的语义和证据要求。。

核对章节：§2–4。研读深度：abstract, document status and selected architecture/protocol sections; not exhaustive protocol review。

### draft-ietf-teas-yang-topology-filter-03 YANG Data Model for Topology Filter

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-teas-yang-topology-filter-03) · Internet-Draft / Active · 类型：topology-management-yang

模块：`ietf-topology-filter`。

支持：管理 TE topology 过滤规则及 filter-set，指定 topology/reference/resource/约束，可供网元和控制器生成受限视图。。

边界：-03 活跃草案；过滤输出是策略视图，不代表底层全部资源或物理全图。。

关键对象/关联：`/ietf-network:networks/ietf-topology-filter:topology-filters`。

核对章节：§1.2；§2。研读深度：abstract, model tree, module import/augment and selected semantic sections; not compiled。

## TVR/时间变化

### RFC8413 Framework for Scheduled Use of Resources

[官方原文](https://www.rfc-editor.org/rfc/rfc8413.html) · INFORMATIONAL · 类型：architecture

模块：无独立 YANG 模块（见文档类型与范围）。

支持：计划使用资源的时间/预留框架；可用于计划带宽和资源预约，RFC9922 Appendix C 展示使用例。。

边界：Informational 框架，非网络历史快照 schema，也不自动执行资源调度。。

核对章节：§3–5。研读深度：abstract, document status and selected architecture/protocol sections; not exhaustive protocol review。

### RFC9657 Time-Variant Routing (TVR) Use Cases

[官方原文](https://www.rfc-editor.org/rfc/rfc9657.html) · INFORMATIONAL · 类型：use-cases

模块：无独立 YANG 模块（见文档类型与范围）。

支持：TVR 的资源节约、运行效率、动态可达性/卫星等可预期变化用例。。

边界：Informational 用例，非 YANG 或路由算法规范。。

核对章节：§3–5。研读深度：abstract, document status and selected architecture/protocol sections; not exhaustive protocol review。

### RFC9922 A Common YANG Data Model for Scheduling

[官方原文](https://www.rfc-editor.org/rfc/rfc9922.html) · PROPOSED STANDARD · 类型：types-yang

模块：`ietf-schedule`。

支持：通用 schedule identity/types/groupings：period、多个层级 recurrence、iCalendar、schedule status，可供拓扑/资源模型复用。。

边界：只提供可复用类型/grouping，没有自身的 writable/state/RPC 实例树；不定义触发动作或冲突检测/解决，不等于拓扑历史或故障预测。。

核对章节：§3–4；§6；Appendices B–C。研读深度：abstract, model tree, module import/augment and selected semantic sections; not compiled。

### draft-ietf-tvr-applicability-01 Applicability of TVR YANG Data Models

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-tvr-applicability-01) · Internet-Draft / Active · 类型：applicability

模块：无独立 YANG 模块（见文档类型与范围）。

支持：TVR node/topology 模型用于集中或分布式场景、编码/管理协议与时钟同步说明。。

边界：-01 活跃草案，无新 YANG 模块；不将 planned schedule 与故障状态历史混为一类。。

核对章节：§3–5。研读深度：abstract, document status and selected architecture/protocol sections; not exhaustive protocol review。

### draft-ietf-tvr-off-path-exposure-03 Using off-path mechanisms for exposing Time-Variant Routing information

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-tvr-off-path-exposure-03) · Internet-Draft / Active · 类型：architecture

模块：无独立 YANG 模块（见文档类型与范围）。

支持：off-path 暴露未来计划变化，用于外部/内部应用；讨论 controller、ALTO 等机制与路由控制平面的分离。。

边界：-03 活跃草案，无新 YANG；替代旧 tvr-alto-exposure 名称，未来计划不能作为已发生事实。。

核对章节：§2–4。研读深度：abstract, document status and selected architecture/protocol sections; not exhaustive protocol review。

### draft-ietf-tvr-requirements-11 Time-Variant Routing (TVR) Requirements

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-tvr-requirements-11) · Internet-Draft / RFC Editor queue (In Progress) · 类型：requirements

模块：无独立 YANG 模块（见文档类型与范围）。

支持：TVR 时间域、计划可见性、配置与运行状态、路径计算的信息和行为需求。。

边界：-11 在 RFC Editor queue；需求文档非 YANG、非实际计划/预测记录。。

核对章节：§2–4。研读深度：abstract, document status and selected architecture/protocol sections; not exhaustive protocol review。

### draft-ietf-tvr-schedule-yang-12 A YANG Data Model for Scheduled Attributes

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-tvr-schedule-yang-12) · Internet-Draft / RFC Editor queue (In Progress) · 类型：topology-device-and-types-yang

模块：`ietf-tvr-schedule`、`ietf-tvr-node`、`ietf-tvr-topology`。

支持：三模块：ietf-tvr-schedule 复用 RFC9922，ietf-tvr-node 设备计划的电源/接口可用性/带宽/邻居，ietf-tvr-topology 给 RFC8345 node/TP 加时间计划。。

边界：-12 在 RFC Editor queue；描述可预期的 planned changes，非即时 config、故障历史或未知故障预测。实际 topology link-availability-schedule 放在 TP augment，而不是直接 augment nt:link；delay 是传播延迟，不含排队。。

关键对象/关联：`/ietf-tvr-node:node-schedule`。

核对章节：§3–6。研读深度：abstract, model tree, module import/augment and selected semantic sections; not compiled。

## VPN 与覆盖网络

### RFC10018 Multicast and Ethernet VPN with Segment Routing Point-to-Multipoint (P2MP) and Ingress Replication

[官方原文](https://www.rfc-editor.org/rfc/rfc10018.html) · PROPOSED STANDARD · 类型：protocol

模块：无独立 YANG 模块（见文档类型与范围）。

支持：2026-08 MVPN/EVPN over SR P2MP 与 ingress replication；更新 RFC6514/7988。。

边界：PMSI/replication tree 是逻辑业务分支，非物理电缆树；无新 YANG 模块。。

核对章节：2；3；4。研读深度：官方元数据、模型相关协议章节与替代/更新关系核对；未逐项穷举全部报文细节。

### RFC4364 BGP/MPLS IP Virtual Private Networks (VPNs)

[官方原文](https://www.rfc-editor.org/rfc/rfc4364.html) · PROPOSED STANDARD · 类型：architecture

模块：无独立 YANG 模块（见文档类型与范围）。

支持：VRF、RD、RT、VPNv4 与 PE/CE 的 BGP/MPLS L3VPN 协议架构。。

边界：RD 使前缀唯一，RT 表达导入导出策略；它们都不能简单等同全球 VRF 名或唯一物理 chassis。。

核对章节：3；4；5；6。研读深度：官方元数据、模型相关协议章节与替代/更新关系核对；未逐项穷举全部报文细节。

### RFC4664 Framework for Layer 2 Virtual Private Networks (L2VPNs)

[官方原文](https://www.rfc-editor.org/rfc/rfc4664.html) · INFORMATIONAL · 类型：architecture

模块：无独立 YANG 模块（见文档类型与范围）。

支持：VPWS/VPLS 与 PE/CE/AC/PW 的 L2VPN 框架。。

边界：逻辑 service/AC/PW 区别于物理线缆；Informational 框架而非 YANG。。

核对章节：3；4；5。研读深度：官方元数据、模型相关协议章节与替代/更新关系核对；未逐项穷举全部报文细节。

### RFC4665 Service Requirements for Layer 2 Provider-Provisioned Virtual Private Networks

[官方原文](https://www.rfc-editor.org/rfc/rfc4665.html) · INFORMATIONAL · 类型：architecture

模块：无独立 YANG 模块（见文档类型与范围）。

支持：运营商 L2VPN 服务需求、管理与弹性要求。。

边界：需求文件不证明服务已部署，不是 YANG。。

核对章节：3；4；5。研读深度：官方元数据、模型相关协议章节与替代/更新关系核对；未逐项穷举全部报文细节。

### RFC4761 Virtual Private LAN Service (VPLS) Using BGP for Auto-Discovery and Signaling

[官方原文](https://www.rfc-editor.org/rfc/rfc4761.html) · PROPOSED STANDARD · 类型：protocol

模块：无独立 YANG 模块（见文档类型与范围）。

支持：BGP 发现/信令 VPLS。。

边界：VPLS 逻辑 bridge 与 PW 全连接不等于物理全连接。。

核对章节：3；4；5。研读深度：官方元数据、模型相关协议章节与替代/更新关系核对；未逐项穷举全部报文细节。

### RFC4762 Virtual Private LAN Service (VPLS) Using Label Distribution Protocol (LDP) Signaling

[官方原文](https://www.rfc-editor.org/rfc/rfc4762.html) · PROPOSED STANDARD · 类型：protocol

模块：无独立 YANG 模块（见文档类型与范围）。

支持：LDP 信令 VPLS、PW 和 MAC 学习。。

边界：远端 MAC 可经多 hop/PW；学习口不是主机线缆直连证明。。

核对章节：3；4；5。研读深度：官方元数据、模型相关协议章节与替代/更新关系核对；未逐项穷举全部报文细节。

### RFC7348 Virtual eXtensible Local Area Network (VXLAN): A Framework for Overlaying Virtualized Layer 2 Networks over Layer 3 Networks

[官方原文](https://www.rfc-editor.org/rfc/rfc7348.html) · INFORMATIONAL · 类型：architecture

模块：无独立 YANG 模块（见文档类型与范围）。

支持：VXLAN VNI、VTEP、UDP 封装、L2 over L3；Informational。。

边界：VNI 与 VLAN 不是全球一一对应；VTEP overlay 连通不证明 underlay 电缆；未定义正式 YANG。。

核对章节：3；4；5。研读深度：官方元数据、模型相关协议章节与替代/更新关系核对；未逐项穷举全部报文细节。

### RFC7432 BGP MPLS-Based Ethernet VPN

[官方原文](https://www.rfc-editor.org/rfc/rfc7432.html) · PROPOSED STANDARD · 类型：protocol

模块：无独立 YANG 模块（见文档类型与范围）。

支持：EVPN ESI、Ethernet tag、MAC/IP route、DF、multihoming 和 RT/RD。。

边界：ESI 是 Ethernet Segment 标识，MAC/IP 通告可含多个接入与 mobility；不能直接按 MAC 合并物理 host。。

核对章节：6；7；8；9；10。研读深度：官方元数据、模型相关协议章节与替代/更新关系核对；未逐项穷举全部报文细节。

### RFC8049 YANG Data Model for L3VPN Service Delivery

[官方原文](https://www.rfc-editor.org/rfc/rfc8049.html) · PROPOSED STANDARD · 类型：YANG服务模型（旧版本）

模块：`ietf-l3vpn-svc`。

支持：L3SM历史版本，仅用于迁移/兼容核对。。

边界：已被RFC8299替代；新增导出应按8299冻结，不能混合同名模块修订。。

关键对象/关联：`/ietf-l3vpn-svc:l3vpn-svc`。

后继文档：RFC8299。

核对章节：§6。研读深度：RFC目录与替代关系核对；旧schema未逐项审查。

### RFC8077 Pseudowire Setup and Maintenance Using the Label Distribution Protocol (LDP)

[官方原文](https://www.rfc-editor.org/rfc/rfc8077.html) · INTERNET STANDARD · 类型：protocol

模块：无独立 YANG 模块（见文档类型与范围）。

支持：PW FEC/label setup、维护和状态；替代 RFC4447/6723。。

边界：PW 是模拟服务端点之间逻辑路径；不等于单根物理介质。。

核对章节：3；4；5。研读深度：官方元数据、模型相关协议章节与替代/更新关系核对；未逐项穷举全部报文细节。

### RFC8299 YANG Data Model for L3VPN Service Delivery

[官方原文](https://www.rfc-editor.org/rfc/rfc8299.html) · PROPOSED STANDARD · 类型：YANG客户服务模型

模块：`ietf-l3vpn-svc`。

支持：L3SM描述客户VPN服务、站点、接入和QoS/可用性要求；需要外部映射到PE与实际接口。。

边界：不是VRF/RIB快照；不能从LLDP/ARP生成客户vpn-id或SLA。。

关键对象/关联：`/ietf-l3vpn-svc:l3vpn-svc`；`/ietf-l3vpn-svc:l3vpn-svc/vpn-services/vpn-service[vpn-id]`；`/ietf-l3vpn-svc:l3vpn-svc/sites/site[site-id]/site-network-accesses/site-network-access[site-network-access-id]`。

核对章节：§4；§6；§8；§9。研读深度：原文范围、关键树/schema路径、RFC目录状态及IANA登记核对；未编译或验证实例。

### RFC8365 A Network Virtualization Overlay Solution Using Ethernet VPN (EVPN)

[官方原文](https://www.rfc-editor.org/rfc/rfc8365.html) · PROPOSED STANDARD · 类型：architecture

模块：无独立 YANG 模块（见文档类型与范围）。

支持：以 EVPN 为 VXLAN/其他 NVO 的控制面，支持 VNI、ESI、multihoming 和封装。。

边界：overlay 路由反射/封装与 underlay 实际路径不同；没有独立发布 device EVPN YANG。。

核对章节：3；4；5。研读深度：官方元数据、模型相关协议章节与替代/更新关系核对；未逐项穷举全部报文细节。

### RFC8466 A YANG Data Model for Layer 2 Virtual Private Network (L2VPN) Service Delivery

[官方原文](https://www.rfc-editor.org/rfc/rfc8466.html) · PROPOSED STANDARD · 类型：YANG客户服务模型

模块：`ietf-l2vpn-svc`。

支持：L2SM描述客户L2VPN服务、站点接入、服务拓扑、以太服务参数。。

边界：不是交换机MAC/VLAN运行树；服务拓扑是服务要求/抽象，不能当物理线缆。。

关键对象/关联：`/ietf-l2vpn-svc:l2vpn-svc`；`/ietf-l2vpn-svc:l2vpn-svc/vpn-services/vpn-service[vpn-id]`；`/ietf-l2vpn-svc:l2vpn-svc/sites/site[site-id]/site-network-accesses`。

核对章节：§3；§5；§6；§8。研读深度：原文范围、关键树/schema路径、RFC目录状态及IANA登记核对；未编译或验证实例。

### RFC9135 Integrated Routing and Bridging in Ethernet VPN (EVPN)

[官方原文](https://www.rfc-editor.org/rfc/rfc9135.html) · PROPOSED STANDARD · 类型：protocol

模块：无独立 YANG 模块（见文档类型与范围）。

支持：EVPN 对称/非对称 IRB 与 MAC-VRF/IP-VRF 转发。。

边界：MAC-VRF、IP-VRF、VLAN/VNI、路由/桥接层需分离；邻居表不能跨这些实例合并。。

核对章节：3；4；5；6。研读深度：官方元数据、模型相关协议章节与替代/更新关系核对；未逐项穷举全部报文细节。

### RFC9136 IP Prefix Advertisement in Ethernet VPN (EVPN)

[官方原文](https://www.rfc-editor.org/rfc/rfc9136.html) · PROPOSED STANDARD · 类型：protocol

模块：无独立 YANG 模块（见文档类型与范围）。

支持：EVPN route-type5 IP-prefix 广告和 overlay next-hop 解析。。

边界：Type5 Prefix route 不证明终端 attachment 或单根物理邻接。。

核对章节：3；4；5。研读深度：官方元数据、模型相关协议章节与替代/更新关系核对；未逐项穷举全部报文细节。

### RFC9181 A Common YANG Data Model for Layer 2 and Layer 3 VPNs

[官方原文](https://www.rfc-editor.org/rfc/rfc9181.html) · PROPOSED STANDARD · 类型：YANG公共类型/groupings

模块：`ietf-vpn-common`。

支持：统一VPN、封装、路由、状态、QoS等可复用词汇供L3NM/L2NM及AC模型使用。。

边界：该RFC正文示例ietf-l2vpn-ntw不是其正式模块；IANA登记归本RFC仅ietf-vpn-common。。

关键对象/关联：`ietf-vpn-common common identities/types`；`公共identities、typedefs、groupings；无独立VPN实例根`。

核对章节：§3；§4。研读深度：原文范围、关键树/schema路径、RFC目录状态及IANA登记核对；未编译或验证实例。

### RFC9182 A YANG Network Data Model for Layer 3 VPNs

[官方原文](https://www.rfc-editor.org/rfc/rfc9182.html) · PROPOSED STANDARD · 类型：YANG运营商网络模型

模块：`ietf-l3vpn-ntw`。

支持：L3NM绑定VPN节点、PE、network-access、路由与服务实施；与L3SM、SAP及底层设备/TE模型关联。。

边界：VPN-node是策略/服务实例抽象，不能直接按物理node-id认同；需显式映射和本地网络实例作用域。。

关键对象/关联：`/ietf-l3vpn-ntw:l3vpn-ntw`；`/ietf-l3vpn-ntw:l3vpn-ntw/vpn-services/vpn-service[vpn-id]/vpn-nodes/vpn-node[vpn-node-id]`；`.../vpn-network-accesses/vpn-network-access[id]`。

核对章节：§4；§5；§7；§8。研读深度：原文范围、关键树/schema路径、RFC目录状态及IANA登记核对；未编译或验证实例。

### RFC9291 A YANG Network Data Model for Layer 2 VPNs

[官方原文](https://www.rfc-editor.org/rfc/rfc9291.html) · PROPOSED STANDARD · 类型：YANG运营商网络模型及公共IANA identities

模块：`iana-bgp-l2-encaps`、`iana-pseudowire-types`、`ietf-ethernet-segment`、`ietf-l2vpn-ntw`。

支持：L2NM建模L2VPN、PW/VPLS/EVPN服务接入、多归属Ethernet Segment及network-access。。

边界：ESI/服务ID、控制平面状态及封装类型必须来自配置/管理证据；观察到两个接口不能造Ethernet Segment。。

关键对象/关联：`/ietf-l2vpn-ntw:l2vpn-ntw`；`/ietf-ethernet-segment:ethernet-segments`；`/ietf-l2vpn-ntw:l2vpn-ntw/vpn-services/vpn-service[vpn-id]/vpn-nodes/vpn-node[vpn-node-id]`；`/ietf-ethernet-segment:ethernet-segments/ethernet-segment[name]`。

核对章节：§4；§5；§6；§7；§8。研读深度：原文范围、关键树/schema路径、RFC目录状态及IANA登记核对；未编译或验证实例。

### RFC9408 A YANG Network Data Model for Service Attachment Points (SAPs)

[官方原文](https://www.rfc-editor.org/rfc/rfc9408.html) · PROPOSED STANDARD · 类型：RFC8345网络增补YANG模型

模块：`ietf-sap-ntw`。

支持：SAP提供/已提供服务的网络参考接入点，可关联parent-termination-point、attachment-interface与服务类型。。

边界：SAP不是自动发现的任意接口别名；不等同AC或物理缆线，需要provider服务交付及接口绑定证据。。

关键对象/关联：`/ietf-network:networks/network/network-types/ietf-sap-ntw:sap-network`；`/ietf-network:networks/network/node/ietf-sap-ntw:service[service-type]/sap[sap-id]`。

核对章节：§3；§4；§5；§6。研读深度：原文范围、关键树/schema路径、RFC目录状态及IANA登记核对；未编译或验证实例。

### RFC9746 BGP EVPN Multihoming Extensions for Split-Horizon Filtering

[官方原文](https://www.rfc-editor.org/rfc/rfc9746.html) · PROPOSED STANDARD · 类型：protocol

模块：无独立 YANG 模块（见文档类型与范围）。

支持：2025-03 multihoming split-horizon filtering 扩展，更新 RFC7432/8365。。

边界：split-horizon 与封装模式影响转发；不能仅凭 topology edge 判定循环/流量可达。。

核对章节：2；3。研读深度：官方元数据、模型相关协议章节与替代/更新关系核对；未逐项穷举全部报文细节。

### RFC9833 A Common YANG Data Model for Attachment Circuits

[官方原文](https://www.rfc-editor.org/rfc/rfc9833.html) · PROPOSED STANDARD · 类型：YANG公共AC类型/groupings

模块：`ietf-ac-common`。

支持：AC公共语义统一bearer、L2/L3接入、配置profile、状态与引用。。

边界：类型/grouping导入不产生业务实例；AC是必要接入配置，bearer为承载物理/逻辑连接，两者不能混称。。

关键对象/关联：`无独立实例根；service-status、ac-profile-cfg、L2/IP connection等可复用groupings`。

核对章节：§3；§4；§5。研读深度：原文范围、关键树/schema路径、RFC目录状态及IANA登记核对；未编译或验证实例。

### RFC9834 YANG Data Models for Bearers and Attachment Circuits as a Service (ACaaS)

[官方原文](https://www.rfc-editor.org/rfc/rfc9834.html) · PROPOSED STANDARD · 类型：YANG bearer与AC客户服务模型

模块：`ietf-ac-svc`、`ietf-bearer-svc`。

支持：将bearer/AC独立作为服务；可先建AC再绑定VPN/切片，支持共享bearer与多个AC。。

边界：不是把全体物理端口直接升级ACaaS；客户参考号、承载编号、开始时间和profile必须有来源。。

关键对象/关联：`/ietf-bearer-svc:bearers/bearer[name]`；`/ietf-ac-svc:attachment-circuits/ac[name]`。

核对章节：§1.2；§3；§5.1；§5.2；§6。研读深度：原文范围、关键树/schema路径、RFC目录状态及IANA登记核对；未编译或验证实例。

### RFC9835 A Network YANG Data Model for Attachment Circuits

[官方原文](https://www.rfc-editor.org/rfc/rfc9835.html) · PROPOSED STANDARD · 类型：RFC8345/SAP网络增补YANG模型

模块：`ietf-ac-ntw`。

支持：运营商AC配置挂在网络/node，关联SAP及服务侧AC引用；承接L2/IP连接与路由profile。。

边界：拓扑发现本身不足以导出完整AC配置或补mandatory键；需区分服务请求与实际network AC。。

关键对象/关联：`/ietf-network:networks/network/ietf-ac-ntw:ac-profile[name]`；`/ietf-network:networks/network/node/ietf-ac-ntw:ac[name]`。

核对章节：§3；§4.2；§5；§6。研读深度：原文范围、关键树/schema路径、RFC目录状态及IANA登记核对；未编译或验证实例。

### RFC9836 A YANG Data Model for Augmenting VPN Service and Network Models with Attachment Circuits

[官方原文](https://www.rfc-editor.org/rfc/rfc9836.html) · PROPOSED STANDARD · 类型：VPN/AC绑定增补YANG模型

模块：`ietf-ac-glue`。

支持：ietf-ac-glue用正式引用绑定VPN客户服务/运营商网络接入与既有AC，减少重复建模。。

边界：以增补模块扩展四个 VPN 模型；RFC-editor 目录未列正式 Updates/Obsoletes 关系。原服务/AC引用需闭合。。

关键对象/关联：`L2/L3VPN service/network attachment circuit augmentations`；`/ietf-l3vpn-svc:l3vpn-svc/sites/site/site-network-accesses/ietf-ac-glue:ac-svc-ref`；`L2SM/L3SM及L2NM/L3NM对应接入位置的AC引用增补`。

核对章节：§3；§5；§6。研读深度：原文范围、关键树/schema路径、RFC目录状态及IANA登记核对；未编译或验证实例。

### draft-chen-nvo3-vxlan-yang-07 YANG Data Model for VxLAN Protocol

[官方原文](https://datatracker.ietf.org/doc/html/draft-chen-nvo3-vxlan-yang-07) · INTERNET-DRAFT; Expired; I-D Exists · 类型：model

模块：`ietf-vxlan`。

支持：历史 VXLAN VNI/VTEP/interface/MAC/VLAN mapping 模型建议。。

边界：个人 Internet-Draft-07，2019 expired，无已发布 RFC；名字含 ietf- 不等于 IETF 正式模型。。

关键对象/关联：`/ietf-vxlan:vxlan`；`EVPN/IF augmentation`。

核对章节：3；4；5；6。研读深度：Datatracker 当前 document/state API、最新版全文与关键 schema 树核对；草案未纳入 RFC 标准冻结集合。

### draft-ietf-bess-evpn-yang-07 Yang Data Model for EVPN

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-bess-evpn-yang-07) · INTERNET-DRAFT; Expired; I-D Exists; WG Document · 类型：model

模块：`ietf-ethernet-segment`、`ietf-evpn`。

支持：历史 device EVPN 和 Ethernet Segment 模型建议。。

边界：2019 已 expired，且依赖旧 draft schema；ietf-ethernet-segment 同名模块后来在 RFC9291 正式发布，不能混用 revision；不能否认 RFC9291 已能表达 EVPN 网络服务属性。。

关键对象/关联：`NI/L2VPN augmentation for EVPN`；`EVPN VPWS pseudowire augmentation`。

核对章节：2；3；4。研读深度：Datatracker 当前 document/state API、最新版全文与关键 schema 树核对；草案未纳入 RFC 标准冻结集合。

### draft-ietf-bess-l3vpn-yang-05 Yang Data Model for BGP/MPLS L3 VPNs

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-bess-l3vpn-yang-05) · INTERNET-DRAFT; Expired; I-D Exists; WG Document · 类型：model

模块：`ietf-bgp-l3vpn`。

支持：历史 BGP/MPLS L3VPN device VRF/interface/BGP AF model。。

边界：2021 expired，rfc=null；不是 RFC8299 服务模型或 RFC9182 网络模型的别名，也不宜作为正式冻结标准。。

关键对象/关联：`/ietf-network-instance:network-instances/network-instance NI type augmentation`。

核对章节：2；3；4。研读深度：Datatracker 当前 document/state API、最新版全文与关键 schema 树核对；草案未纳入 RFC 标准冻结集合。

## 传统 SNMP MIB 与转换

### RFC1724 RIP Version 2 MIB Extension

[官方原文](https://www.rfc-editor.org/rfc/rfc1724.html) · DRAFT STANDARD · 类型：management

模块：`RIPv2-MIB`。

支持：RIPv2 接口和 peer 统计/配置；替代 RFC1389。。

边界：IPv4 RIPv2 范围，不能当 RIPng/跨 VRF 完整模型。。

关键对象/关联：`rip2IfStatTable`；`rip2IfConfTable`；`rip2PeerTable`。

核对章节：3；4。研读深度：官方元数据、MIB module 声明、关键表/索引/描述与对应 YANG 映射核对；未穷举所有计数器。

### RFC2787 Definitions of Managed Objects for the Virtual Router Redundancy Protocol

[官方原文](https://www.rfc-editor.org/rfc/rfc2787.html) · PROPOSED STANDARD · 类型：management

模块：`VRRP-MIB`。

支持：旧 VRRPv2 MIB。。

边界：被 RFC6527 废止；不能用旧单 AF 对象硬装 v3 IPv6。。

关键对象/关联：`vrrpOperTable`。

后继文档：RFC6527。

核对章节：3；4。研读深度：官方元数据、MIB module 声明、关键表/索引/描述与对应 YANG 映射核对；未穷举所有计数器。

### RFC2863 The Interfaces Group MIB

[官方原文](https://www.rfc-editor.org/rfc/rfc2863.html) · DRAFT STANDARD · 类型：management

模块：`IF-MIB`。

支持：接口 index/name/type/status/speed/counters 和本地层次关系。。

边界：ifIndex 为 agent/context 内编号，不能当全网稳定 TP；ifStack 是本地层次，不是远端物理邻接。。

关键对象/关联：`ifTable`；`ifXTable`；`ifStackTable`。

核对章节：3；4。研读深度：官方元数据、MIB module 声明、关键表/索引/描述与对应 YANG 映射核对；未穷举所有计数器。

### RFC3418 Management Information Base (MIB) for the Simple Network Management Protocol (SNMP)

[官方原文](https://www.rfc-editor.org/rfc/rfc3418.html) · INTERNET STANDARD · 类型：management

模块：`SNMPv2-MIB`。

支持：系统信息、正常运行时间和管理模块能力登记。。

边界：sysName 可变且不唯一；sysObjectID 标识产品/管理子系统，不能自动当 chassis 唯一 ID。。

关键对象/关联：`sysDescr`；`sysObjectID`；`sysName`；`sysUpTime`；`sysORTable`。

核对章节：2。研读深度：官方元数据、MIB module 声明、关键表/索引/描述与对应 YANG 映射核对；未穷举所有计数器。

### RFC3433 Entity Sensor Management Information Base

[官方原文](https://www.rfc-editor.org/rfc/rfc3433.html) · PROPOSED STANDARD · 类型：management

模块：`ENTITY-SENSOR-MIB`。

支持：按 entPhysicalIndex 的传感器类型、值、精度和 operStatus。。

边界：传感器健康不是网络链路可用性，也不能推出邻接。。

关键对象/关联：`entPhySensorTable`。

核对章节：2；3；4。研读深度：官方元数据、MIB module 声明、关键表/索引/描述与对应 YANG 映射核对；未穷举所有计数器。

### RFC3811 Definitions of Textual Conventions (TCs) for Multiprotocol Label Switching (MPLS) Management

[官方原文](https://www.rfc-editor.org/rfc/rfc3811.html) · PROPOSED STANDARD · 类型：management

模块：`MPLS-TC-STD-MIB`。

支持：MPLS Label/FEC/interface 等管理类型。。

边界：类型 module 不提供 topology 实例。。

关键对象/关联：`MPLS textual conventions`。

核对章节：3。研读深度：官方元数据、MIB module 声明、关键表/索引/描述与对应 YANG 映射核对；未穷举所有计数器。

### RFC3813 Multiprotocol Label Switching (MPLS) Label Switching Router (LSR) Management Information Base (MIB)

[官方原文](https://www.rfc-editor.org/rfc/rfc3813.html) · PROPOSED STANDARD · 类型：management

模块：`MPLS-LSR-STD-MIB`。

支持：MPLS interface、in/out segment、cross-connect 与 label stack。。

边界：本地 forwarding segment 表需要 scope 和方向，不能直接作为全网 end-to-end LSP/cable。。

关键对象/关联：`mplsInterfaceTable`；`mplsInSegmentTable`；`mplsOutSegmentTable`；`mplsXCTable`；`mplsLabelStackTable`。

核对章节：5；9。研读深度：官方元数据、MIB module 声明、关键表/索引/描述与对应 YANG 映射核对；未穷举所有计数器。

### RFC3814 Multiprotocol Label Switching (MPLS) Forwarding Equivalence Class To Next Hop Label Forwarding Entry (FEC-To-NHLFE) Management Information Base (MIB)

[官方原文](https://www.rfc-editor.org/rfc/rfc3814.html) · PROPOSED STANDARD · 类型：management

模块：`MPLS-FTN-STD-MIB`。

支持：FEC 到 NHLFE 规则、接口映射与性能。。

边界：规则与实际流量走过的路径不同，需要 active forwarding 证据。。

关键对象/关联：`mplsFTNTable`；`mplsFTNMapTable`。

核对章节：5；8。研读深度：官方元数据、MIB module 声明、关键表/索引/描述与对应 YANG 映射核对；未穷举所有计数器。

### RFC3815 Definitions of Managed Objects for the Multiprotocol Label Switching (MPLS), Label Distribution Protocol (LDP)

[官方原文](https://www.rfc-editor.org/rfc/rfc3815.html) · PROPOSED STANDARD · 类型：management

模块：`MPLS-LDP-STD-MIB`、`MPLS-LDP-ATM-STD-MIB`、`MPLS-LDP-FRAME-RELAY-STD-MIB`、`MPLS-LDP-GENERIC-STD-MIB`。

支持：MPLS LDP entity、discovery/session/FEC-label 与各介质类型扩展。。

边界：targeted peer 可能多跳，不能提升为 cable 直连。。

关键对象/关联：`mplsLdpEntityTable`；`mplsLdpPeerTable`；`mplsLdpSessionTable`；`mplsLdpHelloAdjacencyTable`；`mplsFecTable`。

核对章节：3；4；5。研读深度：官方元数据、MIB module 声明、关键表/索引/描述与对应 YANG 映射核对；未穷举所有计数器。

### RFC4133 Entity MIB (Version 3)

[官方原文](https://www.rfc-editor.org/rfc/rfc4133.html) · PROPOSED STANDARD · 类型：management

模块：`ENTITY-MIB`。

支持：旧 Entity MIB Version3。。

边界：已被 RFC6933 废止；仅保留兼容与历史链。。

关键对象/关联：`entPhysicalTable`；`entLogicalTable`。

后继文档：RFC6933。

核对章节：2；5。研读深度：官方元数据、MIB module 声明、关键表/索引/描述与对应 YANG 映射核对；未穷举所有计数器。

### RFC4188 Definitions of Managed Objects for Bridges

[官方原文](https://www.rfc-editor.org/rfc/rfc4188.html) · PROPOSED STANDARD · 类型：management

模块：`BRIDGE-MIB`。

支持：bridge-port 到 ifIndex、FDB 学习 MAC 出口及 STP；替代 RFC1493。。

边界：FDB 出口只表示桥学习方向，可有未管理中间交换机；不是主机物理直连证明，也缺现代 VLAN 的完整隔离上下文。。

关键对象/关联：`dot1dBasePortTable`；`dot1dTpFdbTable`；`dot1dStpPortTable`。

核对章节：3；4。研读深度：官方元数据、MIB module 声明、关键表/索引/描述与对应 YANG 映射核对；未穷举所有计数器。

### RFC4268 Entity State MIB

[官方原文](https://www.rfc-editor.org/rfc/rfc4268.html) · PROPOSED STANDARD · 类型：management

模块：`ENTITY-STATE-TC-MIB`、`ENTITY-STATE-MIB`。

支持：physical entity admin/oper/usage/alarm/standby 状态。。

边界：实体状态层与接口/协议状态层不同；不用 component UP 代替 forwarding path UP。。

关键对象/关联：`entStateTable`。

核对章节：2；3；4；5。研读深度：官方元数据、MIB module 声明、关键表/索引/描述与对应 YANG 映射核对；未穷举所有计数器。

### RFC4273 Definitions of Managed Objects for BGP-4

[官方原文](https://www.rfc-editor.org/rfc/rfc4273.html) · PROPOSED STANDARD · 类型：management

模块：`BGP4-MIB`。

支持：已发布 BGP4-MIB：BGP Identifier、peer 状态/ASN 与传统 IPv4 path attributes；替代 RFC1269/1657。。

边界：不是现代多 AFI/SAFI、四字节 ASN、EVPN 等完整无损 BGP 模型；发布 MIB 不代表 BGP YANG 已发布。。

关键对象/关联：`bgpPeerTable`；`bgp4PathAttrTable`。

核对章节：3；4。研读深度：官方元数据、MIB module 声明、关键表/索引/描述与对应 YANG 映射核对；未穷举所有计数器。

### RFC4292 IP Forwarding Table MIB

[官方原文](https://www.rfc-editor.org/rfc/rfc4292.html) · PROPOSED STANDARD · 类型：management

模块：`IP-FORWARD-MIB`。

支持：AF-aware 目的前缀、policy、next-hop、出接口、route type/proto 和 metric；替代 RFC2096。。

边界：表内路由状态不是端到端转发实测；ECMP 的多个表项不能丢，metric1..5 不应跨协议硬统一。。

关键对象/关联：`inetCidrRouteTable`。

核对章节：4；5。研读深度：官方元数据、MIB module 声明、关键表/索引/描述与对应 YANG 映射核对；未穷举所有计数器。

### RFC4293 Management Information Base for the Internet Protocol (IP)

[官方原文](https://www.rfc-editor.org/rfc/rfc4293.html) · PROPOSED STANDARD · 类型：management

模块：`IP-MIB`。

支持：IPv4/IPv6 地址/前缀、接口、邻居映射和状态；替代 2011/2465/2466。。

边界：邻居表的 ifIndex/AF/IP 作用域不能丢；支持多栈/上下文不意味着任意同名 VRF 全球一致。。

关键对象/关联：`ipAddressTable`；`ipAddressPrefixTable`；`ipNetToPhysicalTable`。

核对章节：3；5。研读深度：官方元数据、MIB module 声明、关键表/索引/描述与对应 YANG 映射核对；未穷举所有计数器。

### RFC4363 Definitions of Managed Objects for Bridges with Traffic Classes, Multicast Filtering, and Virtual LAN Extensions

[官方原文](https://www.rfc-editor.org/rfc/rfc4363.html) · PROPOSED STANDARD · 类型：management

模块：`P-BRIDGE-MIB`、`Q-BRIDGE-MIB`。

支持：P/Q-BRIDGE VLAN membership、PVID、tagging、FDB ID 与 VLAN-aware MAC 学习；替代 RFC2674。。

边界：FDB 表以 FDB-ID/MAC 为键，不应默认 FDB-ID=VID；不同 VLAN 可共享学习库，实际广播/转发域仍须 VLAN/bridge context；IEEE 当前模型另见桥模型章节。。

关键对象/关联：`dot1qVlanCurrentTable`；`dot1qVlanFdbId`；`dot1qTpFdbTable`；`dot1qPvid`；`dot1qVlanStaticEgressPorts`；`dot1qVlanStaticUntaggedPorts`。

核对章节：3；4；5。研读深度：官方元数据、MIB module 声明、关键表/索引/描述与对应 YANG 映射核对；未穷举所有计数器。

### RFC4382 MPLS/BGP Layer 3 Virtual Private Network (VPN) Management Information Base

[官方原文](https://www.rfc-editor.org/rfc/rfc4382.html) · PROPOSED STANDARD · 类型：management

模块：`MPLS-L3VPN-STD-MIB`。

支持：L3VPN VRF/接口、RT、路由、性能和统计。。

边界：VRF 索引设备内，不等于全球 service ID；RD/RT 与 routing scope 需独立保留。。

关键对象/关联：`mplsL3VpnVrfTable`；`mplsL3VpnIfConfTable`；`mplsL3VpnVrfRteTable`；`mplsL3VpnVrfRTTable`。

核对章节：5；7。研读深度：官方元数据、MIB module 声明、关键表/索引/描述与对应 YANG 映射核对；未穷举所有计数器。

### RFC4444 Management Information Base for Intermediate System to Intermediate System (IS-IS)

[官方原文](https://www.rfc-editor.org/rfc/rfc4444.html) · PROPOSED STANDARD · 类型：management

模块：`ISIS-MIB`。

支持：IS-IS circuit/level/邻接/LSDB 与统计。。

边界：pseudonode、level 和 circuit context 必须保留；不等于 physical inventory。。

关键对象/关联：`isisCircTable`；`isisCircLevelTable`；`isisISAdjTable`；`isisLSPSummaryTable`；`isisLSPTLVTable`。

核对章节：2；3；4。研读深度：官方元数据、MIB module 声明、关键表/索引/描述与对应 YANG 映射核对；未穷举所有计数器。

### RFC4750 OSPF Version 2 Management Information Base

[官方原文](https://www.rfc-editor.org/rfc/rfc4750.html) · PROPOSED STANDARD · 类型：management

模块：`OSPF-MIB`、`OSPF-TRAP-MIB`。

支持：OSPFv2 area、interface、metric、neighbor 与 LSDB/通知；替代 RFC1850。。

边界：需要 SNMP context 区分多实例；protocol neighbor/LSDB 不等于物理电缆。。

关键对象/关联：`ospfIfTable`；`ospfIfMetricTable`；`ospfNbrTable`；`ospfLsdbTable`。

核对章节：1.5；2；3。研读深度：官方元数据、MIB module 声明、关键表/索引/描述与对应 YANG 映射核对；未穷举所有计数器。

### RFC5060 Protocol Independent Multicast MIB

[官方原文](https://www.rfc-editor.org/rfc/rfc5060.html) · PROPOSED STANDARD · 类型：management

模块：`PIM-STD-MIB`。

支持：PIM 接口、邻居、RP、组播路由树和计数器。。

边界：流/源/组树与物理图不同，不能通过 group 相同推物理相邻。。

关键对象/关联：`pimInterfaceTable`；`pimNeighborTable`；`pimStarGTable`；`pimSGTable`。

核对章节：4；5。研读深度：官方元数据、MIB module 声明、关键表/索引/描述与对应 YANG 映射核对；未穷举所有计数器。

### RFC5132 IP Multicast MIB

[官方原文](https://www.rfc-editor.org/rfc/rfc5132.html) · PROPOSED STANDARD · 类型：management

模块：`IPMCAST-MIB`。

支持：IPv4/IPv6 multicast routing/cache 与转发状态，替代 RFC2932。。

边界：(S,G)/接口集合是流相关转发关系，不能当无条件广播域。。

关键对象/关联：`ipMcastRouteTable`。

核对章节：4；6。研读深度：官方元数据、MIB module 声明、关键表/索引/描述与对应 YANG 映射核对；未穷举所有计数器。

### RFC5519 Multicast Group Membership Discovery MIB

[官方原文](https://www.rfc-editor.org/rfc/rfc5519.html) · PROPOSED STANDARD · 类型：management

模块：`MGMD-STD-MIB`。

支持：IGMP/MLD 双 AF host/router membership、source-filter、接口和 cache；替代 RFC2933/3019。。

边界：cache/querier 不是 endpoint 物理连接证据。。

关键对象/关联：`mgmdHostInterfaceTable`；`mgmdRouterInterfaceTable`；`mgmdRouterCacheTable`。

核对章节：4；5。研读深度：官方元数据、MIB module 声明、关键表/索引/描述与对应 YANG 映射核对；未穷举所有计数器。

### RFC5643 Management Information Base for OSPFv3

[官方原文](https://www.rfc-editor.org/rfc/rfc5643.html) · PROPOSED STANDARD · 类型：management

模块：`OSPFV3-MIB`。

支持：OSPFv3 interface/邻居/LSDB/IPv6 和 instance/area。。

边界：IPv6 地址区分 scope，RID 不能替代出接口/远端 chassis。。

关键对象/关联：`ospfv3IfTable`；`ospfv3NbrTable`；`ospfv3AreaLsdbTable`。

核对章节：2；3；4。研读深度：官方元数据、MIB module 声明、关键表/索引/描述与对应 YANG 映射核对；未穷举所有计数器。

### RFC6527 Definitions of Managed Objects for Virtual Router Redundancy Protocol Version 3 (VRRPv3)

[官方原文](https://www.rfc-editor.org/rfc/rfc6527.html) · PROPOSED STANDARD · 类型：management

模块：`VRRPV3-MIB`。

支持：VRRPv3 IPv4/IPv6 实例、VRID/接口/AF、VIP、state 和统计；替代 RFC2787。。

边界：虚拟身份不可合并成 physical host，版本/AF/接口范围不能省略。。

关键对象/关联：`vrrpv3OperationsTable`；`vrrpv3AssociatedIpAddrTable`；`vrrpv3StatisticsTable`。

核对章节：4；5。研读深度：官方元数据、MIB module 声明、关键表/索引/描述与对应 YANG 映射核对；未穷举所有计数器。

### RFC6643 Translation of Structure of Management Information Version 2 (SMIv2) MIB Modules to YANG Modules

[官方原文](https://www.rfc-editor.org/rfc/rfc6643.html) · PROPOSED STANDARD · 类型：management

模块：`ietf-yang-smiv2`。

支持：规范 SMIv2 MIB 到只读 YANG 的转换和 OID/index 元信息。。

边界：机械翻译保留 MIB 限制，不等于 NMDA 原生 device model；示例 IF-MIB/acme 翻译不是新标准模块。。

关键对象/关联：`ietf-yang-smiv2:oid`；`SMIv2 table to YANG mapping`。

核对章节：3；4；5。研读深度：官方元数据、MIB module 声明、关键表/索引/描述与对应 YANG 映射核对；未穷举所有计数器。

### RFC6933 Entity MIB (Version 4)

[官方原文](https://www.rfc-editor.org/rfc/rfc6933.html) · PROPOSED STANDARD · 类型：management

模块：`ENTITY-MIB`、`IANA-ENTITY-MIB`、`UUID-TC-MIB`。

支持：chassis/组件、序列号、UUID、包含关系及 logical/context/接口别名映射；Version4 替代 RFC4133。。

边界：entPhysicalIndex/context 仍有 agent scope；序列号空缺不能补造，hardware 架构不含网外线缆两端。。

关键对象/关联：`entPhysicalTable`；`entLogicalTable`；`entAliasMappingTable`；`entPhysicalContainsTable`。

核对章节：2.5；2.6；2.8；2.9；2.12；5。研读深度：官方元数据、MIB module 声明、关键表/索引/描述与对应 YANG 映射核对；未穷举所有计数器。

### RFC7331 Bidirectional Forwarding Detection (BFD) Management Information Base

[官方原文](https://www.rfc-editor.org/rfc/rfc7331.html) · PROPOSED STANDARD · 类型：management

模块：`BFD-STD-MIB`。

支持：BFD 会话、状态/discriminator、性能与地址映射。。

边界：会话存在/UP 是 forwarding path 活性，不是物理邻接或业务全可达。。

关键对象/关联：`bfdSessTable`；`bfdSessPerfTable`；`bfdSessDiscMapTable`；`bfdSessIpMapTable`。

核对章节：4；5。研读深度：官方元数据、MIB module 声明、关键表/索引/描述与对应 YANG 映射核对；未穷举所有计数器。

### RFC8892 Guidelines and Registration Procedures for Interface Types and Tunnel Types

[官方原文](https://www.rfc-editor.org/rfc/rfc8892.html) · PROPOSED STANDARD · 类型：management

模块：无独立 YANG 模块（见文档类型与范围）。

支持：更新 RFC2863，维护 Interface/Tunnel type 的 IANA 注册原则。。

边界：类型登记不是接口实例数据；没有 YANG/MIB 新模块。。

核对章节：2；3；4。研读深度：官方元数据、MIB module 声明、关键表/索引/描述与对应 YANG 映射核对；未穷举所有计数器。

## 传送/OTN 与 L1

### RFC3473 Generalized Multi-Protocol Label Switching (GMPLS) Signaling Resource ReserVation Protocol-Traffic Engineering (RSVP-TE) Extensions

[官方原文](https://www.rfc-editor.org/rfc/rfc3473.html) · PROPOSED STANDARD · 类型：protocol

模块：无独立 YANG 模块（见文档类型与范围）。

支持：GMPLS 对 RSVP-TE 的广义标签、双向/通知及不同交换技术信令扩展。。

边界：非 YANG；需区分信令状态、管理状态与拓扑资源事实。。

核对章节：§2–7。研读深度：abstract, document status and selected architecture/protocol sections; not exhaustive protocol review。

### RFC3945 Generalized Multi-Protocol Label Switching (GMPLS) Architecture

[官方原文](https://www.rfc-editor.org/rfc/rfc3945.html) · PROPOSED STANDARD · 类型：architecture

模块：无独立 YANG 模块（见文档类型与范围）。

支持：GMPLS 的 PSC/L2SC/TDM/LSC/FSC 交换能力与多层控制架构。。

边界：虽然是 Standards Track，仍是架构而非 YANG；不可由 GMPLS 概念补造未观测的时隙或光纤能力。。

核对章节：§1–7。研读深度：abstract, document status and selected architecture/protocol sections; not exhaustive protocol review。

### RFC7062 Framework for GMPLS and PCE Control of G.709 Optical Transport Networks

[官方原文](https://www.rfc-editor.org/rfc/rfc7062.html) · INFORMATIONAL · 类型：architecture

模块：无独立 YANG 模块（见文档类型与范围）。

支持：G.709 OTN 的 GMPLS/PCE 控制框架及 ODU 资源/层次/适配。。

边界：Informational；G.709 技术与 IETF OTN YANG 的发布状态须独立判断。。

核对章节：§3–6。研读深度：abstract, document status and selected architecture/protocol sections; not exhaustive protocol review。

### draft-ietf-ccamp-fgotn-yang-02 YANG Data Models for fine grain Optical Transport Network

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-ccamp-fgotn-yang-02) · Internet-Draft / Active · 类型：topology-and-tunnel-yang

模块：`ietf-fgotn-types`、`ietf-fgotn-topology`、`ietf-fgotn-tunnel`。

支持：sub-1Gbit/s fgOTN 类型、拓扑与 tunnel，augment OTN topo、OTN tunnel/TE，支持细粒度标签与带宽。。

边界：-02 活跃草案；ITU-T G.709 承载技术不因此等于 IETF YANG 已标准化，依赖多个 OTN/L1 草案。。

核对章节：§3–10。研读深度：abstract, model tree, module import/augment and selected semantic sections; not compiled。

### draft-ietf-ccamp-l1csm-yang-28 A YANG Data Model for L1 Connectivity Service Model (L1CSM)

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-ccamp-l1csm-yang-28) · Internet-Draft / RFC Editor queue (In Progress) · 类型：service-yang

模块：`ietf-l1csm`。

支持：客户 L1 connectivity 服务请求/状态、UNI/access 与保护/带宽参数，import Layer1 types。。

边界：-28 在 RFC Editor queue；customer service abstraction，不等于网络资源实现/OTN topo/tunnel。。

关键对象/关联：`/ietf-l1csm:l1-connectivity`。

核对章节：§2–4。研读深度：abstract, model tree, module import/augment and selected semantic sections; not compiled。

### draft-ietf-ccamp-layer1-types-20 Common YANG Data Types for Layer 1 Networks

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-ccamp-layer1-types-20) · Internet-Draft / RFC Editor queue (In Progress) · 类型：types-yang

模块：`ietf-layer1-types`。

支持：传送 client signal、ODU/ODUflex、OTN bandwidth/label/tributary-slot 的 Layer1 公共类型与 grouping。。

边界：-20 在 RFC Editor queue，IANA 预留不表示正式发布；只有类型，没有独立网图或服务实例。TDM/STM-n/SDH 的覆盖要按具体 signal 类型，不得声称完整任意 TDM 拓扑标准。。

核对章节：§4–6。研读深度：abstract, model tree, module import/augment and selected semantic sections; not compiled。

### draft-ietf-ccamp-otn-topo-yang-21 A YANG Data Model for Optical Transport Network Topology

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-ccamp-otn-topo-yang-21) · Internet-Draft / RFC Editor queue (In Progress) · 类型：topology-yang

模块：`ietf-otn-topology`。

支持：OTN TE topology：ODU 交换资源、接口能力、tributary-slot/label 与分层带宽；augment RFC8795，import Layer1 types。。

边界：-21 在 RFC Editor queue；IANA 预登记参考旧 -20，不是正式 RFC。仅拓扑/资源，不等于 OTN tunnel 配置。。

核对章节：§2–4。研读深度：abstract, model tree, module import/augment and selected semantic sections; not compiled。

### draft-ietf-ccamp-otn-tunnel-model-26 A YANG Data Model for Optical Transport Network (OTN) Tunnels and Label Switched Paths

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-ccamp-otn-tunnel-model-26) · Internet-Draft / Active · 类型：device-and-network-yang

模块：`ietf-otn-tunnel`。

支持：OTN tunnel/LSP：在 ietf-te 路径/约束/标签与 bandwidth augment OTN signal 类型、资源与时隙。。

边界：-26 活跃草案，尚无正式 RFC；模型与控制平面协议独立，不等于完成 OTN 内部资源发现。。

核对章节：§2–3。研读深度：abstract, model tree, module import/augment and selected semantic sections; not compiled。

## 传送/以太与客户信号

### draft-ietf-ccamp-client-signal-yang-18 A YANG Data Model for Transport Network Client Signals

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-ccamp-client-signal-yang-18) · Internet-Draft / Active · 类型：service-and-types-yang

模块：`ietf-eth-tran-service`、`ietf-eth-tran-types`、`ietf-trans-client-service`、`ietf-trans-client-svc-types`。

支持：Ethernet 与通用传送客户信号（STM-n、FC 等）的服务接入及 tunnel 关联；含 ethernet/general service 与两份类型模块。。

边界：-18 活跃草案；客户信号接入/映射数据不等于底层全网 topology，也不能由接口名称猜传送封装。。

关键对象/关联：`/ietf-eth-tran-service:etht-svc-instances`；`/ietf-trans-client-service:client-svc/client-svc-instances`。

核对章节：§3–5；YANG 模块。研读深度：abstract, model tree, module import/augment and selected semantic sections; not compiled。

### draft-ietf-ccamp-eth-client-te-topo-yang-11 A YANG Data Model for Ethernet TE Topology

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-ccamp-eth-client-te-topo-yang-11) · Internet-Draft / Active · 类型：topology-yang

模块：`ietf-eth-te-topology`。

支持：Ethernet 作为传送 server 或 OTN 等 underlay 的 client 层的 TE 图，增加 Ether/VLAN label、带宽与属性。。

边界：-11 活跃草案；它是 transport Ethernet TE 扩展，不等于 RFC8944 普通 L2 topology 或 IEEE802.1Q bridge 完整配置模型。。

核对章节：§2–4。研读深度：abstract, model tree, module import/augment and selected semantic sections; not compiled。

### draft-ietf-ccamp-flexe-yang-cm-11 YANG Data Model for FlexE Management

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-ccamp-flexe-yang-cm-11) · Internet-Draft / Active · 类型：device-yang

模块：`ietf-flexe`。

支持：FlexE group/client 资源配置、PHY 绑定与 FlexE 管理关系；import RFC8343 interfaces。。

边界：-11 活跃草案（WG Last Call）；是 FlexE 管理模型，未声明普通 Ethernet 端口必有 FlexE 或定义全网图。。

关键对象/关联：`/ietf-flexe:flexe/flexe-groups`；`/ietf-flexe:flexe/flexe-clients`。

核对章节：§2–4。研读深度：abstract, model tree, module import/augment and selected semantic sections; not compiled。

## 传送/光层 L0

### RFC6163 Framework for GMPLS and Path Computation Element (PCE) Control of Wavelength Switched Optical Networks (WSONs)

[官方原文](https://www.rfc-editor.org/rfc/rfc6163.html) · INFORMATIONAL · 类型：architecture

模块：无独立 YANG 模块（见文档类型与范围）。

支持：WSON 的 RWA、ROADM/交换/转换资源、路由和信令架构，作为 RFC9094 的技术背景。。

边界：Informational，无 YANG，光路 RWA 需要额外资源数据。。

核对章节：§2–5。研读深度：abstract, document status and selected architecture/protocol sections; not exhaustive protocol review。

### RFC7698 Framework and Requirements for GMPLS-Based Control of Flexi-Grid Dense Wavelength Division Multiplexing (DWDM) Networks

[官方原文](https://www.rfc-editor.org/rfc/rfc7698.html) · INFORMATIONAL · 类型：architecture

模块：无独立 YANG 模块（见文档类型与范围）。

支持：GMPLS flexi-grid DWDM 频谱槽和 media channel 管理控制框架。。

边界：Informational；不是 flexi-grid topology/tunnel YANG 发布依据。。

核对章节：§3–5。研读深度：abstract, document status and selected architecture/protocol sections; not exhaustive protocol review。

### RFC9093 A YANG Data Model for Layer 0 Types

[官方原文](https://www.rfc-editor.org/rfc/rfc9093.html) · PROPOSED STANDARD · 类型：types-yang

模块：`ietf-layer0-types`。

支持：固定/灵活栅格光层类型、DWDM/CWDM 参数、波长/频谱标签与标签范围等可复用定义。。

边界：没有独立节点/链路实例；rfc9093-bis-20 拟替代本 RFC，目前仍在 RFC Editor 队列。。

核对章节：§2–3。研读深度：abstract, model tree, module import/augment and selected semantic sections; not compiled。

### RFC9094 A YANG Data Model for Wavelength Switched Optical Networks (WSONs)

[官方原文](https://www.rfc-editor.org/rfc/rfc9094.html) · PROPOSED STANDARD · 类型：topology-yang

模块：`ietf-wson-topology`。

支持：WSON TE 拓扑：在 TE node/link/TP、connectivity/label restrictions 与带宽上增加波长交换资源。。

边界：是 WSON 拓扑，未定义完整 WSON tunnel 配置，也不是端到端光损伤可行性求解器；需要光层资源事实。。

关键对象/关联：`/ietf-network:networks/network/network-types/ietf-te-topology:te-topology/ietf-wson-topology:wson`。

核对章节：§2–3。研读深度：abstract, model tree, module import/augment and selected semantic sections; not compiled。

### draft-ietf-ccamp-dwdm-if-param-yang-16 A YANG data model to manage configurable DWDM optical interfaces

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-ccamp-dwdm-if-param-yang-16) · Internet-Draft / Active · 类型：device-yang

模块：`ietf-wdm-interface`。

支持：相干 100G+ 可配置 DWDM interface 的 supported/operational modes、调制/FEC、功率及监测参数，扩展 RFC8343 interfaces。。

边界：-16 活跃草案；文档明确不保证 transceiver 互通或光路可行性，外部算法/工具负责判断。。

核对章节：§2–4。研读深度：abstract, model tree, module import/augment and selected semantic sections; not compiled。

### draft-ietf-ccamp-flexigrid-yang-19 A YANG Data Model for Flexi-Grid Optical Networks

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-ccamp-flexigrid-yang-19) · Internet-Draft / Active · 类型：topology-yang

模块：`ietf-flexi-grid-topology`。

支持：Flexi-grid 频谱/slot、节点、连接矩阵、标签和 bandwidth 在 RFC8795 TE 图上的技术扩展。。

边界：-19 Datatracker Active + AD Evaluation，文本有效期已过；尚未正式 RFC。不是 tunnel 实例或光损伤求解器。。

核对章节：§5–6。研读深度：abstract, model tree, module import/augment and selected semantic sections; not compiled。

### draft-ietf-ccamp-optical-impairment-topology-yang-24 A YANG Data Model for Optical Impairment-aware Topology

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-ccamp-optical-impairment-topology-yang-24) · Internet-Draft / RFC Editor queue (In Progress) · 类型：topology-yang

模块：`ietf-optical-impairment-topology`。

支持：只读光损伤相关 TE topology：OTSi、OMS/OTS/media channel、transponder/3R 与显式 optical-mode 参数，供 IA-RWA/IA-RSA 路径计算。。

边界：-24 在 RFC Editor queue；模型提供求解所需数据，不自行保证光路可行性或互通。需量测/厂商模式/光参数证据，不从 IP 连通性生成。。

关键对象/关联：`/nw:networks/nw:network/nw:network-types/tet:te-topology/oit:optical-impairment-topology`；`/nw:networks/nw:network/oit:otsis`。

核对章节：§2–5；YANG 模块。研读深度：abstract, model tree, module import/augment and selected semantic sections; not compiled。

### draft-ietf-ccamp-rfc9093-bis-20 Common YANG Data Types for Layer 0 Optical Networks

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-ccamp-rfc9093-bis-20) · Internet-Draft / RFC Editor queue (In Progress) · 类型：types-yang

模块：`ietf-layer0-types`。

支持：更新 L0 光层公用 identity/type/grouping、标签与资源定义，拟 obsoletes RFC9093。。

边界：-20 在 RFC Editor queue，尚非正式替代；同名 ietf-layer0-types 有版本语义差异。。

核对章节：§2–3。研读深度：abstract, model tree, module import/augment and selected semantic sections; not compiled。

### draft-ietf-ccamp-wdm-tunnel-yang-08 A YANG Data Model for WDM Tunnels

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-ccamp-wdm-tunnel-yang-08) · Internet-Draft / Active · 类型：device-and-network-yang

模块：`ietf-wdm-tunnel`。

支持：统一 WSON 与 Flexi-grid 的 WDM tunnel/LSP，增加光路径、标签、transponder 与再生器相关约束；augment 通用 ietf-te。。

边界：-08 活跃草案，Datatracker 明确替代旧 WSON tunnel-09 和 Flexi-grid tunnel-03；不是正式 RFC，依赖通用 TE/L0 草案版本。。

核对章节：§3–5。研读深度：abstract, model tree, module import/augment and selected semantic sections; not compiled。

## 传送/微波

### RFC8432 A Framework for Management and Control of Microwave and Millimeter Wave Interface Parameters

[官方原文](https://www.rfc-editor.org/rfc/rfc8432.html) · INFORMATIONAL · 类型：architecture

模块：无独立 YANG 模块（见文档类型与范围）。

支持：微波/毫米波接口参数管理、carrier/radio-link、保护与 Ethernet 关系的框架。。

边界：Informational；实际设备/拓扑 schema 分别是 RFC8561/RFC9656。。

核对章节：§3–5。研读深度：abstract, document status and selected architecture/protocol sections; not exhaustive protocol review。

### RFC8561 A YANG Data Model for Microwave Radio Link

[官方原文](https://www.rfc-editor.org/rfc/rfc8561.html) · PROPOSED STANDARD · 类型：device-yang

模块：`ietf-microwave-radio-link`、`ietf-interface-protection`、`ietf-microwave-types`。

支持：微波/毫米波 radio-link、carrier termination、调制/容量/功率与性能、接口保护；三模块扩展 RFC8343 接口树。。

边界：设备端无线接口及保护模型，不是全网微波拓扑；频率/调制等没有事实就不可从 Ethernet 相邻推断。。

核对章节：§2–5。研读深度：abstract, model tree, module import/augment and selected semantic sections; not compiled。

### RFC9656 A YANG Data Model for Microwave Topology

[官方原文](https://www.rfc-editor.org/rfc/rfc9656.html) · PROPOSED STANDARD · 类型：topology-yang

模块：`ietf-microwave-topology`。

支持：微波 TE 拓扑扩展：radio links 与多个 carrier 的关系、微波资源、TP、带宽；可通过 supporting 关系承载 client topology。。

边界：扩展 RFC8795，不应单独脱离其依赖；TE 图可以用于非 TE 场景，但并不自动暴露所有内部 carrier 或证明硬件归属。。

核对章节：§2.1–2.5。研读深度：abstract, model tree, module import/augment and selected semantic sections; not compiled。

## 保障与故障

### RFC8632 A YANG Data Model for Alarm Management

[官方原文](https://www.rfc-editor.org/rfc/rfc8632.html) · PROPOSED STANDARD · 类型：YANG告警模型及X.733映射

模块：`ietf-alarms`、`ietf-alarms-x733`。

支持：告警是资源的不良状态；resource可指向YANG实例，支持clear与operator acknowledge/close、关联资源/根因候选。。

边界：告警不等于一般event；规范不要求自动根因推断；检测规则归具体技术/实现而非凭空造告警。。

关键对象/关联：`/ietf-alarms:alarms/alarm-list/alarm[resource alarm-type-id alarm-type-qualifier]`；`/ietf-alarms:alarms/alarm-inventory`。

核对章节：§3.3；§3.5；§3.6；§4；§6；§7。研读深度：原文范围、关键树/schema路径、RFC目录状态及IANA登记核对；未编译或验证实例。

### RFC9417 Service Assurance for Intent-Based Networking Architecture

[官方原文](https://www.rfc-editor.org/rfc/rfc9417.html) · INFORMATIONAL · 类型：SAIN架构（无YANG模块）

模块：无独立 YANG 模块（见文档类型与范围）。

支持：服务分解为subservices、依赖关系及health/symptoms；关联设备/接口可作服务影响分析。。

边界：保障图是服务依赖图，不是物理拓扑；健康度需要指标/评估规则，不能把unknown接口默认健康。。

核对章节：§3；§3.1；§3.3；§3.5。研读深度：原文范围、关键树/schema路径、RFC目录状态及IANA登记核对；未编译或验证实例。

### RFC9418 A YANG Data Model for Service Assurance

[官方原文](https://www.rfc-editor.org/rfc/rfc9418.html) · PROPOSED STANDARD · 类型：YANG保障图及设备/接口增补

模块：`ietf-service-assurance`、`ietf-service-assurance-device`、`ietf-service-assurance-interface`。

支持：保障图节点有依赖、健康与症状；标准device/interface subservice关联被保障设备及其接口名。。

边界：设备身份参数是string关联，部署需稳定映射；附录虚构Acme/IP-connectivity/IS-IS示例模块不是本文三个正式模块。。

关键对象/关联：`/ietf-service-assurance:subservices/subservice[type id]`；`/ietf-service-assurance:assured-services`；`subservice parameter中device/interface参数增补`。

核对章节：§2；§3；§5；§6。研读深度：原文范围、关键树/schema路径、RFC目录状态及IANA登记核对；未编译或验证实例。

### RFC9940 Some Key Terms for Network Fault and Problem Management

[官方原文](https://www.rfc-editor.org/rfc/rfc9940.html) · INFORMATIONAL · 类型：故障/问题术语（无YANG模块）

模块：无独立 YANG 模块（见文档类型与范围）。

支持：区分Event/State/Fault/Problem/Symptom/Cause/Alert/Alarm，指导事实与因果标签。。

边界：2026-04术语RFC不是incident模型，不能把它当已发布ietf-incident schema。。

核对章节：§2；§3。研读深度：原文范围、关键树/schema路径、RFC目录状态及IANA登记核对；未编译或验证实例。

### draft-ietf-nmop-network-incident-yang-17 A YANG Data Model for Network Incident Management

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-nmop-network-incident-yang-17) · Active WG Internet-Draft（2026-09-24） · 类型：YANG Internet-Draft

模块：`ietf-incident`。

支持：事件聚合、service-instance、优先级/状态、诊断/可能根因及alarm关联，适合运维闭环。。

边界：未发布RFC；不是把每次接口down直接补造成已确诊incident或root cause。。

关键对象/关联：`/ietf-incident:incidents/incident[name type incident-qualifier]`。

核对章节：固定-17：incident生命周期/树及YANG module。研读深度：Datatracker状态与固定版本原文、模块名/树路径核对；不作为已发布RFC或正式登记证明；未编译/验证实例。

## 切片与服务映射

### RFC9543 A Framework for Network Slices in Networks Built from IETF Technologies

[官方原文](https://www.rfc-editor.org/rfc/rfc9543.html) · INFORMATIONAL · 类型：网络切片框架（无YANG模块）

模块：无独立 YANG 模块（见文档类型与范围）。

支持：IETF Network Slice Service以SDP、连接、SLO/SLE和隔离要求定义；可多种技术实现。。

边界：RFC本身没有网络切片YANG模块；不把VRF/VLAN等同已保障切片，也不要求所有数字孪生支持切片。。

核对章节：§4；§5；§6；§7；§8。研读深度：原文范围、关键树/schema路径、RFC目录状态及IANA登记核对；未编译或验证实例。

### RFC9731 A YANG Data Model for Virtual Network (VN) Operations

[官方原文](https://www.rfc-editor.org/rfc/rfc9731.html) · PROPOSED STANDARD · 类型：YANG ACTN虚拟网络服务模型

模块：`ietf-vn`。

支持：VN需求、成员源/目的AP、带宽与TE拓扑标识通过leafref关联RFC8795节点/LTP。。

边界：仅适用ACTN/VN场景；VN请求不是一组观察到的物理节点，也不等同本地VRF。。

关键对象/关联：`/ietf-vn:virtual-network/vn`；`/ietf-vn:access-point/ap`；`/ietf-vn:virtual-network/vn[id]`；`/ietf-vn:access-point/ap[id]`。

核对章节：§2；§4；§5；§6。研读深度：原文范围、关键树/schema路径、RFC目录状态及IANA登记核对；未编译或验证实例。

### RFC9732 A Framework for NRP-Based Enhanced Virtual Private Networks

[官方原文](https://www.rfc-editor.org/rfc/rfc9732.html) · INFORMATIONAL · 类型：NRP/enhanced VPN框架（无YANG模块）

模块：无独立 YANG 模块（见文档类型与范围）。

支持：把overlay服务映射至具有特定拓扑/资源的NRP；一个NRP可承载多个服务。。

边界：框架不定义NRP协议或原生YANG实例；单个VPN不能自动证明资源独占/时延保证。。

核对章节：§3；§4；§5.6；§6。研读深度：原文范围、关键树/schema路径、RFC目录状态及IANA登记核对；未编译或验证实例。

### RFC9889 A Realization of Network Slices for 5G Networks Using Current IP/MPLS Technologies

[官方原文](https://www.rfc-editor.org/rfc/rfc9889.html) · INFORMATIONAL · 类型：5G切片实现分析（无YANG模块）

模块：无独立 YANG 模块（见文档类型与范围）。

支持：分析用既有IP/MPLS实现5G网络切片、QoS与承载映射。。

边界：是Informational实现文档；并非新切片YANG模型或一般园区网强制功能。。

核对章节：§3.5；§5；§6；§7。研读深度：原文范围、关键树/schema路径、RFC目录状态及IANA登记核对；未编译或验证实例。

### draft-ietf-teas-ietf-network-slice-nbi-yang-26 A YANG Data Model for the RFC 9543 Network Slice Service

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-teas-ietf-network-slice-nbi-yang-26) · WG Internet-Draft; RFC Ed Queue; 尚未发布RFC · 类型：YANG Internet-Draft

模块：`ietf-network-slice-service`。

支持：切片NBI描述SDP、连接、SLO/SLE模板和服务状态；可复用AC结构。。

边界：批准进入RFC队列仍不能称已发布标准；示例ietf-network-slice-service-proto-ex不作为正式模块列入。。

关键对象/关联：`/ietf-network-slice-service:network-slice-services`；`/ietf-network-slice-service:network-slice-services/slice-service[id]`；`.../sdps/sdp[id]`。

核对章节：固定-26原文：model/tree及YANG module章节。研读深度：Datatracker状态与固定版本原文、模块名/树路径核对；不作为已发布RFC或正式登记证明；未编译/验证实例。

### draft-ietf-teas-nrp-yang-06 YANG Data Models for Network Resource Partitions (NRPs)

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-teas-nrp-yang-06) · Active WG Internet-Draft（2026-06-01） · 类型：YANG Internet-Draft

模块：`ietf-nrp`、`ietf-nrp-device`。

支持：关联网络拓扑、NRP reservation/selector和设备资源策略。。

边界：未发布RFC，名称/schema仍可能变化；资源隔离必须取得配置/实施证据。。

关键对象/关联：`/ietf-nrp-device:nrp-policies`；`/ietf-network:networks/ietf-nrp:nrp-policies/nrp-policy[name]`；`/ietf-nrp-device:nrp-policies/nrp-policy[name]`。

核对章节：固定-06：NRP model结构、network/device modules。研读深度：Datatracker状态与固定版本原文、模块名/树路径核对；不作为已发布RFC或正式登记证明；未编译/验证实例。

### draft-ietf-teas-te-service-mapping-yang-19 Traffic Engineering (TE) and Service Mapping YANG Data Model

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-teas-te-service-mapping-yang-19) · Active WG Internet-Draft（固定-19发布2026-07-04） · 类型：YANG Internet-Draft

模块：`ietf-te-service-mapping-types`、`ietf-l3sm-te-service-mapping`、`ietf-l2sm-te-service-mapping`、`ietf-l1csm-te-service-mapping`、`ietf-l3nm-te-service-mapping`、`ietf-l2nm-te-service-mapping`。

支持：将客户/运营商服务映射TE tunnel/VN/策略约束，是层间映射候选。。

边界：草案不是成熟RFC；不能因为其引用正式VPN/VN模块而提高自身发布级别。。

关键对象/关联：`/ietf-te-service-mapping-types:te-mapping-templates/te-mapping-template[id]`；`L3SM/L2SM/L1CSM/L3NM/L2NM服务位置的te-service-mapping`。

核对章节：固定-19：各service/network model增补模块与树。研读深度：Datatracker状态与固定版本原文、模块名/树路径核对；不作为已发布RFC或正式登记证明；未编译/验证实例。

## 地址配置

### RFC9243 A YANG Data Model for DHCPv6 Configuration

[官方原文](https://www.rfc-editor.org/rfc/rfc9243.html) · PROPOSED STANDARD · 类型：model

模块：`ietf-dhcpv6-client`、`ietf-dhcpv6-common`、`ietf-dhcpv6-relay`、`ietf-dhcpv6-server`。

支持：DHCPv6 server/relay/client配置与状态。

边界：地址租约关系需要采集，不能据配置推定物理接线。

核对章节：Abstract。研读深度：官方摘要、模型类型、RFC索引及IANA登记核对；未逐字段审查。

## 基础拓扑

### RFC8345 A YANG Data Model for Network Topologies

[官方原文](https://www.rfc-editor.org/rfc/rfc8345.html) · PROPOSED STANDARD · 类型：model

模块：`ietf-network`、`ietf-network-state`、`ietf-network-topology`、`ietf-network-topology-state`。

支持：network/node/link/TP 与跨层支撑。

边界：有向点到点基础模型；不规定发现和实体归并算法。

核对章节：4；6。研读深度：范围/概览/关键定义核对；未对本目录整体编译。

### RFC8346 A YANG Data Model for Layer 3 Topologies

[官方原文](https://www.rfc-editor.org/rfc/rfc8346.html) · PROPOSED STANDARD · 类型：model

模块：`ietf-l3-unicast-topology`、`ietf-l3-unicast-topology-state`。

支持：三层单播节点、前缀、度量与TP。

边界：不是设备RIB；example-ospf不是正式模块。

核对章节：4；5；6。研读深度：范围/概览/关键定义核对；未对本目录整体编译。

### RFC8542 A YANG Data Model for Fabric Topology in Data-Center Networks

[官方原文](https://www.rfc-editor.org/rfc/rfc8542.html) · PROPOSED STANDARD · 类型：model

模块：`ietf-dc-fabric-topology`、`ietf-dc-fabric-topology-state`、`ietf-dc-fabric-types`。

支持：fabric抽象节点与设备、端口关联。

边界：fabric不等于单台设备。

核对章节：3；4。研读深度：范围/概览/关键定义核对；未对本目录整体编译。

### RFC8944 A YANG Data Model for Layer 2 Network Topologies

[官方原文](https://www.rfc-editor.org/rfc/rfc8944.html) · PROPOSED STANDARD · 类型：model

模块：`ietf-l2-topology`、`ietf-l2-topology-state`。

支持：二层网络、封装、VLAN、LAG、VXLAN及链路属性。

边界：不含全部桥转发和生成树行为。

核对章节：3；4。研读深度：范围/概览/关键定义核对；未对本目录整体编译。

### draft-ogondio-nmop-isis-topology-01 A YANG Data Model for Intermediate System to intermediate System (IS-IS) Topology

[官方原文](https://datatracker.ietf.org/doc/html/draft-ogondio-nmop-isis-topology-01) · 个人草案；Expired · 类型：model

模块：`ietf-l3-isis-topology`。

支持：基于三层单播拓扑扩展协议特定拓扑视图。

边界：已过期；不能与正式设备协议模型或RFC8346附录示例等同。

核对章节：Abstract；Model structure；YANG module。研读深度：摘要、模块声明、模型层次和Datatracker状态核对；未编译。

### draft-ogondio-nmop-ospf-topology-01 A YANG Data Model for Open Shortest Path First (OSPF) Topology

[官方原文](https://datatracker.ietf.org/doc/html/draft-ogondio-nmop-ospf-topology-01) · 个人草案；Expired · 类型：model

模块：`ietf-l3-ospf-topology`。

支持：基于三层单播拓扑扩展协议特定拓扑视图。

边界：已过期；不能与正式设备协议模型或RFC8346附录示例等同。

核对章节：Abstract；Model structure；YANG module。研读深度：摘要、模块声明、模型层次和Datatracker状态核对；未编译。

## 多播与隧道

### draft-ietf-mboned-amt-yang-09 A YANG Data Model for Automatic Multicast Tunneling (AMT)

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-mboned-amt-yang-09) · 工作组草案；RFC Ed Queue；尚未发布RFC · 类型：model

模块：`ietf-amt`。

支持：AMT协议运行管理。

边界：不是直接物理邻接。

核对章节：Abstract；YANG module declarations。研读深度：官方摘要、模块声明、Datatracker发布状态及IANA登记核对；未逐字段审查。

## 层次与分类

### RFC8199 YANG Module Classification

[官方原文](https://www.rfc-editor.org/rfc/rfc8199.html) · INFORMATIONAL · 类型：框架/分类（无YANG模块）

模块：无独立 YANG 模块（见文档类型与范围）。

支持：分清设备模型、网络服务模型及复用层次。。

边界：分类不规定统一数字孪生实例，也不能把某层名称替代实例证据。。

核对章节：§2；§3。研读深度：原文范围、关键树/schema路径、RFC目录状态及IANA登记核对；未编译或验证实例。

### RFC8309 Service Models Explained

[官方原文](https://www.rfc-editor.org/rfc/rfc8309.html) · INFORMATIONAL · 类型：框架（无YANG模块）

模块：无独立 YANG 模块（见文档类型与范围）。

支持：客户服务请求与运营商实现细节分离；服务可映射网络/设备模型。。

边界：不是拓扑或服务模块；论文式层次图不要求每个系统部署所有层。。

核对章节：§3；§4；§6.1；§7.2。研读深度：原文范围、关键树/schema路径、RFC目录状态及IANA登记核对；未编译或验证实例。

### RFC8969 A Framework for Automating Service and Network Management with YANG

[官方原文](https://www.rfc-editor.org/rfc/rfc8969.html) · INFORMATIONAL · 类型：自动化架构（无YANG模块）

模块：无独立 YANG 模块（见文档类型与范围）。

支持：把service/network/device模型与履约、保障、诊断和跨域映射串联。。

边界：框架不提供完整服务依赖图，也不把观察态自动提升为客户意图。。

核对章节：§3.1；§4.1；§4.3；§5。研读深度：原文范围、关键树/schema路径、RFC目录状态及IANA登记核对；未编译或验证实例。

## 接口与封装

### draft-ietf-netmod-intf-ext-yang-19 Common Interface Extension YANG Data Models

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-netmod-intf-ext-yang-19) · 工作组草案 · 类型：model

模块：`ietf-if-extensions`、`ietf-if-ethernet-like`。

支持：接口公共扩展与以太类型接口属性。

边界：WG草案，feature不能自动视为已支持。

关键对象/关联：`/ietf-interfaces:interfaces/interface`。

核对章节：1；2；3；4。研读深度：范围/概览/关键定义核对；未对本目录整体编译。

### draft-ietf-netmod-sub-intf-vlan-model-18 Sub-interface VLAN YANG Data Models

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-netmod-sub-intf-vlan-model-18) · 工作组草案 · 类型：model

模块：`ietf-if-vlan-encapsulation`、`ietf-if-flexible-encapsulation`。

支持：VLAN到子接口分类及标签改写。

边界：不是基于桥成员的完整802.1Q模型。

关键对象/关联：`/ietf-interfaces:interfaces/interface encapsulation augmentation`。

核对章节：1；2；3；4；5；6。研读深度：范围/概览/关键定义核对；未对本目录整体编译。

## 接口类型

### RFC8675 A YANG Data Model for Tunnel Interface Types

[官方原文](https://www.rfc-editor.org/rfc/rfc8675.html) · PROPOSED STANDARD · 类型：model

模块：`iana-tunnel-type`。

支持：IANA tunnel interface类型identities。

边界：类型注册不是隧道状态模型。

核对章节：Abstract。研读深度：官方摘要、模型类型、RFC索引及IANA登记核对；未逐字段审查。

## 时间同步

### RFC8575 YANG Data Model for the Precision Time Protocol (PTP)

[官方原文](https://www.rfc-editor.org/rfc/rfc8575.html) · PROPOSED STANDARD · 类型：model

模块：`ietf-ptp`。

支持：IEEE1588-2008 PTP设备/时钟/数据集管理。

边界：IETF定义该YANG，所管理协议来自IEEE。

核对章节：Abstract。研读深度：官方摘要、模型类型、RFC索引及IANA登记核对；未逐字段审查。

### RFC9249 A YANG Data Model for NTP

[官方原文](https://www.rfc-editor.org/rfc/rfc9249.html) · PROPOSED STANDARD · 类型：model

模块：`ietf-ntp`。

支持：NTP配置及状态。

边界：时间源可信度影响快照比较，不提供通用拓扑。

核对章节：Abstract。研读深度：官方摘要、模型类型、RFC索引及IANA登记核对；未逐字段审查。

## 服务链

### RFC7665 Service Function Chaining (SFC) Architecture

[官方原文](https://www.rfc-editor.org/rfc/rfc7665.html) · INFORMATIONAL · 类型：SFC架构（无YANG模块）

模块：无独立 YANG 模块（见文档类型与范围）。

支持：定义SF/SFF、分类器、服务功能链和服务路径，可建立业务处理依赖图。。

边界：SFC处理次序不是LLDP物理路径；SF逻辑实体不能靠设备名字或接口计数推断。。

核对章节：§1.4；§3；§4。研读深度：原文范围、关键树/schema路径、RFC目录状态及IANA登记核对；未编译或验证实例。

### RFC8300 Network Service Header (NSH)

[官方原文](https://www.rfc-editor.org/rfc/rfc8300.html) · PROPOSED STANDARD · 类型：NSH数据平面协议（无YANG模块）

模块：无独立 YANG 模块（见文档类型与范围）。

支持：NSH的SPI/SI标识服务路径及索引，提供SFC数据平面上下文。。

边界：定义报文格式，未定义完整SFC管理YANG；不是普通IP route hop的替代。。

核对章节：§2；§3；§4。研读深度：原文范围、关键树/schema路径、RFC目录状态及IANA登记核对；未编译或验证实例。

### RFC8924 Service Function Chaining (SFC) Operations, Administration, and Maintenance (OAM) Framework

[官方原文](https://www.rfc-editor.org/rfc/rfc8924.html) · INFORMATIONAL · 类型：SFC OAM框架（无YANG模块）

模块：无独立 YANG 模块（见文档类型与范围）。

支持：SFC故障检测、连续性、连通性和性能OAM概念可关联逻辑SF路径。。

边界：框架不提供通用SFC YANG模块或自动生成服务链。。

核对章节：§1；§3；§4。研读深度：原文范围、关键树/schema路径、RFC目录状态及IANA登记核对；未编译或验证实例。

### RFC9516 Active Operations, Administration, and Maintenance (OAM) for Service Function Chaining (SFC)

[官方原文](https://www.rfc-editor.org/rfc/rfc9516.html) · PROPOSED STANDARD · 类型：SFC主动OAM协议（无YANG模块）

模块：无独立 YANG 模块（见文档类型与范围）。

支持：主动OAM工具测验SFC数据平面路径/服务功能。。

边界：不是常规设备show观察；实际发送测量流量需要另行授权。。

核对章节：§1；§3。研读深度：原文范围、关键树/schema路径、RFC目录状态及IANA登记核对；未编译或验证实例。

### draft-penno-sfc-yang-15 Yang Data Model for Service Function Chaining

[官方原文](https://datatracker.ietf.org/doc/html/draft-penno-sfc-yang-15) · Expired individual Internet-Draft（最新文本2016-06-19） · 类型：YANG Internet-Draft

模块：`service-function`、`service-function-type`、`service-function-chain`、`service-function-path`、`service-function-forwarder`、`service-locator`、`rendered-service-path`、`sfc-common`、`service-statistics`、`service-function-path-metadata`。

支持：记录早期通用SFC管理建模探索；可用于概念参考。。

边界：过期个人草案，没有IETF标准地位；不得把OpenDaylight实现模块或该草案当正式IETF模型。。

关键对象/关联：`历史示例service-functions、service-function-chains、service-function-paths`。

核对章节：固定-15：SF/SFC/SFP/SFF相关模块。研读深度：Datatracker状态与固定版本原文、模块名/树路径核对；不作为已发布RFC或正式登记证明；未编译/验证实例。

## 模型概念

### RFC3444 On the Difference between Information Models and Data Models

[官方原文](https://www.rfc-editor.org/rfc/rfc3444.html) · INFORMATIONAL · 类型：architecture

模块：无独立 YANG 模块（见文档类型与范围）。

支持：区分Information Model与Data Model。

边界：IRTF NMRG研讨背景；不是YANG或拓扑schema。

核对章节：Abstract。研读深度：官方摘要、模型类型、RFC索引及IANA登记核对；未逐字段审查。

## 策略与安全

### RFC3644 Policy Quality of Service (QoS) Information Model

[官方原文](https://www.rfc-editor.org/rfc/rfc3644.html) · PROPOSED STANDARD · 类型：QoS信息模型（非YANG）

模块：无独立 YANG 模块（见文档类型与范围）。

支持：历史策略QoS概念、分类/条件/动作可帮助语义整理。。

边界：Proposed Standard不等于YANG模块；不可拿抽象信息模型直接声称原生YANG导出。。

核对章节：§1；§3。研读深度：RFC目录、摘要与模型类型核对；未逐项审历史信息类。

### RFC8329 Framework for Interface to Network Security Functions

[官方原文](https://www.rfc-editor.org/rfc/rfc8329.html) · INFORMATIONAL · 类型：I2NSF框架（无YANG模块）

模块：无独立 YANG 模块（见文档类型与范围）。

支持：定义consumer/NSF/registration/monitoring接口、ECA流安全策略及功能角色。。

边界：不是防火墙原生schema；通用接口和能力YANG模型需按各草案当前状态分别判断。。

核对章节：§3；§7。研读深度：原文范围、关键树/schema路径、RFC目录状态及IANA登记核对；未编译或验证实例。

### RFC8512 A YANG Module for Network Address Translation (NAT) and Network Prefix Translation (NPT)

[官方原文](https://www.rfc-editor.org/rfc/rfc8512.html) · PROPOSED STANDARD · 类型：YANG NAT设备模型

模块：`ietf-nat`。

支持：NAT能力、策略、实例和运行映射可解释路径地址/端口翻译及反向流。。

边界：不能只看路由就推测NAT映射；需真实转换表、策略优先级与接口绑定，不应输出密钥/敏感会话信息。。

关键对象/关联：`/ietf-nat:nat/instances/instance[id]`；`.../mapping-table`。

核对章节：§2；§3。研读深度：原文范围、关键树/schema路径、RFC目录状态及IANA登记核对；未编译或验证实例。

### RFC8519 YANG Data Model for Network Access Control Lists (ACLs)

[官方原文](https://www.rfc-editor.org/rfc/rfc8519.html) · PROPOSED STANDARD · 类型：YANG ACL及报文字段公共模型

模块：`ietf-access-control-list`、`ietf-packet-fields`、`ietf-ethertypes`。

支持：ACL顺序匹配、动作、统计和interface-ref绑定；packet-fields/ethertypes提供L2/L3/L4字段复用。。

边界：packet-fields/ethertypes是公共groupings/types，不是独立traffic流实例；ACL通过并不证明stateful防火墙完整行为。；已有正式ACL增强RFC9899及TLS/DTLS扩展RFC9761；基础8519并非后续扩展的全集。。

关键对象/关联：`/ietf-access-control-list:acls/acl`；`/ietf-access-control-list:acls/attachment-points/interface`；`/ietf-access-control-list:acls/acl[name]/aces/ace[name]`；`/ietf-access-control-list:acls/attachment-points/interface[interface-id]/ingress`；`同一interface下egress`。

核对章节：§3.1；§4.1；§4.2。研读深度：原文范围、关键树/schema路径、RFC目录状态及IANA登记核对；未编译或验证实例。

### RFC8520 Manufacturer Usage Description Specification

[官方原文](https://www.rfc-editor.org/rfc/rfc8520.html) · PROPOSED STANDARD · 类型：MUD策略文件/ACL增补YANG

模块：`ietf-acldns`、`ietf-mud`。

支持：厂商Usage Description描述设备预期通信并映射ACL，帮助辨别允许的服务流。。

边界：MUD是受信厂商声明，不能从当前连通性反推；附录detext-example不列正式模块。。

关键对象/关联：`/ietf-mud:mud`；`/ietf-access-control-list:acls/acl/aces/ace/matches中的ietf-acldns DNS匹配增补`。

核对章节：§3；§7；§8。研读深度：原文范围、关键树/schema路径、RFC目录状态及IANA登记核对；未编译或验证实例。

### RFC8783 Distributed Denial-of-Service Open Threat Signaling (DOTS) Data Channel Specification

[官方原文](https://www.rfc-editor.org/rfc/rfc8783.html) · PROPOSED STANDARD · 类型：DOTS数据通道YANG/RESTCONF协议模型

模块：`ietf-dots-data-channel`。

支持：DDoS缓解侧管理别名与ACL过滤配置；可关联受保护资源/流。。

边界：场景限定DOTS缓解服务；不是通用防火墙策略全模型。。

关键对象/关联：`/ietf-dots-data-channel:dots-data/dots-client[cuid]`。

核对章节：§4；§5。研读深度：原文范围、关键树/schema路径、RFC目录状态及IANA登记核对；未编译或验证实例。

### RFC9061 A YANG Data Model for IPsec Flow Protection Based on Software-Defined Networking (SDN)

[官方原文](https://www.rfc-editor.org/rfc/rfc9061.html) · PROPOSED STANDARD · 类型：YANG IPsec/IKE设备控制模型

模块：`ietf-i2nsf-ike`、`ietf-i2nsf-ikec`、`ietf-i2nsf-ikeless`。

支持：PAD/SPD/SAD与IKE或IKE-less配置/状态使安全隧道、流保护策略可关联端点与业务流。。

边界：common模块ietf-i2nsf-ikec只有typedef/groupings；不是所有部署场景都覆盖（host-to-gateway不在本文主要范围）；不能由隧道UP补SA/密钥。。

关键对象/关联：`/ietf-i2nsf-ike:ipsec-ike/pad/pad-entry[name]`；`/ietf-i2nsf-ike:ipsec-ike/conn-entry`；`/ietf-i2nsf-ikeless:ipsec-ikeless/spd/spd-entry[name]`。

核对章节：§3；§5.1；§5.2；§5.3。研读深度：原文范围、关键树/schema路径、RFC目录状态及IANA登记核对；未编译或验证实例。

### RFC9132 Distributed Denial-of-Service Open Threat Signaling (DOTS) Signal Channel Specification

[官方原文](https://www.rfc-editor.org/rfc/rfc9132.html) · PROPOSED STANDARD · 类型：DOTS信令协议与YANG派生报文结构

模块：`iana-dots-signal-channel`、`ietf-dots-signal-channel`。

支持：DDoS缓解请求、范围和状态与服务保障事件关联；含IANA维护信令定义。。

边界：替代RFC8782；其YANG用于协议消息/CBOR结构，不能当普通RFC8345拓扑节点数据。。

关键对象/关联：`DOTS signal-channel报文结构（非通用设备datastore根）`。

核对章节：§4；§5。研读深度：原文范围、关键树/schema路径、RFC目录状态及IANA登记核对；未编译或验证实例。

### RFC9348 A YANG Data Model for IP Traffic Flow Security

[官方原文](https://www.rfc-editor.org/rfc/rfc9348.html) · PROPOSED STANDARD · 类型：IPsec模型增补YANG

模块：`ietf-ipsec-iptfs`。

支持：扩展9061以配置IP Traffic Flow Security的拥塞控制、报文大小和速率等。。

边界：这是IPTFS而非任意防火墙；依赖真实IPsec策略，不应将普通QoS/隧道统计伪装该模块。。

关键对象/关联：`/ietf-i2nsf-ike:ipsec-ike/conn-entry/spd/spd-entry/ipsec-policy-config/processing-info/ipsec-sa-cfg/ietf-ipsec-iptfs:traffic-flow-security`。

核对章节：§2；§3.1；§3.2。研读深度：原文范围、关键树/schema路径、RFC目录状态及IANA登记核对；未编译或验证实例。

### RFC9472 A YANG Data Model for Reporting Software Bills of Materials (SBOMs) and Vulnerability Information

[官方原文](https://www.rfc-editor.org/rfc/rfc9472.html) · PROPOSED STANDARD · 类型：YANG MUD透明度扩展

模块：`ietf-mud-transparency`。

支持：向MUD附加SBOM和漏洞信息的URL、本地well-known或联系信息，关联设备/软件资源的安全保障来源。。

边界：公布检索位置而非完整SBOM内容或已确认漏洞/故障关系；与inventory需外部稳定关联，不能仅由公告断言某设备已受攻击。。

关键对象/关联：`/ietf-mud:mud/ietf-mud-transparency:transparency`；`.../transparency/sboms[version-info]`。

核对章节：§1.2；§3；§4。研读深度：旁支模型目录核对：官方范围、模块、关键路径、RFC目录状态及IANA登记；未逐叶审计/编译/验证实例。

### RFC9761 Manufacturer Usage Description (MUD) for TLS and DTLS Profiles for Internet of Things (IoT) Devices

[官方原文](https://www.rfc-editor.org/rfc/rfc9761.html) · PROPOSED STANDARD · 类型：YANG MUD与ACL的TLS/DTLS扩展

模块：`iana-tls-profile`、`ietf-acl-tls`、`ietf-mud-tls`。

支持：增加TLS/DTLS版本、密码套件和握手参数的客户端profile匹配；以MUD说明设备预期加密通信，关联ACL规则。。

边界：不是通用TLS服务/连接配置模型；(D)TLS1.3加密/ECH会限制旁路可见性，不能把握手profile或制造商要求当作已观测安全状态。。

关键对象/关联：`/ietf-access-control-list:acls/acl/aces/ace/matches/ietf-acl-tls:client-profiles/tls-dtls-profile[name]`；`/ietf-mud:mud/ietf-mud-tls:is-tls-dtls-profile-supported`。

核对章节：§5.1；§5.2；§5.3；§5.4。研读深度：旁支模型目录核对：官方范围、模块、关键路径、RFC目录状态及IANA登记；未逐叶审计/编译/验证实例。

### RFC9899 Extensions to the YANG Data Model for Access Control Lists (ACLs)

[官方原文](https://www.rfc-editor.org/rfc/rfc9899.html) · PROPOSED STANDARD · 类型：YANG ACL增强与IANA类型

模块：`ietf-acl-enh`、`iana-ipv6-ext-types`、`iana-icmpv4-types`、`iana-icmpv6-types`。

支持：扩展RFC8519的集合/alias、VLAN/I-SID/MPLS及载荷匹配、分片/TCP/ICMP和互补动作；能补充策略对端口/流路径的影响。。

边界：通过augmentation扩展8519，不是整体替代，也没有RFC-index正式Updates条目；feature和具体ACL绑定仍需证据。IANA类型模块不是额外策略实例根，不能仅靠配置证明已生效转发。。

关键对象/关联：`/ietf-access-control-list:acls/acl/aces/ace`；`iana-ipv6-ext-types identity definitions`；`/ietf-access-control-list:acls/ietf-acl-enh:defined-sets`；`/ietf-access-control-list:acls/acl/aces/ace/matches（augment payload/MPLS/VLAN/IPv4/IPv6/L4）`；`/ietf-access-control-list:acls/acl/aces/ace/actions（augment logging/counters/rate-limit）`。

核对章节：§3；§4；§6。研读深度：旁支模型目录核对：官方范围、模块、关键路径、RFC目录状态及IANA登记；未逐叶审计/编译/验证实例。

### draft-ietf-i2nsf-capability-data-model-32 I2NSF Capability YANG Data Model

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-i2nsf-capability-data-model-32) · WG Internet-Draft; RFC Ed Queue; 2026-09-09 blocked Waiting for Action Holder · 类型：YANG Internet-Draft

模块：`ietf-i2nsf-capability`。

支持：描述NSF可支持的ECA匹配/动作/解析功能，为选择安全功能提供能力证据。。

边界：未发布RFC；catalog旧别名/实现不能取代固定文本模块名；能力声明不证明当前规则已实施。。

关键对象/关联：`/ietf-i2nsf-capability:nsf[nsf-name]`。

核对章节：固定-32：capability model及YANG module。研读深度：Datatracker状态与固定版本原文、模块名/树路径核对；不作为已发布RFC或正式登记证明；未编译/验证实例。

### draft-ietf-i2nsf-consumer-facing-interface-dm-31 I2NSF Consumer-Facing Interface YANG Data Model

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-i2nsf-consumer-facing-interface-dm-31) · WG Internet-Draft; RFC Ed Queue; 2026-09-09 blocked Waiting for Action Holder · 类型：YANG Internet-Draft

模块：`ietf-i2nsf-cons-facing-interface`。

支持：用户侧流安全意图、终端/用户/位置组及威胁策略，需翻译为NSF侧实际规则。。

边界：没有已发布RFC号；本文是client intent，不是当前设备运行态或拓扑关系。。

关键对象/关联：`/ietf-i2nsf-cons-facing-interface:i2nsf-cfi-policy[name]/rules[name]`；`/ietf-i2nsf-cons-facing-interface:endpoint-groups`。

核对章节：§3 policy tree；§4 endpoint groups；§6 module。研读深度：Datatracker状态与固定版本原文、模块名/树路径核对；不作为已发布RFC或正式登记证明；未编译/验证实例。

### draft-ietf-i2nsf-nsf-facing-interface-dm-29 I2NSF Network Security Function-Facing Interface YANG Data Model

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-i2nsf-nsf-facing-interface-dm-29) · WG Internet-Draft; RFC Ed Queue; 2026-09-09 blocked Waiting for Action Holder · 类型：YANG Internet-Draft

模块：`ietf-i2nsf-nsf-facing-interface`。

支持：NSF侧ECA、匹配、动作、优先级/冲突解决可描述设备安全处理。。

边界：尚非发布RFC；stateful会话/厂商执行差异仍需设备真实证据，不能仅靠ACL外观作等价映射。。

关键对象/关联：`/ietf-i2nsf-nsf-facing-interface:i2nsf-security-policy[name]/rules[name]`。

核对章节：固定-29：security policy tree及module。研读深度：Datatracker状态与固定版本原文、模块名/树路径核对；不作为已发布RFC或正式登记证明；未编译/验证实例。

### draft-ietf-i2nsf-nsf-monitoring-data-model-20 I2NSF NSF Monitoring Interface YANG Data Model

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-i2nsf-nsf-monitoring-data-model-20) · WG Internet-Draft; RFC Ed Queue; 2026-09-09 blocked Waiting for Action Holder · 类型：YANG Internet-Draft

模块：`ietf-i2nsf-monitoring-interface`。

支持：NSF计数、接口引用、事件/告警用于安全服务保障。。

边界：尚未发布RFC；名为Monitoring不等于覆盖所有防火墙会话模型，时间/来源必须实际取得。。

关键对象/关联：`/ietf-i2nsf-monitoring-interface:i2nsf-counters`；`.../system-interface[interface-name]`。

核对章节：固定-20：monitoring tree与YANG module。研读深度：Datatracker状态与固定版本原文、模块名/树路径核对；不作为已发布RFC或正式登记证明；未编译/验证实例。

### draft-ietf-i2nsf-registration-interface-dm-26 I2NSF Registration Interface YANG Data Model for NSF Capability Registration

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-i2nsf-registration-interface-dm-26) · WG Internet-Draft; RFC Ed Queue（固定 -26 文本 2023-05-10；2026-09-09 blocked: Waiting for Action Holder） · 类型：YANG RPC/notification模型草案

模块：`ietf-i2nsf-registration-interface`。

支持：在Security Controller与DMS之间查询/注册NSF能力及访问地址、管理协议，通知能力变化；以NSF身份和访问上下文关联服务实现。。

边界：IANA以RFC-ietf-…占位登记，Datatracker仍RFC Ed Queue且未发布RFC；能力登记不证明功能已部署或当前流可达，也不能由IP/MAC补造NSF能力。。

关键对象/关联：`RPC /ietf-i2nsf-registration-interface:nsf-capability-registration（input/query-nsf-capability；output/nsf[nsf-name]）`；`notification /ietf-i2nsf-registration-interface:nsf-capability-update/nsf[nsf-name]`。

核对章节：§4；§5.1；§5.2。研读深度：旁支模型目录核对：Datatracker状态、固定 -26 原文RPC/notification树、模块与IANA占位登记；未编译/实例验证。

### draft-ietf-opsawg-ucl-acl-15 A YANG Data Model and RADIUS Extension for Policy-Based Network Access Control

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-opsawg-ucl-acl-15) · 工作组草案；RFC Ed Queue；尚未发布RFC · 类型：model

模块：`ietf-ucl-acl`。

支持：用户组身份及时间感知ACL、RADIUS组属性。

边界：不是全部有状态防火墙模型。

核对章节：Abstract；YANG module declarations。研读深度：官方摘要、模块声明、Datatracker发布状态及IANA登记核对；未逐字段审查。

### draft-ietf-rtgwg-qos-model-16 A YANG Data Model for Quality of Service (QoS) in IP Networks

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-rtgwg-qos-model-16) · Active WG Internet-Draft（2026-09-25） · 类型：YANG Internet-Draft

模块：`ietf-qos-types`、`ietf-traffic-policy`、`ietf-qos-action`、`ietf-diffserv`、`ietf-qos-oper`、`ietf-scheduler-policy`、`ietf-queue-policy`。

支持：分类器、policing/marking/queue/scheduler及接口方向统计可进入转发/拥塞推演。。

边界：尚非RFC；不能使用早年ietf-qos-classifier模块名替代本固定版本的ietf-traffic-policy，也不能从DSCP猜厂商队列策略。。

关键对象/关联：`/ietf-traffic-policy:classifiers/classifier[name]`；`/ietf-traffic-policy:policies/policy[name type]`；`/ietf-interfaces:interfaces/interface/ietf-qos-oper:qos-interface-statistics`。

核对章节：§2 model design；各YANG modules；附录树。研读深度：Datatracker状态与固定版本原文、模块名/树路径核对；不作为已发布RFC或正式登记证明；未编译/验证实例。

## 设备、接口、实例与系统

### RFC4861 Neighbor Discovery for IP version 6 (IPv6)

[官方原文](https://www.rfc-editor.org/rfc/rfc4861.html) · DRAFT STANDARD · 类型：protocol

模块：无独立 YANG 模块（见文档类型与范围）。

支持：IPv6 邻居、路由器/前缀发现与可达性确认。。

边界：邻居状态和链路范围需保留；ND 不等于远端 chassis 清单，IPv6 link-local 地址需要接口 zone。。

核对章节：4；5；6；7。研读深度：官方元数据、模型相关协议章节与替代/更新关系核对；未逐项穷举全部报文细节。

### RFC7223 A YANG Data Model for Interface Management

[官方原文](https://www.rfc-editor.org/rfc/rfc7223.html) · PROPOSED STANDARD · 类型：model

模块：`ietf-interfaces`。

支持：早期接口配置/状态分离模型；接口名称、类型、状态及层次。。

边界：已被 RFC8343 废止；不应混用新旧状态树。Appendix ex-* 只是示例模块。。

关键对象/关联：`/ietf-interfaces:interfaces/interface`；`/ietf-interfaces:interfaces-state/interface`。

后继文档：RFC8343。

核对章节：1；3；4；5。研读深度：历史替代链、结构差异和官方元数据核对。

### RFC7224 IANA Interface Type YANG Module

[官方原文](https://www.rfc-editor.org/rfc/rfc7224.html) · PROPOSED STANDARD · 类型：model

模块：`iana-if-type`。

支持：给 RFC8343 接口 type 提供 IANA ifType 身份集合。。

边界：类型表示接口技术，不是设备型号，也不能直接推出 TP、VLAN 或真实邻接。。

关键对象/关联：`iana-if-type:iana-interface-type`；`iana-if-type:ethernetCsmacd`；`iana-if-type:l3ipvlan`。

核对章节：2；3；4。研读深度：模型树、关键定义与适用范围精读；官方元数据和替代关系核对。

### RFC7277 A YANG Data Model for IP Management

[官方原文](https://www.rfc-editor.org/rfc/rfc7277.html) · PROPOSED STANDARD · 类型：model

模块：`ietf-ip`。

支持：早期 IPv4/IPv6 地址、邻居和转发配置。。

边界：已被 RFC8344 废止；状态树迁移应依 NMDA 版本。。

关键对象/关联：`/ietf-interfaces:interfaces/interface/ietf-ip:ipv4`。

后继文档：RFC8344。

核对章节：1；2；3；4。研读深度：历史替代链、模型概要与官方元数据核对。

### RFC7317 A YANG Data Model for System Management

[官方原文](https://www.rfc-editor.org/rfc/rfc7317.html) · PROPOSED STANDARD · 类型：model

模块：`iana-crypt-hash`、`ietf-system`。

支持：系统标识、时钟、DNS、NTP、RADIUS/本地认证和重启控制；与 SNMPv2-MIB 的 system 对象映射。。

边界：系统 hostname、软件版本不等于全局唯一 chassis 身份；不是物理硬件清单，也不表示网络拓扑。。

关键对象/关联：`/ietf-system:system`；`/ietf-system:system-state`。

核对章节：2；3；4；5；6。研读深度：模型树、关键定义与适用范围精读；官方元数据和替代关系核对。

### RFC826 An Ethernet Address Resolution Protocol: Or Converting Network Protocol Addresses to 48.bit Ethernet Address for Transmission on Ethernet Hardware

[官方原文](https://www.rfc-editor.org/rfc/rfc826.html) · INTERNET STANDARD · 类型：protocol

模块：无独立 YANG 模块（见文档类型与范围）。

支持：IPv4 到 Ethernet 地址解析，支持本地链路层邻居缓存语义。。

边界：ARP 缓存可陈旧、静态或代理；IP/MAC 不能全局覆盖 owner，更不能证明物理直连。。

核对章节：Abstract；Protocol definition。研读深度：官方元数据、模型相关协议章节与替代/更新关系核对；未逐项穷举全部报文细节。

### RFC8343 A YANG Data Model for Interface Management

[官方原文](https://www.rfc-editor.org/rfc/rfc8343.html) · PROPOSED STANDARD · 类型：model

模块：`ietf-interfaces`。

支持：接口名称、mandatory type、admin/oper 状态、速度、计数器、物理地址及 higher/lower-layer-if；映射 IF-MIB。。

边界：接口层次是本设备内逻辑关系，不能证明远端端口或物理电缆；未知类型、计数器不能凭端口名补值。。

关键对象/关联：`/ietf-interfaces:interfaces/interface[name]`；`ietf-interfaces:interface-type`。

核对章节：1.1；3.1；3.2；3.3；4；5。研读深度：模型树、关键定义与适用范围精读；官方元数据和替代关系核对。

### RFC8344 A YANG Data Model for IP Management

[官方原文](https://www.rfc-editor.org/rfc/rfc8344.html) · PROPOSED STANDARD · 类型：model

模块：`ietf-ip`。

支持：接口内 IPv4/IPv6 地址、prefix/netmask choice、MTU/forwarding、ARP/ND 邻居、origin 和 IPv6 邻居状态。。

边界：邻居键作用域是接口与地址族；ARP/ND 证明本地邻居缓存关系，不证明远端管理身份或物理直连；未知 prefix/链路层地址不能捏造。。

关键对象/关联：`/ietf-interfaces:interfaces/interface/ietf-ip:ipv4/address[ip]`；`/ietf-interfaces:interfaces/interface/ietf-ip:ipv4/neighbor[ip]`；`/ietf-interfaces:interfaces/interface/ietf-ip:ipv6/neighbor[ip]`。

核对章节：1.1；2；3；4。研读深度：模型树、关键定义与适用范围精读；官方元数据和替代关系核对。

### RFC8348 A YANG Data Model for Hardware Management

[官方原文](https://www.rfc-editor.org/rfc/rfc8348.html) · PROPOSED STANDARD · 类型：model

模块：`ietf-hardware`、`iana-hardware`、`ietf-hardware-state`。

支持：组件 parent/contains-child、class、型号/序列号、软硬件修订、状态和传感器；映射 ENTITY/SENSOR/STATE MIB；附录硬件状态兼容模块有正式注册。。

边界：name/class 等键与 mandatory 字段必须来自真实硬件观测；端口名与邻居协议 RID 不能替代组件身份；不证明线缆两端关系。。

关键对象/关联：`/ietf-hardware:hardware/component[name]`；`iana-hardware:chassis`；`iana-hardware:port`。

核对章节：3.1；4；5；6；7；Appendix A。研读深度：模型树、关键定义与适用范围精读；官方元数据和替代关系核对。

### RFC8529 YANG Data Model for Network Instances

[官方原文](https://www.rfc-editor.org/rfc/rfc8529.html) · PROPOSED STANDARD · 类型：model

模块：`ietf-network-instance`。

支持：设备内 VRF、VSI 及混合实例的挂载点；接口或 IPv4/IPv6 协议族绑定 NI。。

边界：name 明确是 device-scoped；相同 VRF 名称不等于跨设备 VPN，相异名称也不证明隔离；标准没有统一 default VRF 字符串。。

关键对象/关联：`/ietf-network-instance:network-instances/network-instance[name]`；`/ietf-interfaces:interfaces/interface/ietf-network-instance:bind-ni-name`；`vrf-root/vsi-root/vv-root mount points`。

核对章节：2；3.1；3.2；3.4；6。研读深度：模型树、关键定义与适用范围精读；官方元数据和替代关系核对。

### RFC8530 YANG Model for Logical Network Elements

[官方原文](https://www.rfc-editor.org/rfc/rfc8530.html) · PROPOSED STANDARD · 类型：model

模块：`ietf-logical-network-element`。

支持：设备内独立逻辑网络元素及资源分配、managed 标志、schema mount 根。。

边界：LNE name 设备内唯一，逻辑设备不是独立 chassis；不能把每个协议实例当物理设备，也不能无挂载证据推导 LNE。。

关键对象/关联：`/ietf-logical-network-element:logical-network-elements/logical-network-element[name]/root`；`/ietf-interfaces:interfaces/interface/ietf-logical-network-element:bind-lne-name`。

核对章节：2；3.1；3.2；3.3；6。研读深度：模型树、关键定义与适用范围精读；官方元数据和替代关系核对。

### RFC9105 A YANG Data Model for Terminal Access Controller Access-Control System Plus (TACACS+)

[官方原文](https://www.rfc-editor.org/rfc/rfc9105.html) · PROPOSED STANDARD · 类型：model

模块：`ietf-system-tacacs-plus`。

支持：旧 TACACS+ 客户端配置和统计。。

边界：已被 RFC9950 废止；认证信息与身份凭据不可作为拓扑属性暴露。。

关键对象/关联：`/ietf-system:system/ietf-system-tacacs-plus:tacacs-plus`。

后继文档：RFC9950。

核对章节：3；4。研读深度：历史替代链、模型概要与官方元数据核对。

### RFC9742 A YANG Data Model for Syslog Management

[官方原文](https://www.rfc-editor.org/rfc/rfc9742.html) · PROPOSED STANDARD · 类型：model

模块：`ietf-syslog`。

支持：2025-04 标准化 syslog console/file/remote logging 选择、过滤和传输。。

边界：日志事件需另行关联时间和设备；syslog server 地址不是数据面链路。。

关键对象/关联：`/ietf-syslog:syslog/actions`。

核对章节：3；4；5。研读深度：模型树、关键定义与适用范围精读；官方元数据和替代关系核对。

### RFC9950 A YANG Data Model for Terminal Access Controller Access-Control System Plus (TACACS+)

[官方原文](https://www.rfc-editor.org/rfc/rfc9950.html) · PROPOSED STANDARD · 类型：model

模块：`ietf-system-tacacs-plus`。

支持：2026-03 替代 RFC9105，扩充 TACACS+ 安全传输和凭据引用，保留服务器配置/统计。。

边界：管理面客户端模型，不证明控制面邻接或物理 topology；凭据不应进入拓扑导出。。

关键对象/关联：`/ietf-system:system/ietf-system-tacacs-plus:tacacs-plus`。

核对章节：1.1；3；4；5。研读深度：模型树、关键定义与适用范围精读；官方元数据和替代关系核对。

## 语言与数据存储

### RFC10016 System-Defined Configuration

[官方原文](https://www.rfc-editor.org/rfc/rfc10016.html) · PROPOSED STANDARD · 类型：management

模块：`ietf-system-datastore`。

支持：系统定义配置数据存储。

边界：更新NMDA；不能视为已部署能力。

关键对象/关联：`ietf-system-datastore:system identity (base ietf-datastores:conventional)`。

核对章节：3；4；5。研读深度：范围/概览/关键定义核对；未对本目录整体编译。

### RFC10035 YANG Library: Addition of the augmented-by List

[官方原文](https://www.rfc-editor.org/rfc/rfc10035.html) · PROPOSED STANDARD · 类型：management

模块：`ietf-yang-library-augmentedby`。

支持：YANG Library augmented-by关系。

边界：不是拓扑对象关联。

核对章节：3；4。研读深度：范围/概览/关键定义核对；未对本目录整体编译。

### RFC7950 The YANG 1.1 Data Modeling Language

[官方原文](https://www.rfc-editor.org/rfc/rfc7950.html) · PROPOSED STANDARD · 类型：management

模块：无独立 YANG 模块（见文档类型与范围）。

支持：YANG1.1结构与约束。

边界：类型合法不证明事实真实。

核对章节：7；8；9。研读深度：范围/概览/关键定义核对；未对本目录整体编译。

### RFC7951 JSON Encoding of Data Modeled with YANG

[官方原文](https://www.rfc-editor.org/rfc/rfc7951.html) · PROPOSED STANDARD · 类型：management

模块：无独立 YANG 模块（见文档类型与范围）。

支持：JSON编码。

边界：不定义发现算法。

核对章节：4；6。研读深度：范围/概览/关键定义核对；未对本目录整体编译。

### RFC7952 Defining and Using Metadata with YANG

[官方原文](https://www.rfc-editor.org/rfc/rfc7952.html) · PROPOSED STANDARD · 类型：management

模块：`ietf-yang-metadata`。

支持：标准元数据定义机制。

边界：不自带任意证据字段。

核对章节：3；5。研读深度：范围/概览/关键定义核对；未对本目录整体编译。

### RFC8340 YANG Tree Diagrams

[官方原文](https://www.rfc-editor.org/rfc/rfc8340.html) · BEST CURRENT PRACTICE · 类型：management

模块：无独立 YANG 模块（见文档类型与范围）。

支持：YANG树图记法。

边界：图示不是校验器。

核对章节：2。研读深度：范围/概览/关键定义核对；未对本目录整体编译。

### RFC8342 Network Management Datastore Architecture (NMDA)

[官方原文](https://www.rfc-editor.org/rfc/rfc8342.html) · PROPOSED STANDARD · 类型：architecture

模块：`ietf-datastores`、`ietf-origin`。

支持：NMDA数据存储和origin。

边界：配置来源不等于通用置信度。

核对章节：5；6；7。研读深度：范围/概览/关键定义核对；未对本目录整体编译。

### RFC8525 YANG Library

[官方原文](https://www.rfc-editor.org/rfc/rfc8525.html) · PROPOSED STANDARD · 类型：management

模块：`ietf-yang-library`。

支持：模块集、schema与datastore能力清单。

边界：实际feature与deviation须明确。

核对章节：3；4。研读深度：范围/概览/关键定义核对；未对本目录整体编译。

### RFC8528 YANG Schema Mount

[官方原文](https://www.rfc-editor.org/rfc/rfc8528.html) · PROPOSED STANDARD · 类型：management

模块：`ietf-yang-schema-mount`。

支持：实例相关schema mount。

边界：需要已定义挂载点，非自动跨设备绑定。

关键对象/关联：`/ietf-yang-schema-mount:schema-mounts`；`ietf-yang-schema-mount:mount-point`。

核对章节：3；4。研读深度：范围/概览/关键定义核对；未对本目录整体编译。

### RFC8791 YANG Data Structure Extensions

[官方原文](https://www.rfc-editor.org/rfc/rfc8791.html) · PROPOSED STANDARD · 类型：management

模块：`ietf-yang-structure-ext`。

支持：抽象YANG数据结构。

边界：不是网络拓扑实例。

核对章节：3；4。研读深度：范围/概览/关键定义核对；未对本目录整体编译。

### RFC8819 YANG Module Tags

[官方原文](https://www.rfc-editor.org/rfc/rfc8819.html) · PROPOSED STANDARD · 类型：management

模块：`ietf-module-tags`、`ietf-module-tags-state`。

支持：模块标签。

边界：是模块分类而非节点分类。

核对章节：2；3；5。研读深度：范围/概览/关键定义核对；未对本目录整体编译。

### RFC9195 A File Format for YANG Instance Data

[官方原文](https://www.rfc-editor.org/rfc/rfc9195.html) · PROPOSED STANDARD · 类型：management

模块：`ietf-yang-instance-data`。

支持：实例数据文件与schema说明。

边界：文件仍须声明来源和覆盖范围。

核对章节：3；4；5。研读深度：范围/概览/关键定义核对；未对本目录整体编译。

### RFC9254 Encoding of Data Modeled with YANG in the Concise Binary Object Representation (CBOR)

[官方原文](https://www.rfc-editor.org/rfc/rfc9254.html) · PROPOSED STANDARD · 类型：management

模块：无独立 YANG 模块（见文档类型与范围）。

支持：YANG-CBOR编码。

边界：不添加拓扑语义。

核对章节：3；4；5。研读深度：范围/概览/关键定义核对；未对本目录整体编译。

### RFC9595 YANG Schema Item iDentifier (YANG SID)

[官方原文](https://www.rfc-editor.org/rfc/rfc9595.html) · PROPOSED STANDARD · 类型：management

模块：`ietf-sid-file`。

支持：YANG SID及SID文件。

边界：不添加拓扑语义。

核对章节：3；4；5。研读深度：范围/概览/关键定义核对；未对本目录整体编译。

### RFC9890 An Update to YANG Module Names Registration

[官方原文](https://www.rfc-editor.org/rfc/rfc9890.html) · PROPOSED STANDARD · 类型：management

模块：无独立 YANG 模块（见文档类型与范围）。

支持：YANG模块登记规则。

边界：IANA登记不等于RFC已出版。

核对章节：2。研读深度：范围/概览/关键定义核对；未对本目录整体编译。

### RFC9907 Guidelines for Authors and Reviewers of Documents Containing YANG Data Models

[官方原文](https://www.rfc-editor.org/rfc/rfc9907.html) · BEST CURRENT PRACTICE · 类型：management

模块：`iana-template`、`ietf-template`。

支持：模型作者审查BCP。

边界：替代8407，不是拓扑模型。

核对章节：2.5；3；4。研读深度：范围/概览/关键定义核对；未对本目录整体编译。

### RFC9911 Common YANG Data Types

[官方原文](https://www.rfc-editor.org/rfc/rfc9911.html) · PROPOSED STANDARD · 类型：management

模块：`ietf-inet-types`、`ietf-yang-types`。

支持：公共YANG/INET类型。

边界：替代6991，依赖升级须锁定。

核对章节：3；4。研读深度：范围/概览/关键定义核对；未对本目录整体编译。

### draft-ietf-netmod-yang-module-versioning-17 Updated YANG Module Revision Handling

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-netmod-yang-module-versioning-17) · 工作组草案；RFC Ed Queue；尚未发布RFC · 类型：management

模块：`ietf-yang-revisions`、`ietf-yang-library-status`。

支持：模块兼容性、修订与生命周期更新规则。

边界：不直接改变已冻结模块集。

核对章节：Abstract；YANG module declarations。研读深度：官方摘要、模块声明、Datatracker发布状态及IANA登记核对；未逐字段审查。

### draft-ietf-netmod-yang-semver-28 YANG Semantic Versioning

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-netmod-yang-semver-28) · 工作组草案；RFC Ed Queue；尚未发布RFC · 类型：management

模块：`ietf-yang-semver`、`ietf-yang-library-semver`。

支持：YANG语义版本与按版本导入扩展。

边界：对既有RFC的Updates目前是草案提议。

核对章节：Abstract；YANG module declarations。研读深度：官方摘要、模块声明、Datatracker发布状态及IANA登记核对；未逐字段审查。

## 资源清单

### RFC9179 A YANG Grouping for Geographic Locations

[官方原文](https://www.rfc-editor.org/rfc/rfc9179.html) · PROPOSED STANDARD · 类型：model

模块：`ietf-geo-location`。

支持：地理位置grouping。

边界：grouping无独立资源树。

核对章节：2；3。研读深度：范围/概览/关键定义核对；未对本目录整体编译。

## 路径计算/跨域

### RFC5440 Path Computation Element (PCE) Communication Protocol (PCEP)

[官方原文](https://www.rfc-editor.org/rfc/rfc5440.html) · PROPOSED STANDARD · 类型：protocol

模块：无独立 YANG 模块（见文档类型与范围）。

支持：PCEP 的 PCE/PCC 路径计算请求/响应、约束对象与错误处理。。

边界：非 YANG；PCEP 与 TE path-computation YANG RPC 是不同管理/控制契约。。

核对章节：§4–7。研读深度：abstract, document status and selected architecture/protocol sections; not exhaustive protocol review。

### RFC7926 Problem Statement and Architecture for Information Exchange between Interconnected Traffic-Engineered Networks

[官方原文](https://www.rfc-editor.org/rfc/rfc7926.html) · BEST CURRENT PRACTICE · 类型：architecture

模块：无独立 YANG 模块（见文档类型与范围）。

支持：互连 TE 网络之间的可用信息与抽象交换架构，跨域路径选择、信息隐藏与模型关联。。

边界：Informational；不提供新的可加载 topology YANG 模块或自动物理发现机制。。

核对章节：§2–7。研读深度：abstract, document status and selected architecture/protocol sections; not exhaustive protocol review。

### RFC8231 Path Computation Element Communication Protocol (PCEP) Extensions for Stateful PCE

[官方原文](https://www.rfc-editor.org/rfc/rfc8231.html) · PROPOSED STANDARD · 类型：protocol

模块：无独立 YANG 模块（见文档类型与范围）。

支持：PCEP stateful extensions 的 LSP state synchronization、delegation/update。。

边界：非 YANG；PCE 的 LSP view 不是物理图完整性的保证。。

核对章节：§4–7。研读深度：abstract, document status and selected architecture/protocol sections; not exhaustive protocol review。

### RFC8281 Path Computation Element Communication Protocol (PCEP) Extensions for PCE-Initiated LSP Setup in a Stateful PCE Model

[官方原文](https://www.rfc-editor.org/rfc/rfc8281.html) · PROPOSED STANDARD · 类型：protocol

模块：无独立 YANG 模块（见文档类型与范围）。

支持：PCEP 由 PCE 主动发起/删除 LSP 的机制。。

边界：非 YANG；路径请求/意图与实际建立/转发状态需区分。。

核对章节：§4–6。研读深度：abstract, document status and selected architecture/protocol sections; not exhaustive protocol review。

### RFC9826 A YANG Data Model for the Path Computation Element Communication Protocol (PCEP)

[官方原文](https://www.rfc-editor.org/rfc/rfc9826.html) · PROPOSED STANDARD · 类型：device-yang

模块：`ietf-pcep`、`ietf-pcep-stats`。

支持：PCEP speaker 的 entity/role、peer/session、capabilities、PCE domains、PCEPS、通知/RPC 和统计；stateful LSP-DB 记录 PCEP-specific LSP state，并引用通用 TE LSP 状态。。

边界：2025-08 正式发布；是 PCEP 管理模型，不是全网拓扑、路径算法或 TE path-computation RPC schema；LSP-DB 键为 PLSP-ID/PCC-IP/LSP-ID，peer/session 不证明直接物理相连。。

关键对象/关联：`/ietf-pcep:pcep/entity`；`/ietf-pcep:pcep/entity/lsp-db/lsp`；`/ietf-pcep:pcep/entity/peers/peer/ietf-pcep-stats:stats`。

核对章节：§4–8；Appendix A。研读深度：abstract, model tree, module import/augment and sections 4-8; not compiled。

### draft-ietf-ccamp-optical-path-computation-yang-09 YANG Data Models for requesting Path Computation in WDM Optical Networks

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-ccamp-optical-path-computation-yang-09) · Internet-Draft / Active · 类型：rpc-yang

模块：`ietf-wdm-path-computation`。

支持：WDM（WSON/Flexi-grid）路径计算请求/结果：在通用 TE path-computation RPC 增加光参数/标签/约束。。

边界：-09 活跃草案；不是实际光路配置或计算引擎，依赖多份未发布模型。。

核对章节：§2–4。研读深度：abstract, model tree, module import/augment and selected semantic sections; not compiled。

### draft-ietf-ccamp-otn-path-computation-yang-09 A YANG Data Model for requesting Path Computation in an Optical Transport Network (OTN)

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-ccamp-otn-path-computation-yang-09) · Internet-Draft / Active · 类型：rpc-yang

模块：`ietf-otn-path-computation`。

支持：OTN 路径计算请求/结果：在通用 TE RPC 增加 OTN 带宽和 label 属性。。

边界：-09 活跃草案，依赖 Layer1/TE path-computation 草案；不定义调度器或预留成功保证。。

核对章节：§2–3。研读深度：abstract, model tree, module import/augment and selected semantic sections; not compiled。

### draft-ietf-teas-yang-path-computation-28 A YANG Data Model for requesting path computation

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-teas-yang-path-computation-28) · Internet-Draft / Active · 类型：rpc-yang

模块：`ietf-te-path-computation`。

支持：在通用 TE RPC 上增加按需域内路径请求与结果；用于隐去内部拓扑的跨域/packet-optical 场景。。

边界：-28 活跃草案，Waiting for AD Go-Ahead；描述请求/结果契约，不实现算法，不等于 PCEP wire protocol。。

核对章节：§2–4；模型模块。研读深度：abstract, model tree, module import/augment and selected semantic sections; not compiled。

## 路由、多播与标签协议

### RFC10005 BGP Link Bandwidth Extended Community

[官方原文](https://www.rfc-editor.org/rfc/rfc10005.html) · PROPOSED STANDARD · 类型：protocol

模块：无独立 YANG 模块（见文档类型与范围）。

支持：2026-06 Link Bandwidth extended community 和 multipath 分配语义。。

边界：是控制面权重/通告属性，不能直接当接口实际 speed 或实时可用 bandwidth。。

核对章节：2；3；7。研读深度：官方元数据、模型相关协议章节与替代/更新关系核对；未逐项穷举全部报文细节。

### RFC10041 Advertising Unreachable Links in OSPF

[官方原文](https://www.rfc-editor.org/rfc/rfc10041.html) · PROPOSED STANDARD · 类型：model

模块：`ietf-ospf-functional-capability`、`ietf-ospf-unreachable-links`、`iana-ospf-functional-cap-bits`。

支持：2026-09 OSPF LSLinkInfinity 不可达广告、Router Functional Capability 和 YANG；在区域全部支持条件下排除 SPF 链路。。

边界：0xffff 不能脱离区域能力条件硬判不可达；更新 5443/6987/8379/8770，不废止 RFC9129；广告中存在的链路并非一定可用于路径。。

关键对象/关联：`/ietf-routing:routing/control-plane-protocols/control-plane-protocol/ietf-ospf:ospf/areas/area/ietf-ospf-unreachable-links:unreachable-link-advertisement`；`/ietf-routing:routing/control-plane-protocols/control-plane-protocol/ietf-ospf:ospf/areas/area/interfaces/interface/ietf-ospf-unreachable-links:advertise-unreachable-link`；`iana-ospf-functional-cap-bits functional-capability identities`。

核对章节：3.1；3.2；3.3；3.4；3.5；4.2；6.1；6.3。研读深度：模型树、关键定义与适用范围精读；官方元数据和替代关系核对。

### RFC1195 Use of OSI IS-IS for routing in TCP/IP and dual environments

[官方原文](https://www.rfc-editor.org/rfc/rfc1195.html) · PROPOSED STANDARD · 类型：protocol

模块：无独立 YANG 模块（见文档类型与范围）。

支持：OSI IS-IS 在 IPv4/双协议环境的扩展。。

边界：IS-IS 基础协议来自 ISO10589，本 RFC 不是完整 ISO 协议替代；不是 YANG。。

核对章节：Abstract；Integrated IS-IS overview。研读深度：官方元数据、模型相关协议章节与替代/更新关系核对；未逐项穷举全部报文细节。

### RFC2080 RIPng for IPv6

[官方原文](https://www.rfc-editor.org/rfc/rfc2080.html) · PROPOSED STANDARD · 类型：protocol

模块：无独立 YANG 模块（见文档类型与范围）。

支持：IPv6 RIPng 路由信息与距离向量过程。。

边界：IPv6 link-local sender 和多上下文必须保留 scope；无 YANG。。

核对章节：2；3。研读深度：官方元数据、模型相关协议章节与替代/更新关系核对；未逐项穷举全部报文细节。

### RFC2328 OSPF Version 2

[官方原文](https://www.rfc-editor.org/rfc/rfc2328.html) · INTERNET STANDARD · 类型：protocol

模块：无独立 YANG 模块（见文档类型与范围）。

支持：区域、接口状态机、邻接、Router/Network LSA 与 SPF；接口输出 cost 可有方向差异。。

边界：broadcast transit network/virtual link/pseudonode 不应压为一根电缆；同前缀和相同 RID 不能代替当前邻接。。

核对章节：2；9；10；12.4.1；16。研读深度：官方元数据、模型相关协议章节与替代/更新关系核对；未逐项穷举全部报文细节。

### RFC2453 RIP Version 2

[官方原文](https://www.rfc-editor.org/rfc/rfc2453.html) · INTERNET STANDARD · 类型：protocol

模块：无独立 YANG 模块（见文档类型与范围）。

支持：RIPv2 路由信息、距离向量 metric 与收敛；替代 RFC1723。。

边界：hop metric 不证明物理链路或带宽，不能统一为 IGP 接口成本。。

核对章节：3；4。研读深度：官方元数据、模型相关协议章节与替代/更新关系核对；未逐项穷举全部报文细节。

### RFC3031 Multiprotocol Label Switching Architecture

[官方原文](https://www.rfc-editor.org/rfc/rfc3031.html) · PROPOSED STANDARD · 类型：architecture

模块：无独立 YANG 模块（见文档类型与范围）。

支持：FEC、label stack、LSR/LER、NHLFE 与 LSP 的 MPLS 架构。。

边界：标签与 LSP 通常具有方向和局部作用域，不是全局 IP owner 或物理连接身份。。

核对章节：2；3；4。研读深度：官方元数据、模型相关协议章节与替代/更新关系核对；未逐项穷举全部报文细节。

### RFC3209 RSVP-TE: Extensions to RSVP for LSP Tunnels

[官方原文](https://www.rfc-editor.org/rfc/rfc3209.html) · PROPOSED STANDARD · 类型：protocol

模块：无独立 YANG 模块（见文档类型与范围）。

支持：RSVP-TE 建立 MPLS LSP 的 signaling、explicit route、label request、session/traffic 参数。。

边界：非 YANG；已存在的信令协议不表示 RSVP-TE YANG 已正式发布。。

核对章节：§2–4。研读深度：abstract, document status and selected architecture/protocol sections; not exhaustive protocol review。

### RFC3376 Internet Group Management Protocol, Version 3

[官方原文](https://www.rfc-editor.org/rfc/rfc3376.html) · PROPOSED STANDARD · 类型：protocol

模块：无独立 YANG 模块（见文档类型与范围）。

支持：旧 IGMPv3 source-filter membership 规范。。

边界：2025 被 RFC9776 废止；无 YANG。。

后继文档：RFC9776。

核对章节：3；4；6。研读深度：官方元数据、模型相关协议章节与替代/更新关系核对；未逐项穷举全部报文细节。

### RFC3768 Virtual Router Redundancy Protocol (VRRP)

[官方原文](https://www.rfc-editor.org/rfc/rfc3768.html) · DRAFT STANDARD · 类型：protocol

模块：无独立 YANG 模块（见文档类型与范围）。

支持：旧 VRRPv2 协议。。

边界：已被 RFC5798 废止；仅作为 RFC8347 v2 数据语义的历史参考。。

后继文档：RFC5798。

核对章节：5；6。研读深度：官方元数据、模型相关协议章节与替代/更新关系核对；未逐项穷举全部报文细节。

### RFC3810 Multicast Listener Discovery Version 2 (MLDv2) for IPv6

[官方原文](https://www.rfc-editor.org/rfc/rfc3810.html) · PROPOSED STANDARD · 类型：protocol

模块：无独立 YANG 模块（见文档类型与范围）。

支持：旧 IPv6 MLDv2 membership 规范。。

边界：2025 被 RFC9777 废止；无 YANG。。

后继文档：RFC9777。

核对章节：3；4；5。研读深度：官方元数据、模型相关协议章节与替代/更新关系核对；未逐项穷举全部报文细节。

### RFC4271 A Border Gateway Protocol 4 (BGP-4)

[官方原文](https://www.rfc-editor.org/rfc/rfc4271.html) · DRAFT STANDARD · 类型：protocol

模块：无独立 YANG 模块（见文档类型与范围）。

支持：BGP OPEN、邻居状态机、UPDATE 路径属性、选择与传播。。

边界：BGP session 可跨多跳，local/remote ASN 不构成电缆证据；本 RFC 无 YANG 模块。。

核对章节：1；4；5；6；9。研读深度：官方元数据、模型相关协议章节与替代/更新关系核对；未逐项穷举全部报文细节。

### RFC4601 Protocol Independent Multicast - Sparse Mode (PIM-SM): Protocol Specification (Revised)

[官方原文](https://www.rfc-editor.org/rfc/rfc4601.html) · PROPOSED STANDARD · 类型：protocol

模块：无独立 YANG 模块（见文档类型与范围）。

支持：旧 PIM-SM 规范。。

边界：已被 RFC7761 废止。。

后继文档：RFC7761。

核对章节：3；4。研读深度：官方元数据、模型相关协议章节与替代/更新关系核对；未逐项穷举全部报文细节。

### RFC4760 Multiprotocol Extensions for BGP-4

[官方原文](https://www.rfc-editor.org/rfc/rfc4760.html) · DRAFT STANDARD · 类型：protocol

模块：无独立 YANG 模块（见文档类型与范围）。

支持：MP_REACH/UNREACH 与 AFI/SAFI，支持 IPv4/IPv6/VPN 等不同 NLRI。。

边界：AFI/SAFI 不能混表；NLRI 或 next-hop 不是物理端口，route reflector 也不是转发下一跳。。

核对章节：3；4；5。研读深度：官方元数据、模型相关协议章节与替代/更新关系核对；未逐项穷举全部报文细节。

### RFC5036 LDP Specification

[官方原文](https://www.rfc-editor.org/rfc/rfc5036.html) · DRAFT STANDARD · 类型：protocol

模块：无独立 YANG 模块（见文档类型与范围）。

支持：LDP discovery、session、FEC/label 分发，替代 RFC3036。。

边界：targeted session 可跨多跳；标签分发不保证完整业务 forwarding path。。

核对章节：2；3。研读深度：官方元数据、模型相关协议章节与替代/更新关系核对；未逐项穷举全部报文细节。

### RFC5308 Routing IPv6 with IS-IS

[官方原文](https://www.rfc-editor.org/rfc/rfc5308.html) · PROPOSED STANDARD · 类型：protocol

模块：无独立 YANG 模块（见文档类型与范围）。

支持：IS-IS IPv6 reachability/接口地址信息扩展。。

边界：IPv6 reachability 不自动证明 L2 物理邻接或唯一 chassis。。

核对章节：2；3。研读深度：官方元数据、模型相关协议章节与替代/更新关系核对；未逐项穷举全部报文细节。

### RFC5340 OSPF for IPv6

[官方原文](https://www.rfc-editor.org/rfc/rfc5340.html) · PROPOSED STANDARD · 类型：protocol

模块：无独立 YANG 模块（见文档类型与范围）。

支持：OSPFv3 的 IPv6 链路、instance、邻接与 LSA 作用域；废止 RFC2740。。

边界：RID 仍是 32 位标识，不等于接口 IPv6；link-local 的接口范围不能丢。。

核对章节：2；3；4。研读深度：官方元数据、模型相关协议章节与替代/更新关系核对；未逐项穷举全部报文细节。

### RFC5798 Virtual Router Redundancy Protocol (VRRP) Version 3 for IPv4 and IPv6

[官方原文](https://www.rfc-editor.org/rfc/rfc5798.html) · PROPOSED STANDARD · 类型：protocol

模块：无独立 YANG 模块（见文档类型与范围）。

支持：VRRPv3 IPv4/IPv6 旧规范，替代 RFC3768。。

边界：已被 RFC9568 废止；其被 RFC8347 引用不意味着当前协议首选仍是此 RFC。。

后继文档：RFC9568。

核对章节：5；6；7。研读深度：官方元数据、模型相关协议章节与替代/更新关系核对；未逐项穷举全部报文细节。

### RFC5880 Bidirectional Forwarding Detection (BFD)

[官方原文](https://www.rfc-editor.org/rfc/rfc5880.html) · PROPOSED STANDARD · 类型：protocol

模块：无独立 YANG 模块（见文档类型与范围）。

支持：BFD session state、discriminator、计时器和认证，监测 forwarding path。。

边界：路径可含多 hop/虚拟隧道；BFD Up 不保证每条 ECMP 流或所有包长业务成功。。

核对章节：2；4；6。研读深度：官方元数据、模型相关协议章节与替代/更新关系核对；未逐项穷举全部报文细节。

### RFC5881 Bidirectional Forwarding Detection (BFD) for IPv4 and IPv6 (Single Hop)

[官方原文](https://www.rfc-editor.org/rfc/rfc5881.html) · PROPOSED STANDARD · 类型：protocol

模块：无独立 YANG 模块（见文档类型与范围）。

支持：单跳 IPv4/IPv6 BFD 的地址/接口和 TTL 要求。。

边界：单跳 IP 邻接可跨共享链路；不是两 chassis 间唯一电缆证明。。

核对章节：3；4；5。研读深度：官方元数据、模型相关协议章节与替代/更新关系核对；未逐项穷举全部报文细节。

### RFC5883 Bidirectional Forwarding Detection (BFD) for Multihop Paths

[官方原文](https://www.rfc-editor.org/rfc/rfc5883.html) · PROPOSED STANDARD · 类型：protocol

模块：无独立 YANG 模块（见文档类型与范围）。

支持：多跳路径 BFD，端点 session 与路径选择。。

边界：明确是多跳，不应生成一条物理直连边。。

核对章节：3；4。研读深度：官方元数据、模型相关协议章节与替代/更新关系核对；未逐项穷举全部报文细节。

### RFC6286 Autonomous-System-Wide Unique BGP Identifier for BGP-4

[官方原文](https://www.rfc-editor.org/rfc/rfc6286.html) · PROPOSED STANDARD · 类型：protocol

模块：无独立 YANG 模块（见文档类型与范围）。

支持：BGP Identifier 是非零 32 位值，在 AS 内应唯一；更新 RFC4271 的地址形式约束。。

边界：同 ASN+RID 可标识一个协议 speaker 候选，无法证明同 physical chassis；多个逻辑进程/AS 仍需上下文和设备证据。。

核对章节：2；3。研读深度：官方元数据、模型相关协议章节与替代/更新关系核对；未逐项穷举全部报文细节。

### RFC6793 BGP Support for Four-Octet Autonomous System (AS) Number Space

[官方原文](https://www.rfc-editor.org/rfc/rfc6793.html) · PROPOSED STANDARD · 类型：protocol

模块：无独立 YANG 模块（见文档类型与范围）。

支持：四字节 ASN 能力、AS4_PATH/AS4_AGGREGATOR 和过渡语义。。

边界：二字节 BGP4-MIB remoteAS 等旧字段不能无损代表所有四字节 ASN；不能把 AS_TRANS 当实际 ASN。。

核对章节：3；4。研读深度：官方元数据、模型相关协议章节与替代/更新关系核对；未逐项穷举全部报文细节。

### RFC7752 North-Bound Distribution of Link-State and Traffic Engineering (TE) Information Using BGP

[官方原文](https://www.rfc-editor.org/rfc/rfc7752.html) · PROPOSED STANDARD · 类型：protocol

模块：无独立 YANG 模块（见文档类型与范围）。

支持：旧 BGP-LS link-state/TE 分发规范。。

边界：已被 RFC9552 废止，不应作为当前首选；没有 YANG 模块。。

后继文档：RFC9552。

核对章节：3。研读深度：官方元数据、模型相关协议章节与替代/更新关系核对；未逐项穷举全部报文细节。

### RFC7761 Protocol Independent Multicast - Sparse Mode (PIM-SM): Protocol Specification (Revised)

[官方原文](https://www.rfc-editor.org/rfc/rfc7761.html) · INTERNET STANDARD · 类型：protocol

模块：无独立 YANG 模块（见文档类型与范围）。

支持：PIM-SM 的 RP、共享树/SPT、RPF、join/prune 和邻居。。

边界：组播树是流/源/组上下文，不是全体线缆图；单播邻居与组播分支不能互相替代。。

核对章节：3；4。研读深度：官方元数据、模型相关协议章节与替代/更新关系核对；未逐项穷举全部报文细节。

### RFC8022 A YANG Data Model for Routing Management

[官方原文](https://www.rfc-editor.org/rfc/rfc8022.html) · PROPOSED STANDARD · 类型：model

模块：`ietf-routing`、`ietf-ipv4-unicast-routing`、`ietf-ipv6-unicast-routing`。

支持：早期路由配置/状态、静态路由和 RIB；IPv4/IPv6 unicast 与 RA。。

边界：已被 RFC8349 废止；附录 example-rip 不是正式 RIP 标准模型。。

关键对象/关联：`/ietf-routing:routing`；`/ietf-routing:routing-state`。

后继文档：RFC8349。

核对章节：4；5；7；8；9。研读深度：历史替代链、结构差异与官方元数据核对。

### RFC8177 YANG Data Model for Key Chains

[官方原文](https://www.rfc-editor.org/rfc/rfc8177.html) · PROPOSED STANDARD · 类型：model

模块：`ietf-key-chain`。

支持：认证 key-chain、算法、key lifetime，供 OSPF/IS-IS/BFD 等引用。。

边界：密钥与机密不得复制到拓扑事实；配置引用只表示认证配置，不证明邻接已成立。。

关键对象/关联：`/ietf-key-chain:key-chains/key-chain`。

核对章节：3；4；5。研读深度：模型树、关键定义与适用范围精读；官方元数据和替代关系核对。

### RFC8294 Common YANG Data Types for the Routing Area

[官方原文](https://www.rfc-editor.org/rfc/rfc8294.html) · PROPOSED STANDARD · 类型：model

模块：`ietf-routing-types`、`iana-routing-types`。

支持：通用路由类型、时间/标签等 groupings 与 IANA address-family 身份。。

边界：类型模块不携带观测实例；身份相同只表示同一类别，不能合并设备。。

关键对象/关联：`ietf-routing-types:router-id`；`ietf-routing-types:mpls-label`；`iana-routing-types:address-family`。

核对章节：2；3；4。研读深度：模型树、关键定义与适用范围精读；官方元数据和替代关系核对。

### RFC8347 A YANG Data Model for the Virtual Router Redundancy Protocol (VRRP)

[官方原文](https://www.rfc-editor.org/rfc/rfc8347.html) · PROPOSED STANDARD · 类型：model

模块：`ietf-vrrp`。

支持：IPv4/IPv6 接口内 VRRP 实例、mandatory version、VRID、VIP、优先级、master/state/owner 与跟踪。。

边界：VIP/虚拟 MAC 是冗余组身份，不是唯一主机；无真实版本不能硬填 v2/v3。RFC9568 替代协议 RFC5798，没有自动废止此 YANG。。

关键对象/关联：`/ietf-interfaces:interfaces/interface/ietf-ip:ipv4/ietf-vrrp:vrrp/vrrp-instance[vrid]`；`/ietf-interfaces:interfaces/interface/ietf-ip:ipv6/ietf-vrrp:vrrp/vrrp-instance[vrid]`；`ietf-vrrp:vrrp-version identity`。

核对章节：2.1；2.2；2.3；2.4；3；4。研读深度：模型树、关键定义与适用范围精读；官方元数据和替代关系核对。

### RFC8349 A YANG Data Model for Routing Management (NMDA Version)

[官方原文](https://www.rfc-editor.org/rfc/rfc8349.html) · PROPOSED STANDARD · 类型：model

模块：`ietf-routing`、`ietf-ipv4-unicast-routing`、`ietf-ipv6-unicast-routing`。

支持：路由、RIB、AF、source-protocol、preference、simple/ECMP/special next-hop、active-route action；控制协议可按 type/name 扩展。。

边界：RIB 是路由表模型，并非实际转发保证；不能猜 rib 名称、VRF、AF 或丢弃动作，出接口引用必须闭合；同前缀不证明物理邻接。。

关键对象/关联：`/ietf-routing:routing/ribs/rib[name]/routes/route`；`/ietf-routing:routing/control-plane-protocols/control-plane-protocol[type name]`；`ietf-routing:direct`；`ietf-routing:static`。

核对章节：4.1；5.1；5.2；5.3；5.4；6；7；8；9。研读深度：模型树、关键定义与适用范围精读；官方元数据和替代关系核对。

### RFC8431 A YANG Data Model for the Routing Information Base (RIB)

[官方原文](https://www.rfc-editor.org/rfc/rfc8431.html) · PROPOSED STANDARD · 类型：model

模块：`ietf-i2rs-rib`。

支持：I2RS RIB 能力、实例/RIB、IP/MPLS/MAC match、复杂 next-hop 与编程 RPC/通知。。

边界：不同于 RFC8349 核心路由树，不是替代该 RFC；RIB 编程意图不能当作已验证 FIB 或物理 topology。。

关键对象/关联：`/ietf-i2rs-rib:routing-instance/rib-list/route-list`；`ietf-i2rs-rib:label-push/label-pop/label-swap identities`。

核对章节：2.1；2.2；2.3；2.4；2.5；2.6；3。研读深度：模型树、关键定义与适用范围精读；官方元数据和替代关系核对。

### RFC8652 A YANG Data Model for the Internet Group Management Protocol (IGMP) and Multicast Listener Discovery (MLD)

[官方原文](https://www.rfc-editor.org/rfc/rfc8652.html) · PROPOSED STANDARD · 类型：model

模块：`ietf-igmp-mld`。

支持：IGMP/MLD 版本、接口、querier、group/source/filter 状态与统计，客户端/路由器 membership 管理。。

边界：组成员状态不是物理端点清单；不包含全部厂商全局/接口扩展。2025 协议替代不自动改变 2019 模块 revision。。

关键对象/关联：`/ietf-routing:routing/control-plane-protocols/control-plane-protocol/ietf-igmp-mld:igmp/interfaces/interface`；`/ietf-routing:routing/control-plane-protocols/control-plane-protocol/ietf-igmp-mld:mld/interfaces/interface`。

核对章节：2.1；2.2；2.3；3；4。研读深度：模型树、关键定义与适用范围精读；官方元数据和替代关系核对。

### RFC8660 Segment Routing with the MPLS Data Plane

[官方原文](https://www.rfc-editor.org/rfc/rfc8660.html) · PROPOSED STANDARD · 类型：protocol

模块：无独立 YANG 模块（见文档类型与范围）。

支持：SR-MPLS label stack 和 segment 行为。。

边界：配置 label stack 不证明已下发或实际走过某路径；无 YANG 模块。。

核对章节：2；3。研读深度：官方元数据、模型相关协议章节与替代/更新关系核对；未逐项穷举全部报文细节。

### RFC8695 A YANG Data Model for the Routing Information Protocol (RIP)

[官方原文](https://www.rfc-editor.org/rfc/rfc8695.html) · PROPOSED STANDARD · 类型：model

模块：`ietf-rip`。

支持：RIPv2/RIPng 配置、接口、neighbor/route、metric、重分布、计时器与统计。。

边界：跳数 metric 不是接口速度或物理邻接；不能把 README 中 example-rip 当正式模型。。

关键对象/关联：`/ietf-routing:routing/control-plane-protocols/control-plane-protocol/ietf-rip:rip`；`ietf-rip:ripv2/ietf-rip:ripng identities`。

核对章节：2.1；2.2；2.3；2.4；3；4。研读深度：模型树、关键定义与适用范围精读；官方元数据和替代关系核对。

### RFC8754 IPv6 Segment Routing Header (SRH)

[官方原文](https://www.rfc-editor.org/rfc/rfc8754.html) · PROPOSED STANDARD · 类型：protocol

模块：无独立 YANG 模块（见文档类型与范围）。

支持：IPv6 Segment Routing Header 编码与处理。。

边界：SRH 是报文机制，不是 device/topology 数据模型；RFC9800 后续更新须另核压缩场景。。

核对章节：2；4。研读深度：官方元数据、模型相关协议章节与替代/更新关系核对；未逐项穷举全部报文细节。

### RFC8916 A YANG Data Model for the Multicast Source Discovery Protocol (MSDP)

[官方原文](https://www.rfc-editor.org/rfc/rfc8916.html) · PROPOSED STANDARD · 类型：model

模块：`ietf-msdp`。

支持：MSDP peer、SA cache、RP、过滤和 session 状态。。

边界：TCP peer 和共享源信息不证明两设备物理相邻；不应用 IPv4 MSDP 状态推导 IPv6 邻接。。

关键对象/关联：`/ietf-routing:routing/control-plane-protocols/control-plane-protocol/ietf-msdp:msdp/peers/peer[address]`；`/ietf-routing:routing/control-plane-protocols/control-plane-protocol/ietf-msdp:msdp/sa-cache`。

核对章节：2；3.1；3.2；4。研读深度：模型树、关键定义与适用范围精读；官方元数据和替代关系核对。

### RFC8960 A YANG Data Model for MPLS Base

[官方原文](https://www.rfc-editor.org/rfc/rfc8960.html) · PROPOSED STANDARD · 类型：device-yang

模块：`ietf-mpls`。

支持：MPLS 设备底座：接口 MPLS 开关、标签范围及标签交换的 RIB/next-hop 扩展。。

边界：不是全网 MPLS 图；静态 LSP、LDP、RSVP 与 TE 等通过独立模型扩展，不由本模块全部提供。。

关键对象/关联：`/ietf-routing:routing/ietf-mpls:mpls/mpls-label-blocks`；`/ietf-routing:routing/ietf-mpls:mpls/interfaces/interface`；`/ietf-routing:routing/ribs/rib/routes/route/next-hop`；`/ietf-routing:routing/ietf-mpls:mpls`；`/ietf-routing:routing/ribs/rib/routes/route`。

核对章节：§2.1–2.5。研读深度：abstract, model tree, module import/augment and selected semantic sections; not compiled。

### RFC9067 A YANG Data Model for Routing Policy

[官方原文](https://www.rfc-editor.org/rfc/rfc9067.html) · PROPOSED STANDARD · 类型：model

模块：`ietf-routing-policy`。

支持：路由前缀/邻居/tag 集、条件、动作、子策略、评价顺序和应用引用；供 BGP 等扩展。。

边界：不代表路由已被学习/安装，不表达所有厂商私有 route-map 行为；必须结合实际运行 RIB。。

关键对象/关联：`/ietf-routing-policy:routing-policy/defined-sets`；`/ietf-routing-policy:routing-policy/policy-definitions`。

核对章节：3；4；5；6；7。研读深度：模型树、关键定义与适用范围精读；官方元数据和替代关系核对。

### RFC9070 YANG Data Model for MPLS LDP

[官方原文](https://www.rfc-editor.org/rfc/rfc9070.html) · PROPOSED STANDARD · 类型：device-yang

模块：`ietf-mpls-ldp`、`ietf-mpls-ldp-extended`。

支持：基础/扩展 LDP：发现接口与 targeted discovery、peer/session、地址族与标签绑定、配置/状态/RPC/通知。。

边界：基于 routing control-plane-protocol，平台级标签空间、VRF/NI 作用域需明确；不包含完整 mLDP、多拓扑 LDP、OAM 或所有上层应用。peer/targeted session 不必等同直接物理线缆。。

关键对象/关联：`/ietf-routing:routing/control-plane-protocols/control-plane-protocol/ietf-mpls-ldp:mpls-ldp/discovery`；`/ietf-routing:routing/control-plane-protocols/control-plane-protocol/ietf-mpls-ldp:mpls-ldp/peers/peer`；`/ietf-routing:routing/control-plane-protocols/control-plane-protocol/ietf-mpls-ldp:mpls-ldp/bindings`；`/ietf-routing:routing/control-plane-protocols/control-plane-protocol/ietf-mpls-ldp:mpls-ldp`。

核对章节：§3–8；§9。研读深度：abstract, model tree, module import/augment and selected semantic sections; not compiled。

### RFC9127 YANG Data Model for Bidirectional Forwarding Detection (BFD)

[官方原文](https://www.rfc-editor.org/rfc/rfc9127.html) · PROPOSED STANDARD · 类型：model

模块：`iana-bfd-types`、`ietf-bfd-types`、`ietf-bfd`、`ietf-bfd-ip-sh`、`ietf-bfd-ip-mh`、`ietf-bfd-lag`、`ietf-bfd-mpls`。

支持：BFD single-hop/multihop/MPLS/LAG 基础模型与 IANA 类型。。

边界：被 RFC9314 更新而非废止；旧 2021 与新 2022 revision 不应混作同一冻结模块。。

关键对象/关联：`/ietf-routing:routing/control-plane-protocols/control-plane-protocol/ietf-bfd:bfd`；`iana-bfd-types diagnostic/auth-type`。

核对章节：2；2.11；2.12；2.13；2.14；2.15；2.16。研读深度：历史更新链、模型键和官方元数据核对。

### RFC9128 YANG Data Model for Protocol Independent Multicast (PIM)

[官方原文](https://www.rfc-editor.org/rfc/rfc9128.html) · PROPOSED STANDARD · 类型：model

模块：`ietf-pim-base`、`ietf-pim-rp`、`ietf-pim-sm`、`ietf-pim-dm`、`ietf-pim-bidir`。

支持：PIM base、RP、SM/DM/bidir 五模块；邻居、DR、RP/BSR、(S,G)/(*,G)、RPF 与组播接口。。

边界：PIM 的 augment 在 control-plane-protocols 容器，不是通用协议 list entry；RPF/树和单播物理图不同，状态不能跨 NI/AF 混用。。

关键对象/关联：`/ietf-routing:routing/control-plane-protocols/ietf-pim-base:pim`；`ietf-pim-base:pim-sm/pim-dm/pim-bidir identities`。

核对章节：2；3；4。研读深度：模型树、关键定义与适用范围精读；官方元数据和替代关系核对。

### RFC9129 YANG Data Model for the OSPF Protocol

[官方原文](https://www.rfc-editor.org/rfc/rfc9129.html) · PROPOSED STANDARD · 类型：model

模块：`ietf-ospf`。

支持：OSPFv2/v3 实例、AF、RID、区域、接口/cost、邻居状态、LSDB、协议路由与统计；可构建协议邻接/链路状态视图。。

边界：邻接含 broadcast/NBMA、virtual/sham link，不是全体物理电缆；RID 不保证 chassis 身份；cost 为接口成本，不是可随意对应 RFC8346 metric1/2 的端点方向。。

关键对象/关联：`/ietf-routing:routing/control-plane-protocols/control-plane-protocol/ietf-ospf:ospf/areas/area[area-id]/interfaces/interface[name]`；`ietf-ospf:ospfv2/ospfv3`；`router-id/explicit-router-id, neighbors, database, cost`。

核对章节：2.1；2.3；2.5；2.6；2.7；2.8；2.9；3。研读深度：模型树、关键定义与适用范围精读；官方元数据和替代关系核对。

### RFC9130 YANG Data Model for the IS-IS Protocol

[官方原文](https://www.rfc-editor.org/rfc/rfc9130.html) · PROPOSED STANDARD · 类型：model

模块：`ietf-isis`。

支持：IS-IS level、NET/System-ID、multi-topology、接口/邻接、metric、LSDB、FRR 与路由扩展。。

边界：系统 ID、pseudonode 和多 level/topology 必须保留作用域；IS-IS 邻接/LSDB 不等于物理 chassis/线缆清单。。

关键对象/关联：`/ietf-routing:routing/control-plane-protocols/control-plane-protocol/ietf-isis:isis/interfaces/interface`；`/ietf-routing:routing/control-plane-protocols/control-plane-protocol/ietf-isis:isis/database/levels/lsp`；`ietf-isis:isis identity`。

核对章节：2.1；2.2；2.3；2.4；2.9；5；6。研读深度：模型树、关键定义与适用范围精读；官方元数据和替代关系核对。

### RFC9166 A YANG Data Model for Internet Group Management Protocol (IGMP) and Multicast Listener Discovery (MLD) Snooping

[官方原文](https://www.rfc-editor.org/rfc/rfc9166.html) · PROPOSED STANDARD · 类型：model

模块：`ietf-igmp-mld-snooping`。

支持：IGMP/MLD snooping 组/源/端口、router-port、实例，并可绑定 IEEE802.1Q bridge/VLAN。。

边界：L2 snooping 与 L3 membership 是不同模型；该模型不要求 RFC8652；同 bridge-port 的不同 VLAN 不能被当成同一广播域。。

关键对象/关联：`/ietf-routing:routing/control-plane-protocols/control-plane-protocol/ietf-igmp-mld-snooping:igmp-snooping-instance`；`/ieee802-dot1q-bridge:bridges/bridge/component/bridge-vlan/vlan`。

核对章节：2；3.1；3.2；3.3；4。研读深度：模型树、关键定义与适用范围精读；官方元数据和替代关系核对。

### RFC9194 A YANG Module for IS-IS Reverse Metric

[官方原文](https://www.rfc-editor.org/rfc/rfc9194.html) · PROPOSED STANDARD · 类型：model

模块：`ietf-isis-reverse-metric`。

支持：IS-IS reverse metric 的收/发控制、level 范围与邻接状态扩展。。

边界：远端请求 metric 与本地接口成本不是一个字段；存在反向度量更说明不能盲设对称边权。。

关键对象/关联：`/ietf-routing:routing/control-plane-protocols/control-plane-protocol/ietf-isis:isis/interfaces/interface/ietf-isis-reverse-metric:reverse-metric`。

核对章节：2.1；2.2。研读深度：模型树、关键定义与适用范围精读；官方元数据和替代关系核对。

### RFC9398 A YANG Data Model for Internet Group Management Protocol (IGMP) and Multicast Listener Discovery (MLD) Proxy Devices

[官方原文](https://www.rfc-editor.org/rfc/rfc9398.html) · PROPOSED STANDARD · 类型：model

模块：`ietf-igmp-mld-proxy`。

支持：IGMP/MLD proxy upstream/downstream 接口、membership 和 source-filter 状态。。

边界：代理使 membership 汇聚；不能将 proxy group 行直接转换为唯一终端直连关系。。

关键对象/关联：`/ietf-routing:routing/control-plane-protocols/control-plane-protocol/ietf-igmp-mld-proxy:igmp-proxy/interfaces/interface`；`/ietf-routing:routing/control-plane-protocols/control-plane-protocol/ietf-igmp-mld-proxy:mld-proxy/interfaces/interface`。

核对章节：2；3；4。研读深度：模型树、关键定义与适用范围精读；官方元数据和替代关系核对。

### RFC9403 A YANG Data Model for RIB Extensions

[官方原文](https://www.rfc-editor.org/rfc/rfc9403.html) · PROPOSED STANDARD · 类型：model

模块：`ietf-rib-extension`。

支持：扩展 RFC8349 RIB 与 IPv4/IPv6 static next-hop：metric、tag、application-tag、next-hop preference 和 repair-path。。

边界：不是 RFC8431 的 ietf-i2rs-rib；未采 repair-path 或 metric 不得假填；行政 preference 与协议 metric 是不同比较层。。

关键对象/关联：`/ietf-routing:routing/ribs/rib/routes/route/ietf-rib-extension:metric`；`/ietf-routing:routing/ribs/rib/routes/route/next-hop repair-path augmentation`；`/ietf-routing:routing/control-plane-protocols/control-plane-protocol/static-routes next-hop preference/tag augmentations`。

核对章节：3.1；3.2；4；5。研读深度：模型树、关键定义与适用范围精读；官方元数据和替代关系核对。

### RFC9468 Unsolicited Bidirectional Forwarding Detection (BFD) for Sessionless Applications

[官方原文](https://www.rfc-editor.org/rfc/rfc9468.html) · PROPOSED STANDARD · 类型：model

模块：`ietf-bfd-unsolicited`。

支持：无预配置 peer 的 unsolicited BFD 参数和接口开关，用于 sessionless 应用。。

边界：接收请求和初始化不等于已稳定会话；端点活性不能证明物理直连。。

关键对象/关联：`/ietf-routing:routing/control-plane-protocols/control-plane-protocol/ietf-bfd:bfd/ietf-bfd-ip-sh:ip-sh/ietf-bfd-unsolicited:unsolicited`。

核对章节：2；3；4.1；4.2。研读深度：模型树、关键定义与适用范围精读；官方元数据和替代关系核对。

### RFC9568 Virtual Router Redundancy Protocol (VRRP) Version 3 for IPv4 and IPv6

[官方原文](https://www.rfc-editor.org/rfc/rfc9568.html) · PROPOSED STANDARD · 类型：protocol

模块：无独立 YANG 模块（见文档类型与范围）。

支持：2024-04 当前 VRRPv3 IPv4/IPv6 协议，替代 RFC5798。。

边界：冗余组/VIP 不是一台物理设备；协议替代不自动新增 YANG revision。。

核对章节：5；6；7。研读深度：官方元数据、模型相关协议章节与替代/更新关系核对；未逐项穷举全部报文细节。

### RFC9587 YANG Data Model for OSPFv3 Extended Link State Advertisements (LSAs)

[官方原文](https://www.rfc-editor.org/rfc/rfc9587.html) · PROPOSED STANDARD · 类型：model

模块：`ietf-ospfv3-extended-lsa`。

支持：扩充 OSPFv3 Extended LSA 配置和 e-link/e-router/e-prefix 等 LSDB 内容。。

边界：需要采集对应 LSA 与协议版本；基础 OSPF summary 不能虚构这些叶。。

关键对象/关联：`/ietf-routing:routing/control-plane-protocols/control-plane-protocol/ietf-ospf:ospf/extended-lsa-support`；`OSPFv3 LSDB body augmentations`。

核对章节：2；3；4。研读深度：模型树、关键定义与适用范围精读；官方元数据和替代关系核对。

### RFC9647 A YANG Data Model for Babel

[官方原文](https://www.rfc-editor.org/rfc/rfc9647.html) · PROPOSED STANDARD · 类型：model

模块：`ietf-babel`。

支持：2024-10 Babel 实例、router-id、接口、wired/wireless/tunnel metric 算法、邻居/路由和认证。。

边界：Babel router-id 是 binary 协议标识，不应强转 dotted-quad 或 chassis；其路由 metric 不等于物理速度。。

关键对象/关联：`/ietf-routing:routing/control-plane-protocols/control-plane-protocol/ietf-babel:babel`；`/ietf-routing:routing/control-plane-protocols/control-plane-protocol/ietf-babel:babel/interfaces[reference]`；`/ietf-routing:routing/control-plane-protocols/control-plane-protocol/ietf-babel:babel/routes[prefix]`。

核对章节：2.1；2.2；2.3。研读深度：模型树、关键定义与适用范围精读；官方元数据和替代关系核对。

### RFC9719 YANG Data Model for Routing in Fat Trees (RIFT)

[官方原文](https://www.rfc-editor.org/rfc/rfc9719.html) · PROPOSED STANDARD · 类型：model

模块：`ietf-rift`。

支持：2025-04 RIFT fabric/pod/system-id、level、接口、邻接、TIE、路由和通知。。

边界：RIFT level/fabric 属性属于协议拓扑；不是发现任意物理网络的通用替代。。

关键对象/关联：`/ietf-routing:routing/control-plane-protocols/control-plane-protocol/ietf-rift:rift[name]/global`；`/ietf-routing:routing/control-plane-protocols/control-plane-protocol/ietf-rift:rift[name]/interfaces`。

核对章节：2.1；2.3；2.4；2.5；2.6；3。研读深度：模型树、关键定义与适用范围精读；官方元数据和替代关系核对。

### RFC9747 Unaffiliated Bidirectional Forwarding Detection (BFD) Echo

[官方原文](https://www.rfc-editor.org/rfc/rfc9747.html) · PROPOSED STANDARD · 类型：protocol

模块：无独立 YANG 模块（见文档类型与范围）。

支持：2025-03 unaffiliated echo 探测机制，更新 RFC5880。。

边界：非对称实现可只由一端处理，不代表远端有同样 BFD 控制会话；无新 YANG 模块。。

核对章节：3；4；5。研读深度：官方元数据、模型相关协议章节与替代/更新关系核对；未逐项穷举全部报文细节。

### RFC9764 Bidirectional Forwarding Detection (BFD) Encapsulated in Large Packets

[官方原文](https://www.rfc-editor.org/rfc/rfc9764.html) · PROPOSED STANDARD · 类型：model

模块：`ietf-bfd-large`。

支持：2025-04 BFD 大包与 MTU 故障探测，扩展多种 session 的 pdu-size。。

边界：只证明特定探测流和包长行为；不能以 BFD Up 保证所有 ECMP 路径、MTU、业务流可达。。

关键对象/关联：`/ietf-routing:routing/control-plane-protocols/control-plane-protocol/ietf-bfd:bfd/ietf-bfd-ip-sh:ip-sh/sessions/session/ietf-bfd-large:pdu-size`。

核对章节：3；4；5。研读深度：模型树、关键定义与适用范围精读；官方元数据和替代关系核对。

### RFC9776 Internet Group Management Protocol, Version 3

[官方原文](https://www.rfc-editor.org/rfc/rfc9776.html) · INTERNET STANDARD · 类型：protocol

模块：无独立 YANG 模块（见文档类型与范围）。

支持：2025-03 当前 IGMPv3 host/router membership 协议，Internet Standard，替代 RFC3376。。

边界：成员状态不是 endpoint 物理连接证明；没有直接修订 RFC8652 YANG。。

核对章节：2；3；4；5；6。研读深度：官方元数据、模型相关协议章节与替代/更新关系核对；未逐项穷举全部报文细节。

### RFC9777 Multicast Listener Discovery Version 2 (MLDv2) for IPv6

[官方原文](https://www.rfc-editor.org/rfc/rfc9777.html) · INTERNET STANDARD · 类型：protocol

模块：无独立 YANG 模块（见文档类型与范围）。

支持：2025-03 当前 IPv6 MLDv2，Internet Standard，替代 RFC3810。。

边界：scope、接口和 source-filter 必须保留；没有直接修订 RFC8652 YANG。。

核对章节：2；3；4；5；6；7。研读深度：官方元数据、模型相关协议章节与替代/更新关系核对；未逐项穷举全部报文细节。

### RFC9780 Bidirectional Forwarding Detection (BFD) for Multipoint Networks over Point-to-Multipoint MPLS Label Switched Paths (LSPs)

[官方原文](https://www.rfc-editor.org/rfc/rfc9780.html) · PROPOSED STANDARD · 类型：protocol

模块：无独立 YANG 模块（见文档类型与范围）。

支持：2025-05 P2MP MPLS LSP 的 multipoint BFD，更新 RFC8562。。

边界：一个 root/多 leaf 的活性视图不是双向物理拓扑；无新 YANG 模块。。

核对章节：3；4；5。研读深度：官方元数据、模型相关协议章节与替代/更新关系核对；未逐项穷举全部报文细节。

### RFC9825 Extensions to OSPF for Advertising Prefix Administrative Tags

[官方原文](https://www.rfc-editor.org/rfc/rfc9825.html) · PROPOSED STANDARD · 类型：model

模块：`ietf-ospf-admin-tags`。

支持：2025-07 OSPF 前缀 administrative tag 广告、配置与 LSDB/路由扩展，用于策略选择。。

边界：tag 是策略属性，不是 VLAN ID 或物理链路颜色；应与前缀、区域和实例一起关联。。

关键对象/关联：`/ietf-routing:routing/control-plane-protocols/control-plane-protocol/ietf-ospf:ospf/areas/area/interfaces/interface/ietf-ospf-admin-tags:local-prefix-admin-tags`。

核对章节：2；3；4；5；7。研读深度：模型树、关键定义与适用范围精读；官方元数据和替代关系核对。

### RFC9978 Bidirectional Forwarding Detection (BFD) Stability

[官方原文](https://www.rfc-editor.org/rfc/rfc9978.html) · EXPERIMENTAL · 类型：model

模块：`ietf-bfd-stability`。

支持：2026-06 Experimental：NULL auth/序列号用于丢包与乱序检测，带 stability feature 的 YANG 扩展。。

边界：已发布 RFC 但不是 Standards Track；研究性稳定性观测不是物理链路存在证明。。

关键对象/关联：`/ietf-routing:routing/control-plane-protocols/control-plane-protocol/ietf-bfd:bfd/ietf-bfd-ip-sh:ip-sh/sessions/session/ietf-bfd-stability:stability`。

核对章节：3；4；5；6；7。研读深度：模型树、关键定义与适用范围精读；官方元数据和替代关系核对。

### RFC9983 OSPFv2 Anycast Property Advertisement

[官方原文](https://www.rfc-editor.org/rfc/rfc9983.html) · PROPOSED STANDARD · 类型：model

模块：`ietf-ospf-anycast-flag`。

支持：2026-05 OSPFv2 anycast 前缀属性广告与 YANG 配置叶。。

边界：共享地址可有多个端点；anycast 不允许全局 IP owner 覆盖，也不证明唯一设备。。

关键对象/关联：`/ietf-routing:routing/control-plane-protocols/control-plane-protocol/ietf-ospf:ospf/areas/area/interfaces/interface/ietf-ospf-anycast-flag:anycast-flag`。

核对章节：2；3；4。研读深度：模型树、关键定义与适用范围精读；官方元数据和替代关系核对。

### RFC9985 Optimizing Bidirectional Forwarding Detection (BFD) Authentication

[官方原文](https://www.rfc-editor.org/rfc/rfc9985.html) · EXPERIMENTAL · 类型：model

模块：`ietf-bfd-opt-auth`。

支持：2026-06 Experimental：LCI/MCI 认证优化与 reauth-interval YANG。。

边界：认证扩展不是新 topology model，也不能推断客户端业务的转发成功。。

关键对象/关联：`/ietf-routing:routing/control-plane-protocols/control-plane-protocol/ietf-bfd:bfd/ietf-bfd-ip-sh:ip-sh/sessions/session/authentication/ietf-bfd-opt-auth:reauth-interval`。

核对章节：3；4；5；6；7；8。研读深度：模型树、关键定义与适用范围精读；官方元数据和替代关系核对。

### RFC9986 Meticulous Keyed ISAAC for Bidirectional Forwarding Detection (BFD) Optimized Authentication

[官方原文](https://www.rfc-editor.org/rfc/rfc9986.html) · EXPERIMENTAL · 类型：model

模块：`ietf-bfd-met-keyed-isaac`。

支持：2026-06 Experimental：Meticulous Keyed ISAAC 三种格式及认证身份 YANG。。

边界：类型模块为认证类别提供身份，不产生链路实例；此实验协议不应误记为 Proposed Standard。。

关键对象/关联：`ietf-bfd-met-keyed-isaac:optimized-md5-meticulous-keyed-isaac`；`ietf-bfd-met-keyed-isaac:optimized-sha1-meticulous-keyed-isaac`；`IANA BFD auth-type extensions`。

核对章节：2；4；13。研读深度：模型树、关键定义与适用范围精读；官方元数据和替代关系核对。

### draft-ietf-bier-bier-yang-10 YANG Data Model for BIER Protocol

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-bier-bier-yang-10) · 工作组草案；Expired · 类型：model

模块：`ietf-bier`。

支持：增补 ietf-routing 的 BIER 子域、封装参数、BIFT 和 BFR 邻居配置及协议通知。

边界：已过期，尚非 RFC；不是物理接线或通用多播拓扑发现模型；草案声明采用 NMDA；本次未编译模块或逐叶审查 tree/code 一致性。

关键对象/关联：`/routing/bier/sub-domain`；`/routing/bier/bift`。

核对章节：Abstract；2 Design of Data Model；3 Module Structure；4 Configuration；6 YANG Module。研读深度：摘要、模块声明、模型层次和 Datatracker 状态核对；未编译。

### draft-ietf-idr-bgp-model-21 YANG Model for Border Gateway Protocol (BGP-4)

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-idr-bgp-model-21) · INTERNET-DRAFT; Active; I-D Exists; In WG Last Call · 类型：model

模块：`ietf-bgp-types`、`ietf-bgp-community-types`、`ietf-bgp`、`ietf-bgp-policy`、`ietf-bgp-session-security`、`iana-bgp-notification`、`iana-bgp-capabilities`、`iana-bgp-afi-safi-types`、`iana-bgp-community-types`、`iana-bgp-rib-types`。

支持：global/neighbor/peer-group、ASN/RID、AFI-SAFI、session、capability、RIB、path attributes 和 policy；有主模块、辅助模块和 submodule。。

边界：仍是 WG Last Call 的 Internet-Draft，rfc=null；不能声称 IETF 已发布现代 BGP YANG。ASN+RID 的协议身份不能证明 chassis。。

关键对象/关联：`/ietf-routing:routing/control-plane-protocols/control-plane-protocol/ietf-bgp:bgp/global`；`/ietf-routing:routing/control-plane-protocols/control-plane-protocol/ietf-bgp:bgp/neighbors/neighbor`；`/ietf-routing:routing/control-plane-protocols/control-plane-protocol/ietf-bgp:bgp/rib/afi-safis/afi-safi`。

核对章节：2；5；6；7；Appendix C；Appendix D。研读深度：Datatracker 当前 document/state API、最新版全文与关键 schema 树核对；草案未纳入 RFC 标准冻结集合。

### draft-ietf-lisp-yang-25 LISP YANG Model

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-lisp-yang-25) · INTERNET-DRAFT; Active; I-D Exists; WG Document · 类型：model

模块：`ietf-lisp`、`ietf-lisp-itr`、`ietf-lisp-etr`、`ietf-lisp-mapserver`、`ietf-lisp-mapresolver`、`ietf-lisp-address-types`。

支持：LISP EID/RLOC 地址类型、locator-sets、ITR/ETR、Map-Server/Map-Resolver 的配置与运行状态。。

边界：最新 Draft-25（2026-09-03），Active WG Document，rfc=null；EID/RLOC 映射是 overlay 身份/定位分离，不证明 underlay 电缆。。

关键对象/关联：`/ietf-routing:routing/control-plane-protocols/control-plane-protocol/ietf-lisp:lisp/locator-sets`；`ITR/ETR/Map-Server/Map-Resolver augmentations`。

核对章节：3；4；5。研读深度：Datatracker 当前 document/state API、最新版全文与关键 schema 树核对；草案未纳入 RFC 标准冻结集合。

### draft-ietf-lsr-isis-flex-algo-yang-21 A YANG Data Model for IS-IS Application-Specific Link Attributes and Flexible Algorithm

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-lsr-isis-flex-algo-yang-21) · INTERNET-DRAFT; Active; RFC-Ed-Ack; Expert Reviews OK; Version Changed - Review Needed; RFC Ed Queue; In Progress; Submitted to IESG for Publication · 类型：model

模块：`ietf-isis-link-attr`、`ietf-isis-flex-algo`。

支持：IS-IS application-specific link attribute 和 flexible algorithm 配置/LSDB。。

边界：Draft-21 在 RFC Editor Queue，尚 rfc=null；IANA 预注册 RFC-ietf-* 不构成已发布编号。。

关键对象/关联：`/ietf-routing:routing/control-plane-protocols/control-plane-protocol/ietf-isis:isis`。

核对章节：2；3；4。研读深度：Datatracker 当前 document/state API、最新版全文与关键 schema 树核对；草案未纳入 RFC 标准冻结集合。

### draft-ietf-lsr-ospf-flex-algo-yang-16 A YANG Data Model for OSPF Application-Specific Link Attributes and Flexible Algorithm

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-lsr-ospf-flex-algo-yang-16) · INTERNET-DRAFT; Active; RFC-Ed-Ack; Expert Reviews OK; Version Changed - Review Needed; RFC Ed Queue; In Progress; Submitted to IESG for Publication · 类型：model

模块：`ietf-ospf-link-attr`、`ietf-ospf-flex-algo`、`iana-igp-algo-types`、`iana-igp-metric-types`、`iana-igp-link-attr-apps`。

支持：OSPF application-specific link attribute、flexible algorithm 及 IANA algorithm/metric/app 类型。。

边界：Draft-16 在 RFC Editor Queue，尚 rfc=null；同图内不同 algorithm topology 不能混为通用无条件邻接。。

关键对象/关联：`/ietf-routing:routing/control-plane-protocols/control-plane-protocol/ietf-ospf:ospf`。

核对章节：2；3；4。研读深度：Datatracker 当前 document/state API、最新版全文与关键 schema 树核对；草案未纳入 RFC 标准冻结集合。

### draft-ietf-lsvr-bgp-ls-yang-04 A YANG Model for BGP-LS, BGP-LS-VPN, and BGP-LS-SPF

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-lsvr-bgp-ls-yang-04) · INTERNET-DRAFT; Active; I-D Exists; WG Document · 类型：model

模块：`ietf-bgp-ls`、`ietf-bgp-lsdb`、`ietf-bgp-ls-topo-types`。

支持：BGP-LS、BGP-LS-VPN、BGP-LS-SPF 的 session/LSDB/topology 数据和 OSPF node/Link/Prefix 属性。。

边界：WG Draft-04，未发布 RFC；不能把 RFC9815 协议发布误说成此模型也已发布；并非 RFC8345 augmentation。。

关键对象/关联：`/ietf-routing:routing/control-plane-protocols/control-plane-protocol/ietf-bgp:bgp/global/afi-safis/afi-safi/ietf-bgp-ls:link-state`；`bgp-ls-topology/instances/instance[vrf-name protocol identifier]`。

核对章节：2；3；4。研读深度：Datatracker 当前 document/state API、最新版全文与关键 schema 树核对；草案未纳入 RFC 标准冻结集合。

### draft-ietf-mpls-mldp-yang-16 YANG Data Model for MPLS mLDP

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-mpls-mldp-yang-16) · Internet-Draft / Active · 类型：device-yang

模块：`ietf-mpls-mldp`、`ietf-mpls-mldp-extended`。

支持：mLDP base/extended 在 RFC9070 LDP 上添加 multipoint FEC、P2MP/MP2MP LSP、能力、绑定与状态。。

边界：-16 Datatracker 为 Active + Last Call，文本有效期已过；不能称已发布 RFC。多点 LSP 转发树不等同直接多点物理介质。。

核对章节：§3–9。研读深度：abstract, model tree, module import/augment and selected semantic sections; not compiled。

### draft-ietf-rtgwg-vrrp-rfc8347bis-23 A YANG Data Model for the Virtual Router Redundancy Protocol (VRRP)

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-rtgwg-vrrp-rfc8347bis-23) · 工作组草案；RFC Ed Queue；尚未发布RFC · 类型：model

模块：`ietf-vrrp`。

支持：VRRP v2/v3模型修订与术语更新。

边界：拟替代8347，尚未形成正式RFC替代。

核对章节：Abstract；YANG module declarations。研读深度：官方摘要、模块声明、Datatracker发布状态及IANA登记核对；未逐字段审查。

### draft-ietf-spring-srv6-yang-05 YANG Data Model for SRv6 Base and Static

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-spring-srv6-yang-05) · INTERNET-DRAFT; Replaced; I-D Exists; WG Document · 类型：model

模块：`ietf-srv6-types`、`ietf-srv6-base`、`ietf-srv6-static`。

支持：历史合并 SRv6 base/static 模型。。

边界：已被 base/static 两草案替代；Replaced 不等于被某 RFC 废止。。

关键对象/关联：`/ietf-routing:routing/ietf-segment-routing:segment-routing`。

核对章节：3；4；5。研读深度：Datatracker 当前 document/state API、最新版全文与关键 schema 树核对；草案未纳入 RFC 标准冻结集合。

### draft-ietf-spring-srv6-yang-static-00 YANG Data Model for SRv6 Static

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-spring-srv6-yang-static-00) · INTERNET-DRAFT; Expired; I-D Exists; WG Document · 类型：model

模块：`ietf-srv6-static`。

支持：静态 SRv6 local SID/behavior 配置模型建议。。

边界：Draft-00 当前 expired；未发布 RFC。。

关键对象/关联：`ietf-srv6-static augmentation of SRv6 base`。

核对章节：3；4。研读深度：Datatracker 当前 document/state API、最新版全文与关键 schema 树核对；草案未纳入 RFC 标准冻结集合。

### draft-ietf-teas-yang-rsvp-21 A YANG Data Model for Resource Reservation Protocol (RSVP)

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-teas-yang-rsvp-21) · Internet-Draft / Active · 类型：device-yang

模块：`ietf-rsvp`、`ietf-rsvp-extended`。

支持：RSVP base/extended 配置、接口、neighbor/session、状态、RPC/通知；扩展 routing control-plane-protocol，供 RSVP-TE 再 augment。。

边界：-21 活跃草案，尚无正式 RSVP YANG RFC；RSVP 会话不能当成物理邻接，协议扩展仍需对应模型。。

关键对象/关联：`/ietf-routing:routing/control-plane-protocols/control-plane-protocol/ietf-rsvp:rsvp`。

核对章节：§3–5。研读深度：abstract, model tree, module import/augment and selected semantic sections; not compiled。

## 过渡与软线

### RFC8513 A YANG Data Model for Dual-Stack Lite (DS-Lite)

[官方原文](https://www.rfc-editor.org/rfc/rfc8513.html) · PROPOSED STANDARD · 类型：model

模块：`ietf-dslite`。

支持：DS-Lite AFTR/B4的管理模型。

边界：不是普通Ethernet邻接。

核对章节：Abstract。研读深度：官方摘要、模型类型、RFC索引及IANA登记核对；未逐字段审查。

### RFC8676 YANG Modules for IPv4-in-IPv6 Address plus Port (A+P) Softwires

[官方原文](https://www.rfc-editor.org/rfc/rfc8676.html) · PROPOSED STANDARD · 类型：model

模块：`ietf-softwire-br`、`ietf-softwire-ce`、`ietf-softwire-common`。

支持：lw4o6、MAP-E、MAP-T的BR/CE配置运行模型。

边界：封装/翻译作用需具体场景证据。

核对章节：Abstract。研读深度：官方摘要、模型类型、RFC索引及IANA登记核对；未逐字段审查。

## 采集与管理

### RFC6241 Network Configuration Protocol (NETCONF)

[官方原文](https://www.rfc-editor.org/rfc/rfc6241.html) · PROPOSED STANDARD · 类型：management

模块：`ietf-netconf`。

支持：NETCONF操作与配置数据存储。

边界：管理协议不等于支持所有数据模型。

核对章节：6；7；8。研读深度：范围/概览/关键定义核对；未对本目录整体编译。

### RFC7407 A YANG Data Model for SNMP Configuration

[官方原文](https://www.rfc-editor.org/rfc/rfc7407.html) · PROPOSED STANDARD · 类型：management

模块：`ietf-snmp`、`ietf-snmp-common`、`ietf-snmp-community`、`ietf-snmp-engine`、`ietf-snmp-notification`、`ietf-snmp-proxy`、`ietf-snmp-ssh`、`ietf-snmp-target`、`ietf-snmp-tls`、`ietf-snmp-tsm`、`ietf-snmp-usm`、`ietf-snmp-vacm`、`ietf-x509-cert-to-name`。

支持：SNMP引擎配置的YANG定义集合。

边界：不是所有SNMP MIB的YANG实例转换。

核对章节：Abstract。研读深度：官方摘要、模型类型、RFC索引及IANA登记核对；未逐字段审查。

### RFC8040 RESTCONF Protocol

[官方原文](https://www.rfc-editor.org/rfc/rfc8040.html) · PROPOSED STANDARD · 类型：management

模块：`ietf-restconf`、`ietf-restconf-monitoring`。

支持：RESTCONF资源与操作。

边界：API连通不证明拓扑事实正确。

核对章节：3；4。研读深度：范围/概览/关键定义核对；未对本目录整体编译。

### RFC8526 NETCONF Extensions to Support the Network Management Datastore Architecture

[官方原文](https://www.rfc-editor.org/rfc/rfc8526.html) · PROPOSED STANDARD · 类型：management

模块：`ietf-netconf-nmda`。

支持：NETCONF访问NMDA。

边界：数据存储选择须明确。

核对章节：3；4。研读深度：范围/概览/关键定义核对；未对本目录整体编译。

### RFC8527 RESTCONF Extensions to Support the Network Management Datastore Architecture

[官方原文](https://www.rfc-editor.org/rfc/rfc8527.html) · PROPOSED STANDARD · 类型：management

模块：无独立 YANG 模块（见文档类型与范围）。

支持：RESTCONF访问NMDA。

边界：配置与运行状态分别读取。

核对章节：3。研读深度：范围/概览/关键定义核对；未对本目录整体编译。

### RFC8639 Subscription to YANG Notifications

[官方原文](https://www.rfc-editor.org/rfc/rfc8639.html) · PROPOSED STANDARD · 类型：management

模块：`ietf-subscribed-notifications`。

支持：订阅和通知。

边界：事件来源与覆盖需追踪。

核对章节：2；3；4。研读深度：范围/概览/关键定义核对；未对本目录整体编译。

### RFC8640 Dynamic Subscription to YANG Events and Datastores over NETCONF

[官方原文](https://www.rfc-editor.org/rfc/rfc8640.html) · PROPOSED STANDARD · 类型：management

模块：无独立 YANG 模块（见文档类型与范围）。

支持：NETCONF承载订阅通知。

边界：不是新拓扑模型。

核对章节：3；4。研读深度：范围/概览/关键定义核对；未对本目录整体编译。

### RFC8641 Subscription to YANG Notifications for Datastore Updates

[官方原文](https://www.rfc-editor.org/rfc/rfc8641.html) · PROPOSED STANDARD · 类型：management

模块：`ietf-yang-push`。

支持：YANG-Push周期及变化更新。

边界：变化流不替代身份识别。

核对章节：3；4；5。研读深度：范围/概览/关键定义核对；未对本目录整体编译。

### RFC8650 Dynamic Subscription to YANG Events and Datastores over RESTCONF

[官方原文](https://www.rfc-editor.org/rfc/rfc8650.html) · PROPOSED STANDARD · 类型：management

模块：`ietf-restconf-subscribed-notifications`。

支持：RESTCONF承载订阅通知。

边界：不是新拓扑模型。

核对章节：3；4。研读深度：范围/概览/关键定义核对；未对本目录整体编译。

### RFC9144 Comparison of Network Management Datastore Architecture (NMDA) Datastores

[官方原文](https://www.rfc-editor.org/rfc/rfc9144.html) · PROPOSED STANDARD · 类型：management

模块：`ietf-nmda-compare`。

支持：比较NMDA数据存储。

边界：非物理接线真值对照器。

核对章节：3；4。研读深度：范围/概览/关键定义核对；未对本目录整体编译。

### draft-ietf-netconf-adaptive-subscription-18 Adaptive Subscription to YANG Notifications

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-netconf-adaptive-subscription-18) · 工作组草案；RFC Ed Queue；尚未发布RFC · 类型：management

模块：`ietf-adaptive-subscription`。

支持：按条件调整通知周期的实验机制。

边界：仍在RFC发布队列，不能称已发布Experimental RFC。

核对章节：Abstract；YANG module declarations。研读深度：官方摘要、模块声明、Datatracker发布状态及IANA登记核对；未逐字段审查。

## 隧道/基础

### draft-ietf-teas-yang-te-44 A YANG Data Model for Traffic Engineering Tunnels, Label Switched Paths, and Interfaces

[官方原文](https://datatracker.ietf.org/doc/html/draft-ietf-teas-yang-te-44) · Internet-Draft / RFC Editor queue (In Progress) · 类型：device-and-network-yang

模块：`ietf-te`、`ietf-te-device`。

支持：通用 TE tunnel/LSP/path：ietf-te 提供设备无关 globals/tunnels/lsps、约束与 RPC，ietf-te-device 增加设备接口与设备属性；技术模型再 augment。。

边界：仍是 -44，RFC Editor queue，尚无正式 RFC 号。核心依赖 RFC8776-update 草案版本；不能因 IANA 预登记或 RFCXXXX 占位符宣称正式发布。未提供路径计算算法或全部 MPLS/光层细节。。

关键对象/关联：`/ietf-te:te/tunnels/tunnel`；`/ietf-te:te/lsps/lsp`；`/ietf-te:te/ietf-te-device:interfaces/interface`。

核对章节：§3–7。研读深度：abstract, model tree, module import/augment and selected semantic sections; not compiled。

## 注册表交叉核对与未展开部分

IANA 快照共 314 条记录，其中 250 条能关联到上述文档。完整逐行登记与关联结果保存在 [JSON 目录](network-digital-twin-ietf-model-catalog.json)。同名多 revision、历史模块和预登记条目均保留；未展开条目不会计作已研读模型。

下表公开未在上文展开的登记，便于判断本次范围与后续查漏。它们主要是安全传输、凭据、管理会话、引导、历史语言版本或特定业务的辅助模块；不能据此声称其全部字段已经审查。

| 登记模块 | 官方登记引用 | 本次深度 |
|---|---|---|
| `iana-dns-class-rr-type` | [rfc9108](https://www.rfc-editor.org/rfc/rfc9108.html) | 登记与范围筛查，未逐项语义研读 |
| `iana-http-versions` | [RFC-ietf-netconf-http-client-server-31](https://datatracker.ietf.org/doc/html/draft-ietf-netconf-http-client-server-31) | 登记与范围筛查，未逐项语义研读 |
| `iana-sip-option-tags` | [rfc10006](https://www.rfc-editor.org/rfc/rfc10006.html) | 登记与范围筛查，未逐项语义研读 |
| `iana-ssh-encryption-algs` | [rfc9644](https://www.rfc-editor.org/rfc/rfc9644.html) | 登记与范围筛查，未逐项语义研读 |
| `iana-ssh-key-exchange-algs` | [rfc9644](https://www.rfc-editor.org/rfc/rfc9644.html) | 登记与范围筛查，未逐项语义研读 |
| `iana-ssh-mac-algs` | [rfc9644](https://www.rfc-editor.org/rfc/rfc9644.html) | 登记与范围筛查，未逐项语义研读 |
| `iana-ssh-public-key-algs` | [rfc9644](https://www.rfc-editor.org/rfc/rfc9644.html) | 登记与范围筛查，未逐项语义研读 |
| `iana-tls-cipher-suite-algs` | [rfc9645](https://www.rfc-editor.org/rfc/rfc9645.html) | 登记与范围筛查，未逐项语义研读 |
| `ietf-complex-types` | [rfc6095](https://www.rfc-editor.org/rfc/rfc6095.html) | 登记与范围筛查，未逐项语义研读 |
| `ietf-crypto-types` | [rfc9640](https://www.rfc-editor.org/rfc/rfc9640.html) | 登记与范围筛查，未逐项语义研读 |
| `ietf-dots-call-home` | [rfc9066](https://www.rfc-editor.org/rfc/rfc9066.html) | 登记与范围筛查，未逐项语义研读 |
| `ietf-dots-mapping` | [rfc9244](https://www.rfc-editor.org/rfc/rfc9244.html) | 登记与范围筛查，未逐项语义研读 |
| `ietf-dots-robust-trans` | [rfc9362](https://www.rfc-editor.org/rfc/rfc9362.html) | 登记与范围筛查，未逐项语义研读 |
| `ietf-dots-signal-channel` | [rfc8782](https://www.rfc-editor.org/rfc/rfc8782.html) | 登记与范围筛查，未逐项语义研读 |
| `ietf-dots-signal-control` | [rfc9133](https://www.rfc-editor.org/rfc/rfc9133.html) | 登记与范围筛查，未逐项语义研读 |
| `ietf-dots-telemetry` | [rfc9244](https://www.rfc-editor.org/rfc/rfc9244.html) | 登记与范围筛查，未逐项语义研读 |
| `ietf-factory-default` | [rfc8808](https://www.rfc-editor.org/rfc/rfc8808.html) | 登记与范围筛查，未逐项语义研读 |
| `ietf-http-client` | [RFC-ietf-netconf-http-client-server-31](https://datatracker.ietf.org/doc/html/draft-ietf-netconf-http-client-server-31) | 登记与范围筛查，未逐项语义研读 |
| `ietf-http-server` | [RFC-ietf-netconf-http-client-server-31](https://datatracker.ietf.org/doc/html/draft-ietf-netconf-http-client-server-31) | 登记与范围筛查，未逐项语义研读 |
| `ietf-immutable-annotation` | [RFC-ietf-netmod-immutable-flag-14](https://datatracker.ietf.org/doc/html/draft-ietf-netmod-immutable-flag-14) | 登记与范围筛查，未逐项语义研读 |
| `ietf-inet-types` | [rfc6021](https://www.rfc-editor.org/rfc/rfc6021.html) | 登记与范围筛查，未逐项语义研读 |
| `ietf-inet-types` | [rfc6991](https://www.rfc-editor.org/rfc/rfc6991.html) | 登记与范围筛查，未逐项语义研读 |
| `ietf-keystore` | [rfc9642](https://www.rfc-editor.org/rfc/rfc9642.html) | 登记与范围筛查，未逐项语义研读 |
| `ietf-netconf-acm` | [rfc6536](https://www.rfc-editor.org/rfc/rfc6536.html) | 登记与范围筛查，未逐项语义研读 |
| `ietf-netconf-acm` | [rfc8341](https://www.rfc-editor.org/rfc/rfc8341.html) | 登记与范围筛查，未逐项语义研读 |
| `ietf-netconf-client` | [RFC-ietf-netconf-netconf-client-server-41](https://datatracker.ietf.org/doc/html/draft-ietf-netconf-netconf-client-server-41) | 登记与范围筛查，未逐项语义研读 |
| `ietf-netconf-monitoring` | [rfc6022](https://www.rfc-editor.org/rfc/rfc6022.html) | 登记与范围筛查，未逐项语义研读 |
| `ietf-netconf-notifications` | [rfc6470](https://www.rfc-editor.org/rfc/rfc6470.html) | 登记与范围筛查，未逐项语义研读 |
| `ietf-netconf-partial-lock` | [rfc5717](https://www.rfc-editor.org/rfc/rfc5717.html) | 登记与范围筛查，未逐项语义研读 |
| `ietf-netconf-server` | [RFC-ietf-netconf-netconf-client-server-41](https://datatracker.ietf.org/doc/html/draft-ietf-netconf-netconf-client-server-41) | 登记与范围筛查，未逐项语义研读 |
| `ietf-netconf-time` | [rfc7758](https://www.rfc-editor.org/rfc/rfc7758.html) | 登记与范围筛查，未逐项语义研读 |
| `ietf-netconf-with-defaults` | [rfc6243](https://www.rfc-editor.org/rfc/rfc6243.html) | 登记与范围筛查，未逐项语义研读 |
| `ietf-restconf-client` | [RFC-ietf-netconf-restconf-client-server-45](https://datatracker.ietf.org/doc/html/draft-ietf-netconf-restconf-client-server-45) | 登记与范围筛查，未逐项语义研读 |
| `ietf-restconf-server` | [RFC-ietf-netconf-restconf-client-server-45](https://datatracker.ietf.org/doc/html/draft-ietf-netconf-restconf-client-server-45) | 登记与范围筛查，未逐项语义研读 |
| `ietf-schc` | [rfc9363](https://www.rfc-editor.org/rfc/rfc9363.html) | 登记与范围筛查，未逐项语义研读 |
| `ietf-schc-compound-ack` | [rfc9441](https://www.rfc-editor.org/rfc/rfc9441.html) | 登记与范围筛查，未逐项语义研读 |
| `ietf-sip-auto-peering` | [rfc10006](https://www.rfc-editor.org/rfc/rfc10006.html) | 登记与范围筛查，未逐项语义研读 |
| `ietf-ssh-client` | [rfc9644](https://www.rfc-editor.org/rfc/rfc9644.html) | 登记与范围筛查，未逐项语义研读 |
| `ietf-ssh-common` | [rfc9644](https://www.rfc-editor.org/rfc/rfc9644.html) | 登记与范围筛查，未逐项语义研读 |
| `ietf-ssh-server` | [rfc9644](https://www.rfc-editor.org/rfc/rfc9644.html) | 登记与范围筛查，未逐项语义研读 |
| `ietf-sztp-bootstrap-server` | [rfc8572](https://www.rfc-editor.org/rfc/rfc8572.html) | 登记与范围筛查，未逐项语义研读 |
| `ietf-sztp-conveyed-info` | [rfc8572](https://www.rfc-editor.org/rfc/rfc8572.html) | 登记与范围筛查，未逐项语义研读 |
| `ietf-sztp-csr` | [rfc9646](https://www.rfc-editor.org/rfc/rfc9646.html) | 登记与范围筛查，未逐项语义研读 |
| `ietf-tcg-algs` | [rfc9684](https://www.rfc-editor.org/rfc/rfc9684.html) | 登记与范围筛查，未逐项语义研读 |
| `ietf-tcp` | [rfc9648](https://www.rfc-editor.org/rfc/rfc9648.html) | 登记与范围筛查，未逐项语义研读 |
| `ietf-tcp-client` | [rfc9643](https://www.rfc-editor.org/rfc/rfc9643.html) | 登记与范围筛查，未逐项语义研读 |
| `ietf-tcp-common` | [rfc9643](https://www.rfc-editor.org/rfc/rfc9643.html) | 登记与范围筛查，未逐项语义研读 |
| `ietf-tcp-server` | [rfc9643](https://www.rfc-editor.org/rfc/rfc9643.html) | 登记与范围筛查，未逐项语义研读 |
| `ietf-template` | [rfc6087](https://www.rfc-editor.org/rfc/rfc6087.html) | 登记与范围筛查，未逐项语义研读 |
| `ietf-template` | [rfc8407](https://www.rfc-editor.org/rfc/rfc8407.html) | 登记与范围筛查，未逐项语义研读 |
| `ietf-tls-client` | [rfc9645](https://www.rfc-editor.org/rfc/rfc9645.html) | 登记与范围筛查，未逐项语义研读 |
| `ietf-tls-common` | [rfc9645](https://www.rfc-editor.org/rfc/rfc9645.html) | 登记与范围筛查，未逐项语义研读 |
| `ietf-tls-server` | [rfc9645](https://www.rfc-editor.org/rfc/rfc9645.html) | 登记与范围筛查，未逐项语义研读 |
| `ietf-tpm-remote-attestation` | [rfc9684](https://www.rfc-editor.org/rfc/rfc9684.html) | 登记与范围筛查，未逐项语义研读 |
| `ietf-truststore` | [rfc9641](https://www.rfc-editor.org/rfc/rfc9641.html) | 登记与范围筛查，未逐项语义研读 |
| `ietf-udp-client` | [rfc9984](https://www.rfc-editor.org/rfc/rfc9984.html) | 登记与范围筛查，未逐项语义研读 |
| `ietf-udp-server` | [rfc9984](https://www.rfc-editor.org/rfc/rfc9984.html) | 登记与范围筛查，未逐项语义研读 |
| `ietf-voucher` | [rfc8366](https://www.rfc-editor.org/rfc/rfc8366.html) | 登记与范围筛查，未逐项语义研读 |
| `ietf-voucher-request` | [rfc8995](https://www.rfc-editor.org/rfc/rfc8995.html) | 登记与范围筛查，未逐项语义研读 |
| `ietf-yang-library` | [rfc7895](https://www.rfc-editor.org/rfc/rfc7895.html) | 登记与范围筛查，未逐项语义研读 |
| `ietf-yang-patch` | [rfc8072](https://www.rfc-editor.org/rfc/rfc8072.html) | 登记与范围筛查，未逐项语义研读 |
| `ietf-yang-types` | [rfc6021](https://www.rfc-editor.org/rfc/rfc6021.html) | 登记与范围筛查，未逐项语义研读 |
| `ietf-yang-types` | [rfc6991](https://www.rfc-editor.org/rfc/rfc6991.html) | 登记与范围筛查，未逐项语义研读 |
| `ietf-ztp-types` | [rfc9646](https://www.rfc-editor.org/rfc/rfc9646.html) | 登记与范围筛查，未逐项语义研读 |
