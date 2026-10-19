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
import frontend11 from "../../../../src/apps/topology/frontend/index"
export const packagedApps = [{ ...{"id": "materials", "name": "资料管理", "version": "1.2.0", "icon": "document", "digest": "edd7fdf0033a151e75aa11c4f3211e2e1c828ddb61bb693d8fab8152b8187783", "route": "/apps/materials", "aliases": []}, enabled: true, frontend: frontend0 },
{ ...{"id": "assistant", "name": "知识问答", "version": "0.9.0", "icon": "document", "digest": "662e42e552c47be4bb009fa1c8ae687dab3ec97cbf930a48a52c0853bb21fadd", "route": "/apps/assistant", "aliases": []}, enabled: true, frontend: frontend1 },
{ ...{"id": "delivery-probe", "name": "能力包接入验证", "version": "1.1.0", "icon": "document", "digest": "b3acdcb213d4d3176c041883adc284b76222d31c6ebd81fc0928e96e27ddbc4c", "route": "/apps/delivery-probe", "aliases": []}, enabled: true, frontend: frontend2 },
{ ...{"id": "device-access", "name": "SSH 终端", "version": "0.9.11", "icon": "terminal", "digest": "da2ca766928aefb55022f1ff7e1cf5622ca337db2275c5b5ccf4227a9064548d", "route": "/apps/device-access", "aliases": []}, enabled: true, frontend: frontend3 },
{ ...{"id": "device-management", "name": "设备管理", "version": "1.0.4", "icon": "server", "digest": "53b2f64034a9e6c6013c857d1800fdd2aa4d11d19d40f14c08875d1e0e2e3348", "route": "/apps/device-management", "aliases": []}, enabled: true, frontend: frontend4 },
{ ...{"id": "firewall-policy", "name": "防火墙策略生成", "version": "1.1.0", "icon": "shield", "digest": "12cbc12c9ef25cf09ed7dc710194eb991ec87db3f72d30c22a1a423f545f46ee", "route": "/apps/firewall-policy", "aliases": []}, enabled: true, frontend: frontend5 },
{ ...{"id": "hello-demo", "name": "演示应用", "version": "0.2.0", "icon": "document", "digest": "3bdcfbe6d3d1f12adea6d60b18219eb0f9f785629c7efad06ac9a7b722ad911f", "route": "/apps/hello-demo", "aliases": []}, enabled: true, frontend: frontend6 },
{ ...{"id": "inspection", "name": "网络巡检", "version": "1.3.6", "icon": "radar", "digest": "2ac378ffb28b9cad837bed4527b5d22ba17e1a5b9853134216b52c2bdfcd2713", "route": "/apps/inspection", "aliases": ["patrol"]}, enabled: true, frontend: frontend7 },
{ ...{"id": "rule-governance", "name": "规则治理", "version": "1.19.10", "icon": "rules", "digest": "055079cd3464e532f1228a6ceaa9d217883bcf79a01a6cf78881d39adf26b60b", "route": "/apps/rule-governance", "aliases": ["patrol-rules"]}, enabled: true, frontend: frontend8 },
{ ...{"id": "netconf-api", "name": "NETCONF 接口", "version": "0.11.0", "icon": "network", "digest": "1daad9df574fc922a2227113d25811b3e7fff4d1912ba8573ffe8cb346db707f", "route": "/apps/netconf-api", "aliases": []}, enabled: true, frontend: frontend9 },
{ ...{"id": "official-documents", "name": "公文写作", "version": "1.1.0", "icon": "document", "digest": "383a2616ca397fdfe5fc4f3966119d1546cf36a8b33d52c7c7d5f09fea18a382", "route": "/apps/official-documents", "aliases": ["official-document"]}, enabled: import.meta.env.VITE_OFFICIAL_DOCUMENTS_ENABLED !== "false", frontend: frontend10 },
{ ...{"id": "topology", "name": "网络拓扑", "version": "0.19.0", "icon": "network", "digest": "50fbc625418dfde03b07dea3d66c1c6bf771d241338a99e1e28ca866637b567e", "route": "/apps/topology", "aliases": []}, enabled: true, frontend: frontend11 }]
