import StatusNotice from '@netops/components/common/StatusNotice'
import InspectionDetailPage from './InspectionDetailPage'
import type { PatrolBatchSummary } from '@netops/services/api';
import { inspectionDeviceApi as deviceApi, inspectionBatchApi as workflowApi } from './api';
import { useAuth } from '@netops/context/AuthContext';
import { usePatrolDraft } from '@netops/hooks/usePatrolDraft';
import PatrolDeviceSelector from '@netops/components/devices/PatrolDeviceSelector';
import { businessHref } from '@netops/apps/appRoutes';
import React, { useMemo, useState } from 'react';
import { Button, Collapse, Descriptions, Dropdown, Form, Input, Modal, Progress, Row, Col, Select, Space, Switch, Table, Tabs, Tag, Tooltip, Typography, message } from 'antd';
import { CheckCircleOutlined, ExclamationCircleOutlined, CodeOutlined, EyeOutlined, MoreOutlined, PlayCircleOutlined, ReloadOutlined, SearchOutlined, StopOutlined } from '@ant-design/icons';
import { useMutation, useQuery } from 'react-query';
import { useNavigate, useSearchParams } from 'react-router-dom';
import InspectionLayout from './InspectionLayout';
import PatrolPlanPreview from './components/PatrolPlanPreview';
import WorkflowRunMonitor from '@netops/components/skills/WorkflowRunMonitor';
import { type DeviceAssetFilters } from '@netops/components/devices/DeviceAssetInventoryPanel';
import PatrolOperationsPanel from '@netops/components/devices/PatrolOperationsPanel';
import { canCancelPatrolBatch } from '@netops/components/devices/patrolBatchLifecycle';
import EvidenceTimeline from '@netops/components/devices/EvidenceTimeline';
import { type DevicePatrolLaunchResult, type DevicePatrolPreview, type ManagedDevice, type PatrolBatchPage, type PatrolBatchResultSummary, type PatrolBatchDevicePage, type PatrolProductionReadiness, type WorkflowRunSummary } from '@netops/services/api';
import { useIsMobile } from '@netops/hooks/useBreakpoint';
interface PatrolFormValues {
  save_baseline?: boolean;
  ticket_id?: string;
  dynamic_group_id?: string;
  group?: string;
}
const extractErrorMessage = (error: any, fallback: string) => {
  const detail = error?.response?.data?.detail;
  if (!detail) return error?.message || fallback;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail?.errors) && detail.errors.length) {
    return `${detail.message || fallback}：${detail.errors.join('；')}`;
  }
  return detail.message || fallback;
};
const ACTIVE_WORKFLOW_STATUSES = new Set(['pending', 'queued', 'running', 'in_progress', 'collecting', 'dispatching']);
const hasActiveWorkflowRuns = (runs: WorkflowRunSummary[] = []) => runs.some(run => ACTIVE_WORKFLOW_STATUSES.has(String(run.status || '').toLowerCase()));
const hasActivePatrolBatches = (batches: PatrolBatchSummary[] = []) => batches.some(batch => {
  const running = Number(batch.running_count || 0);
  const pending = Number(batch.pending_count || 0);
  return running + pending > 0;
});
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
const patrolBatchStatus = (status?: string) => {
  const normalized = String(status || 'pending').toLowerCase();
  if (normalized === 'completed') return {
    color: 'green',
    label: '已完成'
  };
  if (normalized === 'failed') return {
    color: 'red',
    label: '有失败'
  };
  if (normalized === 'cancelled') return {
    color: 'default',
    label: '已取消'
  };
  if (normalized === 'active') return {
    color: 'processing',
    label: '运行中'
  };
  return {
    color: 'gold',
    label: '等待中'
  };
};
const compactPatrolBatchId = (value: string) => {
  const id = value.replace(/^patrol-batch-/, '');
  return id.length > 18 ? `patrol-batch-${id.slice(0, 8)}…${id.slice(-4)}` : value;
};
type PatrolPreviewIssue = DevicePatrolPreview['errors'][number];
const recordList = (items?: Array<Record<string, unknown>>) => Array.isArray(items) ? items : [];
const recordText = (record: Record<string, unknown>, keys: string[], fallback = '-') => {
  for (const key of keys) {
    const value = record[key];
    if (value !== undefined && value !== null && String(value).trim() !== '') {
      return String(value);
    }
  }
  return fallback;
};
const readinessStatusColor = (status?: string) => {
  const normalized = String(status || '').toLowerCase();
  if (['active', 'healthy', 'ready'].includes(normalized)) return 'green';
  if (['maintenance', 'degraded', 'warning'].includes(normalized)) return 'orange';
  if (['disabled', 'blocked', 'failed', 'unhealthy'].includes(normalized)) return 'red';
  return 'default';
};
const coverageStatusColor = (status?: string) => {
  const normalized = String(status || '').toLowerCase();
  if (['ready', 'success', 'available'].includes(normalized)) return 'green';
  if (['ignored', 'not_required'].includes(normalized)) return 'default';
  if (['empty'].includes(normalized)) return 'blue';
  return 'red';
};
const previewIssueCounts = (preview?: DevicePatrolPreview | null) => {
  if (!preview) return {
    coverage: 0,
    connection: 0
  };
  const coverageCount = (preview.errors || []).reduce((total, item) => total + recordList(item.coverage_violations).length, 0) + (preview.rows || []).reduce((total, item) => total + recordList(item.coverage_violations).length, 0);
  const connectionCount = (preview.errors || []).reduce((total, item) => total + recordList(item.connection_issues).length, 0) + (preview.rows || []).reduce((total, item) => total + recordList(item.connection_issues).length, 0);
  return {
    coverage: coverageCount,
    connection: connectionCount
  };
};
const formatDateTime = (value?: string | null) => {
  if (!value) return '-';
  try {
    return new Date(value).toLocaleString();
  } catch {
    return value;
  }
};
const PATROL_BATCH_PAGE_SIZES = [25, 50, 100];
const positiveInteger = (value: string | null, fallback: number) => {
  const parsed = Number.parseInt(value || '', 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
};
export interface DeviceOperationsWorkspaceProps {
  mode?: 'devices' | 'patrol';
}
const DeviceOperationsWorkspace: React.FC<DeviceOperationsWorkspaceProps> = () => {
  const {
    user
  } = useAuth();
  const {
    draft: patrolDraft,
    saveDraft,
    clearDraft
  } = usePatrolDraft(user!.id);
  const isMobile = useIsMobile();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const batchHistoryPage = positiveInteger(searchParams.get('batch_page'), 1);
  const requestedBatchPageSize = positiveInteger(searchParams.get('batch_page_size'), 25);
  const batchHistoryPageSize = PATROL_BATCH_PAGE_SIZES.includes(requestedBatchPageSize) ? requestedBatchPageSize : 25;
  const batchHistorySearch = searchParams.get('batch_search') || '';
  const batchHistoryStatus = searchParams.get('batch_status') || undefined;
  const showArchivedPatrolBatches = searchParams.get('batch_archived') === '1';
  const updateBatchHistoryQuery = (updates: Record<string, string | number | boolean | undefined>) => {
    const next = new URLSearchParams(searchParams);
    Object.entries(updates).forEach(([key, value]) => {
      if (value === undefined || value === '' || value === false) next.delete(key);else next.set(key, value === true ? '1' : String(value));
    });
    setSearchParams(next);
  };
  const [sourcePickerOpen, setSourcePickerOpen] = useState(false);
  const [patrolForm] = Form.useForm<PatrolFormValues>();
  const [filters,,] = useState<DeviceAssetFilters>({
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
  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>(() => searchParams.has('device_id') ? searchParams.getAll('device_id') : patrolDraft.deviceIds);
  const [patrolOpen, setPatrolOpen] = useState(searchParams.get('launch') === '1');
  const [patrolPreview, setPatrolPreview] = useState<DevicePatrolPreview | null>(null);
  const [patrolLaunch, setPatrolLaunch] = useState<DevicePatrolLaunchResult | null>(null);
  const activePatrolBatchId = searchParams.get('patrol_batch_id');
  const setActivePatrolBatchId = (id: string | null) => {
    setSearchParams(previous => {
      const next = new URLSearchParams(previous);
      if (id) { next.set('patrol_batch_id', id); next.set('patrol_view', 'online'); } else next.delete('patrol_batch_id');
      return next;
    });
  };
  const [batchDevicePage, setBatchDevicePage] = useState(1);
  const [batchDeviceSearch, setBatchDeviceSearch] = useState('');
  const [batchDeviceSelectionStatus, setBatchDeviceSelectionStatus] = useState<string>();
  const [monitorRunId, setMonitorRunId] = useState<string | null>(null);
  const [evidenceOpen, setEvidenceOpen] = useState(false);
  const [evidenceDevice, setEvidenceDevice] = useState<ManagedDevice | null>(null);
  const [evidenceRunId, setEvidenceRunId] = useState<string | null>(null);
  const [evidenceBatchId, setEvidenceBatchId] = useState<string | null>(null);
  const [evidenceTab, setEvidenceTab] = useState<'result' | 'execution'>('result');
  const [devicePage,,] = useState(1);
  const [devicePageSize,,] = useState(20);
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
    data
  } = useQuery(['devices', queryParams], () => deviceApi.list(queryParams), {
    enabled: patrolOpen,
    keepPreviousData: true,
    staleTime: 30000,
    refetchOnWindowFocus: false
  });
  const {
    data: patrolPlatformReadiness,
    error: patrolReadinessError,
    isError: patrolReadinessIsError,
    isFetching: patrolReadinessLoading,
    refetch: refetchPatrolReadiness
  } = useQuery<PatrolProductionReadiness>(['device-patrol-platform-readiness'], () => deviceApi.patrolReadiness({
    workflow_name: 'patrol-change-event',
    launch_mode: 'direct'
  }), {
    enabled: patrolOpen,
    refetchInterval: patrolOpen ? 30000 : false,
    refetchOnWindowFocus: false,
    retry: false,
    staleTime: 0
  });
  const {
    data: activeBatchRuns = [],
    refetch: refetchActiveBatchRuns
  } = useQuery(['device-patrol-batch-runs', activePatrolBatchId], () => workflowApi.listRuns({
    template_name: 'patrol-change-event',
    source: 'device-management',
    patrol_batch_id: activePatrolBatchId || undefined,
    limit: 100
  }), {
    enabled: Boolean(activePatrolBatchId),
    refetchInterval: runs => hasActiveWorkflowRuns(runs as WorkflowRunSummary[]) ? 5000 : false,
    refetchOnWindowFocus: false,
    retry: false
  });
  const {
    data: activeBatchResultSummary,
    refetch: refetchActiveBatchResultSummary
  } = useQuery<PatrolBatchResultSummary>(['device-patrol-batch-result-summary', activePatrolBatchId], () => workflowApi.getPatrolBatchSummary(activePatrolBatchId!), {
    enabled: Boolean(activePatrolBatchId),
    refetchInterval: summary => {
      const counts = summary?.status_counts || {};
      const active = Number(counts.running || 0) + Number(counts.pending || 0) + Number(counts.queued || 0);
      return active > 0 ? 10000 : false;
    },
    refetchOnWindowFocus: false,
    retry: false
  });
  const {
    data: patrolBatchPage,
    isFetching: patrolBatchesLoading,
    refetch: refetchPatrolBatches
  } = useQuery<PatrolBatchPage>(['device-patrol-batches', showArchivedPatrolBatches, batchHistoryPage, batchHistoryPageSize, batchHistorySearch, batchHistoryStatus], () => workflowApi.listPatrolBatches({
    limit: batchHistoryPageSize,
    offset: (batchHistoryPage - 1) * batchHistoryPageSize,
    include_archived: showArchivedPatrolBatches,
    search: batchHistorySearch || undefined,
    status: batchHistoryStatus
  }), {
    keepPreviousData: true,
    enabled: searchParams.get('patrol_view') === 'online' || Boolean(activePatrolBatchId),
    staleTime: 10000,
    refetchInterval: page => batchHistoryPage === 1 && hasActivePatrolBatches((page as PatrolBatchPage | undefined)?.items || []) ? 10000 : false,
    refetchOnWindowFocus: false,
    retry: false
  });
  const patrolBatches = useMemo(() => patrolBatchPage?.items || [], [patrolBatchPage?.items]);
  const {
    data: dynamicGroups = []
  } = useQuery(['device-dynamic-groups'], () => deviceApi.dynamicGroups(), {
    enabled: patrolOpen,
    staleTime: 60000,
    refetchOnWindowFocus: false
  });
  const {
    data: activeBatchDevices,
    isLoading: activeBatchDevicesLoading
  } = useQuery<PatrolBatchDevicePage>(['device-patrol-batch-devices', activePatrolBatchId, batchDevicePage, batchDeviceSearch, batchDeviceSelectionStatus], () => workflowApi.listPatrolBatchDevices(activePatrolBatchId!, {
    limit: 50,
    offset: (batchDevicePage - 1) * 50,
    search: batchDeviceSearch.trim() || undefined,
    selection_status: batchDeviceSelectionStatus
  }), {
    enabled: Boolean(activePatrolBatchId),
    keepPreviousData: true,
    refetchInterval: page => page?.items.some(item => ['pending', 'queued', 'running', 'in_progress'].includes(item.execution_status || item.workflow_status || '')) ? 10000 : false,
    refetchOnWindowFocus: false,
    retry: false
  });
  const {
    data: deviceGroups = []
  } = useQuery(['device-groups'], () => deviceApi.groups(), {
    enabled: patrolOpen,
    staleTime: 60000,
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
  const activeBatch = useMemo(() => patrolBatches.find(item => item.patrol_batch_id === activePatrolBatchId), [activePatrolBatchId, patrolBatches]);
  const activeBatchSummary = useMemo(() => {
    const counts = activeBatchResultSummary?.status_counts || activeBatch?.status_counts || activeBatchRuns.reduce<Record<string, number>>((acc, run) => {
      acc[run.status] = (acc[run.status] || 0) + 1;
      return acc;
    }, {});
    return {
      total: activeBatch?.launched_count ?? activeBatchResultSummary?.run_count ?? activeBatchRuns.length,
      completed: counts.completed || 0,
      failed: counts.failed || 0,
      running: (counts.running || 0) + (counts.in_progress || 0) + (counts.collecting || 0),
      pending: (counts.pending || 0) + (counts.queued || 0),
      cancelled: counts.cancelled || 0
    };
  }, [activeBatch, activeBatchResultSummary, activeBatchRuns]);
  const buildPatrolPayload = (values: PatrolFormValues) => ({
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
      group: values.group || queryParams.group,
      status: queryParams.status,
      connection_status: queryParams.connection_status
    },
    mode: 'production',
    save_baseline: Boolean(values.save_baseline),
    ticket_id: values.ticket_id || undefined,
    launch_mode: 'direct',
    workflow_name: 'patrol-change-event'
  });
  const previewEpoch = React.useRef(0);
  const previewPayload = React.useRef('');
  const invalidatePatrolPreview = () => {
    previewEpoch.current += 1;
    previewPayload.current = '';
    setPatrolPreview(null);
  };
  const previewPatrol = useMutation(async () => {
    invalidatePatrolPreview();
    const epoch = previewEpoch.current;
    const values = await patrolForm.validateFields();
    const payload = buildPatrolPayload(values);
    const result = await deviceApi.previewPatrol(payload);
    return { result, epoch, payload: JSON.stringify(payload) };
  }, {
    onSuccess: ({ result, epoch, payload }) => {
      if (epoch !== previewEpoch.current) return;
      previewPayload.current = payload;
      setPatrolPreview(result);
      setPatrolLaunch(null);
    },
    onError: (error: any) => message.error(extractErrorMessage(error, '巡检预览失败'))
  });
  const launchIntent = React.useRef<{
    payload: string;
    id: string;
  } | null>(null);
  const launchPatrol = useMutation(async () => {
    const values = await patrolForm.validateFields();
    const payload = buildPatrolPayload(values);
    if (!patrolPreview || previewPayload.current !== JSON.stringify(payload)) {
      throw new Error('请先完成巡检预检');
    }
    if (!patrolPreview.platform_readiness?.can_launch) {
      throw new Error('巡检运行环境未就绪，请先处理阻断项');
    }
    if (patrolPreview.ready_count === 0) {
      throw new Error('没有可发起的巡检设备，请先处理预检错误');
    }
    const serialized = JSON.stringify(payload);
    if (launchIntent.current?.payload !== serialized) {
      launchIntent.current = {
        payload: serialized,
        id: crypto.randomUUID()
      };
    }
    return deviceApi.launchPatrol({
      ...payload,
      request_id: launchIntent.current.id
    });
  }, {
    onSuccess: result => {
      setPatrolLaunch(result);
      launchIntent.current = null;
      clearDraft();
      updateBatchHistoryQuery({
        patrol_batch_id: result.patrol_batch_id || undefined,
        batch_page: 1,
        batch_search: undefined,
        batch_status: undefined
      });
      setMonitorRunId(null);
      refetchActiveBatchRuns();
      refetchPatrolBatches();
      message.success('巡检批次已创建，' + result.queued_count + ' 台设备等待分发' + (result.skipped_count || 0 ? '，跳过 ' + result.skipped_count + ' 台未就绪设备' : ''));
    },
    onError: (error: any) => {
      const detail = error?.response?.data?.detail;
      if (detail?.platform_readiness) {
        setPatrolPreview(current => current ? {
          ...current,
          platform_readiness: detail.platform_readiness
        } : current);
      }
      refetchPatrolReadiness();
      message.error(typeof detail === 'string' ? detail : detail?.message || error?.message || '巡检发起失败');
    }
  });
  const retryWorkflowRun = useMutation((_runId: string) => workflowApi.retryPatrolBatch(activePatrolBatchId!), {
    onSuccess: async result => {
      message.success('巡检重试批次已创建');
      setActivePatrolBatchId(result.new_patrol_batch_id || null);
      setMonitorRunId(null);
      await refetchActiveBatchRuns();
      await refetchPatrolBatches();
      await refetchActiveBatchResultSummary();
    },
    onError: (error: any) => message.error(extractErrorMessage(error, '巡检重试失败'))
  });
  const retryPatrolBatch = useMutation((batchId: string) => workflowApi.retryPatrolBatch(batchId), {
    onSuccess: async result => {
      message.success('已重试 ' + result.retried_count + ' 个失败/取消的运行');
      if (result.new_patrol_batch_id) setActivePatrolBatchId(result.new_patrol_batch_id);
      await refetchActiveBatchRuns();
      await refetchPatrolBatches();
    },
    onError: (error: any) => message.error(extractErrorMessage(error, '批次重试失败'))
  });
  const rerunPatrolBatch = useMutation((batchId: string) => workflowApi.rerunPatrolBatch(batchId), {
    onSuccess: async result => {
      message.success('已为 ' + result.rerun_count + ' 台设备启动重新巡检');
      setActivePatrolBatchId(result.new_patrol_batch_id);
      await Promise.all([refetchPatrolBatches(), refetchActiveBatchRuns(), refetchActiveBatchResultSummary()]);
    },
    onError: (error: any) => message.error(extractErrorMessage(error, '批次重新巡检失败'))
  });
  const cancelPatrolBatch = useMutation(({
    batchId,
    reason
  }: {
    batchId: string;
    reason: string;
  }) => workflowApi.cancelPatrolBatch(batchId, {
    reason
  }), {
    onSuccess: async result => {
      if (result.finalization_errors?.length) {
        message.warning(`批次已停止，但 ${result.finalization_errors.length} 台设备未完成取消结算，请在设备明细中处理`);
      } else if (result.idempotent) {
        message.info('该巡检批次已处于取消终态，未重复写入');
      } else {
        message.success(`已停止后续分发，取消 ${result.cancelled_run_count} 个运行，结算 ${result.cancelled_device_count} 台未完成设备`);
      }
      await Promise.all([refetchPatrolBatches(), refetchActiveBatchRuns(), refetchActiveBatchResultSummary()]);
    },
    onError: (error: any) => message.error(extractErrorMessage(error, '取消巡检批次失败'))
  });
  const confirmCancelPatrolBatch = (batchId: string) => {
    let reason = '';
    Modal.confirm({
      title: '取消巡检批次',
      icon: <StopOutlined />,
      content: <Space direction="vertical" size={8} style={{
        width: '100%'
      }}>
          <Typography.Text type="secondary">
            系统将停止继续分发，并把尚未完成的检查项标记为“已取消/无法判断”。已完成结果不会被删除。
          </Typography.Text>
          <Input.TextArea rows={3} maxLength={1000} showCount placeholder="请输入取消原因（必填）" onChange={event => {
          reason = event.target.value;
        }} />
        </Space>,
      okText: '确认取消',
      okButtonProps: {
        danger: true
      },
      cancelText: '继续运行',
      onOk: async () => {
        const normalized = reason.trim();
        if (normalized.length < 3) {
          message.warning('请输入至少 3 个字符的取消原因');
          throw new Error('cancellation reason is required');
        }
        await cancelPatrolBatch.mutateAsync({
          batchId,
          reason: normalized
        });
      }
    });
  };
  const archivePatrolBatch = useMutation((batchId: string) => workflowApi.archivePatrolBatch(batchId, {
    reason: 'manual archive from Device Management'
  }), {
    onSuccess: async result => {
      message.success('已归档批次 ' + result.patrol_batch_id + '，影响 ' + result.affected_runs + ' 个运行');
      if (activePatrolBatchId === result.patrol_batch_id) {
        setActivePatrolBatchId(null);
      }
      await Promise.all([refetchPatrolBatches()]);
      if (!showArchivedPatrolBatches && patrolBatches.length === 1 && batchHistoryPage > 1) {
        updateBatchHistoryQuery({
          batch_page: batchHistoryPage - 1
        });
      }
    },
    onError: (error: any) => {
      const detail = extractErrorMessage(error, '批次归档失败');
      message.error(detail.includes('active runs') ? '该批次仍有设备正在运行，结束后才能归档' : detail);
    }
  });
  const restorePatrolBatch = useMutation((batchId: string) => workflowApi.restorePatrolBatch(batchId, {
    reason: 'manual restore from Device Management'
  }), {
    onSuccess: async result => {
      message.success(`已恢复批次 ${result.patrol_batch_id}`);
      await refetchPatrolBatches();
    },
    onError: (error: any) => message.error(extractErrorMessage(error, '批次恢复失败'))
  });
  const exportPatrolBatchReport = useMutation((batchId: string) => workflowApi.exportPatrolBatchReport(batchId), {
    onSuccess: download => {
      const url = window.URL.createObjectURL(download.blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = download.filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
    },
    onError: (error: any) => message.error(extractErrorMessage(error, '批次报告导出失败'))
  });
  const exportPatrolBatchTechnicalWorkbook = useMutation((batchId: string) => workflowApi.exportPatrolBatchTechnicalWorkbook(batchId), {
    onSuccess: download => {
      const url = window.URL.createObjectURL(download.blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = download.filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
    },
    onError: (error: any) => message.error(extractErrorMessage(error, '巡检明细 Excel 导出失败'))
  });
  const exportPatrolBatchEvidencePackage = useMutation((batchId: string) => workflowApi.exportPatrolBatchEvidencePackage(batchId), {
    onSuccess: download => {
      const url = window.URL.createObjectURL(download.blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = download.filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
    },
    onError: (error: any) => message.error(extractErrorMessage(error, '证据包导出失败'))
  });
  const exportPatrolBatchRawCli = useMutation((batchId: string) => workflowApi.exportPatrolBatchRawCli(batchId), {
    onSuccess: (blob, batchId) => {
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `${batchId}-raw-output.zip`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
    },
    onError: (error: any) => message.error(extractErrorMessage(error, '原始输出导出失败'))
  });
  const exportPatrolBatchEvents = useMutation((batchId: string) => workflowApi.exportPatrolBatchEvents(batchId), {
    onSuccess: (blob, batchId) => {
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `${batchId}-events.csv`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
    },
    onError: (error: any) => message.error(extractErrorMessage(error, '事件数据导出失败'))
  });
  const devices = useMemo(() => data?.items || [], [data?.items]);
  const openDeviceEvidenceTimeline = (deviceId?: string | null, deviceName?: string | null, workflowRunId?: string | null, patrolBatchId?: string | null) => {
    const key = String(deviceId || '').trim();
    const name = String(deviceName || '').trim();
    if (!key && !name) {
      message.warning('缺少设备 ID，无法打开巡检详情');
      return;
    }
    const matched = devices.find(item => item.device_id === key || item.device_name === name);
    setEvidenceDevice(matched || {
      device_id: key || name,
      device_name: name || key
    });
    setEvidenceRunId(workflowRunId || null);
    setEvidenceBatchId(patrolBatchId || null);
    setEvidenceTab('result');
    setEvidenceOpen(true);
  };
  const patrolTaskColumns = [{
    title: '设备',
    dataIndex: 'device_name',
    width: 220
  }, {
    title: 'IP',
    dataIndex: 'ip',
    width: 130
  }, {
    title: '命令数',
    dataIndex: 'command_count',
    width: 100
  }, {
    title: 'Task ID',
    dataIndex: 'celery_task_id',
    render: (value: string) => <Typography.Text copyable code>{value}</Typography.Text>
  }];
  const patrolRunColumns = [{
    title: '设备',
    dataIndex: 'device_name',
    width: 220
  }, {
    title: 'IP',
    dataIndex: 'ip',
    width: 130
  }, {
    title: '命令数',
    dataIndex: 'command_count',
    width: 100
  }, {
    title: 'Workflow',
    dataIndex: 'workflow_name',
    width: 180
  }, {
    title: 'Run ID',
    dataIndex: 'workflow_run_id',
    render: (value: string) => <Typography.Text copyable code>{value}</Typography.Text>
  }, {
    title: '监控',
    dataIndex: 'workflow_run_id',
    width: 110,
    render: (value: string) => <Button size="small" icon={<EyeOutlined />} onClick={() => setMonitorRunId(value)}>查看</Button>
  }];
  const patrolBatchColumns = [{
    title: '批次 / 时间',
    dataIndex: 'patrol_batch_id',
    width: 250,
    render: (value: string, row: PatrolBatchSummary) => {
      const batchStatus = patrolBatchStatus(row.aggregate_status);
      return <Space direction="vertical" size={2}>
            <Tooltip title={value}>
              <Typography.Text copyable={{
            text: value
          }} code>
                {compactPatrolBatchId(value)}
              </Typography.Text>
            </Tooltip>
            <Space size={4} wrap>
              <Tag color={batchStatus.color}>{batchStatus.label}</Tag>
              {row.archived && <Tag color="warning">已归档</Tag>}
            </Space>
            <Typography.Text type="secondary">发起 {formatDateTime(row.created_at)}</Typography.Text>
            {row.completed_at && <Typography.Text type="secondary">完成 {formatDateTime(row.completed_at)}</Typography.Text>}
            {isMobile && <Typography.Text type="secondary" ellipsis={{
          tooltip: row.target_label
        }}>
                {row.target_label || '旧批次未保存范围快照'}
              </Typography.Text>}
          </Space>;
    }
  }, {
    title: '巡检范围',
    dataIndex: 'target_label',
    width: 210,
    render: (value: string, row: PatrolBatchSummary) => <Space direction="vertical" size={2}>
          <Typography.Text ellipsis={{
        tooltip: value
      }}>{value || '旧批次未保存范围快照'}</Typography.Text>
          <Typography.Text type="secondary">
            目标 {row.target_count ?? row.total} · 发起 {row.launched_count ?? row.total} · 跳过 {row.skipped_count ?? 0}
          </Typography.Text>
        </Space>
  }, {
    title: '设备执行进度',
    width: 220,
    render: (_: unknown, row: PatrolBatchSummary) => {
      const target = row.target_count ?? row.total;
      const settled = row.completed_count + row.failed_count + row.cancelled_count + (row.skipped_count || 0);
      const percent = target ? Math.min(100, Math.round(settled / target * 100)) : 0;
      return <Space direction="vertical" size={2} style={{
        width: '100%'
      }}>
            <Progress percent={percent} size="small" showInfo={false} status={row.failed_count || row.skipped_count ? 'exception' : row.running_count || row.pending_count ? 'active' : 'success'} />
            <Typography.Text type="secondary">
              完成 {row.completed_count} · 运行 {row.running_count} · 等待 {row.pending_count} · 失败 {row.failed_count}
            </Typography.Text>
          </Space>;
    }
  }, {
    title: '登录 / 采集',
    width: 160,
    render: (_: unknown, row: PatrolBatchSummary) => row.manifest_available ? <Space wrap size={[4, 4]}>
          <Tag color="green">成功 {row.connection_succeeded_count || 0}</Tag>
          <Tag color={(row.connection_failed_count || 0) > 0 ? 'red' : 'default'}>失败 {row.connection_failed_count || 0}</Tag>
          {(row.connection_pending_count || 0) > 0 && <Tag color="blue">处理中 {row.connection_pending_count}</Tag>}
          {(row.connection_unknown_count || 0) > 0 && <Tag>未确认 {row.connection_unknown_count}</Tag>}
        </Space> : <Typography.Text type="secondary">旧批次无设备清单</Typography.Text>
  }, {
    title: '操作',
    dataIndex: 'patrol_batch_id',
    width: isMobile ? 84 : 108,
    fixed: 'right' as const,
    render: (value: string, row: PatrolBatchSummary) => {
      const hasActiveRuns = row.running_count + row.pending_count > 0;
      const canCancel = canCancelPatrolBatch({
        archived: row.archived,
        dispatchStatus: row.dispatch_status,
        runningCount: row.running_count,
        pendingCount: row.pending_count
      });
      const canRetry = !row.archived && Boolean(row.failed_count || row.cancelled_count);
      return <Space size={2} className="patrol-history-table__actions">
          <Tooltip title={isMobile ? '查看批次详情' : undefined}>
            <Button type="text" size="small" icon={<EyeOutlined />} aria-label={`查看批次详情 ${value}`} onClick={() => {
            setActivePatrolBatchId(value);
            setBatchDevicePage(1);
            setBatchDeviceSearch('');
            setBatchDeviceSelectionStatus(undefined);
          }}>
              {isMobile ? null : '详情'}
            </Button>
          </Tooltip>
          <Tooltip title="更多批次操作">
            <Dropdown menu={{
            items: [...(canCancel ? [{
              key: 'cancel',
              label: '取消运行',
              icon: <StopOutlined />,
              danger: true,
              disabled: cancelPatrolBatch.isLoading
            }] : []), ...(canRetry ? [{
              key: 'retry',
              label: '重试失败项',
              icon: <ReloadOutlined />,
              disabled: retryPatrolBatch.isLoading
            }] : []), {
              key: 'rerun',
              label: hasActiveRuns ? '运行结束后可全量重跑' : '全量重跑',
              icon: <PlayCircleOutlined />,
              disabled: Boolean(row.archived) || hasActiveRuns || rerunPatrolBatch.isLoading
            }, {
              key: row.archived ? 'restore' : 'archive',
              label: row.archived ? '恢复批次' : hasActiveRuns ? '运行结束后可归档' : '归档批次',
              danger: !row.archived,
              disabled: hasActiveRuns || restorePatrolBatch.isLoading || archivePatrolBatch.isLoading
            }],
            onClick: ({
              key
            }) => {
              if (key === 'cancel') confirmCancelPatrolBatch(value);
              if (key === 'retry') retryPatrolBatch.mutate(value);
              if (key === 'rerun') rerunPatrolBatch.mutate(value);
              if (key === 'restore') restorePatrolBatch.mutate(value);
              if (key === 'archive') archivePatrolBatch.mutate(value);
            }
          }}>
              <Button type="text" size="small" icon={<MoreOutlined />} aria-label={`更多批次操作 ${value}`} />
            </Dropdown>
          </Tooltip>
        </Space>;
    }
  }];
  const effectivePatrolReadiness = patrolPreview?.platform_readiness || patrolPlatformReadiness;
  const renderPatrolPlatformReadiness = () => {
    if (patrolReadinessLoading && !effectivePatrolReadiness) {
      return <StatusNotice type="info" showIcon message="正在检查巡检运行环境" />;
    }
    if (patrolReadinessIsError && !effectivePatrolReadiness) {
      return <StatusNotice type="error" showIcon message="无法完成巡检运行环境检查" description={extractErrorMessage(patrolReadinessError, '请检查 API 服务后重试')} action={<Tooltip title="重新检查">
              <Button size="small" icon={<ReloadOutlined />} aria-label="重新检查巡检运行环境" onClick={() => refetchPatrolReadiness()} />
            </Tooltip>} />;
    }
    if (!effectivePatrolReadiness) return null;
    const title = effectivePatrolReadiness.status === 'blocked' ? `运行环境存在 ${effectivePatrolReadiness.blocking_count} 个阻断项` : effectivePatrolReadiness.status === 'degraded' ? `运行环境可用，存在 ${effectivePatrolReadiness.warning_count} 个告警` : '巡检运行环境已就绪';
    const checks = effectivePatrolReadiness.checks || [];
    const checkDetails = isMobile ? <Space direction="vertical" size={8} style={{
      width: '100%'
    }}>
        {checks.map(check => <div key={check.id} style={{
        borderBottom: '1px solid #f0f0f0',
        paddingBottom: 8
      }}>
            <Space wrap size={6}>
              <Typography.Text strong>{check.name}</Typography.Text>
              <Tag color={readinessStatusColor(check.status)}>{statusLabel(check.status)}</Tag>
            </Space>
            <div><Typography.Text type="secondary">{check.message}</Typography.Text></div>
            {check.blocking && <div><Typography.Text type="danger">{check.remediation}</Typography.Text></div>}
          </div>)}
      </Space> : <Table rowKey="id" size="small" pagination={false} dataSource={checks} columns={[{
      title: '检查项',
      dataIndex: 'name',
      width: 170
    }, {
      title: '状态',
      dataIndex: 'status',
      width: 90,
      render: (value: string) => <Tag color={readinessStatusColor(value)}>{statusLabel(value)}</Tag>
    }, {
      title: '检查结果',
      dataIndex: 'message'
    }, {
      title: '处理建议',
      dataIndex: 'remediation',
      render: (value: string, row: PatrolProductionReadiness['checks'][number]) => row.blocking ? <Typography.Text type="danger">{value}</Typography.Text> : '-'
    }]} scroll={{
      x: 840
    }} />;
    return <section className="patrol-launch-readiness" data-status={effectivePatrolReadiness.status} aria-label="运行环境">
      <div className="patrol-launch-readiness-line">
        {effectivePatrolReadiness.can_launch ? <CheckCircleOutlined /> : <ExclamationCircleOutlined />}
        <strong>{title}</strong>
        <span>检查于 {formatDateTime(effectivePatrolReadiness.checked_at)}</span>
        <Button type="text" size="small" icon={<ReloadOutlined />} loading={patrolReadinessLoading} aria-label="重新检查巡检运行环境" onClick={() => refetchPatrolReadiness()} />
      </div>
      <Collapse key={effectivePatrolReadiness.can_launch ? 'ready' : 'blocked'} ghost defaultActiveKey={effectivePatrolReadiness.can_launch ? [] : ['environment']} items={[{
        key: 'environment', label: '运行环境详情', children: checkDetails
      }]} />
    </section>;
  };
  const renderPatrolPreviewIssueDetails = (item: PatrolPreviewIssue) => {
    const coverageViolations = recordList(item.coverage_violations);
    const connectionIssues = recordList(item.connection_issues);
    return <Space direction="vertical" size={12} style={{
      width: '100%'
    }}>
        {coverageViolations.length > 0 && <Table rowKey={(row, index) => `${recordText(row, ['canonical_command', 'command', 'raw_command'], 'command')}-${index}`} size="small" pagination={false} dataSource={coverageViolations} columns={[{
        title: '命令',
        width: 240,
        render: (_: unknown, row: Record<string, unknown>) => recordText(row, ['canonical_command', 'command', 'raw_command'])
      }, {
        title: '策略',
        width: 120,
        render: (_: unknown, row: Record<string, unknown>) => {
          const status = recordText(row, ['policy_status'], 'eligible');
          return <Tag color={status === 'eligible' ? 'blue' : 'default'}>{status === 'eligible' ? '需结构化' : statusLabel(status)}</Tag>;
        }
      }, {
        title: '覆盖状态',
        width: 140,
        render: (_: unknown, row: Record<string, unknown>) => {
          const status = recordText(row, ['coverage_status', 'parser_status'], 'unknown');
          return <Tag color={coverageStatusColor(status)}>{statusLabel(status)}</Tag>;
        }
      }, {
        title: '身份状态',
        width: 130,
        render: (_: unknown, row: Record<string, unknown>) => recordText(row, ['identity_status'])
      }, {
        title: '模板',
        width: 220,
        render: (_: unknown, row: Record<string, unknown>) => recordText(row, ['template_id', 'template_version'])
      }, {
        title: '失败代码',
        width: 170,
        render: (_: unknown, row: Record<string, unknown>) => recordText(row, ['failure_code', 'reason'])
      }]} scroll={{
        x: 1100
      }} />}
        {connectionIssues.length > 0 && <Table rowKey={(row, index) => `${recordText(row, ['failure_code'], 'collector')}-${index}`} size="small" pagination={false} dataSource={connectionIssues} columns={[{
        title: '失败代码',
        width: 180,
        render: (_: unknown, row: Record<string, unknown>) => recordText(row, ['failure_code', 'code'])
      }, {
        title: '采集器',
        width: 160,
        render: (_: unknown, row: Record<string, unknown>) => recordText(row, ['collector', 'platform', 'device_type'])
      }, {
        title: '凭据引用',
        width: 180,
        render: (_: unknown, row: Record<string, unknown>) => recordText(row, ['credential_ref'])
      }, {
        title: '说明',
        render: (_: unknown, row: Record<string, unknown>) => recordText(row, ['message', 'error', 'detail'])
      }]} scroll={{
        x: 900
      }} />}
      </Space>;
  };
  const renderPatrolPreviewErrors = () => {
    const counts = previewIssueCounts(patrolPreview);
    const issues = patrolPreview?.errors || [];
    return <Space direction="vertical" size={12} style={{
      width: '100%'
    }}>
        <Descriptions size="small" bordered column={isMobile ? 1 : 4}>
          <Descriptions.Item label="目标设备">{patrolPreview?.target_count || 0}</Descriptions.Item>
          <Descriptions.Item label="就绪设备">{patrolPreview?.ready_count || 0}</Descriptions.Item>
          <Descriptions.Item label="阻断设备">{patrolPreview?.error_count || 0}</Descriptions.Item>
          <Descriptions.Item label="启动方式">{patrolPreview?.launch_mode || '-'}</Descriptions.Item>
          <Descriptions.Item label="Parser 覆盖告警">{counts.coverage}</Descriptions.Item>
          <Descriptions.Item label="采集器预检问题">{counts.connection}</Descriptions.Item>
          <Descriptions.Item label="规则资产告警">{patrolPreview?.quality_readiness?.runtime_asset_warning_count || 0}</Descriptions.Item>
        </Descriptions>
        {issues.length > 0 && <Table rowKey={row => `${row.device_id || row.device_name || row.error}-${row.failure_code || 'preflight'}`} size="small" pagination={{
        pageSize: 6
      }} dataSource={issues} columns={[{
        title: '设备',
        width: 220,
        render: (_: unknown, row: PatrolPreviewIssue) => row.device_name || row.device_id || '-'
      }, {
        title: '失败代码',
        dataIndex: 'failure_code',
        width: 190,
        render: (value: string) => value || '-'
      }, {
        title: 'Parser',
        width: 90,
        render: (_: unknown, row: PatrolPreviewIssue) => {
          const count = recordList(row.coverage_violations).length;
          return count ? <Tag color="red">{count}</Tag> : '-';
        }
      }, {
        title: '连接',
        width: 90,
        render: (_: unknown, row: PatrolPreviewIssue) => {
          const count = recordList(row.connection_issues).length;
          return count ? <Tag color="orange">{count}</Tag> : '-';
        }
      }, {
        title: '说明',
        render: (_: unknown, row: PatrolPreviewIssue) => row.error ? <Typography.Text type="danger">{row.error}</Typography.Text> : '-'
      }]} expandable={{
        expandedRowRender: renderPatrolPreviewIssueDetails,
        rowExpandable: row => recordList(row.coverage_violations).length + recordList(row.connection_issues).length > 0
      }} scroll={{
        x: 980
      }} />}
      </Space>;
  };
  return <InspectionLayout onNewPatrol={() => setSourcePickerOpen(true)}>
      <Tabs activeKey={"patrol"} renderTabBar={() => <></>} items={[{
      key: 'patrol',
      label: '巡检运行',
      children: <PatrolOperationsPanel embedded onNewPatrol={() => setSourcePickerOpen(true)} activeBatch={activeBatch} activeBatchDevices={activeBatchDevices} activeBatchDevicesLoading={activeBatchDevicesLoading} activeBatchResultSummary={activeBatchResultSummary} activeBatchSummary={activeBatchSummary} activePatrolBatchId={activePatrolBatchId} patrolBatches={patrolBatches} patrolBatchesLoading={patrolBatchesLoading} patrolBatchTotal={patrolBatchPage?.total || 0} patrolBatchPage={batchHistoryPage} patrolBatchPageSize={batchHistoryPageSize} patrolBatchSearch={batchHistorySearch} patrolBatchStatus={batchHistoryStatus} patrolBatchColumns={patrolBatchColumns} showArchivedBatches={showArchivedPatrolBatches} onClearActiveBatch={() => setActivePatrolBatchId(null)} onCancelBatch={confirmCancelPatrolBatch} onExportBatchReport={batchId => exportPatrolBatchReport.mutate(batchId)} onExportBatchTechnicalWorkbook={batchId => exportPatrolBatchTechnicalWorkbook.mutate(batchId)} onExportBatchEvidencePackage={batchId => exportPatrolBatchEvidencePackage.mutate(batchId)} onExportBatchRawCli={batchId => exportPatrolBatchRawCli.mutate(batchId)} onExportBatchEvents={batchId => exportPatrolBatchEvents.mutate(batchId)} onOpenDeviceEvidence={openDeviceEvidenceTimeline} onRefreshActiveBatchRuns={() => refetchActiveBatchRuns()} onRefreshPatrolBatches={() => refetchPatrolBatches()} onPatrolBatchPageChange={(page, pageSize) => {
        updateBatchHistoryQuery({
          patrol_batch_id: undefined,
          batch_page: pageSize === batchHistoryPageSize ? page : 1,
          batch_page_size: pageSize
        });
      }} onPatrolBatchSearch={value => {
        updateBatchHistoryQuery({
          patrol_batch_id: undefined,
          batch_search: value.trim(),
          batch_page: 1
        });
      }} onPatrolBatchStatusChange={value => {
        updateBatchHistoryQuery({
          patrol_batch_id: undefined,
          batch_status: value,
          batch_page: 1
        });
      }} onRetryRun={runId => retryWorkflowRun.mutate(runId)} onToggleArchivedBatches={checked => {
        updateBatchHistoryQuery({
          patrol_batch_id: undefined,
          batch_archived: checked,
          batch_page: 1
        });
      }} batchDevicePage={batchDevicePage} batchDeviceSearch={batchDeviceSearch} batchDeviceSelectionStatus={batchDeviceSelectionStatus} onBatchDevicePageChange={setBatchDevicePage} onBatchDeviceSearchChange={value => {
        setBatchDeviceSearch(value);
        setBatchDevicePage(1);
      }} onBatchDeviceSelectionStatusChange={value => {
        setBatchDeviceSelectionStatus(value);
        setBatchDevicePage(1);
      }} exportBatchReportLoading={exportPatrolBatchReport.isLoading} exportBatchTechnicalWorkbookLoading={exportPatrolBatchTechnicalWorkbook.isLoading} exportBatchEvidencePackageLoading={exportPatrolBatchEvidencePackage.isLoading} exportBatchRawCliLoading={exportPatrolBatchRawCli.isLoading} exportBatchEventsLoading={exportPatrolBatchEvents.isLoading} retryRunLoading={retryWorkflowRun.isLoading} cancelBatchLoading={cancelPatrolBatch.isLoading} />
    }]} />



      <InspectionDetailPage title={evidenceDevice ? `巡检详情 - ${evidenceDevice.device_name}` : '巡检详情'} open={evidenceOpen} onClose={() => {
      setEvidenceOpen(false);
      setEvidenceDevice(null);
      setEvidenceRunId(null);
      setEvidenceBatchId(null);
      setEvidenceTab('result');
    }}>
        <Tabs activeKey={evidenceTab} items={[{
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
      </InspectionDetailPage>



      <Modal title="新建巡检 · 选择数据来源" open={sourcePickerOpen} onCancel={() => setSourcePickerOpen(false)} footer={null}>
        <Space direction="vertical" size={16} style={{
        width: '100%'
      }}>
          <Button block size="large" onClick={() => {
          setSourcePickerOpen(false);
          patrolForm.resetFields();
          patrolForm.setFieldsValue(patrolDraft.values);
          invalidatePatrolPreview();
          setPatrolLaunch(null);
          setPatrolOpen(true);
        }}>在线采集 · 从设备读取数据</Button>
          <Button block size="large" onClick={() => {
          setSourcePickerOpen(false);
          const next = new URLSearchParams(searchParams);
          next.set('patrol_view', 'offline');
          next.set('new_offline', '1');
          next.delete('offline_job_id');
          next.delete('offline_job_tab');
          setSearchParams(next);
        }}>离线文件 · 单周期检查或双周期对比</Button>
          <Typography.Text type="secondary">在线按设备绑定的命令集采集；离线按上传文件分析。发起前核对设备范围、采集时间与检查缺口。</Typography.Text>
        </Space>
      </Modal>




      <InspectionDetailPage title="新建在线巡检" eyebrow="网络巡检 / 在线巡检" subtitle="选择设备，核对采集命令后发起。" open={patrolOpen} backLabel="返回巡检任务" onClose={() => {
        setPatrolOpen(false);
        invalidatePatrolPreview();
        updateBatchHistoryQuery({ launch: undefined });
      }}>
        <div className="patrol-launch-workspace">
          <ol className="patrol-launch-steps" aria-label="发起巡检步骤">
            {['选择设备', '核对命令', '发起巡检'].map((label, index) => <li key={label} aria-current={index === (patrolLaunch ? 2 : patrolPreview ? 1 : 0) ? 'step' : undefined}>
              <span>{String(index + 1).padStart(2, '0')}</span>{label}
            </li>)}
          </ol>
          {renderPatrolPlatformReadiness()}
          <div className="patrol-launch-setup">
            <section className="patrol-launch-form" aria-labelledby="patrol-scope-heading">
              <h2 id="patrol-scope-heading">选择巡检范围</h2>
              <p className="patrol-launch-description">指定设备，或使用设备分组与现有筛选范围。</p>
              {Object.values(filters).some(Boolean) && <Typography.Paragraph type="secondary">当前筛选：{Object.values(filters).filter(Boolean).join(' · ')}</Typography.Paragraph>}
          {<Form.Item className="patrol-launch-device-picker" label="指定设备" help="留空时按下方分组选择；均未指定时使用当前筛选范围。">
            <PatrolDeviceSelector disabled={launchPatrol.isLoading || Boolean(patrolLaunch)} devices={devices} selected={selectedRowKeys.map(String)} onChange={values => {
            setSelectedRowKeys(values);
            patrolForm.setFieldsValue({
              group: undefined,
              dynamic_group_id: undefined
            });
            saveDraft({
              deviceIds: values,
              values: patrolForm.getFieldsValue()
            });
            invalidatePatrolPreview();
            setPatrolLaunch(null);
          }} />
          </Form.Item>}
          <Form<PatrolFormValues> disabled={launchPatrol.isLoading || Boolean(patrolLaunch)} form={patrolForm} initialValues={{
          ...patrolDraft.values,
          group: searchParams.get('group') || patrolDraft.values.group
        }} layout="vertical" requiredMark={false} onValuesChange={(_, values) => {
          saveDraft({
            deviceIds: selectedRowKeys.map(String),
            values
          });
          invalidatePatrolPreview();
          setPatrolLaunch(null);
        }}>
            <Row gutter={20}>
              <Col xs={24} md={12}>
                <Form.Item name="group" label="设备分组">
                  <Select allowClear showSearch optionFilterProp="label" placeholder="使用当前筛选" disabled={Boolean(selectedRowKeys.length)} onChange={value => {
                  if (value) patrolForm.setFieldValue('dynamic_group_id', undefined);
                }} options={deviceGroups.map(group => ({
                  value: group,
                  label: group
                }))} />
                </Form.Item>
              </Col>
              <Col xs={24} md={12}>
                <Form.Item name="dynamic_group_id" label="动态分组">
                  <Select allowClear placeholder="使用当前筛选" disabled={Boolean(selectedRowKeys.length)} onChange={value => {
                  if (value) patrolForm.setFieldValue('group', undefined);
                }} options={dynamicGroups.filter(group => group.status === 'active').map(group => ({
                  value: group.id,
                  label: group.name
                }))} />
                </Form.Item>
              </Col>
              <Col xs={24}><h3 className="patrol-launch-options-heading">执行选项</h3></Col>
              <Col xs={24} md={12}>
                <Form.Item name="ticket_id" label="工单号">
                  <Input placeholder="可选" />
                </Form.Item>
              </Col>
              <Col xs={24} md={12}>
                <Form.Item name="save_baseline" label="保存为后续对比基线" valuePropName="checked" initialValue={false}>
                  <Switch />
                </Form.Item>
              </Col>
            </Row>
          </Form>
            </section>
            <aside className="patrol-launch-summary" aria-label="本次采集摘要">
              <h2>本次采集</h2>
              <dl>
                <dt>设备范围</dt><dd>{patrolPreview ? `${patrolPreview.target_count} 台设备已预检` : selectedRowKeys.length ? `已指定 ${selectedRowKeys.length} 台设备` : '按分组或筛选范围'}</dd>
                <dt>采集命令</dt><dd>{patrolPreview ? `${patrolPreview.rows.reduce((total, row) => total + row.command_count, 0)} 条设备命令` : '预检后展示完整命令'}</dd>
                <dt>命令来源</dt><dd>设备已绑定的命令集</dd>
                <dt>任务状态</dt><dd>{patrolLaunch ? '已创建巡检批次' : patrolPreview ? `${patrolPreview.ready_count} 台就绪 · ${patrolPreview.error_count} 台待处理` : '尚未发起'}</dd>
              </dl>
              <p>使用已生效的采集与规则资产。解析覆盖不足时保留原始证据，并在报告中标明缺口。</p>
            </aside>
          </div>
          {!patrolPreview && <section className="patrol-launch-preview-placeholder" aria-label="待核对命令">
            <CodeOutlined aria-hidden="true" />
            <div><h2>核对设备与命令</h2><p>{previewPatrol.isLoading ? '正在生成预检计划，请稍候…' : '预览后，在此逐台查看管理地址、命令顺序与预检状态。'}</p></div>
          </section>}
          {patrolPreview && <Space direction="vertical" size={8} style={{
          width: '100%'
        }}>
              <Space wrap>
                <Tag>总数 {patrolPreview.target_count}</Tag>
                <Tag color="green">就绪 {patrolPreview.ready_count}</Tag>
                <Tag color={patrolPreview.error_count ? 'red' : 'default'}>错误 {patrolPreview.error_count}</Tag>
                <Tag color={patrolPreview.quality_readiness?.status === 'degraded' ? 'orange' : 'green'}>
                  报告质量 {statusLabel(patrolPreview.quality_readiness?.status)}
                </Tag>
              </Space>
              <StatusNotice type={patrolPreview.error_count > 0 ? 'error' : patrolPreview.quality_readiness?.status === 'degraded' ? 'warning' : 'success'} showIcon message={patrolPreview.error_count > 0 ? '部分设备未通过巡检预检' : patrolPreview.quality_readiness?.status === 'degraded' ? '设备可以巡检，但最终报告质量将降级' : '设备与规则资产预检通过'} description={renderPatrolPreviewErrors()} />
              <PatrolPlanPreview rows={patrolPreview.rows} />
            </Space>}
          {patrolLaunch && <Space direction="vertical" size={8} style={{
          width: '100%'
        }}>
              <StatusNotice type={patrolLaunch.dispatch_status === 'pending' ? 'warning' : 'info'} showIcon message={`批次已创建，${patrolLaunch.queued_count} 台设备${patrolLaunch.dispatch_status === 'pending' ? '等待调度恢复' : '等待后台分发'}${patrolLaunch.skipped_count || 0 ? `，跳过 ${patrolLaunch.skipped_count} 台未就绪设备` : ''}`} />
              {(patrolLaunch.skipped_count || 0) > 0 && <StatusNotice type="warning" showIcon message="部分设备未发起" description={renderPatrolPreviewErrors()} />}
              {patrolLaunch.launch_mode === 'workflow' ? <>
                  <Table rowKey="workflow_run_id" size="small" pagination={{
              pageSize: 8
            }} dataSource={patrolLaunch.runs} columns={patrolRunColumns} scroll={{
              x: 1080
            }} />
                  <WorkflowRunMonitor runId={monitorRunId} embedded onClose={() => setMonitorRunId(null)} />
                </> : <Table rowKey="celery_task_id" size="small" pagination={{
            pageSize: 8
          }} dataSource={patrolLaunch.tasks} columns={patrolTaskColumns} scroll={{
            x: 820
          }} />}
            </Space>}
          <footer className="patrol-launch-footer">
            <span>{patrolLaunch ? '批次已创建，可返回任务页跟进执行进度。' : patrolPreview ? '请核对设备与命令，确认后发起巡检。' : '先预览设备与命令，再发起巡检。'}</span>
            <Space wrap><Button key="preview" type={patrolPreview ? 'default' : 'primary'} icon={<SearchOutlined />} loading={previewPatrol.isLoading} disabled={Boolean(patrolLaunch) || patrolReadinessLoading || !effectivePatrolReadiness?.can_launch} onClick={() => previewPatrol.mutate()}>
            预览设备与命令
          </Button><Button key="launch" type={patrolPreview ? 'primary' : 'default'} icon={<PlayCircleOutlined />} loading={launchPatrol.isLoading} disabled={previewPatrol.isLoading || Boolean(patrolLaunch) || !patrolPreview || !effectivePatrolReadiness?.can_launch || Boolean(patrolPreview && patrolPreview.ready_count === 0)} onClick={() => launchPatrol.mutate()}>
            {patrolLaunch ? '已发起' : '发起正式巡检'}
          </Button></Space>
          </footer>
        </div>
      </InspectionDetailPage>
    </InspectionLayout>;
};
export default DeviceOperationsWorkspace;
