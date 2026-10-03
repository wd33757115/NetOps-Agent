# M1 数据节点映射清单

由 `scripts/topology/m1_mapping.py` 生成，明细见 mapping.json。基线数据节点 21403 个（去重，不含 RPC/通知内容）；当前 App 生产者实际输出 133 个路径，研究实例 133 个。

## 生产者输出与 Schema

| 生产者 | 输出路径 | 不在任何基线集中的路径 |
|---|---|---|
| app:device-state:br1 | 23 | 无 |
| app:device-routing:br1:default | 37 | 无 |
| app:device-lldp:br1 | 4 | 无 |
| app:device-state:br2 | 23 | 无 |
| app:device-routing:br2:default | 37 | 无 |
| app:device-lldp:br2 | 4 | 无 |
| app:device-state:core1 | 18 | 无 |
| app:device-routing:core1:default | 37 | 无 |
| app:device-lldp:core1 | 1 | 无 |
| app:device-state:core2 | 18 | 无 |
| app:device-routing:core2:default | 37 | 无 |
| app:device-lldp:core2 | 1 | 无 |
| app:device-state:dist1 | 26 | 无 |
| app:device-routing:dist1:default | 37 | 无 |
| app:device-lldp:dist1 | 1 | 无 |
| app:device-state:dist2 | 26 | 无 |
| app:device-routing:dist2:default | 37 | 无 |
| app:device-lldp:dist2 | 1 | 无 |
| app:device-state:acc1 | 15 | 无 |
| app:device-routing:acc1:None | 1 | 无 |
| app:device-lldp:acc1 | 1 | 无 |
| app:device-state:acc2 | 15 | 无 |
| app:device-routing:acc2:None | 1 | 无 |
| app:device-lldp:acc2 | 1 | 无 |
| app:topology | 61 | 无 |
| app:inventory:br1 | 11 | 无 |
| app:inventory:br2 | 11 | 无 |
| app:inventory:core1 | 10 | 无 |
| app:inventory:core2 | 10 | 无 |
| app:inventory:dist1 | 10 | 无 |
| app:inventory:dist2 | 10 | 无 |
| app:inventory:acc1 | 10 | 无 |
| app:inventory:acc2 | 10 | 无 |
| research:inventory.json | 11 | 无 |
| research:topology.json | 51 | 无 |
| research:vsr-1-interfaces.partial.json | 9 | 无 |
| research:vsr-1-l2.partial.json | 59 | 无 |
| research:vsr-1-routes.json | 12 | 无 |
| research:vsr-2-interfaces.partial.json | 9 | 无 |
| research:vsr-2-l2.partial.json | 59 | 无 |
| research:vsr-2-routes.json | 12 | 无 |

## 已采集字段的落点

| 结论 | 数量 |
|---|---|
| context | 9 |
| exported | 54 |
| key-blocked | 14 |
| mappable-not-exported | 34 |
| no-native-node | 18 |

