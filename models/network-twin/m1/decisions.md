# M1 模块去留判定

由 `scripts/topology/m1_decisions.py` 从目录与来源锁生成；规则见 decisions.json。
按修订版判定后汇总到模块：同一模块的已发布修订为运行基线，更新它的在审草案修订为候选升级，被取代的修订为参考。

| 判定（模块） | 数量 |
|---|---|
| candidate-upgrade | 105 |
| not-applicable | 2 |
| reference | 61 |
| runtime-baseline | 309 |

| 判定（修订版文件） | 数量 |
|---|---|
| candidate-upgrade | 109 |
| not-applicable | 3 |
| reference | 40 |
| runtime-baseline | 312 |

## ACTN/VN 与切片

| 模块 | 判定 | 依据 | 理由 |
|---|---|---|---|
| `ietf-ns-topo` | candidate-upgrade | draft-ietf-teas-network-slice-topology-yang-04 | active Internet-Draft; enters a set only after review |
| `ietf-ns-underlay-path` | candidate-upgrade | draft-ietf-teas-network-slice-topology-yang-04 | active Internet-Draft; enters a set only after review |
| `ietf-otn-slice` | candidate-upgrade | draft-ietf-ccamp-yang-otn-slicing-12 | active Internet-Draft; enters a set only after review |
| `ietf-otn-slice-mpi` | candidate-upgrade | draft-ietf-ccamp-yang-otn-slicing-12 | active Internet-Draft; enters a set only after review |

## ALTO/信息交换

| 模块 | 判定 | 依据 | 理由 |
|---|---|---|---|
| `ietf-alto` | candidate-upgrade | draft-ietf-alto-oam-yang-18 | active Internet-Draft; enters a set only after review |
| `ietf-alto-stats` | candidate-upgrade | draft-ietf-alto-oam-yang-18 | active Internet-Draft; enters a set only after review |

## DetNet

| 模块 | 判定 | 依据 | 理由 |
|---|---|---|---|
| `ietf-detnet` | runtime-baseline | RFC9633 | published RFC, newest published revision |

## OAM与测量

| 模块 | 判定 | 依据 | 理由 |
|---|---|---|---|
| `iana-ioam-integrity-protection-methods` | candidate-upgrade | draft-ietf-ippm-ioam-integrity-yang-08 | active Internet-Draft; enters a set only after review |
| `ietf-bfd` | runtime-baseline | RFC9314 | published RFC, newest published revision（另有 ietf-bfd@2021-10-21.yang：reference） |
| `ietf-bfd-ip-mh` | runtime-baseline | RFC9314 | published RFC, newest published revision（另有 ietf-bfd-ip-mh@2021-10-21.yang：reference） |
| `ietf-bfd-ip-sh` | runtime-baseline | RFC9314 | published RFC, newest published revision（另有 ietf-bfd-ip-sh@2021-10-21.yang：reference） |
| `ietf-bfd-lag` | runtime-baseline | RFC9314 | published RFC, newest published revision（另有 ietf-bfd-lag@2021-10-21.yang：reference） |
| `ietf-bfd-mpls` | runtime-baseline | RFC9314 | published RFC, newest published revision（另有 ietf-bfd-mpls@2021-10-21.yang：reference） |
| `ietf-bfd-types` | runtime-baseline | RFC9314 | published RFC, newest published revision（另有 ietf-bfd-types@2021-10-21.yang：reference） |
| `ietf-connection-oriented-oam` | runtime-baseline | RFC8531 | published RFC, newest published revision |
| `ietf-connectionless-oam` | runtime-baseline | RFC8532 | published RFC, newest published revision |
| `ietf-connectionless-oam-methods` | runtime-baseline | RFC8533 | published RFC, newest published revision |
| `ietf-ioam` | runtime-baseline | RFC9617 | published RFC, newest published revision |
| `ietf-ioam-integrity` | candidate-upgrade | draft-ietf-ippm-ioam-integrity-yang-08 | active Internet-Draft; enters a set only after review |
| `ietf-ipfix-psamp` | runtime-baseline | RFC6728 | published RFC, newest published revision |
| `ietf-lime-time-types` | runtime-baseline | RFC8532 | published RFC, newest published revision |
| `ietf-lmap-common` | runtime-baseline | RFC8194 | published RFC, newest published revision |
| `ietf-lmap-control` | runtime-baseline | RFC8194 | published RFC, newest published revision |
| `ietf-lmap-report` | runtime-baseline | RFC8194 | published RFC, newest published revision |
| `ietf-network-vpn-pm` | runtime-baseline | RFC9375 | published RFC, newest published revision |
| `ietf-notification-capabilities` | runtime-baseline | RFC9196 | published RFC, newest published revision |
| `ietf-packet-discard-reporting` | candidate-upgrade | draft-ietf-opsawg-discardmodel-16 | active Internet-Draft; enters a set only after review |
| `ietf-packet-discard-reporting-common` | candidate-upgrade | draft-ietf-opsawg-discardmodel-16 | active Internet-Draft; enters a set only after review |
| `ietf-packet-discard-reporting-sx` | candidate-upgrade | draft-ietf-opsawg-discardmodel-16 | active Internet-Draft; enters a set only after review |
| `ietf-stamp` | reference | draft-ietf-ippm-stamp-yang-12 | expired or replaced Internet-Draft |
| `ietf-system-capabilities` | runtime-baseline | RFC9196 | published RFC, newest published revision |
| `ietf-traffic-analyzer` | candidate-upgrade | draft-ietf-bmwg-network-tester-cfg-17 | active Internet-Draft; enters a set only after review |
| `ietf-traffic-generator` | candidate-upgrade | draft-ietf-bmwg-network-tester-cfg-17 | active Internet-Draft; enters a set only after review |
| `ietf-twamp` | runtime-baseline | RFC8913 | published RFC, newest published revision |

## SIMAP与资源清单

| 模块 | 判定 | 依据 | 理由 |
|---|---|---|---|
| `iana-power-and-energy` | runtime-baseline | draft-ietf-green-power-and-energy-yang-04 | fixed draft: GREEN power and energy, plan §3/M2 |
| `ietf-entitlement-inventory` | runtime-baseline | draft-ietf-ivy-entitlement-inventory-05 | fixed draft: IVY entitlement, plan §3/M2 |
| `ietf-network-inventory` | runtime-baseline | draft-ietf-ivy-network-inventory-yang-20 | fixed draft: IVY base inventory, plan §3/M2 |
| `ietf-network-inventory-sw-ext` | runtime-baseline | draft-ietf-ivy-network-inventory-software-04 | fixed draft: IVY software inventory, plan §3/M2 |
| `ietf-network-inventory-topology` | runtime-baseline | draft-ietf-ivy-network-inventory-topology-11 | fixed draft: IVY topology mapping, plan §3/M2 |
| `ietf-ni-location` | runtime-baseline | draft-ietf-ivy-network-inventory-location-06 | fixed draft: IVY location, plan §3/M2 |
| `ietf-nwi-passive-inventory` | runtime-baseline | draft-ietf-ivy-passive-network-inventory-01 | fixed draft: IVY passive inventory, plan §3/M2 |
| `ietf-power-and-energy` | runtime-baseline | draft-ietf-green-power-and-energy-yang-04 | fixed draft: GREEN power and energy, plan §3/M2 |
| `ietf-simap-topology` | runtime-baseline | draft-havel-nmop-simap-yang-03 | fixed draft: SIMAP YANG -03, the owner-adopted 8345bis baseline |

## SR/MPLS 与 SRv6

