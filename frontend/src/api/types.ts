export interface Preferences {
  language: "uk" | "en";
  timezone: string;
  theme: "system" | "dark" | "light";
  accent: "violet" | "blue" | "teal" | "green" | "amber" | "orange" | "rose" | "slate";
  discoverable: boolean;
  notify_login_email: boolean;
  mail_load_remote_images: boolean;
  week_starts_monday: boolean;
}

export interface User {
  id: string;
  username: string;
  email: string;
  display_name: string;
  is_staff: boolean;
  has_2fa: boolean;
  quota_bytes: number;
  used_bytes: number;
  date_joined: string;
  mailbox: string | null;
  preferences: Preferences;
  must_enroll_2fa: boolean;
  features?: { office: boolean };
}

export interface Folder {
  id: string;
  name: string;
  parent: string | null;
  created_at: string;
  updated_at: string;
  deleted_at: string | null;
}

export type FileStatus = "uploading" | "scanning" | "clean" | "infected" | "unscanned" | "failed";

export interface FileItem {
  id: string;
  name: string;
  folder: string | null;
  size: number;
  mime_type: string;
  sha256: string;
  status: FileStatus;
  status_display: string;
  scan_detail: string;
  downloadable: boolean;
  has_thumbnail: boolean;
  content_version: number;
  owner: string;
  shared: boolean | null;
  created_at: string;
  updated_at: string;
  deleted_at: string | null;
}

export interface BrowseResult {
  folder: Folder | null;
  writable: boolean;
  owner: string;
  breadcrumbs: { id: string; name: string }[];
  folders: Folder[];
  files: FileItem[];
}

export interface Share {
  id: string;
  owner: string;
  recipient: string;
  file: string | null;
  folder: string | null;
  target_name: string;
  target_type: "file" | "folder";
  created_at: string;
  file_info?: FileItem;
}

export interface PublicLink {
  id: string;
  file: string;
  file_name: string;
  expires_at: string;
  max_downloads: number | null;
  download_count: number;
  has_password: boolean;
  active: boolean;
  created_at: string;
  url?: string;
}

export interface CalendarT {
  id: string;
  name: string;
  color: string;
  owner: string;
  is_owner: boolean;
  can_edit: boolean;
  has_feed: boolean;
  shared_with: { username: string; can_edit: boolean }[];
}

export interface EventT {
  id: string;
  calendar: string;
  title: string;
  description: string;
  location: string;
  start: string;
  end: string;
  all_day: boolean;
  rrule: string;
  reminder_minutes: number | null;
  color?: string;
  occurrence_start?: string;
  occurrence_end?: string;
}

export interface Conversation {
  id: string;
  is_group: boolean;
  title: string;
  display_title: string;
  participants: { username: string; display_name: string; is_admin: boolean }[];
  last_message: { sender: string | null; body: string; created_at: string } | null;
  last_message_at: string | null;
  unread: number;
}

export interface Message {
  id: string;
  conversation: string;
  sender: string | null;
  body: string;
  file: string | null;
  file_info: { id: string; name: string; size: number } | null;
  created_at: string;
  edited_at: string | null;
  deleted: boolean;
  reactions: Reaction[];
}

export interface Reaction {
  emoji: string;
  count: number;
  users: string[];
}

export interface MailFolder {
  name: string;
  special: string | null;
  messages: number;
  unseen: number;
}

export interface MailSummary {
  uid: number;
  from: string;
  to: string;
  subject: string;
  date: string | null;
  size: number;
  seen: boolean;
  flagged: boolean;
  answered: boolean;
  has_attachments: boolean;
}

export interface MailMessage {
  uid: number;
  folder: string;
  from: string;
  to: string;
  cc: string;
  reply_to: string;
  subject: string;
  date: string | null;
  message_id: string;
  references: string;
  text: string;
  has_html: boolean;
  attachments: { index: number; filename: string; content_type: string; size: number }[];
}

export interface Notification {
  id: string;
  kind: string;
  title: string;
  body: string;
  link: string;
  created_at: string;
  read_at: string | null;
}

export interface Paginated<T> {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
}
