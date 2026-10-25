import InspectionWorkspace from '../../../../../src/apps/inspection/frontend/InspectionWorkspace'
import DeviceInventoryWorkspace from '../../../../../src/apps/device_management/frontend/DeviceInventoryWorkspace'
export default function DeviceOperationsWorkspace({mode = 'devices'}: {mode?: 'devices' | 'patrol'}) {
 return mode === 'patrol' ? <InspectionWorkspace /> : <DeviceInventoryWorkspace />
}