| 字段 | 结论 | 标准节点 | 说明 |
|---|---|---|---|
| `interfaces.ifname` | exported | `/ietf-interfaces:interfaces/interface/name` |  |
| `interfaces.type` | exported | `/ietf-interfaces:interfaces/interface/type` | platform interface kind → iana-if-type identity (ethernetCsmacd, ieee8023adLag, l3ipvlan, softwareLoopback) |
| `interfaces.admin_status` | exported | `/ietf-interfaces:interfaces/interface/enabled` | shutdown is the configured state (RFC 8343 enabled); admin-status needs the if-mib feature and with it a mandatory if-index |
| `interfaces.oper_status` | exported | `/ietf-interfaces:interfaces/interface/oper-status` |  |
| `interfaces.description` | exported | `/ietf-interfaces:interfaces/interface/description` |  |
| `interfaces.raw_name` | no-native-node | `` | CLI abbreviation (Et0/1) of the full name; presentation evidence only |
| `ip.ifname` | exported | `/ietf-interfaces:interfaces/interface/name` |  |
| `ip.address` | exported | `/ietf-interfaces:interfaces/interface/ietf-ip:ipv4/address/ip` | IPv6 rows go to ietf-ip:ipv6/address/ip |
| `ip.prefix_len` | exported | `/ietf-interfaces:interfaces/interface/ietf-ip:ipv4/address/prefix-length` |  |
| `ip.vrf` | exported | `/ietf-interfaces:interfaces/interface/ietf-network-instance:bind-ni-name` | RFC 8529: interface bound to the named VRF, plus /ietf-network-instance:network-instances/network-instance[name] with vrf-root; 'default' (global table) is no NI entry |
| `arp.ifname` | exported | `/ietf-interfaces:interfaces/interface/name` |  |
| `arp.ip` | exported | `/ietf-interfaces:interfaces/interface/ietf-ip:ipv4/neighbor/ip` |  |
| `arp.mac` | exported | `/ietf-interfaces:interfaces/interface/ietf-ip:ipv4/neighbor/link-layer-address` |  |
| `arp.type` | exported | `/ietf-interfaces:interfaces/interface/ietf-ip:ipv4/neighbor/origin` | dynamic/static → origin dynamic/static; interface-own entries → other |
| `arp.age_s` | no-native-node | `` | RFC 8344 neighbor has no age or expiry leaf |
| `arp.vrf` | context | `/ietf-interfaces:interfaces/interface/ietf-network-instance:bind-ni-name` |  |
| `mac.mac` | key-blocked | `/ieee802-dot1q-bridge:bridges/bridge/component/filtering-database/filtering-entry/address` | list key also needs database-id; not shown by the CLI |
| `mac.vlan_or_bd` | key-blocked | `/ieee802-dot1q-bridge:bridges/bridge/component/filtering-database/filtering-entry/vids` |  |
| `mac.port` | key-blocked | `/ieee802-dot1q-bridge:bridges/bridge/component/filtering-database/filtering-entry/port-map/port-ref` | port-ref is the bridge port number; the CLI gives the interface name and no port number |
| `mac.type` | mappable-not-exported | `/ieee802-dot1q-bridge:bridges/bridge/component/filtering-database/filtering-entry/entry-type` | static/dynamic |
| `vlan.vlan_id` | mappable-not-exported | `/ieee802-dot1q-bridge:bridges/bridge/component/bridge-vlan/vlan/vid` |  |
| `vlan.name` | mappable-not-exported | `/ieee802-dot1q-bridge:bridges/bridge/component/bridge-vlan/vlan/name` |  |
| `vlan_members.vlan_id` | mappable-not-exported | `/ieee802-dot1q-bridge:bridges/bridge/component/bridge-vlan/vlan/vid` |  |
| `vlan_members.ports` | mappable-not-exported | `/ieee802-dot1q-bridge:bridges/bridge/component/bridge-vlan/vlan/egress-ports` | untagged members also in untagged-ports |
| `vlan_members.status` | no-native-node | `` | VLAN active/suspended has no IEEE 802.1Q YANG leaf |
| `trunk.ports[].ifname` | exported | `/ietf-interfaces:interfaces/interface/name` |  |
| `trunk.ports[].l2_mode` | mappable-not-exported | `/ieee802-dot1q-bridge:bridges/bridge/component/bridge-vlan/vlan/untagged-ports` | trunk/access is not a leaf in 802.1Q; it is the port's tagged/untagged membership |
| `trunk.ports[].encapsulation` | mappable-not-exported | `/ietf-interfaces:interfaces/interface/ieee802-dot1q-bridge:bridge-port/port-type` | 802.1Q tagging on a customer bridge port → c-vlan-bridge-port |
| `trunk.ports[].native_vlan` | mappable-not-exported | `/ietf-interfaces:interfaces/interface/ieee802-dot1q-bridge:bridge-port/pvid` |  |
| `trunk.ports[].trunk_vlans` | mappable-not-exported | `/ieee802-dot1q-bridge:bridges/bridge/component/bridge-vlan/vlan/egress-ports` | allowed list → membership of each VLAN |
| `trunk.ports[].forwarding_vlans` | mappable-not-exported | `/ietf-interfaces:interfaces/interface/ieee802-dot1q-bridge:bridge-port/ieee802-dot1q-mstp-bridge:port-mstp/msti` | VLANs whose spanning-tree instance forwards on the port; needs the VLAN→MSTI map and per-MSTI port state |
| `trunk.ports[].active_vlans` | mappable-not-exported | `/ieee802-dot1q-bridge:bridges/bridge/component/bridge-vlan/vlan/egress-ports` | allowed and existing VLANs |
| `trunk.ports[].raw_name` | no-native-node | `` | CLI abbreviation |
| `switchport.ports[].ifname` | exported | `/ietf-interfaces:interfaces/interface/name` |  |
| `switchport.ports[].admin_mode` | no-native-node | `` | Cisco DTP administrative mode (dynamic auto/desirable); no IEEE/IETF node |
| `switchport.ports[].oper_mode` | no-native-node | `` | DTP negotiation result; bundle membership goes to the LAG model |
| `switchport.ports[].access_vlan` | mappable-not-exported | `/ietf-interfaces:interfaces/interface/ieee802-dot1q-bridge:bridge-port/pvid` | for an access port |
| `switchport.ports[].native_vlan` | mappable-not-exported | `/ietf-interfaces:interfaces/interface/ieee802-dot1q-bridge:bridge-port/pvid` | for a trunk port |
| `switchport.ports[].pvid` | mappable-not-exported | `/ietf-interfaces:interfaces/interface/ieee802-dot1q-bridge:bridge-port/pvid` |  |
| `switchport.ports[].trunk_vlans` | mappable-not-exported | `/ieee802-dot1q-bridge:bridges/bridge/component/bridge-vlan/vlan/egress-ports` |  |
| `switchport.ports[].admin_native_tagged` | mappable-not-exported | `/ieee802-dot1q-bridge:bridges/bridge/component/bridge-vlan/vlan/untagged-ports` | native VLAN tagged = port absent from untagged-ports of the PVID VLAN |
| `switchport.ports[].lag_parent` | mappable-not-exported | `/ieee802-dot1q-bridge:bridges/bridge/component/bridge-vlan/vlan/egress-ports` | placeholder until the LAG model check below; see lag.members |
| `switchport.ports[].encapsulation` | mappable-not-exported | `/ietf-interfaces:interfaces/interface/ieee802-dot1q-bridge:bridge-port/port-type` |  |
| `switchport.ports[].l2_mode` | mappable-not-exported | `/ieee802-dot1q-bridge:bridges/bridge/component/bridge-vlan/vlan/untagged-ports` |  |
| `switchport.ports[].raw_name` | no-native-node | `` | CLI abbreviation |
| `lag.ifname` | exported | `/ietf-interfaces:interfaces/interface/name` | the aggregator interface, type ieee8023adLag |
| `lag.members` | exported | `/ietf-network:networks/network/node/ietf-network-topology:termination-point/ietf-l2-topology:l2-termination-point-attributes/member-link-tp` | network view (RFC 8944); the device view is IEEE 802.1AX aggregation-port |
| `lag.bundled` | mappable-not-exported | `/ietf-interfaces:interfaces/interface/ieee802-dot1ax-linkagg:aggport/lacp/actor-oper-state` | bundled = collecting/distributing; aggport's mandatory key-group-name reference is not observed, so it is not exported |
| `lag.member_flags` | mappable-not-exported | `/ietf-interfaces:interfaces/interface/ieee802-dot1ax-linkagg:aggport/lacp/actor-oper-state` | IOS member flags (P bundled, s suspended, D down, I stand-alone, H hot-standby); used by the L2 forwarding analysis |
| `lldp.local_if` | key-blocked | `/ieee802-dot1ab-lldp:lldp/port/name` | port list key also needs dest-mac-address (agent); nearest-bridge is assumed only with evidence |
| `lldp.remote_chassis_id` | key-blocked | `/ieee802-dot1ab-lldp:lldp/port/remote-systems-data/chassis-id` | remote-systems-data keys time-mark and remote-index are not collected |
| `lldp.remote_chassis_subtype` | key-blocked | `/ieee802-dot1ab-lldp:lldp/port/remote-systems-data/chassis-id-subtype` |  |
| `lldp.remote_port_id` | key-blocked | `/ieee802-dot1ab-lldp:lldp/port/remote-systems-data/port-id` |  |
| `lldp.remote_port_subtype` | key-blocked | `/ieee802-dot1ab-lldp:lldp/port/remote-systems-data/port-id-subtype` |  |
| `lldp.remote_sysname` | key-blocked | `/ieee802-dot1ab-lldp:lldp/port/remote-systems-data/system-name` |  |
| `lldp.remote_system_description` | key-blocked | `/ieee802-dot1ab-lldp:lldp/port/remote-systems-data/system-description` |  |
| `lldp.remote_mgmt_addresses` | key-blocked | `/ieee802-dot1ab-lldp:lldp/port/remote-systems-data/management-address/address` | hex octet string with address-subtype |
| `lldp_local.chassis_id` | mappable-not-exported | `/ieee802-dot1ab-lldp:lldp/local-system-data/chassis-id` |  |
| `lldp_local.hostname` | exported | `/ieee802-dot1ab-lldp:lldp/local-system-data/system-name` | also /ietf-system:system/hostname |
| `lldp_local.system_description` | exported | `/ieee802-dot1ab-lldp:lldp/local-system-data/system-description` |  |
| `lldp_local.management_addresses` | key-blocked | `/ieee802-dot1ab-lldp:lldp/port/management-address-tx-port/man-address` | IEEE 802.1AB keeps local management addresses per transmitting port (port keys name + dest-mac-address), as hex octets with address-subtype |
| `lldp_local.vendor` | exported | `/ietf-network-inventory:network-inventory/network-elements/network-element/mfg-name` | the element's mfg-name comes from the identified platform vendor |
| `lldp_local.model` | exported | `/ietf-network-inventory:network-inventory/network-elements/network-element/product-name` |  |
| `lldp_local.serial` | key-blocked | `/ietf-network-inventory:network-inventory/network-elements/network-element/components/component/serial-number` | belongs to a chassis component whose component-id is not collected |
| `routes.prefix` | mappable-not-exported | `/ietf-routing:routing/ribs/rib/routes/route/ietf-ipv4-unicast-routing:destination-prefix` |  |
| `routes.protocol` | mappable-not-exported | `/ietf-routing:routing/ribs/rib/routes/route/source-protocol` | CLI protocol code → routing-protocol identity |
| `routes.preference` | mappable-not-exported | `/ietf-routing:routing/ribs/rib/routes/route/route-preference` |  |
| `routes.metric` | mappable-not-exported | `/ietf-routing:routing/ribs/rib/routes/route/ietf-ospf:metric` | only OSPF routes have a metric node (RFC 9129); static/BGP metrics have none in RFC 8349 |
| `routes.next_hops[].ip` | mappable-not-exported | `/ietf-routing:routing/ribs/rib/routes/route/next-hop/ietf-ipv4-unicast-routing:next-hop-address` | several next hops: next-hop-list/next-hop/ietf-ipv4-unicast-routing:address |
| `routes.next_hops[].ifname` | mappable-not-exported | `/ietf-routing:routing/ribs/rib/routes/route/next-hop/outgoing-interface` |  |
| `routes.next_hops[].vrf` | context | `/ietf-network-instance:network-instances/network-instance/name` | next hop resolved in another instance (route leaking) |
| `routes.vrf` | context | `/ietf-network-instance:network-instances/network-instance/name` | each NI holds its own RIB under its schema mount (RFC 8529/8528) |
| `ospf_process.processes[].process_id` | exported | `/ietf-routing:routing/control-plane-protocols/control-plane-protocol/name` | the OSPF process ID the device shows names its RFC 8349 instance (type ietf-ospf:ospfv2) |
| `ospf_process.processes[].router_id` | exported | `/ietf-routing:routing/control-plane-protocols/control-plane-protocol/ietf-ospf:ospf/router-id` |  |
| `ospf_process.processes[].vrf` | context | `/ietf-network-instance:network-instances/network-instance/name` | the NI whose routing tree holds the instance; an instance spread over several NIs is not exported |
| `ospf_process.interfaces[].ifname` | exported | `/ietf-routing:routing/control-plane-protocols/control-plane-protocol/ietf-ospf:ospf/areas/area/interfaces/interface/name` | references the interface list; the interface is copied into the same tree |
| `ospf_process.interfaces[].area` | exported | `/ietf-routing:routing/control-plane-protocols/control-plane-protocol/ietf-ospf:ospf/areas/area/area-id` | IOS area numbers become dotted-quad area IDs |
| `ospf_interfaces.ifname` | exported | `/ietf-routing:routing/control-plane-protocols/control-plane-protocol/ietf-ospf:ospf/areas/area/interfaces/interface/name` |  |
| `ospf_interfaces.area` | exported | `/ietf-routing:routing/control-plane-protocols/control-plane-protocol/ietf-ospf:ospf/areas/area/area-id` | IOS area numbers become dotted-quad area IDs |
| `ospf_interfaces.cost` | exported | `/ietf-routing:routing/control-plane-protocols/control-plane-protocol/ietf-ospf:ospf/areas/area/interfaces/interface/cost` |  |
| `ospf_interfaces.state` | mappable-not-exported | `/ietf-routing:routing/control-plane-protocols/control-plane-protocol/ietf-ospf:ospf/areas/area/interfaces/interface/state` | DR/BDR/DROTHER/P2P/LOOP → if-state-type; not exported yet |
| `ospf_interfaces.process_id` | context | `/ietf-routing:routing/control-plane-protocols/control-plane-protocol/name` |  |
| `ospf_interfaces.address` | context | `/ietf-interfaces:interfaces/interface/ietf-ip:ipv4/address/ip` | binds the OSPF row to its interface when the platform shows no name |
| `ospf_neighbors.neighbor_router_id` | exported | `/ietf-routing:routing/control-plane-protocols/control-plane-protocol/ietf-ospf:ospf/areas/area/interfaces/interface/neighbors/neighbor/neighbor-router-id` | an OSPF router ID, scoped to the routing domain; never a device identity |
| `ospf_neighbors.address` | exported | `/ietf-routing:routing/control-plane-protocols/control-plane-protocol/ietf-ospf:ospf/areas/area/interfaces/interface/neighbors/neighbor/address` |  |
| `ospf_neighbors.state` | exported | `/ietf-routing:routing/control-plane-protocols/control-plane-protocol/ietf-ospf:ospf/areas/area/interfaces/interface/neighbors/neighbor/state` | FULL/2WAY/EXSTART… → nbr-state-type enumeration |
| `ospf_neighbors.role` | mappable-not-exported | `/ietf-routing:routing/control-plane-protocols/control-plane-protocol/ietf-ospf:ospf/areas/area/interfaces/interface/neighbors/neighbor/dr-router-id` | the neighbour's DR/BDR role; needs the DR election view of the interface, not exported |
| `ospf_neighbors.dead_time` | mappable-not-exported | `/ietf-routing:routing/control-plane-protocols/control-plane-protocol/ietf-ospf:ospf/areas/area/interfaces/interface/neighbors/neighbor/dead-timer` | remaining seconds at the observation time; a countdown snapshot, not exported |
| `ospf_neighbors.priority` | no-native-node | `` | RFC 9129 keeps priority as interface configuration, not per neighbour state |
| `ospf_neighbors.ifname` | context | `/ietf-routing:routing/control-plane-protocols/control-plane-protocol/ietf-ospf:ospf/areas/area/interfaces/interface/name` |  |
| `ospf_neighbors.process` | context | `/ietf-routing:routing/control-plane-protocols/control-plane-protocol/name` |  |
| `ospf_neighbors.area` | context | `/ietf-routing:routing/control-plane-protocols/control-plane-protocol/ietf-ospf:ospf/areas/area/area-id` |  |
| `bgp_peers.local_as` | exported | `/ietf-routing:routing/control-plane-protocols/control-plane-protocol/ietf-bgp:bgp/global/as` | also names the RFC 8349 instance (type ietf-bgp:bgp): the CLI identifies its one BGP process by the AS |
| `bgp_peers.router_id` | exported | `/ietf-routing:routing/control-plane-protocols/control-plane-protocol/ietf-bgp:bgp/global/identifier` |  |
| `bgp_peers.peers[].peer` | exported | `/ietf-routing:routing/control-plane-protocols/control-plane-protocol/ietf-bgp:bgp/neighbors/neighbor/remote-address` | also the neighbor-key (leafref to remote-address) |
| `bgp_peers.peers[].peer_as` | exported | `/ietf-routing:routing/control-plane-protocols/control-plane-protocol/ietf-bgp:bgp/neighbors/neighbor/peer-as` |  |
| `bgp_peers.peers[].state` | exported | `/ietf-routing:routing/control-plane-protocols/control-plane-protocol/ietf-bgp:bgp/neighbors/neighbor/session-state` | a prefix count in the State/PfxRcd column means established |
| `bgp_peers.peers[].up_down` | mappable-not-exported | `/ietf-routing:routing/control-plane-protocols/control-plane-protocol/ietf-bgp:bgp/neighbors/neighbor/last-established` | observation time minus the established duration; not exported |
| `bgp_peers.peers[].prefixes_received` | mappable-not-exported | `/ietf-routing:routing/control-plane-protocols/control-plane-protocol/ietf-bgp:bgp/neighbors/neighbor/afi-safis/afi-safi/prefixes/received` | in the afi-safi the command shows (IPv4 unicast); not exported |
| `bgp_peers.peers[].state_detail` | no-native-node | `` | platform qualifier of the state, e.g. Idle (Admin) |
| `vrrp.ifname` | exported | `/ietf-interfaces:interfaces/interface/name` |  |
| `vrrp.vrid` | exported | `/ietf-interfaces:interfaces/interface/ietf-ip:ipv4/ietf-vrrp:vrrp/vrrp-instance/vrid` |  |
| `vrrp.priority` | mappable-not-exported | `/ietf-interfaces:interfaces/interface/ietf-ip:ipv4/ietf-vrrp:vrrp/vrrp-instance/priority` |  |
| `vrrp.state` | exported | `/ietf-interfaces:interfaces/interface/ietf-ip:ipv4/ietf-vrrp:vrrp/vrrp-instance/state` | Initialize/Backup/Master → identity |
| `vrrp.virtual_addresses` | exported | `/ietf-interfaces:interfaces/interface/ietf-ip:ipv4/ietf-vrrp:vrrp/vrrp-instance/virtual-ipv4-addresses/virtual-ipv4-address/ipv4-address` |  |
| `vrrp.last_adv_source` | exported | `/ietf-interfaces:interfaces/interface/ietf-ip:ipv4/ietf-vrrp:vrrp/vrrp-instance/last-adv-source` |  |
| `version.hostname` | exported | `/ietf-network-inventory:network-inventory/network-elements/network-element/name` |  |
| `version.model` | exported | `/ietf-network-inventory:network-inventory/network-elements/network-element/product-name` |  |
| `version.os_version` | exported | `/ietf-network-inventory:network-inventory/network-elements/network-element/software-rev/revision` | per-image revisions below are used when present |
| `version.boot_image` | exported | `/ietf-network-inventory:network-inventory/network-elements/network-element/software-rev/name` |  |
| `version.boot_image_version` | exported | `/ietf-network-inventory:network-inventory/network-elements/network-element/software-rev/revision` |  |
| `version.system_image` | exported | `/ietf-network-inventory:network-inventory/network-elements/network-element/software-rev/name` |  |
| `version.system_image_version` | exported | `/ietf-network-inventory:network-inventory/network-elements/network-element/software-rev/revision` |  |
| `version.image_name` | exported | `/ietf-network-inventory:network-inventory/network-elements/network-element/software-rev/name` |  |
| `version.image_version` | exported | `/ietf-network-inventory:network-inventory/network-elements/network-element/software-rev/revision` |  |
| `version.virtual_cpus` | exported | `/ietf-network-inventory:network-inventory/network-elements/network-element/ne-type` | with virtio NICs: ne-virtual (IVY software extension) |
| `version.virtio_nic` | exported | `/ietf-network-inventory:network-inventory/network-elements/network-element/ne-type` | with vCPUs: ne-virtual |
| `version.unix_processor` | exported | `/ietf-network-inventory:network-inventory/network-elements/network-element/ne-type` | IOS running as a Linux process (IOL): ne-software |
| `version.serial` | key-blocked | `/ietf-network-inventory:network-inventory/network-elements/network-element/components/component/serial-number` | belongs to a chassis component whose component-id is not observed; IOL also prints one |
| `stp.instance` | no-native-node | `` | rapid-PVST/PVST keep one tree per VLAN; IEEE 802.1Q YANG holds one port state per bridge (ieee802-dot1q-rstp-bridge) or per MSTI, so per-VLAN state is evidence for the L2 forwarding analysis |
| `stp.vlan` | no-native-node | `` | rapid-PVST/PVST keep one tree per VLAN; IEEE 802.1Q YANG holds one port state per bridge (ieee802-dot1q-rstp-bridge) or per MSTI, so per-VLAN state is evidence for the L2 forwarding analysis |
| `stp.ifname` | exported | `/ietf-interfaces:interfaces/interface/name` |  |
| `stp.role` | no-native-node | `` | rapid-PVST/PVST keep one tree per VLAN; IEEE 802.1Q YANG holds one port state per bridge (ieee802-dot1q-rstp-bridge) or per MSTI, so per-VLAN state is evidence for the L2 forwarding analysis |
| `stp.state` | no-native-node | `` | rapid-PVST/PVST keep one tree per VLAN; IEEE 802.1Q YANG holds one port state per bridge (ieee802-dot1q-rstp-bridge) or per MSTI, so per-VLAN state is evidence for the L2 forwarding analysis |
| `stp.cost` | no-native-node | `` | rapid-PVST/PVST keep one tree per VLAN; IEEE 802.1Q YANG holds one port state per bridge (ieee802-dot1q-rstp-bridge) or per MSTI, so per-VLAN state is evidence for the L2 forwarding analysis |
| `stp.port_id` | no-native-node | `` | rapid-PVST/PVST keep one tree per VLAN; IEEE 802.1Q YANG holds one port state per bridge (ieee802-dot1q-rstp-bridge) or per MSTI, so per-VLAN state is evidence for the L2 forwarding analysis |
| `stp.link_type` | no-native-node | `` | rapid-PVST/PVST keep one tree per VLAN; IEEE 802.1Q YANG holds one port state per bridge (ieee802-dot1q-rstp-bridge) or per MSTI, so per-VLAN state is evidence for the L2 forwarding analysis |
| `stp.edge` | no-native-node | `` | rapid-PVST/PVST keep one tree per VLAN; IEEE 802.1Q YANG holds one port state per bridge (ieee802-dot1q-rstp-bridge) or per MSTI, so per-VLAN state is evidence for the L2 forwarding analysis |
| `stp.protocol` | no-native-node | `` | rapid-PVST/PVST keep one tree per VLAN; IEEE 802.1Q YANG holds one port state per bridge (ieee802-dot1q-rstp-bridge) or per MSTI, so per-VLAN state is evidence for the L2 forwarding analysis |

