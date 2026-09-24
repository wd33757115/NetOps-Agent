// Generated from src/apps/*/app.yaml. Do not edit.
import frontend0 from "../../../../src/apps/materials/frontend/index"
import frontend1 from "../../../../src/apps/assistant/frontend/index"
import frontend2 from "../../../../src/apps/delivery_probe/frontend/index"
import frontend3 from "../../../../src/apps/device_management/frontend/index"
import frontend4 from "../../../../src/apps/firewall_policy/frontend/index"
import frontend5 from "../../../../src/apps/inspection/frontend/index"
import frontend6 from "../../../../src/apps/official_documents/frontend/index"
import frontend7 from "../../../../src/apps/rule_governance/frontend/index"
export const packagedApps = [{ ...{"id": "materials", "name": "资料管理", "version": "1.2.0", "icon": "document", "digest": "da14fa61cd9abd5b83e75ddcda9a641435cee27ea78cf878e3254df02031c782", "route": "/apps/materials", "aliases": []}, enabled: true, frontend: frontend0 },
{ ...{"id": "assistant", "name": "知识问答", "version": "0.6.2", "icon": "document", "digest": "940a7a98a48f8760bb19e3d38ce0b72f62131fd4aa04a9c262dee6dd4abed7a9", "route": "/apps/assistant", "aliases": []}, enabled: true, frontend: frontend1 },
{ ...{"id": "delivery-probe", "name": "能力包接入验证", "version": "1.1.0", "icon": "document", "digest": "b3acdcb213d4d3176c041883adc284b76222d31c6ebd81fc0928e96e27ddbc4c", "route": "/apps/delivery-probe", "aliases": []}, enabled: true, frontend: frontend2 },
{ ...{"id": "device-management", "name": "设备管理", "version": "1.0.1", "icon": "server", "digest": "da56ffb60d9e6453cb3b8ea42097a4d6d139e4f18a2ae62ed0dc3526795e5efa", "route": "/apps/device-management", "aliases": []}, enabled: true, frontend: frontend3 },
{ ...{"id": "firewall-policy", "name": "防火墙策略生成", "version": "1.0.1", "icon": "shield", "digest": "4356e1e32fcdd8b4a48b9c395eba9a0525f33820dcb35ae0a5bba2cef0e8e732", "route": "/apps/firewall-policy", "aliases": []}, enabled: true, frontend: frontend4 },
{ ...{"id": "inspection", "name": "网络巡检", "version": "1.2.7", "icon": "radar", "digest": "a4132aba0f47f349fce3e1c192cbf007b0efe3ee0bb6b46ac24fe5a1ea5f8f8c", "route": "/apps/inspection", "aliases": ["patrol"]}, enabled: true, frontend: frontend5 },
{ ...{"id": "official-documents", "name": "公文写作", "version": "1.1.0", "icon": "document", "digest": "383a2616ca397fdfe5fc4f3966119d1546cf36a8b33d52c7c7d5f09fea18a382", "route": "/apps/official-documents", "aliases": ["official-document"]}, enabled: import.meta.env.VITE_OFFICIAL_DOCUMENTS_ENABLED !== "false", frontend: frontend6 },
{ ...{"id": "rule-governance", "name": "规则治理", "version": "1.4.3", "icon": "rules", "digest": "1d4fb6ac8f66c3a99b99ac5b63e4cfeb11cd854abebb4863cff8681181c8f3dc", "route": "/apps/rule-governance", "aliases": ["patrol-rules"]}, enabled: true, frontend: frontend7 }]
