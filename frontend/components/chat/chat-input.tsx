"use client"

import { ArrowUpIcon, GlobeIcon, LibraryIcon, UserIcon, FolderIcon } from "lucide-react"
import { FormEvent, forwardRef } from "react"

import { Button } from "@/components/ui/button"
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu"
import { Textarea } from "@/components/ui/textarea"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"

interface ProjectOption {
  id: string
  name: string
}

interface ChatInputProps {
  question: string
  onQuestionChange: (value: string) => void
  onSubmit: (e: FormEvent<HTMLFormElement>) => void
  onLibraryClick: () => void
  isStreaming: boolean
  isLoadingMessages: boolean
  hasActiveProject: boolean
  webSearchEnabled: boolean
  onWebSearchToggle: () => void
  selectedModel: string
  onModelChange: (model: string) => void
  projects?: ProjectOption[]
  chatMode: "individual" | "project"
  activeProjectId: string | null
  onModeChange: (mode: "individual" | "project", projectId?: string) => void
}

const COMMON_MODELS = [
  "qwen2.5:7b",
  "gemma4:e4b",
]

export const ChatInput = forwardRef<HTMLFormElement, ChatInputProps>(
  function ChatInput({
    question,
    onQuestionChange,
    onSubmit,
    onLibraryClick,
    isStreaming,
    isLoadingMessages,
    hasActiveProject,
    webSearchEnabled,
    onWebSearchToggle,
    selectedModel,
    onModelChange,
    projects,
    chatMode,
    activeProjectId,
    onModeChange,
  }: ChatInputProps, ref) {
    const disabled = !hasActiveProject || isStreaming || isLoadingMessages
    const activeProjectName = projects?.find(p => p.id === activeProjectId)?.name

    return (
      <div className="absolute bottom-6 left-0 right-0 px-8 pointer-events-none">
        <div className="max-w-3xl mx-auto flex flex-col gap-2 pointer-events-auto">
          <form
            ref={ref}
            onSubmit={onSubmit}
            className="flex flex-col gap-2 p-3 bg-muted rounded-3xl shadow-sm transition duration-200"
          >
            <Textarea
              value={question}
              onChange={(event) => onQuestionChange(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault()
                  const form = event.currentTarget.closest("form")
                  if (form) form.requestSubmit()
                }
              }}
              placeholder={chatMode === "individual" ? "Ask anything..." : "Ask anything... @sources"}
              disabled={disabled}
              className="min-h-12 max-h-48 w-full resize-none bg-transparent border-none outline-none shadow-none focus-visible:ring-0 p-1 px-2 text-[15px] text-foreground placeholder-muted-foreground/60"
            />

            {/* Project / Chat Mode Selector — Codex-style */}
            <div className="flex items-center gap-1.5 px-1">
              <DropdownMenu>
                <DropdownMenuTrigger>
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    className="text-[11px] h-7 text-muted-foreground hover:text-foreground font-medium px-2 rounded-lg flex items-center gap-1.5 shrink-0"
                  >
                    {chatMode === "individual" ? (
                      <><UserIcon className="size-3" /> Individual</>
                    ) : (
                      <><FolderIcon className="size-3" /> {activeProjectName || "Project"}</>
                    )}
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="start" className="w-56">
                  <DropdownMenuItem
                    onClick={() => onModeChange("individual")}
                    className={cn(
                      "text-xs cursor-pointer",
                      chatMode === "individual" && "bg-muted font-medium"
                    )}
                  >
                    <UserIcon className="size-3 mr-2" />
                    Individual Chat
                  </DropdownMenuItem>
                  <DropdownMenuSeparator />
                  {projects?.map((project) => (
                    <DropdownMenuItem
                      key={project.id}
                      onClick={() => onModeChange("project", project.id)}
                      className={cn(
                        "text-xs cursor-pointer",
                        chatMode === "project" && activeProjectId === project.id && "bg-muted font-medium"
                      )}
                    >
                      <FolderIcon className="size-3 mr-2" />
                      {project.name}
                    </DropdownMenuItem>
                  ))}
                </DropdownMenuContent>
              </DropdownMenu>
            </div>

            <div className="flex items-center justify-between pt-1">
              <div className="flex items-center gap-2">
                {chatMode === "project" && (
                  <Tooltip>
                    <TooltipTrigger>
                      <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        className="text-[11px] h-8 text-muted-foreground hover:text-foreground hover:bg-background/50 font-medium px-2 rounded-lg flex items-center gap-1.5 shrink-0"
                        onClick={onLibraryClick}
                      >
                        <LibraryIcon className="size-3.5" />
                        Library
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent>Manage sources</TooltipContent>
                  </Tooltip>
                )}

                <Tooltip>
                  <TooltipTrigger>
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      className={cn(
                        "text-[11px] h-8 font-medium px-2 rounded-lg flex items-center gap-1.5 shrink-0 transition-colors",
                        webSearchEnabled
                          ? "bg-primary/10 text-primary"
                          : "text-muted-foreground hover:text-foreground hover:bg-background/50"
                      )}
                      onClick={onWebSearchToggle}
                    >
                      <GlobeIcon className="size-3.5" />
                      Web
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>Toggle Google search</TooltipContent>
                </Tooltip>

                <DropdownMenu>
                  <DropdownMenuTrigger>
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      className="text-[11px] h-8 text-muted-foreground hover:text-foreground hover:bg-background/50 font-medium px-2 rounded-lg flex items-center gap-1.5 shrink-0"
                    >
                      {selectedModel}
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="start" className="w-48">
                    {COMMON_MODELS.map((model) => (
                      <DropdownMenuItem
                        key={model}
                        onClick={() => onModelChange(model)}
                        className={cn(
                          "text-xs cursor-pointer",
                          selectedModel === model && "bg-muted font-medium"
                        )}
                      >
                        {model}
                      </DropdownMenuItem>
                    ))}
                  </DropdownMenuContent>
                </DropdownMenu>
              </div>

              <Button
                size="icon-sm"
                type="submit"
                disabled={disabled || !question.trim()}
                className={cn(
                  "size-8 rounded-full flex items-center justify-center transition-all duration-200",
                  question.trim()
                    ? "bg-foreground text-background hover:bg-foreground/90 shadow-sm"
                    : "bg-background text-muted-foreground"
                )}
              >
                <ArrowUpIcon className="size-4 shrink-0" />
              </Button>
            </div>
          </form>
        </div>
      </div>
    )
  }
)
