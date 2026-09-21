import InspectionWorkspace from '../../../../../src/apps/inspection/frontend/InspectionWorkspace'
import DeviceInventoryWorkspace from '../../../../../src/apps/rule_governance/frontend/DeviceInventoryWorkspace'
export default function DeviceOperationsWorkspace({mode = 'devices'}: {mode?: 'devices' | 'patrol'}) {
 return mode === 'patrol' ? <InspectionWorkspace /> : <DeviceInventoryWorkspace />
}