| 模块 | 判定 | 依据 | 理由 |
|---|---|---|---|
| `iana-msd-types` | runtime-baseline | RFC9702 | published RFC, newest published revision（另有 iana-msd-types@2025-01-10.yang：reference） |
| `ietf-isis-sr-mpls` | runtime-baseline | RFC9902 | published RFC, newest published revision |
| `ietf-mpls-msd` | runtime-baseline | RFC9702 | published RFC, newest published revision |
| `ietf-ospf-sr-mpls` | runtime-baseline | RFC9903 | published RFC, newest published revision |
| `ietf-segment-routing` | runtime-baseline | RFC9020 | published RFC, newest published revision |
| `ietf-segment-routing-common` | runtime-baseline | RFC9020 | published RFC, newest published revision |
| `ietf-segment-routing-mpls` | runtime-baseline | RFC9020 | published RFC, newest published revision |
| `ietf-sr-policy` | candidate-upgrade | draft-ietf-spring-sr-policy-yang-08 | active Internet-Draft; enters a set only after review |
| `ietf-sr-policy-types` | candidate-upgrade | draft-ietf-spring-sr-policy-yang-08 | active Internet-Draft; enters a set only after review |
| `ietf-srv6-base` | candidate-upgrade | draft-ietf-spring-srv6-yang-base-02 | active Internet-Draft; enters a set only after review |
| `ietf-srv6-types` | candidate-upgrade | draft-ietf-spring-srv6-yang-base-02 | active Internet-Draft; enters a set only after review |

## TE/包交换

| 模块 | 判定 | 依据 | 理由 |
|---|---|---|---|
| `ietf-l3-te-topology` | candidate-upgrade | draft-ietf-teas-yang-l3-te-topo-19 | active Internet-Draft; enters a set only after review |
| `ietf-l3-te-topology-state` | candidate-upgrade | draft-ietf-teas-yang-l3-te-topo-19 | active Internet-Draft; enters a set only after review |
| `ietf-mpls-te-types` | candidate-upgrade | draft-ietf-teas-yang-te-mpls-topology-05 | active Internet-Draft; enters a set only after review |
| `ietf-te-mpls-topology` | candidate-upgrade | draft-ietf-teas-yang-te-mpls-topology-05 | active Internet-Draft; enters a set only after review |
| `ietf-te-topology-packet` | candidate-upgrade | draft-ietf-teas-yang-l3-te-topo-19 | active Internet-Draft; enters a set only after review |
| `ietf-te-topology-packet-state` | candidate-upgrade | draft-ietf-teas-yang-l3-te-topo-19 | active Internet-Draft; enters a set only after review |

## TE/历史与缺口

| 模块 | 判定 | 依据 | 理由 |
|---|---|---|---|
| `ietf-detnet-topology` | reference | draft-ietf-detnet-topology-yang-02 | expired or replaced Internet-Draft |
| `ietf-flexi-grid-media-channel` | reference | draft-ietf-ccamp-flexigrid-media-channel-yang-04 | expired or replaced Internet-Draft |
| `ietf-flexi-grid-tunnel` | reference | draft-ietf-ccamp-flexigrid-tunnel-yang-03 | expired or replaced Internet-Draft |
| `ietf-mpls-static` | reference | draft-ietf-mpls-static-yang-14 | expired or replaced Internet-Draft |
| `ietf-rsvp-te` | reference | draft-ietf-teas-yang-rsvp-te-10 | expired or replaced Internet-Draft |
| `ietf-rsvp-te-mpls` | reference | draft-ietf-teas-yang-rsvp-te-10 | expired or replaced Internet-Draft |
| `ietf-sr-mpls-topology` | reference | draft-ietf-teas-yang-sr-te-topo-19 | expired or replaced Internet-Draft |
| `ietf-sr-mpls-topology-state` | reference | draft-ietf-teas-yang-sr-te-topo-19 | expired or replaced Internet-Draft |
| `ietf-te-mpls` | reference | draft-ietf-teas-yang-te-mpls-04 | expired or replaced Internet-Draft |
| `ietf-wson-tunnel` | reference | draft-ietf-ccamp-wson-tunnel-model-09 | expired or replaced Internet-Draft |

## TE/基础与多层

| 模块 | 判定 | 依据 | 理由 |
|---|---|---|---|
| `ietf-te-packet-types` | runtime-baseline | RFC8776 | published RFC, newest published revision（另有 ietf-te-packet-types@2026-06-11.yang：candidate-upgrade） |
| `ietf-te-topology` | runtime-baseline | RFC8795 | published RFC, newest published revision |
| `ietf-te-topology-state` | runtime-baseline | RFC8795 | published RFC, newest published revision |
| `ietf-te-types` | runtime-baseline | RFC8776 | published RFC, newest published revision（另有 ietf-te-types@2026-06-11.yang：candidate-upgrade） |
| `ietf-topology-filter` | candidate-upgrade | draft-ietf-teas-yang-topology-filter-03 | active Internet-Draft; enters a set only after review |

## TVR/时间变化

| 模块 | 判定 | 依据 | 理由 |
|---|---|---|---|
| `ietf-schedule` | runtime-baseline | RFC9922 | published RFC, newest published revision |
| `ietf-tvr-node` | candidate-upgrade | draft-ietf-tvr-schedule-yang-12 | active Internet-Draft; enters a set only after review |
| `ietf-tvr-schedule` | candidate-upgrade | draft-ietf-tvr-schedule-yang-12 | active Internet-Draft; enters a set only after review |
| `ietf-tvr-topology` | candidate-upgrade | draft-ietf-tvr-schedule-yang-12 | active Internet-Draft; enters a set only after review |

## VPN 与覆盖网络

| 模块 | 判定 | 依据 | 理由 |
|---|---|---|---|
| `iana-bgp-l2-encaps` | runtime-baseline | RFC9291 | published RFC, newest published revision |
| `iana-pseudowire-types` | runtime-baseline | RFC9291 | published RFC, newest published revision |
| `ietf-ac-common` | runtime-baseline | RFC9833 | published RFC, newest published revision |
| `ietf-ac-glue` | runtime-baseline | RFC9836 | published RFC, newest published revision |
| `ietf-ac-ntw` | runtime-baseline | RFC9835 | published RFC, newest published revision |
| `ietf-ac-svc` | runtime-baseline | RFC9834 | published RFC, newest published revision |
| `ietf-bearer-svc` | runtime-baseline | RFC9834 | published RFC, newest published revision |
| `ietf-bgp-l3vpn` | reference | draft-ietf-bess-l3vpn-yang-05 | expired or replaced Internet-Draft |
| `ietf-ethernet-segment` | runtime-baseline | RFC9291 | published RFC, newest published revision |
| `ietf-evpn` | reference | draft-ietf-bess-evpn-yang-07 | expired or replaced Internet-Draft |
| `ietf-l2vpn-ntw` | runtime-baseline | RFC9291 | published RFC, newest published revision |
| `ietf-l2vpn-svc` | runtime-baseline | RFC8466 | published RFC, newest published revision |
| `ietf-l3vpn-ntw` | runtime-baseline | RFC9182 | published RFC, newest published revision |
| `ietf-l3vpn-svc` | runtime-baseline | RFC8299 | published RFC, newest published revision |
| `ietf-sap-ntw` | runtime-baseline | RFC9408 | published RFC, newest published revision |
| `ietf-vpn-common` | runtime-baseline | RFC9181 | published RFC, newest published revision |
| `ietf-vxlan` | reference | draft-chen-nvo3-vxlan-yang-07 | expired or replaced Internet-Draft |

## 传统 SNMP MIB 与转换

