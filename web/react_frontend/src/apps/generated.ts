// Generated from src/apps/*/app.yaml. Do not edit.
import frontend0 from "../../../../src/apps/materials/frontend/index"
import frontend1 from "../../../../src/apps/assistant/frontend/index"
import frontend2 from "../../../../src/apps/delivery_probe/frontend/index"
import frontend3 from "../../../../src/apps/device_access/frontend/index"
import frontend4 from "../../../../src/apps/device_management/frontend/index"
import frontend5 from "../../../../src/apps/firewall_policy/frontend/index"
import frontend6 from "../../../../src/apps/hello_demo/frontend/index"
import frontend7 from "../../../../src/apps/inspection/frontend/index"
import frontend8 from "../../../../src/apps/rule_governance/frontend/index"
import frontend9 from "../../../../src/apps/netconf_api/frontend/index"
import frontend10 from "../../../../src/apps/official_documents/frontend/index"
import frontend11 from "../../../../src/apps/resource_ledger/frontend/index"
import frontend12 from "../../../../src/apps/topology/frontend/index"
export const packagedApps = [{ ...{"id": "materials", "name": "资料管理", "version": "1.2.0", "icon": "document", "digest": "e7c95efa3cb543f278cf17c8a336e6760f392bcb48eb2f234b1e0905f0b22745", "route": "/apps/materials", "aliases": []}, enabled: true, frontend: frontend0 },
{ ...{"id": "assistant", "name": "知识问答", "version": "0.10.1", "icon": "document", "digest": "3b898cfed491c198fbae9f170c0449795a90e1812aefc89fc64b93e6d2940191", "route": "/apps/assistant", "aliases": []}, enabled: true, frontend: frontend1 },
{ ...{"id": "delivery-probe", "name": "能力包接入验证", "version": "1.1.0", "icon": "document", "digest": "b3acdcb213d4d3176c041883adc284b76222d31c6ebd81fc0928e96e27ddbc4c", "route": "/apps/delivery-probe", "aliases": []}, enabled: true, frontend: frontend2 },
{ ...{"id": "device-access", "name": "SSH 终端", "version": "0.9.11", "icon": "terminal", "digest": "da2ca766928aefb55022f1ff7e1cf5622ca337db2275c5b5ccf4227a9064548d", "route": "/apps/device-access", "aliases": []}, enabled: true, frontend: frontend3 },
{ ...{"id": "device-management", "name": "设备管理", "version": "1.0.4", "icon": "server", "digest": "53b2f64034a9e6c6013c857d1800fdd2aa4d11d19d40f14c08875d1e0e2e3348", "route": "/apps/device-management", "aliases": []}, enabled: true, frontend: frontend4 },
{ ...{"id": "firewall-policy", "name": "防火墙策略生成", "version": "1.6.2", "icon": "shield", "digest": "bf1e57c699b4a038c241d4275efd1c2077e8440c99bbf763f55dd1c07126d109", "route": "/apps/firewall-policy", "aliases": []}, enabled: true, frontend: frontend5 },
{ ...{"id": "hello-demo", "name": "演示应用", "version": "0.2.0", "icon": "document", "digest": "63668931f7f25f05ac915387c7bec7816b22f082dc558450e29b66a356288aa1", "route": "/apps/hello-demo", "aliases": []}, enabled: true, frontend: frontend6 },
{ ...{"id": "inspection", "name": "网络巡检", "version": "1.3.6", "icon": "radar", "digest": "4d4fef34d7abc323aff65fef28b1a75ce8dbcfbee29653c354e1015198440b27", "route": "/apps/inspection", "aliases": ["patrol"]}, enabled: true, frontend: frontend7 },
{ ...{"id": "rule-governance", "name": "规则治理", "version": "1.19.10", "icon": "rules", "digest": "8de6c5db39774380e90b744595950dea86a8639c7a56b7ec0b17232ebcb5faca", "route": "/apps/rule-governance", "aliases": ["patrol-rules"]}, enabled: true, frontend: frontend8 },
{ ...{"id": "netconf-api", "name": "NETCONF 接口", "version": "0.11.0", "icon": "network", "digest": "6343f796c0860ffc1593e45f312ca129efd13025ab48f4c11c1d0895565020c6", "route": "/apps/netconf-api", "aliases": []}, enabled: true, frontend: frontend9 },
{ ...{"id": "official-documents", "name": "公文写作", "version": "1.1.0", "icon": "document", "digest": "d150c98b6ac2e79037a53dceddaad0bc3c159064015693ac86256071b10009f9", "route": "/apps/official-documents", "aliases": ["official-document"]}, enabled: import.meta.env.VITE_OFFICIAL_DOCUMENTS_ENABLED !== "false", frontend: frontend10 },
{ ...{"id": "resource-ledger", "name": "资源台账", "version": "0.4.0", "icon": "server", "digest": "946c4cc8c9bab3a7dcaae3c5f81df4a7c981532d4c35d421d13c250b01c0a347", "route": "/apps/resource-ledger", "aliases": []}, enabled: true, frontend: frontend11 },
{ ...{"id": "topology", "name": "网络拓扑", "version": "0.19.0", "icon": "network", "digest": "fe07b2d0ece405e1c29a83b77ca8bd4c4fbce847ab2b6578a8768d2097166632", "route": "/apps/topology", "aliases": []}, enabled: true, frontend: frontend12 }]
