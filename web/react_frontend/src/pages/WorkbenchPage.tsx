import React, { useEffect } from 'react'
import { Alert, Button, Modal, Spin } from 'antd'
import GrokShellLayout from '../components/Layout/GrokShellLayout'
import DesktopCanvas from '../components/desktop/DesktopCanvas'
import { useAuth } from '../context/AuthContext'
import { useDesktopStore } from '../store/useDesktopStore'

export default function WorkbenchPage() {
  const { user } = useAuth()
  const { load, ready, status, error, flush, userId } = useDesktopStore()
  useEffect(() => {
    if (user && user.id !== useDesktopStore.getState().userId)
      void load(user.id)
  }, [user, load])
  const retry = () => {
    if (!user) return
    if (ready) void flush()
    else void load(user.id)
  }
  const reload = () =>
    Modal.confirm({
      title: '使用服务器上的最新布局？',
      content: '当前本地草稿会被替换。',
      okText: '加载最新布局',
      cancelText: '保留草稿',
      onOk: () => user && load(user.id, true),
    })
  const available = ready && userId === user?.id
  return (
    <GrokShellLayout mode="workbench" title="工作台">
      {error && (
        <Alert
          type={status === 'conflict' ? 'warning' : 'error'}
          showIcon
          message={error}
          action={
            <Button
              size="small"
              onClick={status === 'conflict' ? reload : retry}
            >
              {status === 'conflict' ? '加载最新布局' : '重试'}
            </Button>
          }
        />
      )}
      {available ? (
        <>
          <DesktopCanvas />
          {status !== 'saved' && <div className="desktop-save-status" role="status">
            {status === 'saving'
                ? '正在保存…'
                : status === 'pending'
                  ? '等待同步…'
                  : '本地修改待同步'}
          </div>}
        </>
      ) : status === 'loading' ? (
        <div className="grok-page-loading">
          <Spin tip="正在加载桌面" />
        </div>
      ) : null}
    </GrokShellLayout>
  )
}