| 模块 | 判定 | 依据 | 理由 |
|---|---|---|---|
| `BFD-STD-MIB` | reference |  | SMIv2 MIB: mapping basis, not a YANG capability (plan §9) |
| `BGP4-MIB` | reference |  | SMIv2 MIB: mapping basis, not a YANG capability (plan §9) |
| `BRIDGE-MIB` | reference |  | SMIv2 MIB: mapping basis, not a YANG capability (plan §9) |
| `ENTITY-MIB` | reference |  | SMIv2 MIB: mapping basis, not a YANG capability (plan §9) |
| `ENTITY-SENSOR-MIB` | reference |  | SMIv2 MIB: mapping basis, not a YANG capability (plan §9) |
| `ENTITY-STATE-MIB` | reference |  | SMIv2 MIB: mapping basis, not a YANG capability (plan §9) |
| `ENTITY-STATE-TC-MIB` | reference |  | SMIv2 MIB: mapping basis, not a YANG capability (plan §9) |
| `IANA-ENTITY-MIB` | reference |  | SMIv2 MIB: mapping basis, not a YANG capability (plan §9) |
| `IF-MIB` | reference |  | SMIv2 MIB: mapping basis, not a YANG capability (plan §9) |
| `IP-FORWARD-MIB` | reference |  | SMIv2 MIB: mapping basis, not a YANG capability (plan §9) |
| `IP-MIB` | reference |  | SMIv2 MIB: mapping basis, not a YANG capability (plan §9) |
| `IPMCAST-MIB` | reference |  | SMIv2 MIB: mapping basis, not a YANG capability (plan §9) |
| `ISIS-MIB` | reference |  | SMIv2 MIB: mapping basis, not a YANG capability (plan §9) |
| `MGMD-STD-MIB` | reference |  | SMIv2 MIB: mapping basis, not a YANG capability (plan §9) |
| `MPLS-FTN-STD-MIB` | reference |  | SMIv2 MIB: mapping basis, not a YANG capability (plan §9) |
| `MPLS-L3VPN-STD-MIB` | reference |  | SMIv2 MIB: mapping basis, not a YANG capability (plan §9) |
| `MPLS-LDP-ATM-STD-MIB` | reference |  | SMIv2 MIB: mapping basis, not a YANG capability (plan §9) |
| `MPLS-LDP-FRAME-RELAY-STD-MIB` | reference |  | SMIv2 MIB: mapping basis, not a YANG capability (plan §9) |
| `MPLS-LDP-GENERIC-STD-MIB` | reference |  | SMIv2 MIB: mapping basis, not a YANG capability (plan §9) |
| `MPLS-LDP-STD-MIB` | reference |  | SMIv2 MIB: mapping basis, not a YANG capability (plan §9) |
| `MPLS-LSR-STD-MIB` | reference |  | SMIv2 MIB: mapping basis, not a YANG capability (plan §9) |
| `MPLS-TC-STD-MIB` | reference |  | SMIv2 MIB: mapping basis, not a YANG capability (plan §9) |
| `OSPF-MIB` | reference |  | SMIv2 MIB: mapping basis, not a YANG capability (plan §9) |
| `OSPF-TRAP-MIB` | reference |  | SMIv2 MIB: mapping basis, not a YANG capability (plan §9) |
| `OSPFV3-MIB` | reference |  | SMIv2 MIB: mapping basis, not a YANG capability (plan §9) |
| `P-BRIDGE-MIB` | reference |  | SMIv2 MIB: mapping basis, not a YANG capability (plan §9) |
| `PIM-STD-MIB` | reference |  | SMIv2 MIB: mapping basis, not a YANG capability (plan §9) |
| `Q-BRIDGE-MIB` | reference |  | SMIv2 MIB: mapping basis, not a YANG capability (plan §9) |
| `RIPv2-MIB` | reference |  | SMIv2 MIB: mapping basis, not a YANG capability (plan §9) |
| `SNMPv2-MIB` | reference |  | SMIv2 MIB: mapping basis, not a YANG capability (plan §9) |
| `UUID-TC-MIB` | reference |  | SMIv2 MIB: mapping basis, not a YANG capability (plan §9) |
| `VRRP-MIB` | reference |  | SMIv2 MIB: mapping basis, not a YANG capability (plan §9) |
| `VRRPV3-MIB` | reference |  | SMIv2 MIB: mapping basis, not a YANG capability (plan §9) |
| `ietf-yang-smiv2` | runtime-baseline | RFC6643 | published RFC, newest published revision |

## 传送/OTN 与 L1

| 模块 | 判定 | 依据 | 理由 |
|---|---|---|---|
| `ietf-fgotn-topology` | candidate-upgrade | draft-ietf-ccamp-fgotn-yang-02 | active Internet-Draft; enters a set only after review |
| `ietf-fgotn-tunnel` | candidate-upgrade | draft-ietf-ccamp-fgotn-yang-02 | active Internet-Draft; enters a set only after review |
| `ietf-fgotn-types` | candidate-upgrade | draft-ietf-ccamp-fgotn-yang-02 | active Internet-Draft; enters a set only after review |
| `ietf-l1csm` | candidate-upgrade | draft-ietf-ccamp-l1csm-yang-28 | active Internet-Draft; enters a set only after review |
| `ietf-layer1-types` | candidate-upgrade | draft-ietf-ccamp-layer1-types-20 | active Internet-Draft; enters a set only after review |
| `ietf-otn-topology` | candidate-upgrade | draft-ietf-ccamp-otn-topo-yang-21 | active Internet-Draft; enters a set only after review |
| `ietf-otn-tunnel` | candidate-upgrade | draft-ietf-ccamp-otn-tunnel-model-26 | active Internet-Draft; enters a set only after review |

## 传送/以太与客户信号

| 模块 | 判定 | 依据 | 理由 |
|---|---|---|---|
| `ietf-eth-te-topology` | candidate-upgrade | draft-ietf-ccamp-eth-client-te-topo-yang-11 | active Internet-Draft; enters a set only after review |
| `ietf-eth-tran-service` | candidate-upgrade | draft-ietf-ccamp-client-signal-yang-18 | active Internet-Draft; enters a set only after review |
| `ietf-eth-tran-types` | candidate-upgrade | draft-ietf-ccamp-client-signal-yang-18 | active Internet-Draft; enters a set only after review |
| `ietf-flexe` | candidate-upgrade | draft-ietf-ccamp-flexe-yang-cm-11 | active Internet-Draft; enters a set only after review |
| `ietf-trans-client-service` | candidate-upgrade | draft-ietf-ccamp-client-signal-yang-18 | active Internet-Draft; enters a set only after review |
| `ietf-trans-client-svc-types` | candidate-upgrade | draft-ietf-ccamp-client-signal-yang-18 | active Internet-Draft; enters a set only after review |

## 传送/光层 L0

| 模块 | 判定 | 依据 | 理由 |
|---|---|---|---|
| `ietf-flexi-grid-topology` | candidate-upgrade | draft-ietf-ccamp-flexigrid-yang-19 | active Internet-Draft; enters a set only after review |
| `ietf-layer0-types` | runtime-baseline | RFC9093 | published RFC, newest published revision（另有 ietf-layer0-types@2026-06-12.yang：candidate-upgrade） |
| `ietf-optical-impairment-topology` | candidate-upgrade | draft-ietf-ccamp-optical-impairment-topology-yang-24 | active Internet-Draft; enters a set only after review |
| `ietf-wdm-interface` | candidate-upgrade | draft-ietf-ccamp-dwdm-if-param-yang-16 | active Internet-Draft; enters a set only after review |
| `ietf-wdm-tunnel` | candidate-upgrade | draft-ietf-ccamp-wdm-tunnel-yang-08 | active Internet-Draft; enters a set only after review |
| `ietf-wson-topology` | runtime-baseline | RFC9094 | published RFC, newest published revision |

## 传送/微波

| 模块 | 判定 | 依据 | 理由 |
|---|---|---|---|
| `ietf-interface-protection` | runtime-baseline | RFC8561 | published RFC, newest published revision |
| `ietf-microwave-radio-link` | runtime-baseline | RFC8561 | published RFC, newest published revision |
| `ietf-microwave-topology` | runtime-baseline | RFC9656 | published RFC, newest published revision |
| `ietf-microwave-types` | runtime-baseline | RFC8561 | published RFC, newest published revision |

## 保障与故障

| 模块 | 判定 | 依据 | 理由 |
|---|---|---|---|
| `ietf-alarms` | runtime-baseline | RFC8632 | published RFC, newest published revision（另有 ietf-alarms@2019-09-11.yang：reference） |
| `ietf-alarms-x733` | runtime-baseline | RFC8632 | published RFC, newest published revision |
| `ietf-incident` | candidate-upgrade | draft-ietf-nmop-network-incident-yang-17 | active Internet-Draft; enters a set only after review |
| `ietf-service-assurance` | runtime-baseline | RFC9418 | published RFC, newest published revision |
| `ietf-service-assurance-device` | runtime-baseline | RFC9418 | published RFC, newest published revision |
| `ietf-service-assurance-interface` | runtime-baseline | RFC9418 | published RFC, newest published revision |

## 切片与服务映射

