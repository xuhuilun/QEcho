"""Owner-controlled private-chat workflow; only this module can request sends."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import TYPE_CHECKING

from .adapters import BridgeError, ProviderError

if TYPE_CHECKING:
    from .config import AgentConfig


class Engine:
    def __init__(self, store, model, bridge, *, prompt_config: AgentConfig | None = None):
        self.store = store
        self.model = model
        self.bridge = bridge
        self._prompt_config = prompt_config
        self._locks = defaultdict(asyncio.Lock)
        self._closing = False
        # An interrupted send cannot prove which self event belonged to it.
        for contact_id in {event["contact_id"] for event in self.store.list_self_events()}:
            self._resolve_self_events(contact_id, None)

    def ingest_event(self, payload):
        settings = self.store.get_settings()
        if str(payload.get("self_id", "")) != settings["qq_self_id"] or not settings["qq_self_id"]:
            return {"status": "ignored", "reason": "account"}
        if payload.get("message_type") != "private" or payload.get("sub_type") != "friend":
            return {"status": "ignored", "reason": "scope"}
        if payload.get("post_type") not in ("message", "message_sent"):
            return {"status": "ignored", "reason": "event"}
        external_id = payload.get("message_id")
        if external_id is None or isinstance(external_id, (dict, list, bool)):
            return {"status": "ignored", "reason": "missing_id"}
        is_self = (
            payload.get("post_type") == "message_sent"
            or str(payload.get("user_id")) == settings["qq_self_id"]
        )
        if is_self:
            if self.store.outgoing_external_exists(external_id):
                return {"status": "ignored", "reason": "own_reply"}
            target = payload.get("target_id")
            # Some bridges use user_id for the target in message_sent events.
            if not target and str(payload.get("user_id")) != settings["qq_self_id"]:
                target = payload.get("user_id")
            contact = self.store.find_contact(target) if target else None
            if contact:
                content = self._text(payload.get("message", payload.get("raw_message")))
                sending = self.store.find_sending(contact["id"])
                if sending:
                    # Text cannot distinguish a bridge echo from a human typing
                    # the same sentence. Wait for the send API's message ID.
                    self.store.queue_self_event(contact["id"], external_id, content)
                    return {"status": "pending_self_event"}
                return self._record_takeover(contact["id"], str(external_id), content)
            return {"status": "ignored", "reason": "self"}
        contact = self.store.find_contact(payload.get("user_id", ""))
        if not settings["enabled"] or not contact or not contact["enabled"] or contact["paused"]:
            return {"status": "ignored", "reason": "permission"}
        content = self._text(payload.get("message", payload.get("raw_message")))
        if not content or len(content) > 10000:
            return {"status": "ignored", "reason": "unsupported_content"}
        record = self.store.add_message(
            contact["id"],
            "incoming",
            content,
            "qq",
            "received",
            external_id=str(external_id),
            event_key=f"{settings['qq_self_id']}:{external_id}",
        )
        return (
            {"status": "accepted", "message_id": record["id"]}
            if record
            else {"status": "duplicate"}
        )

    @staticmethod
    def _text(message):
        if isinstance(message, str):
            # CQ segments are not interpreted as text or forwarded to the model.
            return message.strip() if "[CQ:" not in message else ""
        if isinstance(message, list) and all(
            isinstance(s, dict)
            and s.get("type") == "text"
            and isinstance(s.get("data"), dict)
            and isinstance(s["data"].get("text"), str)
            for s in message
        ):
            return "".join(s["data"]["text"] for s in message).strip()
        return ""

    def _record_takeover(self, contact_id, external_id, content):
        if self.store.outgoing_external_exists(external_id):
            return {"status": "ignored", "reason": "own_reply"}
        contact = self.store.get_contact(contact_id)
        if content and contact["enabled"]:
            self.store.add_message(
                contact_id,
                "outgoing",
                content,
                "qq",
                "sent",
                external_id=external_id,
                event_key=f"{self.store.get_settings()['qq_self_id']}:{external_id}",
            )
        self.store.update_contact(contact_id, {"paused": True})
        return {"status": "takeover"}

    def _resolve_self_events(self, contact_id, sent_external_id):
        for event in self.store.list_self_events(contact_id):
            if str(sent_external_id) != event["external_id"]:
                self._record_takeover(contact_id, event["external_id"], event["content"])
            self.store.delete_self_event(event["event_key"])

    def _prompt(self, contact_id, source="qq", through_seq=None):
        settings = self.store.get_settings()
        contact = self.store.get_contact(contact_id)
        details = [settings["global_prompt"]]
        if self._prompt_config is not None:
            group = self._prompt_config.group_for(contact["qq_id"])
            personal = self._prompt_config.friend_prompts.get(contact["qq_id"])
            if group is not None or personal:
                details.append(
                    "全局约束始终适用。称呼、语气和表达风格等偏好冲突时，"
                    "优先采用好友个人提示词，其次采用分组提示词，最后采用全局偏好。"
                )
                if group is not None:
                    details.append(f"【分组提示词】\n{group.prompt}")
                if personal:
                    details.append(f"【好友个人提示词】\n{personal}")
        attributes = [
            ("主人称呼", settings["owner_name"]),
            ("当前联系人", contact["name"]),
        ]
        if not settings["simple_mode"]:
            attributes.extend(
                [
                    ("人格设定", contact["persona"]),
                    ("语气偏好", contact["tone"]),
                    ("双方关系", contact["relationship"]),
                    ("已确认事实", contact["facts"]),
                    ("主人补充指令", contact["instructions"]),
                ]
            )
        for label, value in attributes:
            if value:
                details.append(f"【{label}】\n{value}")
        details.append(f"本轮最多输出 {settings['max_reply_chars']} 个字符。")
        history = []
        records = self.store.list_messages(contact_id)
        parents = {item["id"]: item["seq"] for item in records}
        # Reply generation can finish after the next incoming event is stored.
        # Order replies beside their triggering message, without including future events.
        records.sort(
            key=lambda item: (
                parents.get(item["parent_id"], item["seq"]),
                item["direction"] == "outgoing",
                item["seq"],
            )
        )
        for item in records:
            effective_seq = parents.get(item["parent_id"], item["seq"])
            if item["source"] != source or (
                through_seq is not None and effective_seq > through_seq
            ):
                continue
            if item["direction"] == "incoming":
                if item["status"] not in ("cancelled", "ignored"):
                    history.append({"role": "user", "content": item["content"]})
            elif item["status"] == "sent" or (source == "simulator" and item["status"] == "draft"):
                history.append({"role": "assistant", "content": item["content"]})
        return [
            {"role": "system", "content": "\n\n".join(details)},
            *history[-settings["context_messages"] :],
        ]

    def preview(self, contact_id):
        return self._prompt(contact_id)

    def _allowed(self, record, *, simulation=False):
        if self._closing:
            return False
        try:
            settings = self.store.get_settings()
            contact = self.store.get_contact(record["contact_id"])
        except KeyError:
            return False
        revisions_match = (
            settings["revision"] == record["settings_revision"]
            and contact["revision"] == record["contact_revision"]
        )
        if simulation:
            return revisions_match
        return (
            revisions_match and settings["enabled"] and contact["enabled"] and not contact["paused"]
        )

    async def process_message(self, message_id):
        try:
            incoming = self.store.get_message(message_id)
        except KeyError:
            return {"status": "cancelled"}
        async with self._locks[incoming["contact_id"]]:
            # Concurrent HTTP requests may start their background tasks in a
            # different order from durable acceptance. Consume earlier events first.
            for earlier in self.store.list_messages(incoming["contact_id"]):
                if earlier["seq"] >= incoming["seq"]:
                    break
                if (
                    earlier["source"] == incoming["source"]
                    and earlier["direction"] == "incoming"
                    and earlier["status"] == "received"
                ):
                    await self._process_locked(earlier["id"])
            return await self._process_locked(message_id)

    async def _process_locked(self, message_id, *, regenerate=False):
        try:
            incoming = self.store.get_message(message_id)
            if incoming["status"] != "received" and not regenerate:
                return incoming
            if regenerate:
                incoming = incoming | {
                    "settings_revision": self.store.get_settings()["revision"],
                    "contact_revision": self.store.get_contact(incoming["contact_id"])["revision"],
                }
            simulation = incoming["source"] == "simulator"
            if not self._allowed(incoming, simulation=simulation):
                return self.store.update_message(
                    message_id, status="cancelled", error="配置或接管状态已变化"
                )
            self.store.update_message(message_id, status="processing")
            settings = self.store.get_settings()
            outgoing = self.store.add_message(
                incoming["contact_id"],
                "outgoing",
                "",
                incoming["source"],
                "generating",
                parent_id=message_id,
                settings_revision=incoming["settings_revision"],
                contact_revision=incoming["contact_revision"],
            )
            try:
                text = await self.model.generate(
                    settings,
                    self._prompt(incoming["contact_id"], incoming["source"], incoming["seq"]),
                    self.store.get_secret("model_api_key"),
                )
            except ProviderError as exc:
                current = self.store.get_message(outgoing["id"])
                if current["status"] != "generating" or not self._allowed(
                    outgoing, simulation=simulation
                ):
                    return self.store.update_message(
                        outgoing["id"], status="cancelled", error="生成期间配置或接管状态已变化"
                    )
                self.store.update_message(message_id, status="processed")
                return self.store.update_message(outgoing["id"], status="error", error=str(exc))
            current = self.store.get_message(outgoing["id"])
            if current["status"] != "generating" or not self._allowed(
                outgoing, simulation=simulation
            ):
                return self.store.update_message(
                    outgoing["id"], status="cancelled", error="生成期间配置或接管状态已变化"
                )
            text = text.strip()
            if not text or len(text) > settings["max_reply_chars"]:
                self.store.update_message(message_id, status="processed")
                return self.store.update_message(
                    outgoing["id"],
                    status="error",
                    error="模型回复为空或超过长度限制，请调整提示词后重新演练",
                )
            result = self.store.update_message(outgoing["id"], content=text, status="draft")
            self.store.update_message(message_id, status="processed")
            contact = self.store.get_contact(incoming["contact_id"])
            if (
                not simulation
                and not regenerate
                and contact["mode"] == "auto"
                and settings["model_mode"] == "live"
            ):
                await asyncio.sleep(settings["reply_delay_seconds"])
                current = self.store.get_message(outgoing["id"])
                if current["status"] != "draft" or not self._allowed(current):
                    return self.store.update_message(
                        outgoing["id"], status="cancelled", error="发送前配置或接管状态已变化"
                    )
                return await self._send_locked(outgoing["id"])
            return result
        except KeyError:
            # The owner may delete a conversation while a model request is in flight.
            return {"status": "cancelled"}

    async def simulate(self, contact_id, text):
        text = text.strip()
        if not text or len(text) > 10000:
            raise ValueError("演练消息必须为 1—10000 个字符")
        async with self._locks[contact_id]:
            incoming = self.store.add_message(contact_id, "incoming", text, "simulator", "received")
            return await self._process_locked(incoming["id"])

    async def send_draft(self, message_id, content=None):
        record = self.store.get_message(message_id)
        async with self._locks[record["contact_id"]]:
            return await self._send_locked(message_id, content)

    async def regenerate(self, message_id):
        record = self.store.get_message(message_id)
        if record["source"] != "qq":
            raise ValueError("演练记录不能生成真实 QQ 回复")
        incoming = (
            record
            if record["direction"] == "incoming"
            else self.store.get_message(record["parent_id"])
            if record["parent_id"]
            else None
        )
        if incoming is None:
            raise ValueError("该消息没有可重新回复的来信")
        latest = next(
            (
                item
                for item in reversed(self.store.list_messages(record["contact_id"]))
                if item["source"] == "qq" and item["direction"] == "incoming"
            ),
            None,
        )
        if latest is None or latest["id"] != incoming["id"]:
            raise ValueError("仅可为最后一条 QQ 来信重新生成回复")
        fresh = incoming | {
            "settings_revision": self.store.get_settings()["revision"],
            "contact_revision": self.store.get_contact(incoming["contact_id"])["revision"],
        }
        if not self._allowed(fresh):
            raise ValueError("全局已暂停、联系人未授权或已接管，不能重新生成")
        # Cancel before waiting on the lock so an in-flight generation cannot send.
        self.store.cancel_replies(incoming["id"])
        async with self._locks[incoming["contact_id"]]:
            latest = next(
                (
                    item
                    for item in reversed(self.store.list_messages(record["contact_id"]))
                    if item["source"] == "qq" and item["direction"] == "incoming"
                ),
                None,
            )
            if latest is None or latest["id"] != incoming["id"]:
                raise ValueError("已有更新的 QQ 来信，请对最新来信重新生成")
            self.store.cancel_replies(incoming["id"])
            return await self._process_locked(incoming["id"], regenerate=True)

    async def _send_locked(self, message_id, content=None):
        record = self.store.get_message(message_id)
        if record["source"] != "qq" or record["direction"] != "outgoing":
            raise ValueError("演练记录和接收消息不能发送到 QQ")
        if record["status"] == "sent":
            return record
        if record["status"] != "draft":
            raise ValueError("仅待审核草稿可以发送；发送结果未知时请先人工核对")
        if not self._allowed(record):
            self.store.update_message(message_id, status="cancelled", error="发送权限或规则已变化")
            raise ValueError("全局已暂停、联系人未授权或已接管，或草稿规则已过期")
        settings = self.store.get_settings()
        if settings["model_mode"] != "live":
            raise ValueError("演练模型生成的内容不能发送到 QQ，请切换真实模型并重新生成")
        text = content.strip() if content is not None else record["content"]
        if not text or len(text) > settings["max_reply_chars"]:
            raise ValueError("回复为空或超过配置的长度限制")
        if not settings["qq_self_id"]:
            return self.store.update_message(
                message_id, status="error", error="请先配置本人 QQ 号，再发送回复"
            )
        try:
            actual_account = await self.bridge.login_info(
                settings["bridge_base_url"], self.store.get_secret("bridge_token")
            )
        except BridgeError as exc:
            current = self.store.get_message(message_id)
            if current["status"] != "draft":
                return current
            if not self._allowed(current):
                return self.store.update_message(
                    message_id, status="cancelled", error="账号核验期间权限或规则已变化"
                )
            # Identity queries do not send messages, so even a timeout is a
            # definite failure to send, never an uncertain delivery result.
            return self.store.update_message(message_id, status="error", error=str(exc))
        current = self.store.get_message(message_id)
        if current["status"] != "draft":
            return current
        if not self._allowed(current):
            return self.store.update_message(
                message_id, status="cancelled", error="账号核验期间权限或规则已变化"
            )
        if str(actual_account) != settings["qq_self_id"]:
            return self.store.update_message(
                message_id, status="error", error="桥接实际登录 QQ 与配置账号不一致，已阻止发送"
            )
        contact = self.store.get_contact(record["contact_id"])
        self.store.update_message(message_id, content=text, status="sending")
        try:
            external_id = await self.bridge.send_private(
                settings["bridge_base_url"],
                self.store.get_secret("bridge_token"),
                contact["qq_id"],
                text,
            )
        except BridgeError as exc:
            result = self.store.update_message(
                message_id, status="unknown" if exc.uncertain else "error", error=str(exc)
            )
            self._resolve_self_events(record["contact_id"], None)
            return result
        result = self.store.update_message(
            message_id, status="sent", external_id=str(external_id), error=""
        )
        self._resolve_self_events(record["contact_id"], str(external_id))
        return result

    def cancel_draft(self, message_id):
        record = self.store.get_message(message_id)
        if record["status"] not in ("draft", "generating"):
            raise ValueError("只可取消尚未发送的草稿")
        return self.store.update_message(message_id, status="cancelled", error="主人已取消")

    async def shutdown(self):
        self._closing = True
