"use client"

import { PropsWithChildren, createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react"
import { useRouter } from "next/navigation"
import { useAuth } from "@/hooks/use-auth"
import { useProjects } from "@/hooks/use-projects"
import { useDocuments } from "@/hooks/use-documents"
import { streamChat } from "@/lib/chat-stream"
import {
  type ChatRequest,
  type Conversation,
  type FeedbackRequest,
  type Message,
  type PaginatedResponse,
  type Source,
} from "@/lib/api"
import { toast } from "@/components/ui/toast"

type FeedbackRating = FeedbackRequest["rating"]

const PAGE_SIZE = 10

interface ChatContextValue {
  conversations: Conversation[]
  conversationsTotal: number
  activeConversationId: string | null
  messages: Message[]
  sources: Source[]
  feedbackByMessageId: Record<string, FeedbackRating>
  pendingFeedbackId: string | null
  isLoadingMessages: boolean
  isStreaming: boolean
  executingNode: string | null
  isLoadingMoreConversations: boolean
  chatMode: "individual" | "project"
  openConversation: (conversationId: string) => Promise<void>
  prepareNewChat: (config: { projectId: string; documentIds: string[] }) => Promise<void>
  submitMessage: (question: string, modelOverride?: string) => Promise<boolean>
  sendFeedback: (messageId: string, rating: FeedbackRating) => Promise<void>
  loadMoreConversations: () => Promise<void>
  setChatMode: (mode: "individual" | "project") => void
}

const ChatContext = createContext<ChatContextValue | null>(null)

function getStoredFeedback(message: Message): FeedbackRating | null {
  const feedback = message.metadata_?.feedback
  if (
    typeof feedback === "object" &&
    feedback !== null &&
    "rating" in feedback &&
    ((feedback as { rating: unknown }).rating === "up" ||
      (feedback as { rating: unknown }).rating === "down")
  ) {
    return (feedback as { rating: FeedbackRating }).rating
  }
  return null
}

export function ChatProvider({ children }: PropsWithChildren) {
  const router = useRouter()
  const { token, authedFetch } = useAuth()
  const { activeProjectId, selectProject } = useProjects()
  const { scopedDocumentIds, hasScopedDocuments } = useDocuments()

  const [conversations, setConversations] = useState<Conversation[]>([])
  const [conversationsTotal, setConversationsTotal] = useState(0)
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null)
  const [messages, setMessages] = useState<Message[]>([])
  const [sources, setSources] = useState<Source[]>([])
  const [feedbackByMessageId, setFeedbackByMessageId] = useState<
    Record<string, FeedbackRating>
  >({})
  const [pendingFeedbackId, setPendingFeedbackId] = useState<string | null>(null)
  const [isLoadingMessages, setIsLoadingMessages] = useState(false)
  const [isStreaming, setIsStreaming] = useState(false)
  const [executingNode, setExecutingNode] = useState<string | null>(null)
  const [isLoadingMoreConversations, setIsLoadingMoreConversations] = useState(false)
  const [chatMode, setChatMode] = useState<"individual" | "project">("project")
  const routerRef = useRef(router)
  routerRef.current = router

  const loadConversations = useCallback(
    async (projectId: string, skip = 0) => {
      const response = await authedFetch<PaginatedResponse<Conversation>>(
        `/api/projects/${projectId}/conversations?skip=${skip}&limit=${PAGE_SIZE}`
      )
      if (skip === 0) {
        setConversations(response.items)
      } else {
        setConversations((current) => [...current, ...response.items])
      }
      setConversationsTotal(response.total)
      return response
    },
    [authedFetch]
  )

  // Load user-scoped conversations (both project + individual)
  const loadUserConversations = useCallback(
    async (skip = 0) => {
      try {
        const response = await authedFetch<PaginatedResponse<Conversation>>(
          `/api/conversations?skip=${skip}&limit=${PAGE_SIZE}`
        )
        if (skip === 0) {
          setConversations(response.items)
        } else {
          setConversations((current) => [...current, ...response.items])
        }
        setConversationsTotal(response.total)
        return response
      } catch {
        // Fall back to project-scoped if user endpoint not available
        if (activeProjectId) {
          return loadConversations(activeProjectId, skip)
        }
        return null
      }
    },
    [authedFetch, activeProjectId, loadConversations]
  )

  useEffect(() => {
    void loadUserConversations()
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const openConversation = useCallback(
    async (conversationId: string) => {
      setIsLoadingMessages(true)
      setActiveConversationId(conversationId)
      setMessages([])
      setSources([])
      routerRef.current.push(`/chat/${conversationId}`, { scroll: false })

      try {
        // Try user-scoped endpoint first, fall back to project-scoped
        let conversation: { messages: Message[] } | null = null
        try {
          conversation = await authedFetch<{ messages: Message[] }>(
            `/api/conversations/${conversationId}`
          )
        } catch {
          if (activeProjectId) {
            conversation = await authedFetch<{ messages: Message[] }>(
              `/api/projects/${activeProjectId}/conversations/${conversationId}`
            )
          }
        }

        if (!conversation) {
          toast.error("Conversation not found")
          routerRef.current.push("/chat", { scroll: false })
          return
        }

        setMessages(conversation.messages)
        setFeedbackByMessageId(
          Object.fromEntries(
            conversation.messages
              .map((m) => [m.id, getStoredFeedback(m)] as const)
              .filter((entry): entry is [string, FeedbackRating] =>
                Boolean(entry[0] && entry[1])
              )
          )
        )
      } catch (caught) {
        toast.error(caught instanceof Error ? caught.message : "Could not load conversation")
      } finally {
        setIsLoadingMessages(false)
      }
    },
    [activeProjectId, authedFetch]
  )

  const prepareNewChat = useCallback(
    async (config: { projectId: string; documentIds: string[] }) => {
      await selectProject(config.projectId)
      setActiveConversationId(null)
      setMessages([])
      setSources([])
      setFeedbackByMessageId({})
      routerRef.current.push("/chat", { scroll: false })
    },
    [selectProject]
  )

  const submitMessage = useCallback(
    async (question: string, modelOverride?: string) => {
      if (!question.trim() || isStreaming || !token) return false
      if (chatMode === "project" && !activeProjectId) return false

      const userQuestion = question.trim()
      setIsStreaming(true)
      setExecutingNode(null)
      setSources([])
      setMessages((current) => [
        ...current,
        { role: "user", content: userQuestion } as Message,
        { role: "assistant", content: "" } as Message,
      ])

      try {
        const request: ChatRequest = {
          conversation_id: activeConversationId,
          message: userQuestion,
          document_ids: chatMode === "project" ? scopedDocumentIds : [],
          model: modelOverride,
        }

        await streamChat({
          projectId: chatMode === "project" ? activeProjectId! : undefined,
          token,
          request,
          individualChat: chatMode === "individual",
          onConversation: (conversation) => {
            setActiveConversationId(conversation.id)
            routerRef.current.push(`/chat/${conversation.id}`, { scroll: false })
          },
          onSources: (newSources) => {
            setSources(newSources)
          },
          onToken: (tokenChunk) => {
            setMessages((current) => {
              const next = [...current]
              const last = next[next.length - 1]
              next[next.length - 1] = {
                ...last,
                content: `${last.content}${tokenChunk}`,
              }
              return next
            })
          },
          onNode: (nodeName) => {
            setExecutingNode(nodeName)
          },
          onFinal: (payload) => {
            setMessages((current) => {
              const next = [...current]
              const last = next[next.length - 1]
              next[next.length - 1] = {
                ...last,
                id: payload.message_id,
                content: payload.content,
              }
              return next
            })
          },
        })

        if (activeProjectId) {
          await loadConversations(activeProjectId)
        }
        void loadUserConversations()
        return true
      } catch (caught) {
        toast.error(caught instanceof Error ? caught.message : "Chat failed")
        setMessages((current) => current.slice(0, Math.max(0, current.length - 1)))
        return false
      } finally {
        setIsStreaming(false)
        setExecutingNode(null)
      }
    },
    [
      activeConversationId,
      activeProjectId,
      chatMode,
      hasScopedDocuments,
      isStreaming,
      loadConversations,
      loadUserConversations,
      scopedDocumentIds,
      token,
    ]
  )

  const sendFeedback = useCallback(
    async (messageId: string, rating: FeedbackRating) => {
      if (!activeProjectId) return

      setPendingFeedbackId(messageId)
      try {
        await authedFetch<{ status: string }>(
          `/api/projects/${activeProjectId}/chat/messages/${messageId}/feedback`,
          {
            method: "POST",
            body: JSON.stringify({ rating } satisfies FeedbackRequest),
          }
        )
        setFeedbackByMessageId((current) => ({ ...current, [messageId]: rating }))
        setMessages((current) =>
          current.map((m) => {
            if (m.id !== messageId) return m
            return {
              ...m,
              metadata_: { ...m.metadata_, feedback: { rating, comment: null } },
            }
          })
        )
        toast.success("Feedback saved")
      } catch (caught) {
        toast.error(caught instanceof Error ? caught.message : "Could not save feedback")
      } finally {
        setPendingFeedbackId(null)
      }
    },
    [activeProjectId, authedFetch]
  )

  const loadMoreConversations = useCallback(async () => {
    if (conversations.length >= conversationsTotal) return

    setIsLoadingMoreConversations(true)
    try {
      await loadUserConversations(conversations.length)
    } catch {
      toast.error("Failed to load more conversations")
    } finally {
      setIsLoadingMoreConversations(false)
    }
  }, [conversations.length, conversationsTotal, loadUserConversations])

  const value = useMemo<ChatContextValue>(
    () => ({
      conversations,
      conversationsTotal,
      activeConversationId,
      messages,
      sources,
      feedbackByMessageId,
      pendingFeedbackId,
      isLoadingMessages,
      isStreaming,
      executingNode,
      isLoadingMoreConversations,
      chatMode,
      openConversation,
      prepareNewChat,
      submitMessage,
      sendFeedback,
      loadMoreConversations,
      setChatMode,
    }),
    [
      conversations,
      conversationsTotal,
      activeConversationId,
      messages,
      sources,
      feedbackByMessageId,
      pendingFeedbackId,
      isLoadingMessages,
      isStreaming,
      executingNode,
      isLoadingMoreConversations,
      chatMode,
      openConversation,
      prepareNewChat,
      submitMessage,
      sendFeedback,
      loadMoreConversations,
    ]
  )

  return <ChatContext.Provider value={value}>{children}</ChatContext.Provider>
}

export function useChat() {
  const value = useContext(ChatContext)
  if (!value) throw new Error("useChat must be used inside ChatProvider")
  return value
}
