"""The small public workflow: sync friends, choose a scope, start or stop."""

from urllib.parse import urlsplit

from .schemas import AutomationStartInput, validate_qq


class SimpleService:
    def __init__(self, store, bridge):
        self.store = store
        self.bridge = bridge

    @staticmethod
    def _model_ready(settings):
        if settings["model_mode"] != "live" or not settings["model_name"].strip():
            return False
        try:
            value = settings["model_base_url"]
            parsed = urlsplit(value)
            parsed.port
            return bool(
                parsed.scheme in {"http", "https"}
                and parsed.hostname
                and parsed.username is None
                and parsed.password is None
                and not parsed.query
                and not parsed.fragment
                and not any(c.isspace() for c in value)
            )
        except ValueError:
            return False

    def status(self):
        settings = self.store.get_settings()
        contacts = self.store.list_contacts()
        return {
            "running": settings["enabled"],
            "account_id": settings["qq_self_id"],
            "selected_ids": [contact["qq_id"] for contact in contacts if contact["enabled"]],
            "paused_ids": [
                contact["qq_id"] for contact in contacts if contact["enabled"] and contact["paused"]
            ],
            "active_count": sum(
                1 for contact in contacts if contact["enabled"] and not contact["paused"]
            ),
            "prompt": settings["global_prompt"],
            "revision": settings["revision"],
            "model_ready": self._model_ready(settings),
            "login_url": settings["bridge_login_url"],
            "recent": self.store.recent_replies(),
        }

    async def sync(self):
        settings = self.store.get_settings()
        token = self.store.get_secret("bridge_token")
        account = validate_qq(str(await self.bridge.login_info(settings["bridge_base_url"], token)))
        current = self.store.sync_account(account, settings["revision"])
        friends = await self.bridge.friends(settings["bridge_base_url"], token)
        confirmed = str(await self.bridge.login_info(settings["bridge_base_url"], token))
        if self.store.get_settings()["revision"] != current["revision"]:
            raise ValueError("连接配置已变化，请重新同步好友")
        if confirmed != account:
            self.store.sync_account(confirmed, current["revision"])
            raise ValueError("同步期间登录 QQ 已变化，请重新同步好友")
        return self.status() | {"friends": friends}

    async def start(self, qq_ids, prompt, expected_revision):
        request = AutomationStartInput(
            qq_ids=qq_ids, prompt=prompt, expected_revision=expected_revision
        )
        settings = self.store.pause_automation(request.expected_revision)
        expected_contacts = self.store.contact_revisions()
        if not self._model_ready(settings):
            raise ValueError("请先配置真实模型的接口地址和模型名称，再开始自动回复")
        token = self.store.get_secret("bridge_token")
        account = validate_qq(str(await self.bridge.login_info(settings["bridge_base_url"], token)))
        # Binding also enforces the single-account history boundary.
        current = self.store.sync_account(
            account, settings["revision"], expected_contacts=expected_contacts
        )
        expected_contacts = self.store.contact_revisions()
        friends = await self.bridge.friends(settings["bridge_base_url"], token)
        by_id = {str(friend["qq_id"]): friend for friend in friends}
        if not set(request.qq_ids) <= by_id.keys():
            raise ValueError("所选好友不在当前 QQ 的好友列表中，请重新同步并选择")
        confirmed = str(await self.bridge.login_info(settings["bridge_base_url"], token))
        if confirmed != account:
            self.store.sync_account(confirmed, current["revision"])
            raise ValueError("开始期间登录 QQ 已变化，请重新同步好友后开始")
        selected = [
            {"qq_id": qq_id, "name": str(by_id[qq_id].get("name") or qq_id).strip()[:100] or qq_id}
            for qq_id in request.qq_ids
        ]
        self.store.activate_automation(
            account,
            selected,
            request.prompt,
            current["revision"],
            expected_contacts=expected_contacts,
        )
        return self.status()

    def stop(self):
        self.store.pause_automation()
        return self.status()
