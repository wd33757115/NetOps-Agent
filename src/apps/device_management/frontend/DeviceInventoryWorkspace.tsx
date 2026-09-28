import type { DeviceBulkRequest } from '@netops/services/api';
import { businessHref } from '@netops/apps/appRoutes';
import React, { useMemo, useState } from 'react';
import { Alert, AutoComplete, Button, Descriptions, Drawer, Dropdown, Form, Input, Modal, Row, Col, Select, Space, Table, Tabs, Tag, Typography, Upload, message } from 'antd';
import { DeleteOutlined, DownloadOutlined, EditOutlined, EyeOutlined, ImportOutlined, LinkOutlined, MoreOutlined, PlusOutlined, ReloadOutlined, SafetyCertificateOutlined, SearchOutlined } from '@ant-design/icons';
import { useMutation, useQuery } from 'react-query';
import { useNavigate, useSearchParams } from 'react-router-dom';
import DeviceWorkspaceLayout from './DeviceWorkspaceLayout';
import WorkflowRunMonitor from '@netops/components/skills/WorkflowRunMonitor';
import PatrolCommandConfiguration from './components/PatrolCommandConfiguration';
import DeviceAssetInventoryPanel, { type DeviceAssetFilters } from './components/DeviceAssetInventoryPanel';
import EvidenceTimeline from '@netops/components/devices/EvidenceTimeline';
import { deviceApi, type DeviceBulkPreview, type DeviceBulkDeletePreview, type DeviceBulkUpdate, type DeviceConnectionHealthRecord, type DeviceCredentialInspection, type DeviceDynamicGroup, type DeviceDynamicGroupPreview, type DeviceEffectivePlanPreview, type DeviceIdentityDiscoveryResult, type DeviceImportPayload, type DeviceConnectivityTestResult, type DeviceImportPreview, type DeviceImportTemplate, type DeviceUpsertPayload, type ManagedDevice } from '@netops/services/api';
import { useIsMobile } from '@netops/hooks/useBreakpoint';
const codeStyle: React.CSSProperties = {
  background: '#f5f5f5',
  border: '1px solid #e5e7eb',
  borderRadius: 6,
  fontFamily: 'Consolas, Monaco, monospace',
  margin: 0,
  overflow: 'auto',
  padding: 12,
  whiteSpace: 'pre-wrap'
};
interface DeviceFormValues extends Omit<DeviceUpsertPayload, 'groups' | 'attributes'> {
  groups?: string;
  attributes_json?: string;
}
interface BulkFormValues extends Omit<DeviceBulkUpdate, 'groups'> {
  groups?: string;
  dynamic_group_id?: string;
  credential_ref?: string;
}
const fileToBase64 = (file: File) => new Promise<string>((resolve, reject) => {
  const reader = new FileReader();
  reader.onload = () => {
    const result = String(reader.result || '');
    resolve(result.includes(',') ? result.split(',').pop() || '' : result);
  };
  reader.onerror = () => reject(reader.error || new Error('File read failed'));
  reader.readAsDataURL(file);
});
const downloadTextFile = (filename: string, content: string, mimeType = 'text/csv;charset=utf-8') => {
  const blob = new Blob([content], {
    type: mimeType
  });
  const url = window.URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.URL.revokeObjectURL(url);
};
const extractErrorMessage = (error: any, fallback: string) => {
  const detail = error?.response?.data?.detail;
  if (!detail) return error?.message || fallback;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail?.errors) && detail.errors.length) {
    return `${detail.message || fallback}：${detail.errors.join('；')}`;
  }
  return detail.message || fallback;
};
const statusLabel = (status?: string) => {
  const labels: Record<string, string> = {
    active: '启用',
    blocked: '阻断',
    cancelled: '已取消',
    completed: '已完成',
    connected: '已连接',
    create: '新增',
    degraded: '降级',
    disabled: '停用',
    eligible: '需结构化',
    error: '异常',
    failed: '失败',
    healthy: '健康',
    ignored: '忽略',
    in_progress: '进行中',
    maintenance: '维护中',
    not_required: '无需检查',
    pending: '等待中',
    preflight_failed: '预检失败',
    queued: '排队中',
    ready: '就绪',
    not_evaluated: '未评估',
    ok: '正常',
    running: '运行中',
    success: '成功',
    template_missing: '缺少模板',
    unavailable: '不可用',
    unhealthy: '不健康',
    unknown: '未知',
    update: '更新',
    warning: '告警'
  };
  return status ? labels[status] || status : '未知';
};
interface DynamicGroupFormValues {
  name: string;
  description?: string;
  selector_json: string;
  status?: string;
}
interface DynamicGroupRule {
  id: string;
  field: string;
  values: string[];
}
const dynamicGroupSelectorFields = [{
  value: 'vendor',
  label: '厂商'
}, {
  value: 'model',
  label: '型号'
}, {
  value: 'family',
  label: '设备族'
}, {
  value: 'platform',
  label: '平台'
}, {
  value: 'device_type',
  label: '设备类型'
}, {
  value: 'site',
  label: '站点'
}, {
  value: 'role',
  label: '角色'
}, {
  value: 'group',
  label: '分组'
}, {
  value: 'groups',
  label: '分组列表'
}, {
  value: 'status',
  label: '状态'
}, {
  value: 'last_connection_status',
  label: '连接状态'
}];
const splitGroups = (value?: string): string[] => (value || '').split(',').map(item => item.trim()).filter(Boolean);
const parseAttributes = (value?: string): Record<string, unknown> | undefined => {
  const text = (value || '').trim();
  if (!text) return undefined;
  return JSON.parse(text) as Record<string, unknown>;
};
const buildDeviceAttributes = (attributesJson?: string): Record<string, unknown> | undefined => {
  const attributes = {
    ...(parseAttributes(attributesJson) || {})
  };
  delete attributes.patrol_commands;
  return Object.keys(attributes).length ? attributes : undefined;
};
const createRuleId = () => `${Date.now()}-${Math.random().toString(16).slice(2)}`;
const selectorToRules = (selector?: Record<string, unknown>): DynamicGroupRule[] => {
  if (!selector) return [];
  return Object.entries(selector).filter(([, value]) => value !== undefined && value !== null && value !== '').map(([field, value]) => ({
    id: createRuleId(),
    field,
    values: Array.isArray(value) ? value.map(item => String(item)).filter(Boolean) : [String(value)].filter(Boolean)
  }));
};
const rulesToSelector = (rules: DynamicGroupRule[]): Record<string, unknown> => {
  const selector: Record<string, unknown> = {};
  rules.forEach(rule => {
    const field = rule.field.trim();
    const values = (rule.values || []).map(item => item.trim()).filter(Boolean);
    if (!field || values.length === 0) return;
    selector[field] = values.length === 1 ? values[0] : values;
  });
  return selector;
};
const formatDateTime = (value?: string | null) => {
  if (!value) return '-';
  try {
    return new Date(value).toLocaleString();
  } catch {
    return value;
  }
};
const importExample = ['device_name,management_ip,vendor,model,family,platform,device_type,os_version,serial_number,site,role,status,credential_ref,command_profile,groups,attributes_json', 'SW1,10.0.0.1,Cisco,WS-C2960XR-24TS-I,IOS-C2960,ios,access_switch,15.2(7)E10,FOC1234,xian,access,active,env:NETOPS_PASSWORD,browser-test-patrol,"core,xian","{""collector_options"": {""read_timeout"": 60}}"'].join('\n');
export interface DeviceOperationsWorkspaceProps {
  mode?: 'devices' | 'patrol';
}
const DeviceOperationsWorkspace: React.FC<DeviceOperationsWorkspaceProps> = () => {
  const isMobile = useIsMobile();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedTab = searchParams.get('tab') || 'overview';
  const activeTab = ['overview', 'commands'].includes(requestedTab) ? requestedTab : 'overview';
  const [form] = Form.useForm<DeviceFormValues>();
  const [bulkForm] = Form.useForm<BulkFormValues>();
  const [dynamicGroupForm] = Form.useForm<DynamicGroupFormValues>();
  const [filters, setFilters] = useState<DeviceAssetFilters>({
    keyword: searchParams.get('scope_keyword') || '',
    vendor: searchParams.get('scope_vendor') || '',
    model: searchParams.get('scope_model') || '',
    family: searchParams.get('scope_family') || '',
    platform: searchParams.get('scope_platform') || '',
    deviceType: searchParams.get('scope_deviceType') || '',
    site: searchParams.get('scope_site') || '',
    role: searchParams.get('scope_role') || '',
    group: searchParams.get('scope_group') || '',
    status: searchParams.get('scope_status') || '',
    connectionStatus: searchParams.get('scope_connectionStatus') || ''
  });
  const [selected, setSelected] = useState<ManagedDevice | null>(null);
  const [plan, setPlan] = useState<DeviceEffectivePlanPreview | null>(null);
  const [connectivityResult, setConnectivityResult] = useState<DeviceConnectivityTestResult | null>(null);
  const [identityDiscoveryResult, setIdentityDiscoveryResult] = useState<DeviceIdentityDiscoveryResult | null>(null);
  const [identityDiscoveryTargetId, setIdentityDiscoveryTargetId] = useState<string | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<ManagedDevice | null>(null);
  const [importOpen, setImportOpen] = useState(false);
  const [importText, setImportText] = useState(importExample);
  const [importFilePayload, setImportFilePayload] = useState<DeviceImportPayload | null>(null);
  const [importPreview, setImportPreview] = useState<DeviceImportPreview | null>(null);
  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>(() => searchParams.has('device_id') ? searchParams.getAll('device_id') : []);
  const [bulkOpen, setBulkOpen] = useState(false);
  const [bulkPreview, setBulkPreview] = useState<DeviceBulkPreview | null>(null);
  const [bulkDeleteOpen, setBulkDeleteOpen] = useState(false);
  const [bulkDeletePreview, setBulkDeletePreview] = useState<DeviceBulkDeletePreview | null>(null);
  const [healthOpen, setHealthOpen] = useState(false);
  const [evidenceOpen, setEvidenceOpen] = useState(false);
  const [evidenceDevice, setEvidenceDevice] = useState<ManagedDevice | null>(null);
  const [evidenceRunId, setEvidenceRunId] = useState<string | null>(null);
  const [evidenceBatchId, setEvidenceBatchId] = useState<string | null>(null);
  const [evidenceTab, setEvidenceTab] = useState<'result' | 'execution'>('result');
  const [dynamicGroupOpen, setDynamicGroupOpen] = useState(false);
  const [editingDynamicGroup, setEditingDynamicGroup] = useState<DeviceDynamicGroup | null>(null);
  const [dynamicGroupPreview, setDynamicGroupPreview] = useState<DeviceDynamicGroupPreview | null>(null);
  const [dynamicGroupRules, setDynamicGroupRules] = useState<DynamicGroupRule[]>([]);
  const [credentialInspection, setCredentialInspection] = useState<DeviceCredentialInspection | null>(null);
  const [devicePage, setDevicePage] = useState(1);
  const [devicePageSize, setDevicePageSize] = useState(20);
  const queryParams = useMemo(() => {
    const keyword = filters.keyword.trim();
    return {
      device_name: keyword || undefined,
      vendor: filters.vendor || undefined,
      model: filters.model || undefined,
      family: filters.family || undefined,
      platform: filters.platform || undefined,
      device_type: filters.deviceType || undefined,
      site: filters.site || undefined,
      role: filters.role || undefined,
      group: filters.group || undefined,
      status: filters.status || undefined,
      connection_status: filters.connectionStatus || undefined,
      include_patrol_readiness: 'true',
      limit: devicePageSize,
      offset: (devicePage - 1) * devicePageSize
    };
  }, [filters, devicePage, devicePageSize]);
  const {
    data,
    error: devicesError,
    isError: devicesIsError,
    isLoading,
    refetch
  } = useQuery(['devices', queryParams], () => deviceApi.list(queryParams), {
    keepPreviousData: true
  });
  const {
    data: dynamicGroups = [],
    refetch: refetchDynamicGroups
  } = useQuery(['device-dynamic-groups'], () => deviceApi.dynamicGroups(), {
    refetchOnWindowFocus: false
  });
  const {
    data: deviceGroups = [],
    refetch: refetchDeviceGroups
  } = useQuery(['device-groups'], () => deviceApi.groups(), {
    refetchOnWindowFocus: false
  });
  const {
    data: credentialRefs = [],
    refetch: refetchCredentialRefs
  } = useQuery(['device-credentials'], () => deviceApi.credentials(), {
    refetchOnWindowFocus: false,
    enabled: true
  });
  const {
    data: importTemplate
  } = useQuery<DeviceImportTemplate>(['device-import-template'], () => deviceApi.importTemplate(), {
    refetchOnWindowFocus: false,
    enabled: true
  });
  const {
    data: healthHistory,
    refetch: refetchHealthHistory
  } = useQuery(['device-connection-health', selected?.device_id], () => deviceApi.connectionHealth(selected!.device_id, {
    limit: 50
  }), {
    enabled: Boolean(selected?.device_id && healthOpen),
    refetchOnWindowFocus: false
  });
  const {
    data: evidenceTimeline
  } = useQuery(['device-evidence-timeline', evidenceDevice?.device_id, evidenceRunId, evidenceBatchId], () => deviceApi.evidenceTimeline(evidenceDevice!.device_id, {
    limit: evidenceRunId || evidenceBatchId ? 1 : 10,
    workflow_run_id: evidenceRunId || undefined,
    patrol_batch_id: evidenceBatchId || undefined
  }), {
    enabled: Boolean(evidenceDevice?.device_id && evidenceOpen),
    refetchOnWindowFocus: false
  });
  const {
    data: executionPlans
  } = useQuery(['device-execution-plans', evidenceDevice?.device_id], () => deviceApi.executionPlans(evidenceDevice!.device_id, {
    limit: 10
  }), {
    enabled: Boolean(evidenceDevice?.device_id && evidenceOpen),
    refetchOnWindowFocus: false
  });
  const saveDevice = useMutation(async (values: DeviceFormValues) => {
    const payload: DeviceUpsertPayload = {
      ...values,
      groups: splitGroups(values.groups),
      attributes: buildDeviceAttributes(values.attributes_json)
    };
    delete (payload as any).attributes_json;
    if (editing) {
      return deviceApi.update(editing.device_id, payload);
    }
    return deviceApi.upsert(payload);
  }, {
    onSuccess: async () => {
      message.success(editing ? '设备已更新' : '设备已保存');
      setModalOpen(false);
      setEditing(null);
      form.resetFields();
      await refetch();
      await refetchDeviceGroups();
    },
    onError: (error: any) => message.error(extractErrorMessage(error, '保存设备失败'))
  });
  const deleteDevice = useMutation((device: ManagedDevice) => deviceApi.delete(device.device_id), {
    onSuccess: async (_, device) => {
      message.success(`设备 ${device.device_name || device.device_id} 已从资产清单删除`);
      if (selected?.device_id === device.device_id) {
        setSelected(null);
        setPlan(null);
        setConnectivityResult(null);
      }
      setSelectedRowKeys(keys => keys.filter(key => String(key) !== device.device_id));
      await refetch();
    },
    onError: (error: any) => message.error(extractErrorMessage(error, '删除设备失败'))
  });
  const setDeviceStatus = useMutation(({
    device,
    status
  }: {
    device: ManagedDevice;
    status: 'active' | 'disabled';
  }) => deviceApi.setStatus(device.device_id, status), {
    onSuccess: async result => {
      message.success(`设备 ${result.device?.device_name || result.device?.device_id || ''} 状态已更新`);
      await refetch();
    },
    onError: (error: any) => message.error(extractErrorMessage(error, '更新设备状态失败'))
  });
  const previewPlan = useMutation((device: ManagedDevice) => deviceApi.effectivePlan(device.device_id, {
    action: 'patrol',
    mode: 'production'
  }), {
    onSuccess: setPlan,
    onError: (error: any) => message.error(extractErrorMessage(error, '预览执行计划失败'))
  });
  const testConnectivity = useMutation((device: ManagedDevice) => deviceApi.testConnectivity(device.device_id), {
    onSuccess: result => {
      setConnectivityResult(result);
      setSelected(result.device);
      refetch();
      refetchHealthHistory();
      if (result.success) {
        message.success('连接测试成功');
      } else {
        message.error(result.error || result.failure_code || '连接测试失败');
      }
    },
    onError: (error: any) => message.error(extractErrorMessage(error, '连接测试失败'))
  });
  const discoverIdentity = useMutation((device: ManagedDevice) => {
    setIdentityDiscoveryTargetId(device.device_id);
    return deviceApi.discoverIdentity(device.device_id);
  }, {
    onSuccess: async result => {
      setIdentityDiscoveryResult(result);
      setSelected(result.device);
      await refetch();
      refetchHealthHistory();
      if (result.success) {
        message.success(`设备身份已识别：${result.identity?.vendor || '-'} ${result.identity?.model || '-'}`);
      } else {
        message.warning(result.error || result.failure_code || '设备身份未能可靠识别');
      }
    },
    onError: (error: any) => message.error(extractErrorMessage(error, '设备身份识别失败')),
    onSettled: () => setIdentityDiscoveryTargetId(null)
  });
  const importPayload = (): DeviceImportPayload => importFilePayload || {
    content: importText,
    format: 'csv',
    filename: 'pasted-devices.csv'
  };
  const previewImport = useMutation(() => deviceApi.previewImport(importPayload()), {
    onSuccess: setImportPreview,
    onError: (error: any) => message.error(extractErrorMessage(error, '导入预览失败'))
  });
  const applyImport = useMutation(() => deviceApi.applyImport(importPayload()), {
    onSuccess: async result => {
      message.success('已导入 ' + result.applied_count + ' 台设备');
      setImportPreview(result);
      await refetch();
      await refetchDeviceGroups();
    },
    onError: (error: any) => message.error(extractErrorMessage(error, '应用导入失败'))
  });
  const saveDynamicGroup = useMutation(async (values: DynamicGroupFormValues) => {
    const selector = parseAttributes(values.selector_json);
    if (!selector || Object.keys(selector).length === 0) {
      throw new Error('选择器 JSON 不能为空');
    }
    return deviceApi.saveDynamicGroup({
      name: values.name,
      description: values.description,
      selector,
      status: values.status || 'active'
    }, editingDynamicGroup?.id);
  }, {
    onSuccess: async result => {
      message.success('动态分组已保存');
      setEditingDynamicGroup(result.group);
      setDynamicGroupPreview(null);
      await refetchDynamicGroups();
    },
    onError: (error: any) => message.error(extractErrorMessage(error, '保存动态分组失败'))
  });
  const previewDynamicGroup = useMutation((groupId: string) => deviceApi.previewDynamicGroup(groupId, {
    limit: 100
  }), {
    onSuccess: setDynamicGroupPreview,
    onError: (error: any) => message.error(extractErrorMessage(error, '预览动态分组失败'))
  });
  const setDynamicGroupStatus = useMutation(({
    groupId,
    status
  }: {
    groupId: string;
    status: string;
  }) => deviceApi.setDynamicGroupStatus(groupId, status), {
    onSuccess: async () => {
      message.success('动态分组状态已更新');
      await refetchDynamicGroups();
    },
    onError: (error: any) => message.error(extractErrorMessage(error, '更新动态分组状态失败'))
  });
  const inspectCredential = useMutation((credentialRef?: string) => deviceApi.inspectCredential(credentialRef), {
    onSuccess: result => {
      setCredentialInspection(result);
      if (result.available) {
        message.success('凭据引用可用');
      } else {
        message.warning(result.failure_code || '凭据引用不可用');
      }
    },
    onError: (error: any) => message.error(extractErrorMessage(error, '检查凭据失败'))
  });
  const currentFilterSelector = (): Record<string, unknown> => {
    const selector: Record<string, unknown> = {
      device_name: queryParams.device_name,
      vendor: queryParams.vendor,
      model: queryParams.model,
      family: queryParams.family,
      platform: queryParams.platform,
      device_type: queryParams.device_type,
      site: queryParams.site,
      role: queryParams.role,
      group: queryParams.group,
      status: queryParams.status,
      last_connection_status: queryParams.connection_status
    };
    Object.keys(selector).forEach(key => {
      if (!selector[key]) delete selector[key];
    });
    return selector;
  };
  const openProfileBinding = () => {
    const next = new URLSearchParams(searchParams);
    next.set('tab', 'commands');
    setSearchParams(next);
  };
  const buildBulkPayload = (values: BulkFormValues): DeviceBulkRequest => {
    const update: DeviceBulkUpdate = {
      command_profile: values.command_profile || undefined,
      credential_ref: values.credential_ref || undefined,
      status: values.status || undefined,
      platform: values.platform || undefined,
      device_type: values.device_type || undefined,
      site: values.site || undefined,
      role: values.role || undefined,
      groups: values.groups ? splitGroups(values.groups) : undefined,
      group_mode: values.group_mode || 'replace'
    };
    Object.keys(update).forEach(key => {
      const value = update[key as keyof DeviceBulkUpdate];
      if (value === undefined || Array.isArray(value) && value.length === 0) {
        delete update[key as keyof DeviceBulkUpdate];
      }
    });
    return {
      device_ids: selectedRowKeys.length ? selectedRowKeys.map(String) : undefined,
      dynamic_group_id: selectedRowKeys.length ? undefined : values.dynamic_group_id || undefined,
      filters: selectedRowKeys.length || values.dynamic_group_id ? undefined : {
        device_name: queryParams.device_name,
        vendor: queryParams.vendor,
        model: queryParams.model,
        family: queryParams.family,
        platform: queryParams.platform,
        device_type: queryParams.device_type,
        site: queryParams.site,
        role: queryParams.role,
        group: queryParams.group,
        status: queryParams.status,
        connection_status: queryParams.connection_status
      },
      update
    };
  };
  const previewBulk = useMutation(async () => {
    const values = await bulkForm.validateFields();
    return deviceApi.previewBulk(buildBulkPayload(values));
  }, {
    onSuccess: setBulkPreview,
    onError: (error: any) => message.error(extractErrorMessage(error, '批量更新预览失败'))
  });
  const applyBulk = useMutation(async () => {
    const values = await bulkForm.validateFields();
    return deviceApi.applyBulk(buildBulkPayload(values));
  }, {
    onSuccess: async result => {
      message.success('已更新 ' + result.applied_count + ' 台设备');
      setBulkPreview(result);
      setSelectedRowKeys([]);
      await refetch();
      await refetchDeviceGroups();
    },
    onError: (error: any) => message.error(extractErrorMessage(error, '批量更新失败'))
  });
  const buildBulkSelectionPayload = () => ({
    device_ids: selectedRowKeys.length ? selectedRowKeys.map(String) : undefined,
    filters: selectedRowKeys.length ? undefined : {
      device_name: queryParams.device_name,
      vendor: queryParams.vendor,
      model: queryParams.model,
      family: queryParams.family,
      platform: queryParams.platform,
      device_type: queryParams.device_type,
      site: queryParams.site,
      role: queryParams.role,
      group: queryParams.group,
      status: queryParams.status,
      connection_status: queryParams.connection_status
    }
  });
  const previewBulkDelete = useMutation(() => deviceApi.previewBulkDelete(buildBulkSelectionPayload()), {
    onSuccess: setBulkDeletePreview,
    onError: (error: any) => message.error(extractErrorMessage(error, '批量删除预览失败'))
  });
  const applyBulkDelete = useMutation(() => deviceApi.bulkDelete(buildBulkSelectionPayload()), {
    onSuccess: async result => {
      message.success('已删除 ' + result.deleted_count + ' 台设备资产，历史证据已保留');
      setBulkDeletePreview(null);
      setBulkDeleteOpen(false);
      setSelectedRowKeys([]);
      await refetch();
      await refetchDeviceGroups();
    },
    onError: (error: any) => message.error(extractErrorMessage(error, '批量删除失败'))
  });
  const applyBulkStatus = useMutation((status: 'active' | 'disabled') => deviceApi.applyBulk({
    ...buildBulkSelectionPayload(),
    update: {
      status
    }
  }), {
    onSuccess: async result => {
      message.success('已更新 ' + result.applied_count + ' 台设备状态');
      setSelectedRowKeys([]);
      await refetch();
    },
    onError: (error: any) => message.error(extractErrorMessage(error, '批量状态更新失败'))
  });
  const confirmBulkStatus = (status: 'active' | 'disabled') => {
    Modal.confirm({
      title: status === 'active' ? '批量启用设备' : '批量停用设备',
      content: selectedRowKeys.length ? '将操作已勾选的 ' + selectedRowKeys.length + ' 台设备。' : '未勾选设备时，将操作当前筛选结果中的设备。',
      okText: status === 'active' ? '启用' : '停用',
      cancelText: '取消',
      onOk: () => applyBulkStatus.mutateAsync(status)
    });
  };
  const openDetail = async (device: ManagedDevice) => {
    setSelected(device);
    setPlan(null);
    setConnectivityResult(null);
    setIdentityDiscoveryResult(null);
    previewPlan.mutate(device);
  };
  const openCreate = () => {
    setEditing(null);
    setCredentialInspection(null);
    form.resetFields();
    form.setFieldsValue({
      status: 'active'
    });
    refetchCredentialRefs();
    setModalOpen(true);
  };
  const openEdit = (device: ManagedDevice) => {
    setEditing(device);
    setCredentialInspection(null);
    refetchCredentialRefs();
    form.setFieldsValue({
      device_name: device.device_name,
      management_ip: device.management_ip || device.ip,
      username: device.username,
      credential_ref: device.credential_ref,
      vendor: device.vendor,
      model: device.model,
      family: device.family,
      platform: device.platform,
      device_type: device.device_type,
      os_version: device.os_version,
      serial_number: device.serial_number,
      site: device.site,
      role: device.role,
      status: device.status || 'active',
      command_profile: device.command_file,
      groups: (device.groups || []).join(', '),
      attributes_json: device.attributes ? JSON.stringify(device.attributes, null, 2) : undefined
    });
    setModalOpen(true);
  };
  const openDynamicGroupCreate = () => {
    setEditingDynamicGroup(null);
    setDynamicGroupPreview(null);
    const initialSelector = Object.fromEntries(Object.entries({
      site: filters.site || undefined,
      role: filters.role || undefined
    }).filter(([, value]) => Boolean(value)));
    const initialRules = selectorToRules(initialSelector);
    setDynamicGroupRules(initialRules);
    dynamicGroupForm.setFieldsValue({
      name: '',
      description: '',
      selector_json: JSON.stringify(initialSelector, null, 2),
      status: 'active'
    });
    setDynamicGroupOpen(true);
  };
  const openDynamicGroupEdit = (group: DeviceDynamicGroup) => {
    setEditingDynamicGroup(group);
    setDynamicGroupPreview(null);
    setDynamicGroupRules(selectorToRules(group.selector || {}));
    dynamicGroupForm.setFieldsValue({
      name: group.name,
      description: group.description || '',
      selector_json: JSON.stringify(group.selector || {}, null, 2),
      status: group.status || 'active'
    });
    setDynamicGroupOpen(true);
  };
  const syncDynamicGroupRules = (rules: DynamicGroupRule[]) => {
    setDynamicGroupRules(rules);
    dynamicGroupForm.setFieldsValue({
      selector_json: JSON.stringify(rulesToSelector(rules), null, 2)
    });
  };
  const updateDynamicGroupRule = (id: string, patch: Partial<DynamicGroupRule>) => {
    syncDynamicGroupRules(dynamicGroupRules.map(rule => rule.id === id ? {
      ...rule,
      ...patch
    } : rule));
  };
  const removeDynamicGroupRule = (id: string) => {
    syncDynamicGroupRules(dynamicGroupRules.filter(rule => rule.id !== id));
  };
  const addDynamicGroupRule = () => {
    syncDynamicGroupRules([...dynamicGroupRules, {
      id: createRuleId(),
      field: 'site',
      values: []
    }]);
  };
  const devices = useMemo(() => data?.items || [], [data?.items]);
  const credentialOptions = credentialRefs.map(item => ({
    value: item.ref,
    label: item.ref + (item.available ? '' : '（不可用）')
  }));
  const readinessColor = (status?: string) => {
    if (status === 'ready') return 'green';
    if (status === 'blocked' || status === 'error') return 'red';
    if (status === 'not_required') return 'default';
    return 'blue';
  };
  const readinessSourceText = (value: ManagedDevice['patrol_readiness']) => {
    if (!value) return '-';
    const profile = value.profile_name || '-';
    if (value.policy_source === 'device_profile') return `设备指定命令集：${profile}`;
    if (value.policy_source === 'profile_binding') return `绑定命令集：${profile}`;
    if (value.policy_source === 'default') return `默认命令集：${profile}`;
    if (value.policy_source) return `${value.policy_source}：${profile}`;
    return profile;
  };
  const runtimeAssetIssues = (value?: ManagedDevice['patrol_readiness'] | DeviceEffectivePlanPreview['coverage_summary']) => {
    const health = value?.runtime_asset_health;
    if (!health || ['ready', 'not_required', 'not_checked'].includes(String(health.status || ''))) return 0;
    if (typeof health.missing_count === 'number') return health.missing_count;
    return (health.missing_observation_contracts?.length || 0) + (health.missing_change_policies?.length || 0);
  };
  const runtimeAssetIssueText = (value?: ManagedDevice['patrol_readiness'] | DeviceEffectivePlanPreview['coverage_summary']) => {
    const health = value?.runtime_asset_health;
    if (!health || ['ready', 'not_required', 'not_checked'].includes(String(health.status || ''))) return null;
    const missingContracts = health.missing_observation_contracts || [];
    const missingPolicies = health.missing_change_policies || [];
    const parts = [missingContracts.length ? `缺 ObservationContract：${missingContracts.join(', ')}` : '', missingPolicies.length ? `缺 ChangePolicy：${missingPolicies.join(', ')}` : '', health.error ? `错误：${health.error}` : ''].filter(Boolean);
    return parts.length ? parts.join('；') : `运行时资产状态：${health.status || 'unknown'}`;
  };
  const connectionColor = (status?: string) => {
    if (status === 'connected') return 'green';
    if (status === 'failed' || status === 'preflight_failed') return 'red';
    return 'default';
  };
  const confirmDeleteDevice = (device: ManagedDevice) => {
    Modal.confirm({
      title: '删除设备资产',
      content: '设备 ' + (device.device_name || device.device_id) + ' 将从资产清单中删除，历史巡检详情和执行记录会保留。',
      okText: '删除',
      okButtonProps: {
        danger: true,
        loading: deleteDevice.isLoading
      },
      cancelText: '取消',
      onOk: () => deleteDevice.mutateAsync(device)
    });
  };
  const columns = [{
    title: '设备',
    dataIndex: 'device_name',
    width: 220
  }, {
    title: 'IP',
    dataIndex: 'ip',
    width: 130
  }, {
    title: '厂商',
    dataIndex: 'vendor',
    width: 110
  }, {
    title: '型号',
    dataIndex: 'model',
    width: 170
  }, {
    title: '设备族',
    dataIndex: 'family',
    width: 150
  }, {
    title: '平台',
    dataIndex: 'platform',
    width: 130,
    render: (value: string) => value || '-'
  }, {
    title: '类型',
    dataIndex: 'device_type',
    width: 150,
    render: (value: string) => value || '-'
  }, {
    title: 'OS',
    dataIndex: 'os_version',
    width: 130,
    render: (value: string) => value || '-'
  }, {
    title: '序列号',
    dataIndex: 'serial_number',
    width: 150,
    render: (value: string) => value || '-'
  }, {
    title: '站点',
    dataIndex: 'site',
    width: 130
  }, {
    title: '角色',
    dataIndex: 'role',
    width: 120
  }, {
    title: '分组',
    dataIndex: 'groups',
    render: (value: string[]) => <Space wrap>{(value || []).map(item => <Tag key={item}>{item}</Tag>)}</Space>
  }, {
    title: '状态',
    dataIndex: 'status',
    width: 100,
    render: (value: string) => <Tag color={value === 'active' ? 'green' : 'default'}>{statusLabel(value)}</Tag>
  }, {
    title: '连接状态',
    dataIndex: 'last_connection_status',
    width: 170,
    render: (value: string, row: ManagedDevice) => <Space direction="vertical" size={2}>
          <Tag color={connectionColor(value)}>{statusLabel(value || 'unknown')}</Tag>
          <Typography.Text type={value === 'connected' ? 'secondary' : 'danger'} title={row.last_connection_error || undefined}>
            {row.last_connection_checked_at ? formatDateTime(row.last_connection_checked_at) : '未检查'}
          </Typography.Text>
        </Space>
  }, {
    title: '巡检就绪',
    dataIndex: 'patrol_readiness',
    width: 170,
    render: (value: ManagedDevice['patrol_readiness']) => {
      if (!value) return '-';
      const parserIssues = value.blocked_count || 0;
      const connectionIssues = value.connection_issues?.length || 0;
      const planIssues = value.plan_violations?.length || 0;
      const assetIssues = runtimeAssetIssues(value);
      const assetIssueText = runtimeAssetIssueText(value);
      const blockerCount = connectionIssues + planIssues + assetIssues;
      const title = value.error || [readinessSourceText(value), `${value.ready_count}/${value.eligible_count} 个结构化命令已就绪`, `计划阻断 ${planIssues}`, `Parser 缺口 ${parserIssues}`, `规则资产阻断 ${assetIssues}`, `连接阻断 ${connectionIssues}`, assetIssueText].join('；');
      return <Space direction="vertical" size={2}>
            <Tag color={readinessColor(value.status)}>{statusLabel(value.status)}</Tag>
            <Typography.Text type={blockerCount ? 'danger' : 'secondary'} title={title}>
              {value.command_count} 命令 / {blockerCount} 阻断 / {parserIssues} Parser 缺口
            </Typography.Text>
            <Typography.Text type="secondary" title={title}>
              {readinessSourceText(value)}
            </Typography.Text>
          </Space>;
    }
  }, {
    title: '操作',
    width: 220,
    render: (_: unknown, row: ManagedDevice) => <Space>
          <Button icon={<EyeOutlined />} size="small" title="查看执行计划" onClick={() => openDetail(row)} />
          <Button icon={<LinkOutlined />} size="small" title="测试连接" loading={testConnectivity.isLoading && connectivityResult?.device?.device_id === row.device_id} onClick={() => {
        setSelected(row);
        setConnectivityResult(null);
        testConnectivity.mutate(row);
      }} />
          <Button icon={<SafetyCertificateOutlined />} size="small" title="识别设备身份" loading={discoverIdentity.isLoading && identityDiscoveryTargetId === row.device_id} onClick={() => {
        setSelected(row);
        setIdentityDiscoveryResult(null);
        discoverIdentity.mutate(row);
      }} />
          <Button icon={<EditOutlined />} size="small" title="编辑设备" onClick={() => openEdit(row)} />
          <Button size="small" loading={setDeviceStatus.isLoading} onClick={() => setDeviceStatus.mutate({
        device: row,
        status: row.status === 'active' ? 'disabled' : 'active'
      })}>
            {row.status === 'active' ? '停用' : '启用'}
          </Button>
          <Button danger icon={<DeleteOutlined />} size="small" title="删除设备" loading={deleteDevice.isLoading} onClick={() => confirmDeleteDevice(row)} />
        </Space>
  }];
  const importColumns = [{
    title: '行号',
    dataIndex: 'row_number',
    width: 70
  }, {
    title: '动作',
    dataIndex: 'action',
    width: 100,
    render: (value: string) => <Tag color={value === 'create' ? 'green' : value === 'update' ? 'blue' : 'red'}>{statusLabel(value)}</Tag>
  }, {
    title: '设备',
    dataIndex: ['device', 'device_name'],
    width: 180
  }, {
    title: 'IP',
    dataIndex: ['device', 'management_ip'],
    width: 130
  }, {
    title: '厂商',
    dataIndex: ['device', 'vendor'],
    width: 110
  }, {
    title: '型号',
    dataIndex: ['device', 'model'],
    width: 160
  }, {
    title: '平台',
    dataIndex: ['device', 'platform'],
    width: 120,
    render: (value: string) => value || '-'
  }, {
    title: '类型',
    dataIndex: ['device', 'device_type'],
    width: 130,
    render: (value: string) => value || '-'
  }, {
    title: '序列号',
    dataIndex: ['device', 'serial_number'],
    width: 140,
    render: (value: string) => value || '-'
  }, {
    title: '分组',
    dataIndex: ['device', 'groups'],
    width: 180,
    render: (value: string[]) => <Space wrap>{(value || []).map(item => <Tag key={item}>{item}</Tag>)}</Space>
  }, {
    title: '错误定位',
    dataIndex: 'errors',
    render: (value: string[]) => value?.length ? <Typography.Text type="danger">{value.join('; ')}</Typography.Text> : '-'
  }];
  const bulkColumns = [{
    title: '设备',
    dataIndex: 'device_name',
    width: 220
  }, {
    title: 'IP',
    dataIndex: 'ip',
    width: 130
  }, {
    title: '历史 Profile 引用',
    dataIndex: ['update', 'command_profile'],
    width: 170,
    render: (value: string) => value || '-'
  }, {
    title: '凭据引用',
    dataIndex: ['update', 'credential_ref'],
    width: 180,
    render: (value: string) => value || '-'
  }, {
    title: '状态',
    dataIndex: ['update', 'status'],
    width: 100,
    render: (value: string) => statusLabel(value) || '-'
  }, {
    title: '平台',
    dataIndex: ['update', 'platform'],
    width: 120,
    render: (value: string) => value || '-'
  }, {
    title: '设备类型',
    dataIndex: ['update', 'device_type'],
    width: 130,
    render: (value: string) => value || '-'
  }, {
    title: '站点',
    dataIndex: ['update', 'site'],
    width: 120,
    render: (value: string) => value || '-'
  }, {
    title: '角色',
    dataIndex: ['update', 'role'],
    width: 120,
    render: (value: string) => value || '-'
  }, {
    title: '分组',
    dataIndex: ['update', 'groups'],
    render: (value: string[]) => value?.length ? <Space wrap>{value.map(item => <Tag key={item}>{item}</Tag>)}</Space> : '-'
  }];
  const planCoverageColumns = [{
    title: '命令',
    dataIndex: 'command',
    width: 220,
    render: (_: unknown, row: Record<string, unknown>) => String(row.canonical_command || row.command || row.raw_command || '-')
  }, {
    title: '策略',
    dataIndex: 'policy_status',
    width: 110,
    render: (value: string) => <Tag color={value === 'eligible' ? 'blue' : 'default'}>{value === 'eligible' ? '需结构化' : statusLabel(value)}</Tag>
  }, {
    title: '覆盖状态',
    dataIndex: 'coverage_status',
    width: 140,
    render: (value: string) => {
      const status = value || 'unknown';
      const color = status === 'ready' ? 'green' : status === 'ignored' ? 'default' : 'red';
      return <Tag color={color}>{statusLabel(status)}</Tag>;
    }
  }, {
    title: '模板',
    dataIndex: 'template_id',
    width: 220,
    render: (value: string) => value || '-'
  }, {
    title: '原因',
    dataIndex: 'failure_code',
    render: (value: string, row: Record<string, unknown>) => String(value || row.parser_status || row.identity_status || '-')
  }];
  const healthColumns = [{
    title: '检查时间',
    dataIndex: 'checked_at',
    width: 190,
    render: (value: string) => formatDateTime(value)
  }, {
    title: '状态',
    dataIndex: 'status',
    width: 130,
    render: (value: string) => <Tag color={connectionColor(value)}>{statusLabel(value)}</Tag>
  }, {
    title: '来源',
    dataIndex: 'source',
    width: 150
  }, {
    title: '失败代码',
    dataIndex: 'failure_code',
    width: 170,
    render: (value: string) => value || '-'
  }, {
    title: '延迟',
    dataIndex: 'latency_ms',
    width: 110,
    render: (value: number | null) => value !== null && value !== undefined ? `${value} ms` : '-'
  }, {
    title: '错误',
    dataIndex: 'error',
    render: (value: string) => value ? <Typography.Text type="danger">{value}</Typography.Text> : '-'
  }];
  const dynamicGroupColumns = [{
    title: '分组名称',
    dataIndex: 'name',
    width: 180
  }, {
    title: '状态',
    dataIndex: 'status',
    width: 110,
    render: (value: string) => <Tag color={value === 'active' ? 'green' : 'default'}>{statusLabel(value)}</Tag>
  }, {
    title: '选择器',
    dataIndex: 'selector',
    render: (value: Record<string, unknown>) => <Typography.Text code>{JSON.stringify(value || {})}</Typography.Text>
  }, {
    title: '操作',
    width: 240,
    render: (_: unknown, row: DeviceDynamicGroup) => <Space>
          <Button size="small" icon={<EditOutlined />} onClick={() => openDynamicGroupEdit(row)}>编辑</Button>
          <Button size="small" icon={<EyeOutlined />} onClick={() => previewDynamicGroup.mutate(row.id)}>预览</Button>
          <Button size="small" onClick={() => setDynamicGroupStatus.mutate({
        groupId: row.id,
        status: row.status === 'active' ? 'disabled' : 'active'
      })}>
            {row.status === 'active' ? '停用' : '启用'}
          </Button>
        </Space>
  }];
  return <DeviceWorkspaceLayout activeKey={activeTab} onImport={() => setImportOpen(true)} onGroups={openDynamicGroupCreate} title={"设备管理"} subtitle={"维护设备资产、连接、分组与巡检配置"} toolbar={<Space wrap>
          {<Button icon={<PlusOutlined />} type="primary" onClick={openCreate}>新增设备</Button>}
          {<Button icon={<LinkOutlined />} onClick={openProfileBinding}>绑定巡检命令集</Button>}
          {<Dropdown trigger={['click']} menu={{
      items: [{
        key: 'import',
        icon: <ImportOutlined />,
        label: '导入设备',
        onClick: () => setImportOpen(true)
      }, {
        key: 'dynamic-group',
        icon: <SearchOutlined />,
        label: '动态分组',
        onClick: openDynamicGroupCreate
      }, {
        type: 'divider'
      }, {
        key: 'refresh',
        icon: <ReloadOutlined />,
        label: '刷新当前数据',
        onClick: () => refetch()
      }]
    }}>
            <Button icon={<MoreOutlined />} aria-label="更多设备操作">更多</Button>
          </Dropdown>}
          {false}
          {activeTab === 'overview' && selectedRowKeys.length > 0 && <Space wrap className="device-selection-toolbar">
              <Typography.Text type="secondary">已选择 {selectedRowKeys.length} 台</Typography.Text>
              <Button onClick={() => {
        bulkForm.resetFields();
        setBulkPreview(null);
        setBulkOpen(true);
      }}>批量更新</Button>
              <Button loading={applyBulkStatus.isLoading} onClick={() => confirmBulkStatus('active')}>启用</Button>
              <Button loading={applyBulkStatus.isLoading} onClick={() => confirmBulkStatus('disabled')}>停用</Button>
              <Button danger icon={<DeleteOutlined />} onClick={() => {
        setBulkDeletePreview(null);
        setBulkDeleteOpen(true);
        previewBulkDelete.mutate();
      }}>
                删除
              </Button>
            </Space>}
        </Space>}>
      <Tabs activeKey={activeTab} onChange={key => {
      const next = new URLSearchParams(searchParams);
      if (key === 'overview') {
        next.delete('tab');
      } else {
        next.set('tab', key);
      }
      setSearchParams(next);
    }} renderTabBar={() => <></>} items={[{
      key: 'overview',
      label: '设备资产',
      children: <Space direction="vertical" size={16} style={{
        width: '100%'
      }}>
        {devicesIsError && <Alert type="error" showIcon message="设备列表加载失败" description={extractErrorMessage(devicesError, '请检查 BFF、FastAPI 和数据库连接状态。')} />}
        <DeviceAssetInventoryPanel columns={columns.filter(column => !['family', 'platform', 'device_type', 'os_version', 'serial_number', 'role', 'groups'].includes(String(column.dataIndex || '')))} devices={devices} filters={filters} loading={isLoading} selectedRowKeys={selectedRowKeys} total={data?.total || 0} page={devicePage} pageSize={devicePageSize} onPageChange={(page, pageSize) => {
          setDevicePage(page);
          setDevicePageSize(pageSize);
        }} onFiltersChange={patch => {
          setFilters(value => ({
            ...value,
            ...patch
          }));
          setDevicePage(1);
          setSelectedRowKeys([]);
        }} onSelectedRowKeysChange={setSelectedRowKeys} />
              </Space>
    }, {
      key: 'commands',
      label: '配置命令集',
      children: <PatrolCommandConfiguration open={activeTab === 'commands'} onSaved={() => { refetch(); }} selectedDeviceIds={selectedRowKeys.map(String)} currentSelector={currentFilterSelector()} groups={deviceGroups} dynamicGroups={dynamicGroups} />

    }]} />

      <Drawer title={selected?.device_name || '设备详情'} open={Boolean(selected)} onClose={() => {
      setSelected(null);
      setPlan(null);
      setConnectivityResult(null);
      setIdentityDiscoveryResult(null);
    }} width={isMobile ? '100vw' : 760} destroyOnClose>
        {selected && <Space direction="vertical" size={16} style={{
        width: '100%'
      }}>
            <Descriptions bordered size="small" column={2}>
              <Descriptions.Item label="设备 ID" span={2}>{selected.device_id}</Descriptions.Item>
              <Descriptions.Item label="IP">{selected.ip || selected.management_ip}</Descriptions.Item>
              <Descriptions.Item label="状态">{statusLabel(selected.status)}</Descriptions.Item>
              <Descriptions.Item label="厂商">{selected.vendor}</Descriptions.Item>
              <Descriptions.Item label="型号">{selected.model}</Descriptions.Item>
              <Descriptions.Item label="设备族">{selected.family}</Descriptions.Item>
              <Descriptions.Item label="平台">{selected.platform || '-'}</Descriptions.Item>
              <Descriptions.Item label="设备类型">{selected.device_type || '-'}</Descriptions.Item>
              <Descriptions.Item label="OS 版本">{selected.os_version}</Descriptions.Item>
              <Descriptions.Item label="序列号">{selected.serial_number}</Descriptions.Item>
              <Descriptions.Item label="角色">{selected.role}</Descriptions.Item>
              <Descriptions.Item label="站点">{selected.site}</Descriptions.Item>
              <Descriptions.Item label="凭据引用">{selected.credential_ref}</Descriptions.Item>
              <Descriptions.Item label="连接状态">
                <Tag color={connectionColor(selected.last_connection_status)}>{selected.last_connection_status || 'unknown'}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="最近检查">{formatDateTime(selected.last_connection_checked_at)}</Descriptions.Item>
              <Descriptions.Item label="连接失败原因">{selected.last_connection_failure_code || '-'}</Descriptions.Item>
              <Descriptions.Item label="延迟">
                {selected.last_connection_latency_ms !== undefined && selected.last_connection_latency_ms !== null ? `${selected.last_connection_latency_ms} ms` : '-'}
              </Descriptions.Item>
              {selected.last_connection_error && <Descriptions.Item label="连接错误" span={2}>
                  <Typography.Text type="danger">{selected.last_connection_error}</Typography.Text>
                </Descriptions.Item>}
              <Descriptions.Item label="分组" span={2}>
                <Space wrap>{(selected.groups || []).map(item => <Tag key={item}>{item}</Tag>)}</Space>
              </Descriptions.Item>
            </Descriptions>
            <Space>
              <Button icon={<LinkOutlined />} loading={testConnectivity.isLoading} onClick={() => testConnectivity.mutate(selected)}>
                测试连接
              </Button>
              <Button icon={<SafetyCertificateOutlined />} loading={discoverIdentity.isLoading && identityDiscoveryTargetId === selected.device_id} onClick={() => {
            setIdentityDiscoveryResult(null);
            discoverIdentity.mutate(selected);
          }}>
                识别身份
              </Button>
              <Button icon={<EyeOutlined />} onClick={() => {
            setHealthOpen(true);
            refetchHealthHistory();
          }}>
                连接历史
              </Button>
              <Button icon={<SearchOutlined />} onClick={() => {
            setEvidenceDevice(selected);
            setEvidenceOpen(true);
          }}>
                巡检详情
              </Button>
            </Space>
            {connectivityResult && <Alert type={connectivityResult.success ? 'success' : 'error'} showIcon message={connectivityResult.success ? '连接测试成功' : '连接测试失败'} description={<Space direction="vertical" size={4}>
                    {connectivityResult.failure_code && <Typography.Text>原因：{connectivityResult.failure_code}</Typography.Text>}
                    {connectivityResult.error && <Typography.Text type="danger">{connectivityResult.error}</Typography.Text>}
                    {(connectivityResult.connection_issues || []).map(issue => <Typography.Text key={`${String(issue.failure_code)}-${String(issue.message)}`} type="danger">
                        {String(issue.failure_code || 'preflight')}: {String(issue.message || '')}
                      </Typography.Text>)}
                    {connectivityResult.device_type && <Typography.Text>设备类型：{connectivityResult.device_type}</Typography.Text>}
                    {connectivityResult.prompt && <Typography.Text>Prompt: {connectivityResult.prompt}</Typography.Text>}
                    {connectivityResult.elapsed_seconds !== undefined && <Typography.Text>耗时：{connectivityResult.elapsed_seconds}s</Typography.Text>}
                  </Space>} />}
            {identityDiscoveryResult && <Alert type={identityDiscoveryResult.success ? 'success' : 'warning'} showIcon message={identityDiscoveryResult.success ? '设备身份识别成功' : '设备身份未能可靠识别'} description={<Space direction="vertical" size={6} style={{
          width: '100%'
        }}>
                    <Space wrap>
                      <Tag color="blue">状态：{statusLabel(identityDiscoveryResult.identity_status)}</Tag>
                      {identityDiscoveryResult.identity?.vendor && <Tag>厂商：{identityDiscoveryResult.identity.vendor}</Tag>}
                      {identityDiscoveryResult.identity?.model && <Tag color="green">型号：{identityDiscoveryResult.identity.model}</Tag>}
                      {identityDiscoveryResult.identity?.family && <Tag color="purple">设备族：{identityDiscoveryResult.identity.family}</Tag>}
                      {identityDiscoveryResult.identity?.confidence !== undefined && <Tag>置信度：{Math.round((identityDiscoveryResult.identity.confidence || 0) * 100)}%</Tag>}
                    </Space>
                    {identityDiscoveryResult.error && <Typography.Text type="danger">{identityDiscoveryResult.error}</Typography.Text>}
                    {(identityDiscoveryResult.warnings || []).map(warning => <Typography.Text key={`${warning.code}-${warning.message}`} type="warning">
                        {warning.code}: {warning.message}
                      </Typography.Text>)}
                    {identityDiscoveryResult.identity?.evidence_command && <Typography.Text>证据命令：{identityDiscoveryResult.identity.evidence_command}</Typography.Text>}
                    {identityDiscoveryResult.identity?.evidence_text && <Typography.Paragraph style={codeStyle}>
                        {identityDiscoveryResult.identity.evidence_text}
                      </Typography.Paragraph>}
                    {identityDiscoveryResult.commands?.length ? <Typography.Text type="secondary">
                        Bootstrap 命令：{identityDiscoveryResult.commands.join(' | ')}
                      </Typography.Text> : null}
                  </Space>} />}
            {plan && <Space direction="vertical" size={12} style={{
          width: '100%'
        }}>
                <Alert type={plan.coverage_summary?.status === 'blocked' ? 'warning' : 'success'} showIcon message={'巡检计划：' + plan.commands.length + ' 条命令'} description={<Space direction="vertical" size={8} style={{
            width: '100%'
          }}>
                      <Space wrap>
                        <Tag color="blue">来源：{plan.plan.policy_source || 'unknown'}</Tag>
                        {plan.plan.binding_id && <Tag color="green">binding: {plan.plan.binding_id}</Tag>}
                        {plan.plan.profile_version_id && <Tag>version: {plan.plan.profile_version_id}</Tag>}
                        {plan.coverage_summary && <Tag color={readinessColor(plan.coverage_summary.status)}>
                            {statusLabel(plan.coverage_summary.status)}
                          </Tag>}
                      </Space>
                      <Descriptions size="small" bordered column={isMobile ? 1 : 3}>
                        <Descriptions.Item label="采集命令集">{plan.plan.profile_name || '-'}</Descriptions.Item>
                        <Descriptions.Item label="模式">{plan.plan.mode || plan.coverage_summary?.mode || '-'}</Descriptions.Item>
                        <Descriptions.Item label="动作">{plan.plan.action || '-'}</Descriptions.Item>
                        <Descriptions.Item label="凭据引用">
                          {plan.plan.credential_ref || selected.credential_ref || '-'}
                        </Descriptions.Item>
                        <Descriptions.Item label="就绪命令">
                          {plan.coverage_summary ? `${plan.coverage_summary.ready_count}/${plan.coverage_summary.eligible_count}` : '-'}
                        </Descriptions.Item>
                        <Descriptions.Item label="阻塞">
                          {plan.coverage_summary ? plan.coverage_summary.blocked_count + (plan.coverage_summary.connection_issues?.length || 0) + runtimeAssetIssues(plan.coverage_summary) : '-'}
                        </Descriptions.Item>
                        <Descriptions.Item label="规则资产">
                          {plan.coverage_summary?.runtime_asset_health ? statusLabel(plan.coverage_summary.runtime_asset_health.status || 'unknown') : '-'}
                        </Descriptions.Item>
                        <Descriptions.Item label="覆盖率">
                          {plan.coverage_summary ? `${Math.round((plan.coverage_summary.coverage_rate || 0) * 100)}%` : '-'}
                        </Descriptions.Item>
                        <Descriptions.Item label="采集参数" span={isMobile ? 1 : 3}>
                          {plan.plan.collector_options && Object.keys(plan.plan.collector_options).length > 0 ? <Typography.Text code>
                                {JSON.stringify(plan.plan.collector_options)}
                              </Typography.Text> : '-'}
                        </Descriptions.Item>
                      </Descriptions>
                      {(plan.coverage_summary?.connection_issues || []).length > 0 && <Alert type="error" showIcon message="采集器预检问题" description={<Space direction="vertical" size={2}>
                              {plan.coverage_summary?.connection_issues.map((issue, index) => <Typography.Text key={index} type="danger">
                                  {String(issue.failure_code || 'preflight')}: {String(issue.message || '')}
                                </Typography.Text>)}
                            </Space>} />}
                      {runtimeAssetIssueText(plan.coverage_summary) && <Alert type="warning" showIcon message="运行时规则资产不完整" description={runtimeAssetIssueText(plan.coverage_summary)} />}
                      {plan.plan.selector && Object.keys(plan.plan.selector).length > 0 && <Typography.Paragraph style={codeStyle}>
                          {JSON.stringify({
                selector: plan.plan.selector
              }, null, 2)}
                        </Typography.Paragraph>}
                    </Space>} />
                <Table rowKey={(row, index) => `${String(row.command || index)}-state-${index}`} size="small" pagination={false} dataSource={plan.command_asset_states || []} columns={[{
            title: '命令',
            dataIndex: 'command',
            width: 220
          }, {
            title: 'Parser',
            dataIndex: 'parser_status',
            width: 110,
            render: (value: string) => <Tag color={value === 'ready' ? 'green' : value === 'ignored' ? 'default' : 'orange'}>{statusLabel(value)}</Tag>
          }, {
            title: '命令适配器',
            dataIndex: 'projection_status',
            width: 110,
            render: (value: string) => <Tag color={value === 'ready' ? 'green' : 'orange'}>{statusLabel(value)}</Tag>
          }, {
            title: 'Policy',
            dataIndex: 'policy_status',
            width: 110,
            render: (value: string) => <Tag color={value === 'ready' ? 'green' : 'orange'}>{statusLabel(value)}</Tag>
          }, {
            title: 'EventRule',
            dataIndex: 'event_rule_status',
            width: 110,
            render: (value: string) => <Tag color={value === 'ready' ? 'green' : 'orange'}>{statusLabel(value)}</Tag>
          }, {
            title: '下一步',
            dataIndex: 'next_action',
            width: 160
          }, {
            title: '操作',
            width: 120,
            render: (_: unknown, row: NonNullable<DeviceEffectivePlanPreview['command_asset_states']>[number]) => <Button size="small" type="link" onClick={() => {
              const focus = row.focus || {};
              const params = new URLSearchParams();
              params.set('workspace', String(focus.workspace || 'inbox'));
              if (focus.subtab) params.set('subtab', String(focus.subtab));
              if (focus.command || row.command) params.set('command', String(focus.command || row.command));
              navigate(businessHref(`/skills/parser?${params.toString()}`));
            }}>
                          打开控制面
                        </Button>
          }]} scroll={{
            x: 980
          }} locale={{
            emptyText: '暂无命令四态数据'
          }} style={{
            marginBottom: 12
          }} />
                <Table<Record<string, unknown>> rowKey={(row, index) => `${String(row.canonical_command || row.command || index)}-${index}`} size="small" pagination={false} dataSource={plan.parser_coverage || []} columns={planCoverageColumns} scroll={{
            x: 860
          }} locale={{
            emptyText: '当前模式无需 Parser 覆盖率检查，或没有需要结构化的命令'
          }} />
                <Table<{
            command: string;
            action?: string;
            collection_item_id?: string;
          }> rowKey={(row, index) => `${row.command}-${index}`} size="small" pagination={false} dataSource={plan.plan.collection_items || []} columns={[{
            title: '命令',
            dataIndex: 'command'
          }, {
            title: '动作',
            dataIndex: 'action',
            width: 120
          }, {
            title: '采集项',
            dataIndex: 'collection_item_id',
            width: 240,
            render: (value: string) => value || '-'
          }]} scroll={{
            x: 720
          }} />
              </Space>}
          </Space>}
      </Drawer>

      <Drawer title={selected ? `连接历史 - ${selected.device_name}` : '连接历史'} open={healthOpen} onClose={() => setHealthOpen(false)} width={isMobile ? '100vw' : 860} destroyOnClose>
        <Table<DeviceConnectionHealthRecord> rowKey="id" size="small" pagination={{
        pageSize: 10
      }} dataSource={healthHistory?.items || []} columns={healthColumns} scroll={{
        x: 920
      }} />
      </Drawer>

      <Drawer title={evidenceDevice ? `巡检详情 - ${evidenceDevice.device_name}` : '巡检详情'} open={evidenceOpen} onClose={() => {
      setEvidenceOpen(false);
      setEvidenceDevice(null);
      setEvidenceRunId(null);
      setEvidenceBatchId(null);
      setEvidenceTab('result');
    }} width={isMobile ? '100vw' : 1200} destroyOnClose>
        <Tabs activeKey={evidenceTab} onChange={key => setEvidenceTab(key as 'result' | 'execution')} items={[{
        key: 'result',
        label: '巡检结果',
        children: <EvidenceTimeline items={evidenceTimeline?.items || []} executionPlans={evidenceRunId ? [] : executionPlans?.items || []} isMobile={isMobile} onNavigate={(kind, id) => {
          if (kind === 'workflow-run') {
            setEvidenceRunId(id);
            setEvidenceTab('execution');
            return;
          }
          setEvidenceOpen(false);
          if (kind === 'parser-template') {
            navigate(businessHref(`/skills/parser?focus=${kind}&id=${encodeURIComponent(id)}&workspace=parser-assets&subtab=templates`));
            return;
          }
          if (kind === 'observation-contract') {
            navigate(businessHref(`/skills/parser?focus=${kind}&id=${encodeURIComponent(id)}&workspace=catalog`));
            return;
          }
          if (kind === 'change-policy' || kind === 'change') {
            navigate(businessHref(`/skills/parser?focus=${kind}&id=${encodeURIComponent(id)}&workspace=rule-assets&subtab=change-policies`));
            return;
          }
          if (kind === 'event-rule') {
            navigate(businessHref(`/skills/parser?focus=${kind}&id=${encodeURIComponent(id)}&workspace=rule-assets&subtab=event-rules`));
            return;
          }
          if (kind === 'snapshot') {
            navigate(businessHref(`/skills/parser?focus=${kind}&id=${encodeURIComponent(id)}&workspace=parser-assets&subtab=templates`));
          }
        }} />
      }, {
        key: 'execution',
        label: '执行过程',
        disabled: !evidenceRunId,
        children: <WorkflowRunMonitor runId={evidenceRunId} plain />
      }]} />
      </Drawer>

      <Drawer title="动态设备分组" open={dynamicGroupOpen} onClose={() => setDynamicGroupOpen(false)} width={isMobile ? '100vw' : 1100} destroyOnClose>
        <Space direction="vertical" size={16} style={{
        width: '100%'
      }}>
          <Form<DynamicGroupFormValues> form={dynamicGroupForm} layout="vertical" onFinish={values => saveDynamicGroup.mutate(values)} requiredMark={false}>
            <Row gutter={12}>
              <Col xs={24} md={8}>
                <Form.Item name="name" label="名称" rules={[{
                required: true,
                message: '名称不能为空'
              }]}>
                  <Input />
                </Form.Item>
              </Col>
              <Col xs={24} md={8}>
                <Form.Item name="description" label="描述">
                  <Input />
                </Form.Item>
              </Col>
              <Col xs={24} md={8}>
                <Form.Item name="status" label="状态" initialValue="active">
                  <Select options={[{
                  value: 'active',
                  label: '启用'
                }, {
                  value: 'disabled',
                  label: '停用'
                }]} />
                </Form.Item>
              </Col>
              <Col span={24}>
                <Space direction="vertical" size={8} style={{
                width: '100%'
              }}>
                  <Space align="center" style={{
                  justifyContent: 'space-between',
                  width: '100%'
                }}>
                    <Typography.Text strong>选择器构建器</Typography.Text>
                    <Button size="small" icon={<PlusOutlined />} onClick={addDynamicGroupRule}>
                      添加条件
                    </Button>
                  </Space>
                  {dynamicGroupRules.length === 0 && <Alert type="info" showIcon message="请至少添加一个条件，也可以直接编辑选择器 JSON。" />}
                  {dynamicGroupRules.map(rule => <Row key={rule.id} gutter={8} align="middle">
                      <Col xs={24} md={7}>
                        <Select value={rule.field} options={dynamicGroupSelectorFields} style={{
                      width: '100%'
                    }} showSearch optionFilterProp="label" onChange={value => updateDynamicGroupRule(rule.id, {
                      field: value
                    })} />
                      </Col>
                      <Col xs={24} md={15}>
                        <Select mode="tags" value={rule.values} tokenSeparators={[',']} placeholder="一个或多个值" style={{
                      width: '100%'
                    }} onChange={values => updateDynamicGroupRule(rule.id, {
                      values
                    })} />
                      </Col>
                      <Col xs={24} md={2}>
                        <Button danger icon={<DeleteOutlined />} onClick={() => removeDynamicGroupRule(rule.id)} title="删除条件" />
                      </Col>
                    </Row>)}
                </Space>
              </Col>
              <Col span={24}>
                <Form.Item name="selector_json" label="选择器 JSON" rules={[{
                required: true,
                validator: async (_, value) => {
                  try {
                    const parsed = parseAttributes(value);
                    if (!parsed || Object.keys(parsed).length === 0) {
                      throw new Error('Selector JSON is required');
                    }
                  } catch {
                    throw new Error('Selector must be a non-empty JSON object');
                  }
                }
              }]}>
                  <Input.TextArea rows={5} style={{
                  fontFamily: 'Consolas, Monaco, monospace'
                }} onBlur={event => {
                  try {
                    const parsed = parseAttributes(event.target.value);
                    setDynamicGroupRules(selectorToRules(parsed || {}));
                  } catch {
                    // Keep the invalid JSON visible; form validation will surface the error on save.
                  }
                }} />
                </Form.Item>
              </Col>
            </Row>
            <Space>
              <Button type="primary" loading={saveDynamicGroup.isLoading} onClick={() => dynamicGroupForm.submit()}>
                {editingDynamicGroup ? '保存分组' : '创建分组'}
              </Button>
              {editingDynamicGroup && <Button loading={previewDynamicGroup.isLoading} onClick={() => previewDynamicGroup.mutate(editingDynamicGroup.id)}>
                  预览
                </Button>}
              <Button onClick={openDynamicGroupCreate}>新建</Button>
            </Space>
          </Form>
          <Table<DeviceDynamicGroup> rowKey="id" size="small" pagination={{
          pageSize: 8
        }} dataSource={dynamicGroups} columns={dynamicGroupColumns} scroll={{
          x: 900
        }} />
          {dynamicGroupPreview && <Space direction="vertical" size={8} style={{
          width: '100%'
        }}>
              <Space wrap>
                <Tag color="blue">{dynamicGroupPreview.group.name}</Tag>
                <Tag>总数 {dynamicGroupPreview.total}</Tag>
              </Space>
              <Table<ManagedDevice> rowKey="device_id" size="small" pagination={{
            pageSize: 8
          }} dataSource={dynamicGroupPreview.items} columns={[{
            title: '设备',
            dataIndex: 'device_name',
            width: 220
          }, {
            title: 'IP',
            dataIndex: 'ip',
            width: 130
          }, {
            title: '厂商',
            dataIndex: 'vendor',
            width: 100
          }, {
            title: '平台',
            dataIndex: 'platform',
            width: 120
          }, {
            title: '设备类型',
            dataIndex: 'device_type',
            width: 140
          }, {
            title: '站点',
            dataIndex: 'site',
            width: 120
          }, {
            title: '角色',
            dataIndex: 'role',
            width: 120
          }]} scroll={{
            x: 950
          }} />
            </Space>}
        </Space>
      </Drawer>

      <Modal title={editing ? '编辑设备' : '新增设备'} open={modalOpen} onCancel={() => {
      setModalOpen(false);
      setEditing(null);
      form.resetFields();
    }} onOk={() => form.submit()} confirmLoading={saveDevice.isLoading} width={760} destroyOnClose>
        <Form<DeviceFormValues> form={form} layout="vertical" onFinish={values => saveDevice.mutate(values)} requiredMark={false}>
          <Row gutter={12}>
            <Col xs={24} md={12}>
              <Form.Item name="device_name" label="设备名称" rules={[{
              required: true,
              message: '设备名称不能为空'
            }]}>
                <Input />
              </Form.Item>
            </Col>
            <Col xs={24} md={12}>
              <Form.Item name="management_ip" label="管理 IP" rules={[{
              required: true,
              message: '管理 IP 不能为空'
            }]}>
                <Input />
              </Form.Item>
            </Col>
            <Col xs={12} md={8}><Form.Item name="vendor" label="厂商"><Input /></Form.Item></Col>
            <Col xs={12} md={8}><Form.Item name="model" label="型号"><Input /></Form.Item></Col>
            <Col xs={12} md={8}><Form.Item name="family" label="设备族"><Input /></Form.Item></Col>
            <Col xs={12} md={8}><Form.Item name="platform" label="平台"><Input placeholder="ios, nxos, comware" /></Form.Item></Col>
            <Col xs={12} md={8}><Form.Item name="device_type" label="设备类型"><Input placeholder="router, access_switch, firewall" /></Form.Item></Col>
            <Col xs={12} md={8}><Form.Item name="os_version" label="OS 版本"><Input /></Form.Item></Col>
            <Col xs={12} md={8}><Form.Item name="serial_number" label="序列号"><Input /></Form.Item></Col>
            <Col xs={12} md={8}><Form.Item name="site" label="站点"><Input /></Form.Item></Col>
            <Col xs={12} md={8}><Form.Item name="role" label="角色"><Input /></Form.Item></Col>
            <Col xs={12} md={8}>
              <Form.Item name="status" label="状态">
                <Select options={[{
                value: 'active',
                label: '启用'
              }, {
                value: 'disabled',
                label: '停用'
              }]} />
              </Form.Item>
            </Col>
            <Col xs={24} md={12}><Form.Item name="username" label="用户名"><Input /></Form.Item></Col>
            <Col xs={24} md={12}>
              <Form.Item label="凭据引用">
                <Space.Compact style={{
                width: '100%'
              }}>
                  <Form.Item name="credential_ref" noStyle>
                    <AutoComplete options={credentialOptions} filterOption={(input, option) => String(option?.value || '').toLowerCase().includes(input.toLowerCase())}>
                      <Input placeholder="env:NETOPS_PASSWORD" />
                    </AutoComplete>
                  </Form.Item>
                  <Button loading={inspectCredential.isLoading} onClick={() => inspectCredential.mutate(form.getFieldValue('credential_ref'))}>
                    检查
                  </Button>
                </Space.Compact>
              </Form.Item>
            </Col>
            <Form.Item name="command_profile" hidden><Input /></Form.Item>
            <Col xs={24} md={12}><Form.Item name="groups" label="分组"><Input placeholder="core, xian-dc" /></Form.Item></Col>
            <Col span={24}>
              <Typography.Paragraph type="secondary">保存设备后，在设备列表勾选设备，使用“绑定巡检命令集”配置要执行的命令。实际生效范围和命令以巡检预览为准。</Typography.Paragraph>
            </Col>
            <Col span={24}>
              <details><summary style={{
                marginBottom: 12
              }}>高级采集设置</summary>
              <Form.Item name="attributes_json" label="扩展属性 JSON" rules={[{
                validator: async (_, value) => {
                  if (!value) return;
                  try {
                    parseAttributes(value);
                  } catch {
                    throw new Error('扩展属性必须是合法 JSON');
                  }
                }
              }]}>
                <Input.TextArea rows={4} placeholder='{"collector_options": {"read_timeout": 60}}' />
              </Form.Item>
              </details>
            </Col>
          </Row>
          {credentialInspection && <Alert type={credentialInspection.available ? 'success' : 'warning'} showIcon message={credentialInspection.available ? '凭据可用' : '凭据不可用'} description={`${credentialInspection.ref || '-'} ${credentialInspection.failure_code ? `- ${credentialInspection.failure_code}` : ''}`} />}
        </Form>
      </Modal>

      <Modal title="导入设备" open={importOpen} onCancel={() => setImportOpen(false)} footer={[<Button key="close" onClick={() => setImportOpen(false)}>关闭</Button>, <Button key="preview" icon={<SearchOutlined />} loading={previewImport.isLoading} onClick={() => previewImport.mutate()}>
            预览
          </Button>, <Button key="apply" type="primary" icon={<ImportOutlined />} loading={applyImport.isLoading} disabled={!importPreview || importPreview.invalid_count > 0} onClick={() => applyImport.mutate()}>
            应用导入
          </Button>]} width={980} destroyOnClose>
        <Space direction="vertical" size={12} style={{
        width: '100%'
      }}>
          <Alert type="info" showIcon message="导入前会先校验表头和行内容；合法行按 management_ip 或 device_name 幂等写入，已有设备显示为 update。" />
          <Space wrap>
            <Button icon={<DownloadOutlined />} onClick={() => {
            const template = importTemplate;
            if (!template?.csv) {
              message.warning('导入模板暂不可用，请稍后重试');
              return;
            }
            downloadTextFile(template.filename || 'device-import-template.csv', template.csv);
          }}>
              下载 CSV 模板
            </Button>
            <Upload accept=".csv,.xlsx" maxCount={1} showUploadList={false} beforeUpload={async file => {
            const name = file.name || '';
            const lowerName = name.toLowerCase();
            try {
              if (lowerName.endsWith('.xlsx')) {
                setImportFilePayload({
                  content: await fileToBase64(file),
                  format: 'xlsx',
                  filename: name
                });
              } else {
                setImportFilePayload({
                  content: await file.text(),
                  format: 'csv',
                  filename: name || 'devices.csv'
                });
              }
              setImportPreview(null);
              message.success(`已读取 ${name || '设备导入文件'}`);
            } catch (error: any) {
              message.error(error?.message || '读取导入文件失败');
            }
            return false;
          }}>
              <Button icon={<ImportOutlined />}>选择 CSV/XLSX</Button>
            </Upload>
            {importFilePayload && <>
                <Tag color="blue">{importFilePayload.format?.toUpperCase()} {importFilePayload.filename}</Tag>
                <Button size="small" onClick={() => {
              setImportFilePayload(null);
              setImportPreview(null);
            }}>
                    使用粘贴的 CSV
                  </Button>
              </>}
          </Space>
          <Input.TextArea rows={8} value={importText} disabled={Boolean(importFilePayload)} onChange={event => {
          setImportText(event.target.value);
          setImportFilePayload(null);
          setImportPreview(null);
        }} style={{
          fontFamily: 'Consolas, Monaco, monospace'
        }} />
          {importTemplate && <Table rowKey="name" size="small" pagination={false} dataSource={importTemplate.columns} columns={[{
          title: '列名',
          dataIndex: 'name',
          width: 180
        }, {
          title: '是否必填',
          dataIndex: 'required',
          width: 90,
          render: (value: boolean) => <Tag color={value ? 'red' : 'default'}>{value ? '必填' : '可选'}</Tag>
        }, {
          title: '可用别名',
          dataIndex: 'aliases',
          width: 180,
          render: (value: string[]) => value?.length ? value.join(', ') : '-'
        }, {
          title: '说明',
          dataIndex: 'description'
        }, {
          title: '示例',
          dataIndex: 'example',
          width: 180,
          render: (value: string) => value || '-'
        }]} scroll={{
          x: 960
        }} />}
          {importPreview && <Space direction="vertical" size={8} style={{
          width: '100%'
        }}>
              <Space wrap>
                <Tag>总数 {importPreview.total}</Tag>
                <Tag color="green">新增 {importPreview.create_count}</Tag>
                <Tag color="blue">更新 {importPreview.update_count}</Tag>
                <Tag color={importPreview.invalid_count ? 'red' : 'default'}>无效 {importPreview.invalid_count}</Tag>
              </Space>
              {importPreview.invalid_count > 0 && <Alert type="warning" showIcon message="存在无效行，请根据错误定位修复后再应用导入。" />}
              <Table rowKey="row_number" size="small" pagination={{
            pageSize: 8
          }} dataSource={importPreview.rows} columns={importColumns} scroll={{
            x: 900
          }} />
            </Space>}
        </Space>
      </Modal>

      <Modal title="批量更新设备" open={bulkOpen} onCancel={() => setBulkOpen(false)} footer={[<Button key="close" onClick={() => setBulkOpen(false)}>关闭</Button>, <Button key="preview" icon={<SearchOutlined />} loading={previewBulk.isLoading} onClick={() => previewBulk.mutate()}>
            预览
          </Button>, <Button key="apply" type="primary" loading={applyBulk.isLoading} disabled={!bulkPreview || bulkPreview.target_count === 0} onClick={() => applyBulk.mutate()}>
            应用
          </Button>]} width={980} destroyOnClose>
        <Space direction="vertical" size={12} style={{
        width: '100%'
      }}>
          <Alert type="info" showIcon message={selectedRowKeys.length ? '将操作已勾选的 ' + selectedRowKeys.length + ' 台设备。' : '未勾选设备时，将按当前筛选条件匹配目标设备。'} />
          <Form<BulkFormValues> form={bulkForm} layout="vertical" requiredMark={false}>
            <Row gutter={12}>
              <Col xs={24} md={8}>
                <Form.Item name="dynamic_group_id" label="动态分组">
                  <Select allowClear placeholder="使用当前筛选" options={dynamicGroups.filter(group => group.status === 'active').map(group => ({
                  value: group.id,
                  label: group.name
                }))} />
                </Form.Item>
              </Col>
              <Col xs={24} md={8}><Form.Item name="command_profile" label="历史 Profile 引用"><Input disabled /></Form.Item></Col>
              <Col xs={24} md={8}>
                <Form.Item name="credential_ref" label="凭据引用">
                  <AutoComplete options={credentialOptions} filterOption={(input, option) => String(option?.value || '').toLowerCase().includes(input.toLowerCase())} placeholder="env:NETOPS_PASSWORD" />
                </Form.Item>
              </Col>
              <Col xs={12} md={8}><Form.Item name="platform" label="平台"><Input /></Form.Item></Col>
              <Col xs={12} md={8}><Form.Item name="device_type" label="设备类型"><Input /></Form.Item></Col>
              <Col xs={12} md={8}><Form.Item name="site" label="站点"><Input /></Form.Item></Col>
              <Col xs={12} md={8}><Form.Item name="role" label="角色"><Input /></Form.Item></Col>
              <Col xs={12} md={8}>
                <Form.Item name="status" label="状态">
                  <Select allowClear options={[{
                  value: 'active',
                  label: '启用'
                }, {
                  value: 'disabled',
                  label: '停用'
                }]} />
                </Form.Item>
              </Col>
              <Col xs={12} md={8}>
                <Form.Item name="group_mode" label="分组更新模式" initialValue="replace">
                  <Select options={[{
                  value: 'replace',
                  label: 'replace'
                }, {
                  value: 'append',
                  label: 'append'
                }]} />
                </Form.Item>
              </Col>
              <Col xs={24} md={8}><Form.Item name="groups" label="分组"><Input placeholder="core, xian-dc" /></Form.Item></Col>
            </Row>
          </Form>
          {bulkPreview && <Space direction="vertical" size={8} style={{
          width: '100%'
        }}>
              <Space wrap>
                <Tag>总数 {bulkPreview.target_count}</Tag>
                {bulkPreview.changed_fields.map(field => <Tag key={field} color="blue">{field}</Tag>)}
              </Space>
              <Table rowKey="device_id" size="small" pagination={{
            pageSize: 8
          }} dataSource={bulkPreview.rows} columns={bulkColumns} scroll={{
            x: 900
          }} />
            </Space>}
        </Space>
      </Modal>

      <Modal title="批量删除设备资产" open={bulkDeleteOpen} onCancel={() => setBulkDeleteOpen(false)} footer={[<Button key="close" onClick={() => setBulkDeleteOpen(false)}>关闭</Button>, <Button key="refresh" icon={<SearchOutlined />} loading={previewBulkDelete.isLoading} onClick={() => previewBulkDelete.mutate()}>
            重新预览
          </Button>, <Button key="delete" danger type="primary" loading={applyBulkDelete.isLoading} disabled={!bulkDeletePreview || bulkDeletePreview.target_count === 0} onClick={() => applyBulkDelete.mutate()}>
            确认删除
          </Button>]} width={860} destroyOnClose>
        <Space direction="vertical" size={12} style={{
        width: '100%'
      }}>
          <Alert type="warning" showIcon message={selectedRowKeys.length ? `将删除已勾选的 ${selectedRowKeys.length} 台设备资产。` : '未勾选设备时，将删除当前筛选结果中的设备资产。'} description="删除只影响设备资产清单，不会删除历史巡检详情、执行记录或事件数据。" />
          {bulkDeletePreview && <Space direction="vertical" size={8} style={{
          width: '100%'
        }}>
              <Tag color={bulkDeletePreview.target_count ? 'red' : 'default'}>
                目标设备 {bulkDeletePreview.target_count}
              </Tag>
              <Table rowKey="device_id" size="small" pagination={{
            pageSize: 8
          }} dataSource={bulkDeletePreview.rows} columns={[{
            title: '设备',
            dataIndex: 'device_name',
            width: 220
          }, {
            title: 'IP',
            dataIndex: 'ip',
            width: 140
          }, {
            title: '厂商',
            dataIndex: ['current', 'vendor'],
            width: 120
          }, {
            title: '型号',
            dataIndex: ['current', 'model'],
            width: 180
          }, {
            title: '状态',
            dataIndex: ['current', 'status'],
            width: 100,
            render: (value: string) => <Tag>{statusLabel(value)}</Tag>
          }]} scroll={{
            x: 780
          }} />
            </Space>}
        </Space>
      </Modal>

    </DeviceWorkspaceLayout>;
};
export default DeviceOperationsWorkspace;
