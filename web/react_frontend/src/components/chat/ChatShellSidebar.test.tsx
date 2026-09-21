import React from 'react'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, expect, it, vi } from 'vitest'
import ChatShellSidebar from './ChatShellSidebar'
import { useChatStore } from '../../store/useChatStore'
const { list, get } = vi.hoisted(() => ({ list:vi.fn(), get:vi.fn() }))
vi.mock('../../services/api', () => ({ conversationApi:{ getConversations:list, getConversation:get } }))
vi.mock('../../context/AuthContext', () => ({ useAuth:()=>({user:{id:1,username:'Alice',app_ids:['assistant']},logout:vi.fn()}) }))
beforeEach(()=>{useChatStore.getState().reset();vi.clearAllMocks()})
it('loads history directly from the workbench menu and opens it', async()=>{
 list.mockResolvedValue([{id:'c1',title:'网络诊断历史',message_count:1}])
 get.mockResolvedValue({conversation:{title:'网络诊断历史'},messages:[{id:'m1',role:'assistant',content:'历史结果'}]})
 const navigate=vi.fn()
 render(<MemoryRouter initialEntries={['/workbench']}><ChatShellSidebar embedded workbench onNavigate={navigate}/></MemoryRouter>)
 fireEvent.click(await screen.findByRole('button',{name:'网络诊断历史',exact:true}))
 await waitFor(()=>expect(navigate).toHaveBeenCalled())
 expect(get).toHaveBeenCalledWith('c1')
 expect(useChatStore.getState().currentConversationId).toBe('c1')
})
it('discards late history responses after logout or account change', async()=>{
 let resolve!: (value:unknown[])=>void
 list.mockImplementation(()=>new Promise(r=>{resolve=r}))
 const request=useChatStore.getState().loadConversations()
 await waitFor(()=>expect(list).toHaveBeenCalled())
 useChatStore.getState().reset()
 resolve([{id:'private',title:'上一个用户',message_count:0}])
 await request
 expect(useChatStore.getState().conversations).toEqual([])
 expect(useChatStore.getState().currentConversationId).toBeNull()
})