## 部分实例：已输出容器/列表下缺少的键与必选子节点

### app

| 已输出 | 缺少 | 原因 |
|---|---|---|
| `/ietf-interfaces:interfaces/interface` | `admin-status` | mandatory（有 if-feature） |
| `/ietf-interfaces:interfaces/interface` | `if-index` | mandatory（有 if-feature） |
| `/ietf-interfaces:interfaces/interface` | `statistics/discontinuity-time` | mandatory |
| `/ietf-interfaces:interfaces/interface` | `ieee802-dot1ax-linkagg:lag/key-group-name` | mandatory（有 when 条件） |
| `/ietf-interfaces:interfaces/interface` | `ietf-microwave-radio-link:mode` | mandatory（有 when 条件） |
| `/ietf-interfaces:interfaces/interface` | `ietf-microwave-radio-link:carrier-terminations` | mandatory（有 when 条件） |
| `/ietf-interfaces:interfaces/interface` | `ietf-microwave-radio-link:tx-frequency` | mandatory（有 when 条件） |
| `/ietf-interfaces:interfaces/interface` | `ietf-microwave-radio-link:channel-separation` | mandatory（有 when 条件） |
| `/ietf-interfaces:interfaces/interface/ietf-ip:ipv4/ietf-vrrp:vrrp/vrrp-instance` | `version` | mandatory |

