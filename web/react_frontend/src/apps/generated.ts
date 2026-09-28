// Generated from src/apps/*/app.yaml. Do not edit.
import frontend0 from "../../../../src/apps/materials/frontend/index"
import frontend1 from "../../../../src/apps/assistant/frontend/index"
import frontend2 from "../../../../src/apps/delivery_probe/frontend/index"
import frontend3 from "../../../../src/apps/device_access/frontend/index"
import frontend4 from "../../../../src/apps/device_management/frontend/index"
import frontend5 from "../../../../src/apps/firewall_policy/frontend/index"
import frontend6 from "../../../../src/apps/inspection/frontend/index"
import frontend7 from "../../../../src/apps/official_documents/frontend/index"
import frontend8 from "../../../../src/apps/rule_governance/frontend/index"
import frontend9 from "../../../../src/apps/topology/frontend/index"
export const packagedApps = [{ ...{"id": "materials", "name": "资料管理", "version": "1.2.0", "icon": "document", "digest": "edd7fdf0033a151e75aa11c4f3211e2e1c828ddb61bb693d8fab8152b8187783", "route": "/apps/materials", "aliases": []}, enabled: true, frontend: frontend0 },
{ ...{"id": "assistant", "name": "知识问答", "version": "0.7.0", "icon": "document", "digest": "14307a9404bfa0f7514b4d3cdcb441507203f06579e5da28f58e72063d30e5b3", "route": "/apps/assistant", "aliases": []}, enabled: true, frontend: frontend1 },
{ ...{"id": "delivery-probe", "name": "能力包接入验证", "version": "1.1.0", "icon": "document", "digest": "b3acdcb213d4d3176c041883adc284b76222d31c6ebd81fc0928e96e27ddbc4c", "route": "/apps/delivery-probe", "aliases": []}, enabled: true, frontend: frontend2 },
{ ...{"id": "device-access", "name": "设备访问", "version": "0.6.0", "icon": "terminal", "digest": "c90ebdcef973c3d2be8c562c2fc61e32abb14631409207dfb779edd8b65e519f", "route": "/apps/device-access", "aliases": []}, enabled: true, frontend: frontend3 },
{ ...{"id": "device-management", "name": "设备管理", "version": "1.0.3", "icon": "server", "digest": "bef2cd3c3d51f39cc08e6b56496adeec7b03eb936186efcce6956ffeb1109004", "route": "/apps/device-management", "aliases": []}, enabled: true, frontend: frontend4 },
{ ...{"id": "firewall-policy", "name": "防火墙策略生成", "version": "1.0.1", "icon": "shield", "digest": "4356e1e32fcdd8b4a48b9c395eba9a0525f33820dcb35ae0a5bba2cef0e8e732", "route": "/apps/firewall-policy", "aliases": []}, enabled: true, frontend: frontend5 },
{ ...{"id": "inspection", "name": "网络巡检", "version": "1.3.0", "icon": "radar", "digest": "c05fa40bdfda8ed69446b93ec19868704b144516ac211d9d267c5562b6fb9d8f", "route": "/apps/inspection", "aliases": ["patrol"]}, enabled: true, frontend: frontend6 },
{ ...{"id": "official-documents", "name": "公文写作", "version": "1.1.0", "icon": "document", "digest": "383a2616ca397fdfe5fc4f3966119d1546cf36a8b33d52c7c7d5f09fea18a382", "route": "/apps/official-documents", "aliases": ["official-document"]}, enabled: import.meta.env.VITE_OFFICIAL_DOCUMENTS_ENABLED !== "false", frontend: frontend7 },
{ ...{"id": "rule-governance", "name": "规则治理", "version": "1.4.5", "icon": "rules", "digest": "f14ace844a40f9854bdced76e155a406ecaac494a9516a8aca0a56af512fdb6f", "route": "/apps/rule-governance", "aliases": ["patrol-rules"]}, enabled: true, frontend: frontend8 },
{ ...{"id": "topology", "name": "网络拓扑", "version": "0.3.4", "icon": "network", "digest": "1dcd4b1ba22526f462ba9219c6301a00215d022b0f94692d95779013476c1f53", "route": "/apps/topology", "aliases": []}, enabled: true, frontend: frontend9 }]