| 模块 | 判定 | 依据 | 理由 |
|---|---|---|---|
| `ietf-l1csm-te-service-mapping` | candidate-upgrade | draft-ietf-teas-te-service-mapping-yang-19 | active Internet-Draft; enters a set only after review |
| `ietf-l2nm-te-service-mapping` | candidate-upgrade | draft-ietf-teas-te-service-mapping-yang-19 | active Internet-Draft; enters a set only after review |
| `ietf-l2sm-te-service-mapping` | candidate-upgrade | draft-ietf-teas-te-service-mapping-yang-19 | active Internet-Draft; enters a set only after review |
| `ietf-l3nm-te-service-mapping` | candidate-upgrade | draft-ietf-teas-te-service-mapping-yang-19 | active Internet-Draft; enters a set only after review |
| `ietf-l3sm-te-service-mapping` | candidate-upgrade | draft-ietf-teas-te-service-mapping-yang-19 | active Internet-Draft; enters a set only after review |
| `ietf-network-slice-service` | candidate-upgrade | draft-ietf-teas-ietf-network-slice-nbi-yang-26 | active Internet-Draft; enters a set only after review |
| `ietf-nrp` | candidate-upgrade | draft-ietf-teas-nrp-yang-06 | active Internet-Draft; enters a set only after review |
| `ietf-nrp-device` | candidate-upgrade | draft-ietf-teas-nrp-yang-06 | active Internet-Draft; enters a set only after review |
| `ietf-te-service-mapping-types` | candidate-upgrade | draft-ietf-teas-te-service-mapping-yang-19 | active Internet-Draft; enters a set only after review |
| `ietf-vn` | runtime-baseline | RFC9731 | published RFC, newest published revision |

## 地址配置

| 模块 | 判定 | 依据 | 理由 |
|---|---|---|---|
| `ietf-dhcpv6-client` | runtime-baseline | RFC9243 | published RFC, newest published revision |
| `ietf-dhcpv6-common` | runtime-baseline | RFC9243 | published RFC, newest published revision |
| `ietf-dhcpv6-relay` | runtime-baseline | RFC9243 | published RFC, newest published revision |
| `ietf-dhcpv6-server` | runtime-baseline | RFC9243 | published RFC, newest published revision |

## 基础拓扑

| 模块 | 判定 | 依据 | 理由 |
|---|---|---|---|
| `ietf-dc-fabric-topology` | runtime-baseline | RFC8542 | published RFC, newest published revision |
| `ietf-dc-fabric-topology-state` | runtime-baseline | RFC8542 | published RFC, newest published revision |
| `ietf-dc-fabric-types` | runtime-baseline | RFC8542 | published RFC, newest published revision |
| `ietf-l2-topology` | runtime-baseline | RFC8944 | published RFC, newest published revision |
| `ietf-l2-topology-state` | runtime-baseline | RFC8944 | published RFC, newest published revision |
| `ietf-l3-isis-topology` | reference | draft-ogondio-nmop-isis-topology-01 | expired or replaced Internet-Draft |
| `ietf-l3-ospf-topology` | reference | draft-ogondio-nmop-ospf-topology-01 | expired or replaced Internet-Draft |
| `ietf-l3-unicast-topology` | runtime-baseline | RFC8346 | published RFC, newest published revision |
| `ietf-l3-unicast-topology-state` | runtime-baseline | RFC8346 | published RFC, newest published revision |
| `ietf-network` | runtime-baseline | RFC8345 | published RFC, newest published revision |
| `ietf-network-state` | runtime-baseline | RFC8345 | published RFC, newest published revision |
| `ietf-network-topology` | runtime-baseline | RFC8345 | published RFC, newest published revision |
| `ietf-network-topology-state` | runtime-baseline | RFC8345 | published RFC, newest published revision |

## 多播与隧道

| 模块 | 判定 | 依据 | 理由 |
|---|---|---|---|
| `ietf-amt` | candidate-upgrade | draft-ietf-mboned-amt-yang-09 | active Internet-Draft; enters a set only after review |

## 接口与封装

| 模块 | 判定 | 依据 | 理由 |
|---|---|---|---|
| `ietf-if-ethernet-like` | candidate-upgrade | draft-ietf-netmod-intf-ext-yang-19 | active Internet-Draft; enters a set only after review |
| `ietf-if-extensions` | candidate-upgrade | draft-ietf-netmod-intf-ext-yang-19 | active Internet-Draft; enters a set only after review |
| `ietf-if-flexible-encapsulation` | candidate-upgrade | draft-ietf-netmod-sub-intf-vlan-model-18 | active Internet-Draft; enters a set only after review |
| `ietf-if-vlan-encapsulation` | candidate-upgrade | draft-ietf-netmod-sub-intf-vlan-model-18 | active Internet-Draft; enters a set only after review |

## 接口类型

| 模块 | 判定 | 依据 | 理由 |
|---|---|---|---|
| `iana-tunnel-type` | runtime-baseline | RFC8675 | published RFC, newest published revision（另有 iana-tunnel-type@2019-11-16.yang：reference） |

## 时间同步

| 模块 | 判定 | 依据 | 理由 |
|---|---|---|---|
| `ietf-ntp` | runtime-baseline | RFC9249 | published RFC, newest published revision |
| `ietf-ptp` | runtime-baseline | RFC8575 | published RFC, newest published revision |

## 服务链

| 模块 | 判定 | 依据 | 理由 |
|---|---|---|---|
| `rendered-service-path` | reference | draft-penno-sfc-yang-15 | expired or replaced Internet-Draft |
| `service-function` | reference | draft-penno-sfc-yang-15 | expired or replaced Internet-Draft |
| `service-function-chain` | reference | draft-penno-sfc-yang-15 | expired or replaced Internet-Draft |
| `service-function-forwarder` | reference | draft-penno-sfc-yang-15 | expired or replaced Internet-Draft |
| `service-function-path` | reference | draft-penno-sfc-yang-15 | expired or replaced Internet-Draft |
| `service-function-path-metadata` | reference | draft-penno-sfc-yang-15 | expired or replaced Internet-Draft |
| `service-function-type` | reference | draft-penno-sfc-yang-15 | expired or replaced Internet-Draft |
| `service-locator` | reference | draft-penno-sfc-yang-15 | expired or replaced Internet-Draft |
| `service-statistics` | reference | draft-penno-sfc-yang-15 | expired or replaced Internet-Draft |
| `sfc-common` | reference | draft-penno-sfc-yang-15 | expired or replaced Internet-Draft |

## 策略与安全