### research

| 已输出 | 缺少 | 原因 |
|---|---|---|
| `/ieee802-dot1q-bridge:bridges/bridge` | `address` | mandatory |
| `/ietf-interfaces:interfaces/interface` | `admin-status` | mandatory（有 if-feature） |
| `/ietf-interfaces:interfaces/interface` | `if-index` | mandatory（有 if-feature） |
| `/ietf-interfaces:interfaces/interface` | `statistics/discontinuity-time` | mandatory |
| `/ietf-interfaces:interfaces/interface` | `ieee802-dot1ax-linkagg:lag/key-group-name` | mandatory（有 when 条件） |
| `/ietf-interfaces:interfaces/interface` | `ietf-microwave-radio-link:mode` | mandatory（有 when 条件） |
| `/ietf-interfaces:interfaces/interface` | `ietf-microwave-radio-link:carrier-terminations` | mandatory（有 when 条件） |
| `/ietf-interfaces:interfaces/interface` | `ietf-microwave-radio-link:tx-frequency` | mandatory（有 when 条件） |
| `/ietf-interfaces:interfaces/interface` | `ietf-microwave-radio-link:channel-separation` | mandatory（有 when 条件） |

## 模块覆盖（按 App 已输出节点数）

| 模块 | 数据节点 | 配置 | 状态 | App 输出 | 研究实例 |
|---|---|---|---|---|---|
| `ietf-simap-topology` | 41 | 25 | 16 | 17 | 16 |
| `ietf-ospf` | 1522 | 176 | 1346 | 14 | 0 |
| `ietf-l2-topology` | 32 | 32 | 0 | 12 | 7 |
| `ietf-bgp` | 909 | 459 | 450 | 11 | 0 |
| `ietf-network` | 11 | 11 | 0 | 11 | 11 |
| `ietf-network-inventory` | 42 | 0 | 42 | 11 | 11 |
| `ietf-network-topology` | 17 | 17 | 0 | 11 | 11 |
| `ietf-l3-unicast-topology` | 21 | 21 | 0 | 10 | 6 |
| `ietf-ip` | 60 | 27 | 33 | 8 | 8 |
| `ietf-vrrp` | 94 | 47 | 47 | 8 | 0 |
| `ietf-interfaces` | 57 | 7 | 50 | 7 | 5 |
| `ietf-routing` | 53 | 13 | 40 | 5 | 10 |
| `ieee802-dot1ab-lldp` | 81 | 25 | 56 | 4 | 29 |
| `ietf-network-instance` | 11 | 11 | 0 | 4 | 0 |
| `ieee802-dot1q-bridge` | 237 | 179 | 58 | 0 | 17 |
| `ietf-ipv4-unicast-routing` | 19 | 13 | 6 | 0 | 2 |

其余 182 个基线模块尚无 App 输出节点（见 mapping.json 的 modules）。
