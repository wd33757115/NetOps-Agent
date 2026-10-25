// SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
// SPDX-License-Identifier: Apache-2.0

import React, { useMemo, useState } from 'react'
import {
  Alert,
  Button,
  Form,
  Input,
  Modal,
  Popconfirm,
  Select,
  Switch,
  Table,
  message,
} from 'antd'
import { PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { useMutation, useQuery, useQueryClient } from 'react-query'
import SystemLayout from '../components/system/SystemLayout'
import { GrokChip, GrokInfoBar, GrokRowAction, GrokToolBtn } from '../components/ui/GrokUi'
import { useAuth } from '../context/AuthContext'
import { useIsMobile } from '../hooks/useBreakpoint'
import { ManagedUser, userAdminApi } from '../services/api'

const ROLE_OPTIONS = [
  { value: 'admin', label: 'admin — 平台管理员' },
  { value: 'operator', label: '普通用户 — 按应用授权访问' },
]

const ROLE_FILTER_OPTIONS = [
  { value: 'admin', label: 'admin' },
  { value: 'operator', label: '普通用户' },
]

const PASSWORD_HINT =
  '至少 8 位，不能与用户名/邮箱过于相似，且不能使用常见密码（如 admin123、password）'

const PASSWORD_RULES = [
  { required: true, message: '请输入密码' },
  { min: 8, message: '密码至少 8 位' },
]

function isSystemAdmin(user: ManagedUser): boolean {
  return user.username.toLowerCase() === 'admin'
}

function deleteBlockedReason(
  user: ManagedUser,
  users: ManagedUser[],
  currentUserId?: number
): string | null {
  if (isSystemAdmin(user)) return '系统内置账号不可删除'
  if (currentUserId != null && user.id === currentUserId) return '不能删除当前登录账号'
  if (user.role === 'admin' && user.is_active) {
    const activeAdmins = users.filter((u) => u.role === 'admin' && u.is_active)
    if (activeAdmins.length <= 1) return '至少保留一名启用的管理员'
  }
  return null
}

function canDeleteUser(user: ManagedUser, users: ManagedUser[], currentUserId?: number): boolean {
  return deleteBlockedReason(user, users, currentUserId) == null
}

const UsersPage: React.FC = () => {
  const isMobile = useIsMobile()
  const { user: currentUser, refreshProfile } = useAuth()
  const queryClient = useQueryClient()
  const [createOpen, setCreateOpen] = useState(false)
  const [editUser, setEditUser] = useState<ManagedUser | null>(null)
  const [resetUser, setResetUser] = useState<ManagedUser | null>(null)
  const [search, setSearch] = useState('')
  const [roleFilter, setRoleFilter] = useState<string | undefined>()
  const [createForm] = Form.useForm()
  const [editForm] = Form.useForm()
  const [resetForm] = Form.useForm()

  const { data: users = [], isLoading, isError, error, refetch } = useQuery<ManagedUser[]>(
    'managed-users',
    userAdminApi.list,
    { refetchOnWindowFocus: false }
  )

  const activeCount = users.filter((u) => u.is_active).length
  const hasLastLogin = useMemo(() => users.some((u) => u.last_login), [users])

  const filteredUsers = useMemo(() => {
    const q = search.trim().toLowerCase()
    return users.filter((user) => {
      if (roleFilter && user.role !== roleFilter) return false
      if (!q) return true
      return (
        user.username.toLowerCase().includes(q) ||
        (user.email || '').toLowerCase().includes(q)
      )
    })
  }, [users, search, roleFilter])

  const createMutation = useMutation(userAdminApi.create, {
    onSuccess: () => {
      message.success('用户已创建')
      setCreateOpen(false)
      createForm.resetFields()
      queryClient.invalidateQueries('managed-users')
        queryClient.invalidateQueries(['system-access'])
        queryClient.invalidateQueries(['system-groups'])
        void refreshProfile()
    },
    onError: (err: Error) => message.error(err.message || '创建失败'),
  })

  const updateMutation = useMutation(
    ({ id, data }: { id: number; data: Partial<ManagedUser> }) => userAdminApi.update(id, data),
    {
      onSuccess: () => {
        message.success('用户已更新')
        setEditUser(null)
        editForm.resetFields()
        queryClient.invalidateQueries('managed-users')
        queryClient.invalidateQueries(['system-access'])
        queryClient.invalidateQueries(['system-groups'])
        void refreshProfile()
      },
      onError: (err: Error) => message.error(err.message || '更新失败'),
    }
  )

  const resetMutation = useMutation(
    ({ id, password }: { id: number; password: string }) => userAdminApi.resetPassword(id, password),
    {
      onSuccess: () => {
        message.success('密码已重置')
        setResetUser(null)
        resetForm.resetFields()
      },
      onError: (err: Error) => message.error(err.message || '重置失败'),
    }
  )

  const deleteMutation = useMutation((id: number) => userAdminApi.delete(id), {
    onSuccess: () => {
      message.success('用户已删除')
      queryClient.invalidateQueries('managed-users')
        queryClient.invalidateQueries(['system-access'])
        queryClient.invalidateQueries(['system-groups'])
        void refreshProfile()
    },
    onError: (err: Error) => message.error(err.message || '删除失败'),
  })

  const currentUserId = currentUser?.id

  const columns: ColumnsType<ManagedUser> = useMemo(() => {
    const base: ColumnsType<ManagedUser> = [
      {
        title: '用户名',
        dataIndex: 'username',
        key: 'username',
        width: 168,
        render: (username: string) => (
          <span className="grok-user-name-cell">
            <span className="grok-user-name">{username}</span>
            {username.toLowerCase() === 'admin' ? (
              <GrokChip className="is-system-badge">系统账号</GrokChip>
            ) : null}
          </span>
        ),
      },
      {
        title: '邮箱',
        dataIndex: 'email',
        key: 'email',
        width: 180,
        ellipsis: true,
        render: (email: string) => email || <span className="grok-table-muted">未设置</span>,
      },
      {
        title: '角色',
        dataIndex: 'role',
        key: 'role',
        width: 160,
        render: (_, row) => row.role === 'admin' ? 'admin · 全部权限' : row.netops_configure ? 'netops-config · 应用授权' : '普通用户',
      },
      {
        title: '状态',
        dataIndex: 'is_active',
        key: 'is_active',
        width: 88,
        render: (active: boolean) => (
          <GrokChip tone={active ? 'ok' : 'default'}>{active ? '启用' : '禁用'}</GrokChip>
        ),
      },
    ]

    if (hasLastLogin) {
      base.push({
        title: '最后登录',
        dataIndex: 'last_login',
        key: 'last_login',
        width: 168,
        render: (value: string | null) =>
          value ? (
            new Date(value).toLocaleString()
          ) : (
            <span className="grok-table-muted">从未登录</span>
          ),
      })
    }

    base.push({
      title: '操作',
      key: 'actions',
      width: 220,
      fixed: 'right',
      render: (_, record) => {
        const deletable = canDeleteUser(record, users, currentUserId)
        const blockedReason = deleteBlockedReason(record, users, currentUserId)

        return (
          <span className="grok-row-actions grok-row-actions-nowrap">
            <GrokRowAction
              onClick={() => {
                setEditUser(record)
                editForm.setFieldsValue({
                  email: record.email,
                  role: record.role,
                  netops_configure: record.netops_configure,
                  is_active: record.is_active,
                })
              }}
            >
              编辑
            </GrokRowAction>
            <GrokRowAction onClick={() => setResetUser(record)}>重置密码</GrokRowAction>
            {deletable ? (
              <Popconfirm
                title={`确认删除用户「${record.username}」？`}
                description="删除后无法恢复"
                okText="删除"
                okType="danger"
                onConfirm={() => deleteMutation.mutate(record.id)}
              >
                <GrokRowAction danger disabled={deleteMutation.isLoading}>
                  删除
                </GrokRowAction>
              </Popconfirm>
            ) : (
              <span className="grok-row-action-hint" title={blockedReason ?? undefined}>
                不可删除
              </span>
            )}
          </span>
        )
      },
    })

    return base
  }, [editForm, users, currentUserId, deleteMutation, hasLastLogin])

  const toolbar = (
    <>
      <Input
        className="grok-search-input"
        placeholder="搜索用户名或邮箱…"
        allowClear
        value={search}
        onChange={(e) => setSearch(e.target.value)}
      />
      <Select
        className="grok-users-role-filter"
        allowClear
        aria-label="平台角色筛选"
        placeholder="全部角色"
        value={roleFilter}
        onChange={(value) => setRoleFilter(value)}
        options={ROLE_FILTER_OPTIONS}
      />
      <GrokToolBtn icon={<ReloadOutlined />} onClick={() => refetch()}>
        刷新
      </GrokToolBtn>
      <GrokToolBtn primary icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
        新建用户
      </GrokToolBtn>
    </>
  )

  const emptyText =
    search.trim() || roleFilter
      ? '没有匹配的用户，请调整搜索或筛选条件'
      : '暂无用户，点击「新建用户」创建第一个账号'

  return (
    <SystemLayout
      title="用户"
      subtitle="创建用户、分配角色、启用/禁用账号与重置密码"
      toolbar={toolbar}
    >
      <div className="system-summary"><span>用户总数<strong>{isLoading || isError ? '—' : users.length}</strong></span><span>已启用<strong>{isLoading || isError ? '—' : activeCount}</strong></span><span>已停用<strong>{isLoading || isError ? '—' : users.length - activeCount}</strong></span></div>
      {isError && <Alert type="error" showIcon message="用户列表加载失败" description={error instanceof Error ? error.message : '请稍后重试'} action={<Button onClick={() => refetch()}>重试</Button>} />}

      <GrokInfoBar>
        <span>密码需满足复杂度要求；新建或重置密码时可在表单中查看详细策略</span>
      </GrokInfoBar>

      <section className="grok-panel grok-panel-flush" hidden={isError}>
        <Table
          className="grok-table grok-table-users"
          rowKey="id"
          loading={isLoading}
          size={isMobile ? 'small' : 'middle'}
          columns={columns}
          dataSource={filteredUsers}
          scroll={{ x: isMobile ? 640 : hasLastLogin ? 920 : 760 }}
          locale={{ emptyText }}
          pagination={{
            pageSize: isMobile ? 8 : 10,
            showSizeChanger: !isMobile,
            simple: isMobile,
            hideOnSinglePage: true,
            showTotal: isMobile ? undefined : (total) => `共 ${total} 人`,
          }}
        />
      </section>

      <Modal
        title="新建用户"
        open={createOpen}
        onCancel={() => {
          setCreateOpen(false)
          createForm.resetFields()
        }}
        onOk={() => createForm.submit()}
        confirmLoading={createMutation.isLoading}
        maskClosable={false}
        destroyOnClose
      >
        <Form
          form={createForm}
          layout="vertical"
          initialValues={{ role: 'operator' }}
          onFinish={(values) => createMutation.mutate(values)}
        >
          <Form.Item name="username" label="用户名" rules={[{ required: true, message: '请输入用户名' }]}>
            <Input autoComplete="username" />
          </Form.Item>
          <Form.Item name="email" label="邮箱">
            <Input type="email" autoComplete="email" />
          </Form.Item>
          <Form.Item
            name="password"
            label="初始密码"
            rules={PASSWORD_RULES}
            extra={PASSWORD_HINT}
          >
            <Input.Password autoComplete="new-password" />
          </Form.Item>
          <Form.Item name="role" label="平台角色" extra="应用使用权在应用授权页单独配置；获得 App 授权后可使用全部业务功能。" rules={[{ required: true }]}>
            <Select options={ROLE_OPTIONS} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={`编辑用户：${editUser?.username ?? ''}`}
        open={!!editUser}
        onCancel={() => {
          setEditUser(null)
          editForm.resetFields()
        }}
        onOk={() => editForm.submit()}
        confirmLoading={updateMutation.isLoading}
        maskClosable={false}
        destroyOnClose
      >
        <Form
          form={editForm}
          layout="vertical"
          onFinish={(values) => {
            if (!editUser) return
            updateMutation.mutate({ id: editUser.id, data: values })
          }}
        >
          <Form.Item name="email" label="邮箱">
            <Input type="email" />
          </Form.Item>
          <Form.Item name="role" label="平台角色" extra="应用使用权在应用授权页单独配置；获得 App 授权后可使用全部业务功能。" rules={[{ required: true }]}>
            <Select options={ROLE_OPTIONS} />
          </Form.Item>
          <Form.Item name="netops_configure" label="应用授权管理员（netops-config）" valuePropName="checked" extra="可为用户和用户组授权应用，不能管理账号或提升系统权限。"><Switch /></Form.Item>
          <Form.Item name="is_active" label="启用" valuePropName="checked">
            <Switch checkedChildren="启用" unCheckedChildren="禁用" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={`重置密码：${resetUser?.username ?? ''}`}
        open={!!resetUser}
        onCancel={() => {
          setResetUser(null)
          resetForm.resetFields()
        }}
        onOk={() => resetForm.submit()}
        confirmLoading={resetMutation.isLoading}
        maskClosable={false}
        destroyOnClose
      >
        <Form
          form={resetForm}
          layout="vertical"
          onFinish={(values) => {
            if (!resetUser) return
            resetMutation.mutate({ id: resetUser.id, password: values.new_password })
          }}
        >
          <Form.Item
            name="new_password"
            label="新密码"
            rules={PASSWORD_RULES}
            extra={PASSWORD_HINT}
          >
            <Input.Password autoComplete="new-password" />
          </Form.Item>
        </Form>
      </Modal>
    </SystemLayout>
  )
}

export default UsersPage
