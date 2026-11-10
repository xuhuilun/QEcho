"""Small single-process SQLite store with transactional permission revisions."""

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .runtime import protect_path
from .schemas import DEFAULT_SETTINGS, ContactCreate, ContactPatch, SettingsPatch


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS secrets (name TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS contacts (
                id TEXT PRIMARY KEY, qq_id TEXT UNIQUE NOT NULL, data TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                id TEXT UNIQUE NOT NULL,
                contact_id TEXT NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
                direction TEXT NOT NULL, content TEXT NOT NULL, source TEXT NOT NULL,
                status TEXT NOT NULL, external_id TEXT, event_key TEXT UNIQUE,
                parent_id TEXT, created_at TEXT NOT NULL, error TEXT NOT NULL DEFAULT '',
                settings_revision INTEGER NOT NULL, contact_revision INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS messages_contact ON messages(contact_id, seq);
            CREATE INDEX IF NOT EXISTS messages_external ON messages(external_id);
            CREATE TABLE IF NOT EXISTS seen_events (event_key TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS self_events (
                event_key TEXT PRIMARY KEY,
                contact_id TEXT NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
                external_id TEXT NOT NULL, content TEXT NOT NULL
            );
        """)
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO settings VALUES (1, ?)", (json.dumps(DEFAULT_SETTINGS),)
            )
            # A crash may have happened after QQ accepted a request. Never replay it.
            self.db.execute(
                "UPDATE messages SET status='unknown', error='服务重启，发送结果待人工核对' WHERE status='sending'"
            )
            self.db.execute(
                "UPDATE messages SET status='cancelled', error='服务重启，未完成任务已取消' WHERE status IN ('generating','received','processing')"
            )
        if self.path != ":memory:":
            protect_path(self.path, 0o600)

    def close(self):
        with self._lock:
            self.db.close()

    def get_settings(self, include_secrets=False):
        with self._lock:
            saved = json.loads(
                self.db.execute("SELECT data FROM settings WHERE id=1").fetchone()[0]
            )
            result = DEFAULT_SETTINGS | saved
            for name in ("model_api_key", "bridge_token"):
                value = self.get_secret(name)
                result[name + "_set"] = bool(value)
                if include_secrets:
                    result[name] = value
            return result

    def get_secret(self, name):
        with self._lock:
            row = self.db.execute("SELECT value FROM secrets WHERE name=?", (name,)).fetchone()
            return row[0] if row else ""

    def update_settings(self, patch):
        patch = SettingsPatch.model_validate(patch).model_dump(
            exclude_none=True, exclude_unset=True
        )
        with self._lock, self.db:
            result = self.get_settings()
            if "qq_self_id" in patch and patch["qq_self_id"] != result["qq_self_id"]:
                if self.db.execute("SELECT 1 FROM messages WHERE source='qq' LIMIT 1").fetchone():
                    raise ValueError("请先清理所有 QQ 会话记录，再更换连接账号，避免混用历史")
            for name in ("model_api_key", "bridge_token"):
                if patch.pop("clear_" + name, False):
                    self.db.execute("DELETE FROM secrets WHERE name=?", (name,))
                value = patch.pop(name, None)
                if value:
                    self.db.execute("INSERT OR REPLACE INTO secrets VALUES (?,?)", (name, value))
            result = {k: v for k, v in result.items() if not k.endswith("_set")}
            result.update(patch)
            result["revision"] += 1
            self.db.execute("UPDATE settings SET data=? WHERE id=1", (json.dumps(result),))
            self.db.execute(
                "UPDATE messages SET status='cancelled', error='全局配置已修改，请重新生成' WHERE status IN ('draft','generating')"
            )
        return self.get_settings()

    def import_file_settings(self, patch):
        """Apply a complete file-owned configuration while preserving runtime identity."""
        values = SettingsPatch.model_validate(patch).model_dump(
            exclude_none=True, exclude_unset=True
        )
        with self._lock, self.db:
            settings = DEFAULT_SETTINGS | json.loads(
                self.db.execute("SELECT data FROM settings WHERE id=1").fetchone()[0]
            )
            bound_account = settings["qq_self_id"]
            for name in ("model_api_key", "bridge_token"):
                value = values.pop(name, "")
                values.pop("clear_" + name, None)
                if value:
                    self.db.execute("INSERT OR REPLACE INTO secrets VALUES (?,?)", (name, value))
                else:
                    self.db.execute("DELETE FROM secrets WHERE name=?", (name,))
            settings.update(values)
            settings.update(
                qq_self_id=bound_account,
                enabled=False,
                simple_mode=True,
                revision=settings["revision"] + 1,
            )
            self.db.execute("UPDATE settings SET data=? WHERE id=1", (json.dumps(settings),))
            for row in self.db.execute("SELECT id,data FROM contacts").fetchall():
                contact = json.loads(row[1])
                contact.update(enabled=False, revision=contact["revision"] + 1)
                self.db.execute(
                    "UPDATE contacts SET data=? WHERE id=?", (json.dumps(contact), row[0])
                )
            self.db.execute(
                "UPDATE messages SET status='cancelled',error='已导入文件配置，旧未发送任务已取消' WHERE status IN ('received','processing','generating','draft')"
            )
        return self.get_settings()

    def list_contacts(self):
        with self._lock:
            return [
                json.loads(r[0])
                for r in self.db.execute("SELECT data FROM contacts ORDER BY rowid")
            ]

    def contact_revisions(self):
        """Read the complete contact set without opening or committing a transaction."""
        with self._lock:
            return {
                row[0]: json.loads(row[1])["revision"]
                for row in self.db.execute("SELECT id,data FROM contacts")
            }

    def get_contact(self, contact_id):
        with self._lock:
            row = self.db.execute("SELECT data FROM contacts WHERE id=?", (contact_id,)).fetchone()
            if not row:
                raise KeyError("联系人不存在")
            return json.loads(row[0])

    def find_contact(self, qq_id):
        with self._lock:
            row = self.db.execute(
                "SELECT data FROM contacts WHERE qq_id=?", (str(qq_id),)
            ).fetchone()
            return json.loads(row[0]) if row else None

    def create_contact(self, values):
        result = ContactCreate.model_validate(values).model_dump()
        result.update(id=str(uuid4()), revision=1, created_at=now())
        try:
            with self._lock, self.db:
                self.db.execute(
                    "INSERT INTO contacts VALUES (?,?,?)",
                    (result["id"], result["qq_id"], json.dumps(result)),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("该 QQ 联系人已存在") from exc
        return result

    def update_contact(self, contact_id, patch):
        patch = ContactPatch.model_validate(patch).model_dump(exclude_none=True, exclude_unset=True)
        try:
            with self._lock, self.db:
                result = self.get_contact(contact_id)
                if "qq_id" in patch and patch["qq_id"] != result["qq_id"]:
                    raise ValueError("联系人 QQ 号不可更改，请新建联系人以保持会话隔离")
                result.update(patch)
                result["revision"] += 1
                self.db.execute(
                    "UPDATE contacts SET qq_id=?,data=? WHERE id=?",
                    (result["qq_id"], json.dumps(result), contact_id),
                )
                self.db.execute(
                    "UPDATE messages SET status='cancelled',error='联系人规则或接管状态已修改，请重新生成' WHERE contact_id=? AND status IN ('draft','generating')",
                    (contact_id,),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("该 QQ 联系人已存在") from exc
        return result

    def delete_contact(self, contact_id):
        with self._lock, self.db:
            self.get_contact(contact_id)
            self.db.execute("DELETE FROM contacts WHERE id=?", (contact_id,))

    def list_messages(self, contact_id):
        with self._lock:
            self.get_contact(contact_id)
            return [
                dict(r)
                for r in self.db.execute(
                    "SELECT * FROM messages WHERE contact_id=? ORDER BY seq", (contact_id,)
                )
            ]

    def clear_messages(self, contact_id):
        with self._lock, self.db:
            self.get_contact(contact_id)
            self.db.execute("DELETE FROM messages WHERE contact_id=?", (contact_id,))

    def get_message(self, message_id):
        with self._lock:
            row = self.db.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
            if not row:
                raise KeyError("消息不存在")
            return dict(row)

    def add_message(
        self,
        contact_id,
        direction,
        content,
        source,
        status,
        *,
        external_id=None,
        event_key=None,
        parent_id=None,
        settings_revision=None,
        contact_revision=None,
    ):
        with self._lock, self.db:
            settings = self.get_settings()
            contact = self.get_contact(contact_id)
            message_id = str(uuid4())
            try:
                if event_key:
                    existing = self.db.execute(
                        "SELECT 1 FROM seen_events WHERE event_key=?", (event_key,)
                    ).fetchone()
                    if existing:
                        return None
                    self.db.execute("INSERT INTO seen_events VALUES (?)", (event_key,))
                self.db.execute(
                    """INSERT INTO messages
                    (id,contact_id,direction,content,source,status,external_id,event_key,parent_id,created_at,settings_revision,contact_revision)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        message_id,
                        contact_id,
                        direction,
                        content,
                        source,
                        status,
                        external_id,
                        event_key,
                        parent_id,
                        now(),
                        settings_revision or settings["revision"],
                        contact_revision or contact["revision"],
                    ),
                )
            except sqlite3.IntegrityError:
                if (
                    event_key
                    and self.db.execute(
                        "SELECT 1 FROM messages WHERE event_key=?", (event_key,)
                    ).fetchone()
                ):
                    return None
                raise
            return self.get_message(message_id)

    def update_message(self, message_id, **patch):
        allowed = {"content", "status", "error", "external_id"}
        if not patch or set(patch) - allowed:
            raise ValueError("无效消息更新")
        with self._lock, self.db:
            self.get_message(message_id)
            fields = ",".join(k + "=?" for k in patch)
            self.db.execute(
                f"UPDATE messages SET {fields} WHERE id=?", (*patch.values(), message_id)
            )
            return self.get_message(message_id)

    def outgoing_external_exists(self, external_id):
        with self._lock:
            return bool(
                self.db.execute(
                    "SELECT 1 FROM messages WHERE direction='outgoing' AND source='qq' AND external_id=?",
                    (str(external_id),),
                ).fetchone()
            )

    def find_sending(self, contact_id):
        with self._lock:
            row = self.db.execute(
                "SELECT * FROM messages WHERE contact_id=? AND source='qq' AND direction='outgoing' AND status='sending' ORDER BY seq DESC LIMIT 1",
                (contact_id,),
            ).fetchone()
            return dict(row) if row else None

    def queue_self_event(self, contact_id, external_id, content):
        with self._lock, self.db:
            key = f"{self.get_settings()['qq_self_id']}:{external_id}"
            self.db.execute(
                "INSERT OR IGNORE INTO self_events VALUES (?,?,?,?)",
                (key, contact_id, str(external_id), content),
            )

    def list_self_events(self, contact_id=None):
        with self._lock:
            if contact_id is None:
                return [
                    dict(row) for row in self.db.execute("SELECT * FROM self_events ORDER BY rowid")
                ]
            return [
                dict(row)
                for row in self.db.execute(
                    "SELECT * FROM self_events WHERE contact_id=? ORDER BY rowid", (contact_id,)
                )
            ]

    def delete_self_event(self, event_key):
        with self._lock, self.db:
            self.db.execute("DELETE FROM self_events WHERE event_key=?", (event_key,))

    def cancel_replies(self, incoming_id):
        with self._lock, self.db:
            if self.db.execute(
                "SELECT 1 FROM messages WHERE parent_id=? AND status IN ('sending','unknown')",
                (incoming_id,),
            ).fetchone():
                raise ValueError("该来信已有发送中或结果未知的回复，请先人工核对")
            self.db.execute(
                "UPDATE messages SET status='cancelled',error='主人要求重新生成' WHERE parent_id=? AND status IN ('draft','generating')",
                (incoming_id,),
            )

    def recent_replies(self, limit=8):
        """A bounded status summary deliberately excludes content and credentials."""
        with self._lock:
            rows = self.db.execute(
                """SELECT contacts.data, messages.status, messages.error, messages.created_at
                FROM messages JOIN contacts ON contacts.id=messages.contact_id
                WHERE messages.direction='outgoing' AND messages.source='qq'
                ORDER BY messages.seq DESC LIMIT ?""",
                (min(max(int(limit), 0), 8),),
            ).fetchall()
            return [
                {
                    "contact_name": json.loads(row[0])["name"],
                    "status": row[1],
                    "error": row[2],
                    "created_at": row[3],
                }
                for row in rows
            ]

    def pause_automation(self, expected_revision=None):
        """Commit a stop barrier without waiting for a model or a conversation lock."""
        with self._lock, self.db:
            settings = DEFAULT_SETTINGS | json.loads(
                self.db.execute("SELECT data FROM settings WHERE id=1").fetchone()[0]
            )
            if expected_revision is not None and settings["revision"] != expected_revision:
                raise ValueError("配置已变化，请刷新后重新开始")
            settings.update(enabled=False, revision=settings["revision"] + 1)
            self.db.execute("UPDATE settings SET data=? WHERE id=1", (json.dumps(settings),))
            self.db.execute(
                "UPDATE messages SET status='cancelled',error='自动回复已停止或正在重新配置' WHERE status IN ('received','processing','generating','draft')"
            )
        return self.get_settings()

    def sync_account(self, account_id, expected_revision, *, expected_contacts=None):
        """Bind a discovered account; switching with QQ history is forbidden."""
        account_id = SettingsPatch(qq_self_id=account_id).qq_self_id
        blocked = False
        with self._lock, self.db:
            settings = DEFAULT_SETTINGS | json.loads(
                self.db.execute("SELECT data FROM settings WHERE id=1").fetchone()[0]
            )
            if settings["revision"] != expected_revision:
                raise ValueError("连接配置已变化，请重新同步好友")
            if expected_contacts is not None and self.contact_revisions() != expected_contacts:
                raise ValueError("联系人权限或接管状态已变化，请刷新后重新开始")
            if settings["qq_self_id"] != account_id:
                blocked = bool(
                    self.db.execute("SELECT 1 FROM messages WHERE source='qq' LIMIT 1").fetchone()
                )
                settings.update(enabled=False, revision=settings["revision"] + 1)
                if not blocked:
                    settings["qq_self_id"] = account_id
                    # A newly connected account must not inherit the previous selection.
                    for row in self.db.execute("SELECT id,data FROM contacts").fetchall():
                        contact = json.loads(row[1])
                        contact.update(enabled=False, revision=contact["revision"] + 1)
                        self.db.execute(
                            "UPDATE contacts SET data=? WHERE id=?", (json.dumps(contact), row[0])
                        )
                self.db.execute("UPDATE settings SET data=? WHERE id=1", (json.dumps(settings),))
                self.db.execute(
                    "UPDATE messages SET status='cancelled',error='连接 QQ 账号已变化' WHERE status IN ('received','processing','generating','draft')"
                )
        if blocked:
            # Raise after committing the stop barrier, rather than rolling it back.
            raise ValueError(
                "检测到登录 QQ 已换号，自动回复已停止；请为新账号使用独立数据目录，避免混用原账号历史"
            )
        return self.get_settings()

    def activate_automation(
        self, account_id, friends, prompt, expected_revision, *, expected_contacts
    ):
        """Replace the entire authorization set and prompt in one transaction."""
        selected = {friend["qq_id"]: friend["name"] for friend in friends}
        with self._lock, self.db:
            settings = DEFAULT_SETTINGS | json.loads(
                self.db.execute("SELECT data FROM settings WHERE id=1").fetchone()[0]
            )
            if settings["revision"] != expected_revision:
                raise ValueError("配置或停止状态已变化，请刷新后重新开始")
            if self.contact_revisions() != expected_contacts:
                raise ValueError("联系人权限或接管状态已变化，请刷新后重新开始")
            if (
                settings["qq_self_id"] != account_id
                and self.db.execute("SELECT 1 FROM messages WHERE source='qq' LIMIT 1").fetchone()
            ):
                raise ValueError("登录 QQ 已换号，请为新账号使用独立数据目录")
            existing = set()
            for row in self.db.execute("SELECT id,qq_id,data FROM contacts").fetchall():
                contact = json.loads(row[2])
                enabled = row[1] in selected
                contact.update(enabled=enabled, revision=contact["revision"] + 1)
                if enabled:
                    contact.update(name=selected[row[1]], mode="auto", paused=False)
                    existing.add(row[1])
                self.db.execute(
                    "UPDATE contacts SET data=? WHERE id=?", (json.dumps(contact), row[0])
                )
            for qq_id in selected.keys() - existing:
                contact = ContactCreate(
                    qq_id=qq_id, name=selected[qq_id], enabled=True, mode="auto"
                ).model_dump()
                contact.update(id=str(uuid4()), revision=1, created_at=now())
                self.db.execute(
                    "INSERT INTO contacts VALUES (?,?,?)",
                    (contact["id"], qq_id, json.dumps(contact)),
                )
            settings.update(
                qq_self_id=account_id,
                global_prompt=prompt,
                simple_mode=True,
                enabled=True,
                revision=settings["revision"] + 1,
            )
            self.db.execute("UPDATE settings SET data=? WHERE id=1", (json.dumps(settings),))
            self.db.execute(
                "UPDATE messages SET status='cancelled',error='好友范围或统一提示词已更新' WHERE status IN ('received','processing','generating','draft')"
            )
        return self.get_settings()
