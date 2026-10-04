import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { del, errorText, get, patch, post } from "../api/client";
import type { Conversation, Message } from "../api/types";
import Icon from "../components/Icon";
import Modal from "../components/Modal";
import { useToast } from "../components/Toast";
import UserPicker from "../components/UserPicker";
import { useAuth } from "../hooks/useAuth";
import { sendEvent, useEvents } from "../hooks/useEvents";
import { formatBytes, formatDate } from "../lib/format";

export default function ChatPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { user } = useAuth();
  const toast = useToast();
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [messages, setMessages] = useState<Message[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [text, setText] = useState("");
  const [typing, setTyping] = useState<string | null>(null);
  const [newChat, setNewChat] = useState(false);
  const [editing, setEditing] = useState<Message | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const typingTimer = useRef<number>();
  const lastTypingSent = useRef(0);

  const loadConversations = useCallback(
    () => get<Conversation[]>("/api/chat/conversations/").then(setConversations).catch(() => {}),
    [],
  );

  const loadMessages = useCallback(async () => {
    if (!id) return;
    try {
      const r = await get<{ results: Message[]; has_more: boolean }>(`/api/chat/conversations/${id}/messages/`);
      setMessages(r.results);
      setHasMore(r.has_more);
      post(`/api/chat/conversations/${id}/read/`).then(loadConversations);
      setTimeout(() => bottomRef.current?.scrollIntoView(), 0);
    } catch (e) {
      toast(errorText(e), "error");
      navigate("/chat");
    }
  }, [id, loadConversations, navigate, toast]);

  useEffect(() => {
    loadConversations();
  }, [loadConversations]);
  useEffect(() => {
    setMessages([]);
    loadMessages();
  }, [loadMessages]);

  useEvents((event, payload) => {
    if (event === "message.new") {
      if (payload.conversation === id) {
        setMessages((ms) => (ms.some((m) => m.id === payload.id) ? ms : [...ms, payload]));
        setTimeout(() => bottomRef.current?.scrollIntoView({ behavior: "smooth" }), 0);
        if (payload.sender !== user?.username) post(`/api/chat/conversations/${id}/read/`);
      }
      loadConversations();
    }
    if (event === "message.updated" && payload.conversation === id) {
      setMessages((ms) => ms.map((m) => (m.id === payload.id ? payload : m)));
    }
    if (event === "conversation.updated") loadConversations();
    if (event === "typing" && payload.conversation === id) {
      setTyping(payload.username);
      window.clearTimeout(typingTimer.current);
      typingTimer.current = window.setTimeout(() => setTyping(null), 3000);
    }
  });

  const loadOlder = async () => {
    if (!id || !messages.length) return;
    const r = await get<{ results: Message[]; has_more: boolean }>(
      `/api/chat/conversations/${id}/messages/?before=${encodeURIComponent(messages[0].created_at)}`,
    );
    setMessages([...r.results, ...messages]);
    setHasMore(r.has_more);
  };

  const send = async (e: FormEvent) => {
    e.preventDefault();
    const body = text.trim();
    if (!body || !id) return;
    if (body.length > 10000) {
      toast("Повідомлення задовге (максимум 10 000 символів).", "error");
      return;
    }
    try {
      if (editing) {
        await patch(`/api/chat/messages/${editing.id}/`, { body });
        setEditing(null);
      } else {
        const msg = await post<Message>(`/api/chat/conversations/${id}/messages/`, { body });
        setMessages((ms) => (ms.some((m) => m.id === msg.id) ? ms : [...ms, msg]));
      }
      setText("");
      setTimeout(() => bottomRef.current?.scrollIntoView({ behavior: "smooth" }), 0);
    } catch (err) {
      toast(errorText(err), "error");
    }
  };

  const current = conversations.find((c) => c.id === id);

  return (
    <div className="page chat-page">
      <div className={`chat-layout ${id ? "has-open" : ""}`}>
        <aside className="card chat-list">
          <div className="row-between chat-list-head">
            <strong>Розмови</strong>
            <button className="icon-btn" onClick={() => setNewChat(true)} aria-label="Нова розмова">
              <Icon name="plus" size={16} />
            </button>
          </div>
          {conversations.length === 0 && <div className="empty-small">Почніть нову розмову</div>}
          {conversations.map((c) => (
            <button key={c.id} className={`conv ${c.id === id ? "active" : ""}`} onClick={() => navigate(`/chat/${c.id}`)}>
              <div className="avatar">{c.is_group ? <Icon name="users" size={16} /> : c.display_title.slice(0, 1).toUpperCase()}</div>
              <div className="conv-body">
                <div className="row-between">
                  <span className="truncate conv-title">{c.display_title}</span>
                  {c.last_message && <span className="small muted nowrap">{formatDate(c.last_message.created_at)}</span>}
                </div>
                <div className="row-between">
                  <span className="truncate small muted">
                    {c.last_message ? `${c.last_message.sender === user?.username ? "Ви: " : ""}${c.last_message.body}` : "Немає повідомлень"}
                  </span>
                  {c.unread > 0 && <span className="count">{c.unread}</span>}
                </div>
              </div>
            </button>
          ))}
        </aside>

        <section className="card chat-window">
          {!id && (
            <div className="empty">
              <Icon name="chat" size={32} />
              Оберіть розмову
            </div>
          )}
          {id && (
            <>
              <div className="chat-head">
                <button className="icon-btn mobile-only" onClick={() => navigate("/chat")} aria-label="Назад">
                  <Icon name="chevronLeft" />
                </button>
                <div>
                  <strong>{current?.display_title}</strong>
                  <div className="small muted">
                    {typing ? `${typing} друкує…` : current?.participants.map((p) => p.username).join(", ")}
                  </div>
                </div>
                {current?.is_group && (
                  <button
                    className="btn btn-sm"
                    onClick={async () => {
                      if (!window.confirm("Вийти з групи?")) return;
                      await post(`/api/chat/conversations/${id}/leave/`);
                      navigate("/chat");
                      loadConversations();
                    }}
                  >
                    Вийти
                  </button>
                )}
              </div>
              <div className="messages">
                {hasMore && (
                  <button className="link-btn center" onClick={loadOlder}>
                    Завантажити попередні
                  </button>
                )}
                {messages.map((m) => {
                  const mine = m.sender === user?.username;
                  return (
                    <div key={m.id} className={`msg ${mine ? "mine" : ""}`}>
                      {!mine && current?.is_group && <div className="msg-sender">{m.sender}</div>}
                      <div className="bubble">
                        {m.deleted ? (
                          <em className="muted">повідомлення видалено</em>
                        ) : (
                          <>
                            <div className="msg-text">{m.body}</div>
                            {m.file_info && (
                              <a className="msg-file" href={`/api/files/items/${m.file_info.id}/download/`}>
                                <Icon name="file" size={14} /> {m.file_info.name} ({formatBytes(m.file_info.size)})
                              </a>
                            )}
                          </>
                        )}
                        <div className="msg-meta">
                          {formatDate(m.created_at)}
                          {m.edited_at && " · змінено"}
                          {mine && !m.deleted && (
                            <>
                              <button className="link-btn" onClick={() => { setEditing(m); setText(m.body); }}>
                                ред.
                              </button>
                              <button
                                className="link-btn"
                                onClick={() => window.confirm("Видалити повідомлення?") && del(`/api/chat/messages/${m.id}/`)}
                              >
                                вид.
                              </button>
                            </>
                          )}
                        </div>
                      </div>
                    </div>
                  );
                })}
                <div ref={bottomRef} />
              </div>
              <form className="composer" onSubmit={send}>
                {editing && (
                  <div className="editing-bar small">
                    Редагування повідомлення{" "}
                    <button type="button" className="link-btn" onClick={() => { setEditing(null); setText(""); }}>
                      скасувати
                    </button>
                  </div>
                )}
                <textarea
                  rows={1}
                  placeholder="Повідомлення…"
                  value={text}
                  maxLength={10000}
                  onChange={(e) => {
                    setText(e.target.value);
                    if (Date.now() - lastTypingSent.current > 2500) {
                      lastTypingSent.current = Date.now();
                      sendEvent({ type: "typing", conversation: id });
                    }
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      send(e as unknown as FormEvent);
                    }
                  }}
                />
                <button className="btn btn-primary" disabled={!text.trim()} aria-label="Надіслати">
                  <Icon name="send" size={16} />
                </button>
              </form>
            </>
          )}
        </section>
      </div>

      {newChat && (
        <NewChatDialog
          onClose={() => setNewChat(false)}
          onCreated={(cid) => {
            setNewChat(false);
            loadConversations();
            navigate(`/chat/${cid}`);
          }}
        />
      )}
    </div>
  );
}

