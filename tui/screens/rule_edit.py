import subprocess
from typing import Callable

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Checkbox, Input, Label

from tui.config_io import ensure_rule_uuid, load_config, save_config
from tui.widgets.chat_picker import ChatPickerModal
from paths import CONFIG_PATH


def _chat_label(title: str, chat_id: int | None) -> str:
    if not chat_id:
        return "(none selected)"
    return f"{title} ({chat_id})" if title else str(chat_id)


class RuleEditScreen(Screen):
    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+s", "save", "Save"),
    ]

    def __init__(self, rule: dict | None, on_save: Callable):
        super().__init__()
        self._rule = dict(rule) if rule else {}
        self._on_save = on_save
        self._is_new = rule is None
        self._source_chat_id: int | None = self._rule.get("chat_id")
        self._source_title: str = self._rule.get("source_title", "")
        self._dest_chat_id: int | None = (self._rule.get("destination") or {}).get("chat_id")
        self._dest_title: str = (self._rule.get("destination") or {}).get("title", "")
        self._dest_topic_id: int | None = (self._rule.get("destination") or {}).get("topic_id")
        self._source_topics: list[dict] = []
        self._dest_topics: list[dict] = []

    def compose(self) -> ComposeResult:
        yield Label("Add Rule" if self._is_new else "Edit Rule", id="form-title")

        with VerticalScroll(id="form-scroll"):
            yield Label("Name")
            yield Input(value=self._rule.get("name", ""), placeholder="Rule name", id="name-input")

            yield Label("Source chat")
            yield Button(_chat_label(self._source_title, self._source_chat_id), id="source-btn")

            yield Label("Source topics")
            topics = self._rule.get("topics", "all")
            yield Checkbox("All topics", value=(topics == "all"), id="topics-all-check")
            yield Vertical(id="source-topics-list")

            yield Label("Destination chat")
            yield Button(_chat_label(self._dest_title, self._dest_chat_id), id="dest-btn")

            yield Label("Destination topic")
            yield Checkbox("None (general chat)", value=(self._dest_topic_id is None), id="dest-topic-none")
            yield Vertical(id="dest-topics-list")

            yield Label("Keyword filters (comma-separated, empty = all)")
            kw = self._rule.get("filters", {}).get("keywords") or []
            yield Input(value=", ".join(kw), placeholder="e.g. BUY, SELL, ALERT", id="keywords-input")

            yield Label("Media type filters")
            mt = set(self._rule.get("filters", {}).get("media_types") or [])
            with Horizontal():
                for mtype in ("text", "photo", "video", "audio", "document", "gif"):
                    yield Checkbox(mtype, value=(mtype in mt), id=f"mt-{mtype}")

            with Horizontal(id="form-actions"):
                yield Button("Save (Ctrl+S)", variant="primary", id="save-btn")
                yield Button("Cancel (Esc)", id="cancel-btn")

    def on_mount(self) -> None:
        if self._source_chat_id:
            self._fetch_source_topics(self._source_chat_id)
        if self._dest_chat_id:
            self._fetch_dest_topics(self._dest_chat_id)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        match event.button.id:
            case "source-btn":
                self.app.push_screen(ChatPickerModal("Select source chat"), self._on_source_selected)
            case "dest-btn":
                self.app.push_screen(ChatPickerModal("Select destination chat"), self._on_dest_selected)
            case "save-btn":
                self.action_save()
            case "cancel-btn":
                self.action_cancel()

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        cb = event.checkbox
        # Dest topic: selecting one deselects all others
        if cb.has_class("dest-topic-cb") and event.value:
            self.query_one("#dest-topic-none", Checkbox).value = False
            for other in self.query(".dest-topic-cb"):
                if other.id != cb.id:
                    other.value = False
        elif cb.id == "dest-topic-none" and event.value:
            for other in self.query(".dest-topic-cb"):
                other.value = False

    def _on_source_selected(self, result: ChatPickerModal.Selected | None) -> None:
        if result is None:
            return
        self._source_chat_id = result.chat_id
        self._source_title = result.title
        self.query_one("#source-btn", Button).label = f"{result.title} ({result.chat_id})"
        self.query_one("#topics-all-check", Checkbox).value = True
        self._fetch_source_topics(result.chat_id)

    def _on_dest_selected(self, result: ChatPickerModal.Selected | None) -> None:
        if result is None:
            return
        self._dest_chat_id = result.chat_id
        self._dest_title = result.title
        self._dest_topic_id = None
        self.query_one("#dest-btn", Button).label = f"{result.title} ({result.chat_id})"
        self.query_one("#dest-topic-none", Checkbox).value = True
        self._fetch_dest_topics(result.chat_id)

    @work(exclusive=True)
    async def _fetch_source_topics(self, chat_id: int) -> None:
        from tui.rpc_client import call as rpc_call
        container = self.query_one("#source-topics-list", Vertical)
        await container.remove_children()
        try:
            resp = await rpc_call("list_topics", chat_id=chat_id)
        except Exception as e:
            await container.mount(Label(f"[red]Error: {e}[/red]"))
            return
        if not resp.get("ok"):
            await container.mount(Label(f"[red]{resp.get('error')}[/red]"))
            return
        self._source_topics = resp["result"]
        if not self._source_topics:
            await container.mount(Label("[dim]Not a forum — topics not available[/dim]"))
            return
        existing = self._rule.get("topics", "all")
        selected = set(existing) if isinstance(existing, list) else set()
        await container.mount(*[
            Checkbox(t["title"], value=(t["id"] in selected),
                     id=f"src-topic-{t['id']}", classes="src-topic-cb")
            for t in self._source_topics
        ])

    @work(exclusive=True)
    async def _fetch_dest_topics(self, chat_id: int) -> None:
        from tui.rpc_client import call as rpc_call
        container = self.query_one("#dest-topics-list", Vertical)
        await container.remove_children()
        try:
            resp = await rpc_call("list_topics", chat_id=chat_id)
        except Exception as e:
            await container.mount(Label(f"[red]Error: {e}[/red]"))
            return
        if not resp.get("ok"):
            await container.mount(Label(f"[red]{resp.get('error')}[/red]"))
            return
        self._dest_topics = resp["result"]
        if not self._dest_topics:
            await container.mount(Label("[dim]Not a forum — topics not available[/dim]"))
            return
        await container.mount(*[
            Checkbox(t["title"], value=(t["id"] == self._dest_topic_id),
                     id=f"dest-topic-{t['id']}", classes="dest-topic-cb")
            for t in self._dest_topics
        ])
        if self._dest_topic_id is not None:
            self.query_one("#dest-topic-none", Checkbox).value = False

    def action_cancel(self) -> None:
        self.app.pop_screen()

    def action_save(self) -> None:
        name = self.query_one("#name-input", Input).value.strip()
        if not name:
            self.notify("Rule name is required", severity="error")
            return
        if not self._source_chat_id:
            self.notify("Source chat is required", severity="error")
            return
        if not self._dest_chat_id:
            self.notify("Destination chat is required", severity="error")
            return

        if self.query_one("#topics-all-check", Checkbox).value:
            topics: list | str = "all"
        else:
            topics = [
                t["id"] for t in self._source_topics
                if self.query(f"#src-topic-{t['id']}") and
                   self.query_one(f"#src-topic-{t['id']}", Checkbox).value
            ] or "all"

        dest_topic: int | None = None
        if not self.query_one("#dest-topic-none", Checkbox).value:
            for t in self._dest_topics:
                if self.query(f"#dest-topic-{t['id']}") and \
                   self.query_one(f"#dest-topic-{t['id']}", Checkbox).value:
                    dest_topic = t["id"]
                    break

        kw_raw = self.query_one("#keywords-input", Input).value
        keywords = [k.strip() for k in kw_raw.split(",") if k.strip()]
        media_types = [
            m for m in ("text", "photo", "video", "audio", "document", "gif")
            if self.query_one(f"#mt-{m}", Checkbox).value
        ]

        rule = {
            "name": name,
            "chat_id": self._source_chat_id,
            "source_title": self._source_title,
            "topics": topics,
            "destination": {
                "chat_id": self._dest_chat_id,
                "title": self._dest_title,
                "topic_id": dest_topic,
            },
            "filters": {"keywords": keywords, "media_types": media_types},
        }
        if "uuid" in self._rule:
            rule["uuid"] = self._rule["uuid"]
        ensure_rule_uuid(rule)

        try:
            cfg = load_config(CONFIG_PATH)
            sources = list(cfg.get("sources") or [])
            for i, r in enumerate(sources):
                if r.get("uuid") == rule.get("uuid"):
                    sources[i] = rule
                    break
            else:
                sources.append(rule)
            cfg["sources"] = sources
            save_config(cfg, CONFIG_PATH)
        except Exception as e:
            self.notify(f"Save failed: {e}", severity="error")
            return

        self._on_save()
        self.app.pop_screen()

        try:
            restart_cmd = load_config(CONFIG_PATH).get("restart_cmd", "")
            if restart_cmd:
                self.app.push_screen(RestartPromptScreen(restart_cmd))
        except Exception:
            pass


class RestartPromptScreen(Screen):
    def __init__(self, restart_cmd: str):
        super().__init__()
        self._cmd = restart_cmd

    def compose(self) -> ComposeResult:
        yield Label(f"Restart daemon now?\n  {self._cmd}")
        with Horizontal():
            yield Button("Yes", variant="primary", id="yes-btn")
            yield Button("No", id="no-btn")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "yes-btn":
            try:
                subprocess.run(self._cmd.split(), check=True)
                self.notify("Daemon restarted")
            except Exception as e:
                self.notify(f"Restart failed: {e}", severity="error")
        self.app.pop_screen()
