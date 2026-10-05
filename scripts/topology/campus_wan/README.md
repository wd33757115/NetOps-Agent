# 园区 + 双出口实验网（campus-wan）

路线图 §11.1 的第一套复杂拓扑。路由部分参照 [Batfish 示例网络](https://github.com/batfish/batfish/tree/master/networks/example)（双 border、双 core、双汇聚），二层部分参照常见园区三层组网（VRRP + rapid-PVST + LACP）。按当前 GNS3 VM（10GB）可同时运行的规模设计：2 台 H3C VSR + 7 台 Cisco IOL + 6 台 VPCS。

```text
                ISP（IOL L3，AS 65100，不纳管，不发 LLDP）  8.8.8.0/24、198.51.100.0/24 ─ INET-PC
                  /                        \
      BR1（H3C VSR）─────────────────── BR2（H3C VSR）    AS 65000：eBGP 上联；BR1 本地优先级 200，BR2 出方向 AS-path 加长
         |        \                     /       |
      CORE1（IOL L3）════ /31 ═══ CORE2（IOL L3）         OSPF area 0；两台都是 BGP 路由反射器
         |        \                     /       |
      DIST1（IOL L2）══ Po1 LACP ══ DIST2（IOL L2）       ABR area 0/10；VRRP：VLAN10 主 DIST1，VLAN20 主 DIST2；iBGP 客户端
         |        \                     /       |
      ACC1（IOL L2）                    ACC2（IOL L2）     双上联 trunk；rapid-PVST 按 VLAN 阻塞一条
      PC1(V10) PC2(V20)                PC3(V10)  UNM-SW（GNS3 交换机，trunk 进）─ PC4(V20)、PC5(V10)
带外管理：MGMT-SW ─ 云（GNS3 VM eth1）─ 主机 10.10.1.254；Cisco 管理口在 VRF MGMT，管理口不收发 LLDP
```

## 地址

| 设备 | 管理 | Loopback0 | 互联 |
|---|---|---|---|
| BR1 | 10.10.1.11（GE1/0） | 10.255.0.1 | GE2/0 100.64.1.2/30（ISP）、GE3/0 10.0.13.1/30（CORE1）、GE4/0 10.0.14.1/30（CORE2）、GE5/0 10.0.12.1/30（BR2） |
| BR2 | 10.10.1.12 | 10.255.0.2 | GE2/0 100.64.2.2/30（ISP）、GE3/0 10.0.24.1/30（CORE2）、GE4/0 10.0.23.1/30（CORE1）、GE5/0 10.0.12.2/30 |
| CORE1 | 10.10.1.13（e0/0） | 10.255.0.3 | e0/1 10.0.13.2、e0/2 10.0.23.2、e0/3 10.0.34.0/31（CORE2）、e1/0 10.0.35.1（DIST1）、e1/1 10.0.36.1（DIST2） |
| CORE2 | 10.10.1.14 | 10.255.0.4 | e0/1 10.0.24.2、e0/2 10.0.14.2、e0/3 10.0.34.1/31、e1/0 10.0.46.1（DIST2）、e1/1 10.0.45.1（DIST1） |
| DIST1 | 10.10.1.15 | 10.255.0.5 | e0/1 10.0.35.2、e0/2 10.0.45.2；Vlan10 172.16.10.2、Vlan20 172.16.20.2；e1/0-1 Po1；e2/0 ACC1、e2/1 ACC2 |
| DIST2 | 10.10.1.16 | 10.255.0.6 | e0/1 10.0.46.2、e0/2 10.0.36.2；Vlan10 172.16.10.3、Vlan20 172.16.20.3；e1/0-1 Po1；e2/0 ACC2、e2/1 ACC1 |
| ACC1 | 10.10.1.17 | — | e0/1 DIST1、e0/2 DIST2；e1/0 PC1（V10）、e1/1 PC2（V20） |
| ACC2 | 10.10.1.18 | — | e0/1 DIST2、e0/2 DIST1；e1/0 PC3（V10）、e1/1 trunk 到 UNM-SW |
| ISP | — | 8.8.8.8/24 | e0/1 100.64.1.1、e0/2 100.64.2.1、e0/3 198.51.100.1/24 |

VRRP 虚地址：172.16.10.1、172.16.20.1。终端：PC1 172.16.10.11、PC2 172.16.20.12、PC3 172.16.10.13、PC4 172.16.20.14、PC5 172.16.10.15、INET-PC 198.51.100.10。

## 搭建

1. GNS3 首选项 → IOS on UNIX：关闭 IOU license 检查（IOL 17.x 来自 CML，不需要 iourc）。
2. `lab.py project` 生成工程；在 GNS3 中关闭当前工程（旧实验网会断电，先确认已 `save`），打开 `~/GNS3/projects/netops-campus-wan/netops-campus-wan.gns3`，全部启动。IOL 用 `lab.py push NODE` 经控制台加载 `configs/*.cfg` 并保存；工程文件不嵌入启动配置。
3. VSR：`lab.py type BR1` 在 VNC 控制台给 aux 口授 network-admin，`lab.py push BR1` 经串口推送 `configs/BR1.txt` 并保存；BR2 同理。`lab.py screen BR1 --out x.png` 截 VNC 屏幕。
4. 负责人运行 `lab.py account`：从 `NETOPS_LAB_PASSWORD` 在 8 台纳管设备上建实验账号（密码不进配置文件、不打印）。
5. `lab.py traffic` 让每台 PC 持续 ping，ARP/MAC 表保持有内容；`--stop` 停止。

BR1 的配置末尾是一套 ACL、NAT、策略路由样例（2026-10-05，NETCONF 接口对照用，见 `docs/netconf-api-handoff.md` 第 6 节）。规则只匹配文档保留地址 192.0.2.0/24、203.0.113.0/24，不影响实验网流量；实验或注入故障时注意这些规则一直存在。