function NewChatDialog({ onClose, onCreated }: { onClose: () => void; onCreated: (id: string) => void }) {
  const [selected, setSelected] = useState<string[]>([]);
  const [title, setTitle] = useState("");
  const [error, setError] = useState("");

  const create = async () => {
    if (!selected.length) return setError("Оберіть хоча б одного учасника.");
    try {
      const c = await post<Conversation>("/api/chat/conversations/", { usernames: selected, title: selected.length > 1 ? title : "" });
      onCreated(c.id);
    } catch (e) {
      setError(errorText(e));
    }
  };

  return (
    <Modal title="Нова розмова" onClose={onClose}>
      <div className="stack">
        <UserPicker onPick={(u) => !selected.includes(u.username) && setSelected([...selected, u.username])} />
        <div className="row gap wrap">
          {selected.map((u) => (
            <span key={u} className="pill">
              {u}
              <button type="button" className="pill-x" onClick={() => setSelected(selected.filter((x) => x !== u))} aria-label="Прибрати">
                ×
              </button>
            </span>
          ))}
        </div>
        {selected.length > 1 && <input placeholder="Назва групи" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={100} />}
        {error && <div className="form-error">{error}</div>}
        <div className="modal-actions">
          <button className="btn btn-primary" onClick={create}>
            Почати
          </button>
        </div>
      </div>
    </Modal>
  );
}