| 模块 | 判定 | 依据 | 理由 |
|---|---|---|---|
| `iana-dots-signal-channel` | runtime-baseline | RFC9132 | published RFC, newest published revision |
| `iana-icmpv4-types` | runtime-baseline | RFC9899 | published RFC, newest published revision |
| `iana-icmpv6-types` | runtime-baseline | RFC9899 | published RFC, newest published revision |
| `iana-ipv6-ext-types` | runtime-baseline | RFC9899 | published RFC, newest published revision |
| `iana-tls-profile` | runtime-baseline | RFC9761 | published RFC, newest published revision |
| `ietf-access-control-list` | runtime-baseline | RFC8519 | published RFC, newest published revision |
| `ietf-acl-enh` | runtime-baseline | RFC9899 | published RFC, newest published revision |
| `ietf-acl-tls` | runtime-baseline | RFC9761 | published RFC, newest published revision |
| `ietf-acldns` | runtime-baseline | RFC8520 | published RFC, newest published revision |
| `ietf-diffserv` | candidate-upgrade | draft-ietf-rtgwg-qos-model-16 | active Internet-Draft; enters a set only after review |
| `ietf-dots-data-channel` | runtime-baseline | RFC8783 | published RFC, newest published revision |
| `ietf-dots-signal-channel` | runtime-baseline | RFC9132 | published RFC, newest published revision |
| `ietf-ethertypes` | runtime-baseline | RFC8519 | published RFC, newest published revision |
| `ietf-i2nsf-capability` | candidate-upgrade | draft-ietf-i2nsf-capability-data-model-32 | active Internet-Draft; enters a set only after review |
| `ietf-i2nsf-cons-facing-interface` | candidate-upgrade | draft-ietf-i2nsf-consumer-facing-interface-dm-31 | active Internet-Draft; enters a set only after review |
| `ietf-i2nsf-ike` | runtime-baseline | RFC9061 | published RFC, newest published revision |
| `ietf-i2nsf-ikec` | runtime-baseline | RFC9061 | published RFC, newest published revision |
| `ietf-i2nsf-ikeless` | runtime-baseline | RFC9061 | published RFC, newest published revision |
| `ietf-i2nsf-monitoring-interface` | candidate-upgrade | draft-ietf-i2nsf-nsf-monitoring-data-model-20 | active Internet-Draft; enters a set only after review |
| `ietf-i2nsf-nsf-facing-interface` | candidate-upgrade | draft-ietf-i2nsf-nsf-facing-interface-dm-29 | active Internet-Draft; enters a set only after review |
| `ietf-i2nsf-registration-interface` | candidate-upgrade | draft-ietf-i2nsf-registration-interface-dm-26 | active Internet-Draft; enters a set only after review |
| `ietf-ipsec-iptfs` | runtime-baseline | RFC9348 | published RFC, newest published revision |
| `ietf-mud` | runtime-baseline | RFC8520 | published RFC, newest published revision |
| `ietf-mud-tls` | runtime-baseline | RFC9761 | published RFC, newest published revision |
| `ietf-mud-transparency` | runtime-baseline | RFC9472 | published RFC, newest published revision |
| `ietf-nat` | runtime-baseline | RFC8512 | published RFC, newest published revision |
| `ietf-packet-fields` | runtime-baseline | RFC8519 | published RFC, newest published revision |
| `ietf-qos-action` | candidate-upgrade | draft-ietf-rtgwg-qos-model-16 | active Internet-Draft; enters a set only after review |
| `ietf-qos-oper` | candidate-upgrade | draft-ietf-rtgwg-qos-model-16 | active Internet-Draft; enters a set only after review |
| `ietf-qos-types` | candidate-upgrade | draft-ietf-rtgwg-qos-model-16 | active Internet-Draft; enters a set only after review |
| `ietf-queue-policy` | candidate-upgrade | draft-ietf-rtgwg-qos-model-16 | active Internet-Draft; enters a set only after review |
| `ietf-scheduler-policy` | candidate-upgrade | draft-ietf-rtgwg-qos-model-16 | active Internet-Draft; enters a set only after review |
| `ietf-traffic-policy` | candidate-upgrade | draft-ietf-rtgwg-qos-model-16 | active Internet-Draft; enters a set only after review |
| `ietf-ucl-acl` | candidate-upgrade | draft-ietf-opsawg-ucl-acl-15 | active Internet-Draft; enters a set only after review |

## 设备、接口、实例与系统

| 模块 | 判定 | 依据 | 理由 |
|---|---|---|---|
| `iana-crypt-hash` | runtime-baseline | RFC7317 | published RFC, newest published revision |
| `iana-hardware` | runtime-baseline | RFC8348 | published RFC, newest published revision |
| `iana-if-type` | runtime-baseline | RFC7224 | published RFC, newest published revision（另有 iana-if-type@2014-05-08.yang：reference） |
| `ietf-hardware` | runtime-baseline | RFC8348 | published RFC, newest published revision |
| `ietf-hardware-state` | runtime-baseline | RFC8348 | published RFC, newest published revision |
| `ietf-interfaces` | runtime-baseline | RFC8343 | published RFC, newest published revision |
| `ietf-ip` | runtime-baseline | RFC8344 | published RFC, newest published revision |
| `ietf-logical-network-element` | runtime-baseline | RFC8530 | published RFC, newest published revision |
| `ietf-network-instance` | runtime-baseline | RFC8529 | published RFC, newest published revision |
| `ietf-syslog` | runtime-baseline | RFC9742 | published RFC, newest published revision |
| `ietf-system` | runtime-baseline | RFC7317 | published RFC, newest published revision |
| `ietf-system-tacacs-plus` | runtime-baseline | RFC9950 | published RFC, newest published revision |

## 语言与数据存储

| 模块 | 判定 | 依据 | 理由 |
|---|---|---|---|
| `iana-template` | not-applicable | RFC9907 | RFC 9907 authoring template; placeholder revision dates by design（另有 iana-template@unknown.yang：not-applicable） |
| `ietf-datastores` | runtime-baseline | RFC8342 | published RFC, newest published revision |
| `ietf-inet-types` | runtime-baseline | RFC9911 | published RFC, newest published revision（另有 ietf-inet-types@2010-09-24.yang：runtime-baseline；ietf-inet-types@2013-07-15.yang：runtime-baseline） |
| `ietf-module-tags` | runtime-baseline | RFC8819 | published RFC, newest published revision |
| `ietf-module-tags-state` | runtime-baseline | RFC8819 | published RFC, newest published revision |
| `ietf-origin` | runtime-baseline | RFC8342 | published RFC, newest published revision |
| `ietf-sid-file` | runtime-baseline | RFC9595 | published RFC, newest published revision |
| `ietf-system-datastore` | runtime-baseline | RFC10016 | published RFC, newest published revision |
| `ietf-template` | not-applicable | RFC9907 | RFC 9907 authoring template; placeholder revision dates by design |
| `ietf-yang-instance-data` | runtime-baseline | RFC9195 | published RFC, newest published revision |
| `ietf-yang-library` | runtime-baseline | RFC8525 | published RFC, newest published revision |
| `ietf-yang-library-augmentedby` | runtime-baseline | RFC10035 | published RFC, newest published revision |
| `ietf-yang-library-semver` | candidate-upgrade | draft-ietf-netmod-yang-semver-28 | active Internet-Draft; enters a set only after review |
| `ietf-yang-library-status` | candidate-upgrade | draft-ietf-netmod-yang-module-versioning-17 | active Internet-Draft; enters a set only after review |
| `ietf-yang-metadata` | runtime-baseline | RFC7952 | published RFC, newest published revision |
| `ietf-yang-revisions` | candidate-upgrade | draft-ietf-netmod-yang-module-versioning-17 | active Internet-Draft; enters a set only after review |
| `ietf-yang-schema-mount` | runtime-baseline | RFC8528 | published RFC, newest published revision |
| `ietf-yang-semver` | runtime-baseline | draft-ietf-netmod-yang-semver-28 | active Internet-Draft; enters a set only after review; required by baseline iana-bgp-afi-safi-types@2026-08-14.yang, iana-bgp-capabilities@2026-08-14.yang, iana-bgp-community-types@2026-08-14.yang, iana-bgp-notification@2026-08-14.yang, iana-bgp-rib-types@2026-08-14.yang, ietf-bgp-capabilities@2026-08-14.yang |
| `ietf-yang-structure-ext` | runtime-baseline | RFC8791 | published RFC, newest published revision |
| `ietf-yang-types` | runtime-baseline | RFC9911 | published RFC, newest published revision（另有 ietf-yang-types@2013-07-15.yang：runtime-baseline） |

## 资源清单

| 模块 | 判定 | 依据 | 理由 |
|---|---|---|---|
| `ietf-geo-location` | runtime-baseline | RFC9179 | published RFC, newest published revision |

## 路径计算/跨域

| 模块 | 判定 | 依据 | 理由 |
|---|---|---|---|
| `ietf-otn-path-computation` | candidate-upgrade | draft-ietf-ccamp-otn-path-computation-yang-09 | active Internet-Draft; enters a set only after review |
| `ietf-pcep` | runtime-baseline | RFC9826 | published RFC, newest published revision |
| `ietf-pcep-stats` | runtime-baseline | RFC9826 | published RFC, newest published revision |
| `ietf-te-path-computation` | candidate-upgrade | draft-ietf-teas-yang-path-computation-28 | active Internet-Draft; enters a set only after review |
| `ietf-wdm-path-computation` | candidate-upgrade | draft-ietf-ccamp-optical-path-computation-yang-09 | active Internet-Draft; enters a set only after review |

## 路由、多播与标签协议

