// Generated from src/apps/*/app.yaml. Do not edit.
import frontend0 from "../../../../src/apps/materials/frontend/index"
import frontend1 from "../../../../src/apps/assistant/frontend/index"
import frontend2 from "../../../../src/apps/delivery_probe/frontend/index"
import frontend3 from "../../../../src/apps/firewall_policy/frontend/index"
import frontend4 from "../../../../src/apps/inspection/frontend/index"
import frontend5 from "../../../../src/apps/official_documents/frontend/index"
import frontend6 from "../../../../src/apps/rule_governance/frontend/index"
export const packagedApps = [{ ...{"id": "materials", "name": "资料管理", "version": "1.2.0", "icon": "document", "digest": "da14fa61cd9abd5b83e75ddcda9a641435cee27ea78cf878e3254df02031c782", "route": "/apps/materials", "aliases": []}, enabled: true, frontend: frontend0 },
{ ...{"id": "assistant", "name": "知识问答", "version": "0.6.1", "icon": "document", "digest": "b21b37903b2330210db6db85c6b1fff82aa4d943b0a4e9776636e02dcc1485bc", "route": "/apps/assistant", "aliases": []}, enabled: true, frontend: frontend1 },
{ ...{"id": "delivery-probe", "name": "能力包接入验证", "version": "1.1.0", "icon": "document", "digest": "b3acdcb213d4d3176c041883adc284b76222d31c6ebd81fc0928e96e27ddbc4c", "route": "/apps/delivery-probe", "aliases": []}, enabled: true, frontend: frontend2 },
{ ...{"id": "firewall-policy", "name": "防火墙策略生成", "version": "1.0.1", "icon": "shield", "digest": "4356e1e32fcdd8b4a48b9c395eba9a0525f33820dcb35ae0a5bba2cef0e8e732", "route": "/apps/firewall-policy", "aliases": []}, enabled: true, frontend: frontend3 },
{ ...{"id": "inspection", "name": "网络巡检", "version": "1.2.0", "icon": "radar", "digest": "27e667a2e949fa46fe4212d244b9ea2ddafa88a541bf1c83fb50b3169ab8e9a4", "route": "/apps/inspection", "aliases": ["patrol"]}, enabled: true, frontend: frontend4 },
{ ...{"id": "official-documents", "name": "公文写作", "version": "1.1.0", "icon": "document", "digest": "383a2616ca397fdfe5fc4f3966119d1546cf36a8b33d52c7c7d5f09fea18a382", "route": "/apps/official-documents", "aliases": ["official-document"]}, enabled: import.meta.env.VITE_OFFICIAL_DOCUMENTS_ENABLED !== "false", frontend: frontend5 },
{ ...{"id": "rule-governance", "name": "规则治理", "version": "1.0.0", "icon": "rules", "digest": "7ec8302ee4ef2c6d6bc71856962e86945ec09e837140a4fb67b3c71b7ca7910f", "route": "/apps/rule-governance", "aliases": ["patrol-rules"]}, enabled: true, frontend: frontend6 }]