| 模块 | 判定 | 依据 | 理由 |
|---|---|---|---|
| `iana-bfd-types` | runtime-baseline | RFC9127 | published RFC, newest published revision（另有 iana-bfd-types@2021-10-21.yang：reference） |
| `iana-bgp-afi-safi-types` | runtime-baseline | draft-ietf-idr-bgp-model-21 | fixed draft: BGP model, fixed draft per plan M4 |
| `iana-bgp-capabilities` | runtime-baseline | draft-ietf-idr-bgp-model-21 | fixed draft: BGP model, fixed draft per plan M4 |
| `iana-bgp-community-types` | runtime-baseline | draft-ietf-idr-bgp-model-21 | fixed draft: BGP model, fixed draft per plan M4 |
| `iana-bgp-notification` | runtime-baseline | draft-ietf-idr-bgp-model-21 | fixed draft: BGP model, fixed draft per plan M4 |
| `iana-bgp-rib-types` | runtime-baseline | draft-ietf-idr-bgp-model-21 | fixed draft: BGP model, fixed draft per plan M4 |
| `iana-igp-algo-types` | candidate-upgrade | draft-ietf-lsr-ospf-flex-algo-yang-16 | active Internet-Draft; enters a set only after review |
| `iana-igp-link-attr-apps` | candidate-upgrade | draft-ietf-lsr-ospf-flex-algo-yang-16 | active Internet-Draft; enters a set only after review |
| `iana-igp-metric-types` | candidate-upgrade | draft-ietf-lsr-ospf-flex-algo-yang-16 | active Internet-Draft; enters a set only after review |
| `iana-ospf-functional-cap-bits` | runtime-baseline | RFC10041 | published RFC, newest published revision |
| `iana-routing-types` | runtime-baseline | RFC8294 | published RFC, newest published revision（另有 iana-routing-types@2017-12-04.yang：reference） |
| `ietf-babel` | runtime-baseline | RFC9647 | published RFC, newest published revision |
| `ietf-bfd-large` | runtime-baseline | RFC9764 | published RFC, newest published revision |
| `ietf-bfd-met-keyed-isaac` | runtime-baseline | RFC9986 | published RFC, newest published revision |
| `ietf-bfd-opt-auth` | runtime-baseline | RFC9985 | published RFC, newest published revision |
| `ietf-bfd-stability` | runtime-baseline | RFC9978 | published RFC, newest published revision |
| `ietf-bfd-unsolicited` | runtime-baseline | RFC9468 | published RFC, newest published revision |
| `ietf-bgp` | runtime-baseline | draft-ietf-idr-bgp-model-21 | fixed draft: BGP model, fixed draft per plan M4 |
| `ietf-bgp-community-types` | runtime-baseline | draft-ietf-idr-bgp-model-21 | fixed draft: BGP model, fixed draft per plan M4 |
| `ietf-bgp-ls` | candidate-upgrade | draft-ietf-lsvr-bgp-ls-yang-04 | active Internet-Draft; enters a set only after review |
| `ietf-bgp-ls-topo-types` | candidate-upgrade | draft-ietf-lsvr-bgp-ls-yang-04 | active Internet-Draft; enters a set only after review |
| `ietf-bgp-lsdb` | candidate-upgrade | draft-ietf-lsvr-bgp-ls-yang-04 | active Internet-Draft; enters a set only after review |
| `ietf-bgp-policy` | runtime-baseline | draft-ietf-idr-bgp-model-21 | fixed draft: BGP model, fixed draft per plan M4 |
| `ietf-bgp-session-security` | runtime-baseline | draft-ietf-idr-bgp-model-21 | fixed draft: BGP model, fixed draft per plan M4 |
| `ietf-bgp-types` | runtime-baseline | draft-ietf-idr-bgp-model-21 | fixed draft: BGP model, fixed draft per plan M4 |
| `ietf-bier` | reference | draft-ietf-bier-bier-yang-10 | expired or replaced Internet-Draft |
| `ietf-i2rs-rib` | runtime-baseline | RFC8431 | published RFC, newest published revision |
| `ietf-igmp-mld` | runtime-baseline | RFC8652 | published RFC, newest published revision |
| `ietf-igmp-mld-proxy` | runtime-baseline | RFC9398 | published RFC, newest published revision |
| `ietf-igmp-mld-snooping` | runtime-baseline | RFC9166 | published RFC, newest published revision |
| `ietf-ipv4-unicast-routing` | runtime-baseline | RFC8349 | published RFC, newest published revision |
| `ietf-ipv6-unicast-routing` | runtime-baseline | RFC8349 | published RFC, newest published revision |
| `ietf-isis` | runtime-baseline | RFC9130 | published RFC, newest published revision |
| `ietf-isis-flex-algo` | candidate-upgrade | draft-ietf-lsr-isis-flex-algo-yang-21 | active Internet-Draft; enters a set only after review |
| `ietf-isis-link-attr` | candidate-upgrade | draft-ietf-lsr-isis-flex-algo-yang-21 | active Internet-Draft; enters a set only after review |
| `ietf-isis-reverse-metric` | runtime-baseline | RFC9194 | published RFC, newest published revision |
| `ietf-key-chain` | runtime-baseline | RFC8177 | published RFC, newest published revision |
| `ietf-lisp` | candidate-upgrade | draft-ietf-lisp-yang-25 | active Internet-Draft; enters a set only after review |
| `ietf-lisp-address-types` | candidate-upgrade | draft-ietf-lisp-yang-25 | active Internet-Draft; enters a set only after review |
| `ietf-lisp-etr` | candidate-upgrade | draft-ietf-lisp-yang-25 | active Internet-Draft; enters a set only after review |
| `ietf-lisp-itr` | candidate-upgrade | draft-ietf-lisp-yang-25 | active Internet-Draft; enters a set only after review |
| `ietf-lisp-mapresolver` | candidate-upgrade | draft-ietf-lisp-yang-25 | active Internet-Draft; enters a set only after review |
| `ietf-lisp-mapserver` | candidate-upgrade | draft-ietf-lisp-yang-25 | active Internet-Draft; enters a set only after review |
| `ietf-mpls` | runtime-baseline | RFC8960 | published RFC, newest published revision |
| `ietf-mpls-ldp` | runtime-baseline | RFC9070 | published RFC, newest published revision |
| `ietf-mpls-ldp-extended` | runtime-baseline | RFC9070 | published RFC, newest published revision |
| `ietf-mpls-mldp` | candidate-upgrade | draft-ietf-mpls-mldp-yang-16 | active Internet-Draft; enters a set only after review |
| `ietf-mpls-mldp-extended` | candidate-upgrade | draft-ietf-mpls-mldp-yang-16 | active Internet-Draft; enters a set only after review |
| `ietf-msdp` | runtime-baseline | RFC8916 | published RFC, newest published revision |
| `ietf-ospf` | runtime-baseline | RFC9129 | published RFC, newest published revision |
| `ietf-ospf-admin-tags` | runtime-baseline | RFC9825 | published RFC, newest published revision |
| `ietf-ospf-anycast-flag` | runtime-baseline | RFC9983 | published RFC, newest published revision |
| `ietf-ospf-flex-algo` | candidate-upgrade | draft-ietf-lsr-ospf-flex-algo-yang-16 | active Internet-Draft; enters a set only after review |
| `ietf-ospf-functional-capability` | runtime-baseline | RFC10041 | published RFC, newest published revision |
| `ietf-ospf-link-attr` | candidate-upgrade | draft-ietf-lsr-ospf-flex-algo-yang-16 | active Internet-Draft; enters a set only after review |
| `ietf-ospf-unreachable-links` | runtime-baseline | RFC10041 | published RFC, newest published revision |
| `ietf-ospfv3-extended-lsa` | runtime-baseline | RFC9587 | published RFC, newest published revision |
| `ietf-pim-base` | runtime-baseline | RFC9128 | published RFC, newest published revision |
| `ietf-pim-bidir` | runtime-baseline | RFC9128 | published RFC, newest published revision |
| `ietf-pim-dm` | runtime-baseline | RFC9128 | published RFC, newest published revision |
| `ietf-pim-rp` | runtime-baseline | RFC9128 | published RFC, newest published revision |
| `ietf-pim-sm` | runtime-baseline | RFC9128 | published RFC, newest published revision |
| `ietf-rib-extension` | runtime-baseline | RFC9403 | published RFC, newest published revision |
| `ietf-rift` | runtime-baseline | RFC9719 | published RFC, newest published revision |
| `ietf-rip` | runtime-baseline | RFC8695 | published RFC, newest published revision |
| `ietf-routing` | runtime-baseline | RFC8349 | published RFC, newest published revision |
| `ietf-routing-policy` | runtime-baseline | RFC9067 | published RFC, newest published revision |
| `ietf-routing-types` | runtime-baseline | RFC8294 | published RFC, newest published revision |
| `ietf-rsvp` | candidate-upgrade | draft-ietf-teas-yang-rsvp-21 | active Internet-Draft; enters a set only after review |
| `ietf-rsvp-extended` | candidate-upgrade | draft-ietf-teas-yang-rsvp-21 | active Internet-Draft; enters a set only after review |
| `ietf-srv6-static` | reference | draft-ietf-spring-srv6-yang-05 | expired or replaced Internet-Draft |
| `ietf-vrrp` | runtime-baseline | RFC8347 | published RFC, newest published revision（另有 ietf-vrrp@2026-06-18.yang：candidate-upgrade） |

## 过渡与软线

| 模块 | 判定 | 依据 | 理由 |
|---|---|---|---|
| `ietf-dslite` | runtime-baseline | RFC8513 | published RFC, newest published revision |
| `ietf-softwire-br` | runtime-baseline | RFC8676 | published RFC, newest published revision |
| `ietf-softwire-ce` | runtime-baseline | RFC8676 | published RFC, newest published revision |
| `ietf-softwire-common` | runtime-baseline | RFC8676 | published RFC, newest published revision |

## 采集与管理

| 模块 | 判定 | 依据 | 理由 |
|---|---|---|---|
| `ietf-adaptive-subscription` | candidate-upgrade | draft-ietf-netconf-adaptive-subscription-18 | active Internet-Draft; enters a set only after review |
| `ietf-netconf` | runtime-baseline | RFC6241 | published RFC, newest published revision |
| `ietf-netconf-nmda` | runtime-baseline | RFC8526 | published RFC, newest published revision |
| `ietf-nmda-compare` | runtime-baseline | RFC9144 | published RFC, newest published revision |
| `ietf-restconf` | runtime-baseline | RFC8040 | published RFC, newest published revision |
| `ietf-restconf-monitoring` | runtime-baseline | RFC8040 | published RFC, newest published revision |
| `ietf-restconf-subscribed-notifications` | runtime-baseline | RFC8650 | published RFC, newest published revision |
| `ietf-snmp` | runtime-baseline | RFC7407 | published RFC, newest published revision |
| `ietf-snmp-common` | runtime-baseline | RFC7407 | published RFC, newest published revision |
| `ietf-snmp-community` | runtime-baseline | RFC7407 | published RFC, newest published revision |
| `ietf-snmp-engine` | runtime-baseline | RFC7407 | published RFC, newest published revision |
| `ietf-snmp-notification` | runtime-baseline | RFC7407 | published RFC, newest published revision |
| `ietf-snmp-proxy` | runtime-baseline | RFC7407 | published RFC, newest published revision |
| `ietf-snmp-ssh` | runtime-baseline | RFC7407 | published RFC, newest published revision |
| `ietf-snmp-target` | runtime-baseline | RFC7407 | published RFC, newest published revision |
| `ietf-snmp-tls` | runtime-baseline | RFC7407 | published RFC, newest published revision |
| `ietf-snmp-tsm` | runtime-baseline | RFC7407 | published RFC, newest published revision |
| `ietf-snmp-usm` | runtime-baseline | RFC7407 | published RFC, newest published revision |
| `ietf-snmp-vacm` | runtime-baseline | RFC7407 | published RFC, newest published revision |
| `ietf-subscribed-notifications` | runtime-baseline | RFC8639 | published RFC, newest published revision |
| `ietf-x509-cert-to-name` | runtime-baseline | RFC7407 | published RFC, newest published revision |
| `ietf-yang-push` | runtime-baseline | RFC8641 | published RFC, newest published revision |

## 隧道/基础

| 模块 | 判定 | 依据 | 理由 |
|---|---|---|---|
| `ietf-te` | candidate-upgrade | draft-ietf-teas-yang-te-44 | active Internet-Draft; enters a set only after review |
| `ietf-te-device` | candidate-upgrade | draft-ietf-teas-yang-te-44 | active Internet-Draft; enters a set only after review |

## （仅作依赖）

| 模块 | 判定 | 依据 | 理由 |
|---|---|---|---|
| `iana-http-versions` | candidate-upgrade | draft-ietf-netconf-http-client-server-33 | active Internet-Draft; enters a set only after review |
| `iana-tls-cipher-suite-algs` | runtime-baseline | RFC9645 | published RFC, newest published revision |
| `ieee1588-ptp-tt` | runtime-baseline | ieee1588-ptp-tt@2023-08-14.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-dot1ab-lldp` | runtime-baseline | ieee802-dot1ab-lldp@2022-03-15.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-dot1ab-types` | runtime-baseline | ieee802-dot1ab-types@2022-03-15.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-dot1ae-pry` | runtime-baseline | ieee802-dot1ae-pry@2022-06-17.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-dot1ae-secy` | runtime-baseline | ieee802-dot1ae-secy@2022-06-14.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-dot1as-ftt` | runtime-baseline | ieee802-dot1as-ftt@2026-02-11.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-dot1as-gptp` | runtime-baseline | ieee802-dot1as-gptp@2025-12-10.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-dot1as-hs` | runtime-baseline | ieee802-dot1as-hs@2025-12-10.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-dot1ax-drni` | runtime-baseline | ieee802-dot1ax-drni@2025-09-30.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-dot1ax-linkagg` | runtime-baseline | ieee802-dot1ax-linkagg@2025-09-30.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-dot1ax-types` | runtime-baseline | ieee802-dot1ax-types@2025-09-30.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-dot1cb-frer` | runtime-baseline | ieee802-dot1cb-frer@2021-12-08.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-dot1cb-frer-types` | runtime-baseline | ieee802-dot1cb-frer-types@2021-12-08.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-dot1cb-mask-and-match` | runtime-baseline | ieee802-dot1cb-mask-and-match@2021-12-09.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-dot1cb-stream-identification` | runtime-baseline | ieee802-dot1cb-stream-identification@2021-12-08.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-dot1cb-stream-identification-types` | runtime-baseline | ieee802-dot1cb-stream-identification-types@2021-12-09.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-dot1cs-lrp` | runtime-baseline | ieee802-dot1cs-lrp@2024-02-15.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-dot1dc-ats-if` | runtime-baseline | ieee802-dot1dc-ats-if@2024-09-26.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-dot1dc-cbsa-if` | runtime-baseline | ieee802-dot1dc-cbsa-if@2024-09-26.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-dot1dc-gfqos` | runtime-baseline | ieee802-dot1dc-gfqos@2024-09-26.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-dot1dc-preemption-if` | runtime-baseline | ieee802-dot1dc-preemption-if@2024-09-26.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-dot1dc-psfp-sys` | runtime-baseline | ieee802-dot1dc-psfp-sys@2024-09-26.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-dot1dc-sched-if` | runtime-baseline | ieee802-dot1dc-sched-if@2024-09-26.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-dot1q-ats` | runtime-baseline | ieee802-dot1q-ats@2023-07-03.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-dot1q-ats-bridge` | runtime-baseline | ieee802-dot1q-ats-bridge@2023-07-03.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-dot1q-bridge` | runtime-baseline | ieee802-dot1q-bridge@2023-10-26.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-dot1q-cbsa` | runtime-baseline | ieee802-dot1q-cbsa@2024-08-07.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-dot1q-cbsa-bridge` | runtime-baseline | ieee802-dot1q-cbsa-bridge@2024-08-07.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-dot1q-cfm` | runtime-baseline | ieee802-dot1q-cfm@2022-01-19.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-dot1q-cfm-alarm` | runtime-baseline | ieee802-dot1q-cfm-alarm@2022-01-19.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-dot1q-cfm-bridge` | runtime-baseline | ieee802-dot1q-cfm-bridge@2022-01-19.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-dot1q-cfm-types` | runtime-baseline | ieee802-dot1q-cfm-types@2022-10-29.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-dot1q-cnc-config` | runtime-baseline | ieee802-dot1q-cnc-config@2024-01-31.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-dot1q-congestion-isolation` | runtime-baseline | ieee802-dot1q-congestion-isolation@2023-07-03.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-dot1q-congestion-isolation-bridge` | runtime-baseline | ieee802-dot1q-congestion-isolation-bridge@2023-07-03.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-dot1q-lldp-basic-tlv` | runtime-baseline | ieee802-dot1q-lldp-basic-tlv@2023-07-03.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-dot1q-lldp-ci-tlv` | runtime-baseline | ieee802-dot1q-lldp-ci-tlv@2023-07-03.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-dot1q-lldp-cn-tlv` | runtime-baseline | ieee802-dot1q-lldp-cn-tlv@2023-07-03.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-dot1q-lldp-dcbx-tlv` | runtime-baseline | ieee802-dot1q-lldp-dcbx-tlv@2023-07-03.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-dot1q-lldp-evb-tlv` | runtime-baseline | ieee802-dot1q-lldp-evb-tlv@2023-07-03.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-dot1q-lldp-pbbn-aa-tlv` | runtime-baseline | ieee802-dot1q-lldp-pbbn-aa-tlv@2023-10-26.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-dot1q-lldp-tr-tlv` | runtime-baseline | ieee802-dot1q-lldp-tr-tlv@2023-07-03.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-dot1q-mstp` | runtime-baseline | ieee802-dot1q-mstp@2025-02-04.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-dot1q-mstp-bridge` | runtime-baseline | ieee802-dot1q-mstp-bridge@2025-02-04.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-dot1q-pb` | runtime-baseline | ieee802-dot1q-pb@2023-10-22.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-dot1q-preemption` | runtime-baseline | ieee802-dot1q-preemption@2023-10-26.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-dot1q-preemption-bridge` | runtime-baseline | ieee802-dot1q-preemption-bridge@2023-10-26.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-dot1q-psfp` | runtime-baseline | ieee802-dot1q-psfp@2023-10-26.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-dot1q-psfp-bridge` | runtime-baseline | ieee802-dot1q-psfp-bridge@2023-10-26.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-dot1q-rstp` | runtime-baseline | ieee802-dot1q-rstp@2025-02-04.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-dot1q-rstp-bridge` | runtime-baseline | ieee802-dot1q-rstp-bridge@2025-02-04.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-dot1q-sched` | runtime-baseline | ieee802-dot1q-sched@2023-10-22.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-dot1q-sched-bridge` | runtime-baseline | ieee802-dot1q-sched-bridge@2023-10-26.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-dot1q-stream-filters-gates` | runtime-baseline | ieee802-dot1q-stream-filters-gates@2023-07-03.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-dot1q-stream-filters-gates-bridge` | runtime-baseline | ieee802-dot1q-stream-filters-gates-bridge@2023-07-03.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-dot1q-tpmr` | runtime-baseline | ieee802-dot1q-tpmr@2022-01-19.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-dot1q-tsn-types` | runtime-baseline | ieee802-dot1q-tsn-types@2022-10-29.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-dot1q-types` | runtime-baseline | ieee802-dot1q-types@2023-10-26.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-dot1q-vlan-bridge` | runtime-baseline | ieee802-dot1q-vlan-bridge@2018-03-07.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-dot1x` | runtime-baseline | ieee802-dot1x@2020-02-18.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-dot1x-eapol` | runtime-baseline | ieee802-dot1x-eapol@2022-05-25.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-dot1x-types` | runtime-baseline | ieee802-dot1x-types@2020-02-18.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-ethernet-interface` | runtime-baseline | ieee802-ethernet-interface@2025-09-10.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-ethernet-interface-half-duplex` | runtime-baseline | ieee802-ethernet-interface-half-duplex@2025-09-10.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-ethernet-link-oam` | runtime-baseline | ieee802-ethernet-link-oam@2025-09-10.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-ethernet-lldp` | runtime-baseline | ieee802-ethernet-lldp@2025-09-10.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-ethernet-mac-merge` | runtime-baseline | ieee802-ethernet-mac-merge@2025-09-10.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-ethernet-phy` | runtime-baseline | ieee802-ethernet-phy@2025-09-10.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-ethernet-phy-type` | runtime-baseline | ieee802-ethernet-phy-type@2025-09-10.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-ethernet-pon` | runtime-baseline | ieee802-ethernet-pon@2025-09-10.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-ethernet-pse` | runtime-baseline | ieee802-ethernet-pse@2025-09-10.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-ethernet-pse-2` | runtime-baseline | ieee802-ethernet-pse-2@2025-09-10.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-ethertype` | runtime-baseline | ieee802-ethertype@2023-04-17.yang | IEEE published module (YangModels standard/ieee/published) |
| `ieee802-types` | runtime-baseline | ieee802-types@2023-10-22.yang | IEEE published module (YangModels standard/ieee/published) |
| `ietf-bgp-capabilities` | runtime-baseline | draft-ietf-idr-bgp-model-21 | fixed draft: BGP model, fixed draft per plan M4 |
| `ietf-bgp-common` | runtime-baseline | draft-ietf-idr-bgp-model-21 | fixed draft: BGP model, fixed draft per plan M4 |
| `ietf-bgp-common-errors` | runtime-baseline | draft-ietf-idr-bgp-model-21 | fixed draft: BGP model, fixed draft per plan M4 |
| `ietf-bgp-common-multiprotocol` | runtime-baseline | draft-ietf-idr-bgp-model-21 | fixed draft: BGP model, fixed draft per plan M4 |
| `ietf-bgp-ls-topo-attr` | candidate-upgrade | draft-ietf-lsvr-bgp-ls-yang-04 | active Internet-Draft; enters a set only after review |
| `ietf-bgp-ls-topo-ospf` | candidate-upgrade | draft-ietf-lsvr-bgp-ls-yang-04 | active Internet-Draft; enters a set only after review |
| `ietf-bgp-neighbor` | runtime-baseline | draft-ietf-idr-bgp-model-21 | fixed draft: BGP model, fixed draft per plan M4 |
| `ietf-bgp-rib` | runtime-baseline | draft-ietf-idr-bgp-model-21 | fixed draft: BGP model, fixed draft per plan M4 |
| `ietf-bgp-rib-attributes` | runtime-baseline | draft-ietf-idr-bgp-model-21 | fixed draft: BGP model, fixed draft per plan M4 |
| `ietf-bgp-rib-tables` | runtime-baseline | draft-ietf-idr-bgp-model-21 | fixed draft: BGP model, fixed draft per plan M4 |
| `ietf-crypto-types` | runtime-baseline | RFC9640 | published RFC, newest published revision |
| `ietf-http-server` | candidate-upgrade | draft-ietf-netconf-http-client-server-33 | active Internet-Draft; enters a set only after review |
| `ietf-ipv6-router-advertisements` | runtime-baseline | RFC8349 | published RFC, newest published revision |
| `ietf-keystore` | runtime-baseline | RFC9642 | published RFC, newest published revision |
| `ietf-netconf-acm` | runtime-baseline | RFC8341 | published RFC, newest published revision |
| `ietf-netconf-with-defaults` | runtime-baseline | RFC6243 | published RFC, newest published revision |
| `ietf-tcp` | runtime-baseline | RFC9648 | published RFC, newest published revision |
| `ietf-tcp-common` | runtime-baseline | RFC9643 | published RFC, newest published revision |
| `ietf-tcp-server` | runtime-baseline | RFC9643 | published RFC, newest published revision |
| `ietf-tls-client` | runtime-baseline | RFC9645 | published RFC, newest published revision |
| `ietf-tls-common` | runtime-baseline | RFC9645 | published RFC, newest published revision |
| `ietf-tls-server` | runtime-baseline | RFC9645 | published RFC, newest published revision |
| `ietf-truststore` | runtime-baseline | RFC9641 | published RFC, newest published revision |
| `ietf-udp-server` | runtime-baseline | RFC9984 | published RFC, newest published revision |
| `ietf-yang-patch` | runtime-baseline | RFC8072 | published RFC, newest published revision |
