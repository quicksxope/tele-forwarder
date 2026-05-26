import subprocess
from typing import Callable

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Checkbox, Input, Label, Static

from tui.config_io import ensure_rule_uuid, load_config, save_config
from tui.widgets.chat_picker import ChatPickerModal
from tui.widgets.topic_picker import TopicPickerModal
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
        # Work on a copy so cancelling doesn't mutate anything
        self._rule = dict(rule) if rule else {}
        self._on_save = on_save
        self._is_new = rule is None
        # State for picker results
        self._source_chat_id: int | None = self._rule.get("chat_id")
        self._source_title: str = self._rule.get("source_title", "")
        self._source_is_forum: bool = False
        self._dest_chat_id: int | None = (self._rule.get("destination") or {}).get("chat_id")
        self._dest_title: str = (self._rule.get("destination") or {}).get("title", "")
        self._dest_topic_id: int | None = (self._rule.get("destination") or {}).get("topic_id")
        # Tracks which picker was last opened: False = source, True = dest
        self._awaiting_dest: bool = False

    def compose(self) -> ComposeResult:
        title = "Add Rule" if self._is_new else "Edit Rule"
        yield Label(title, id="form-title")

        with Vertical():
            yield Label("Name")
            yield Input(
                value=self._rule.get("name", ""),
                placeholder="Rule name",
                id="name-input",
            )

            yield Label("Source chat")
            src_label = _chat_label(self._source_title, self._source_chat_id)
            yield Button(src_label, id="source-btn")

            yield Label("Source topics")
            topics = self._rule.get("topics", "all")
            topics_val = "" if topics == "all" else ",".join(str(t) for t in topics)
            yield Checkbox("All topics", value=(topics == "all"), id="topics-all-check")
            yield Input(
                value=topics_val,
                placeholder="Topic IDs, comma-separated (or pick below)",
                id="topics-input",
            )
            yield Button("Pick topic", id="pick-topic-btn")

            yield Label("Destination chat")
            dest_label = _chat_label(self._dest_title, self._dest_chat_id)
            yield Button(dest_label, id="dest-btn")

            yield Label("Destination topic ID (optional)")
            yield Input(
                value=str(self._dest_topic_id) if self._dest_topic_id else "",
                placeholder="Leave blank for general chat",
                id="dest-topic-input",
            )

            yield Label("Keyword filters (comma-separated, empty = all)")
            kw = self._rule.get("filters", {}).get("keywords") or []
            yield Input(
                value=", ".join(kw),
                placeholder="e.g. BUY, SELL, ALERT",
                id="keywords-input",
            )

            yield Label("Media type filters")
            mt = set(self._rule.get("filters", {}).get("media_types") or [])
            with Horizontal():
                for mtype in ("text", "photo", "video", "audio", "document", "gif"):
                    yield Checkbox(mtype, value=(mtype in mt), id=f"mt-{mtype}")

        with Horizontal():
            yield Button("Save (Ctrl+S)", variant="primary", id="save-btn")
            yield Button("Cancel (Esc)", id="cancel-btn")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "source-btn":
            self._awaiting_dest = False
            self.app.push_screen(ChatPickerModal(label="Select source chat"))
        elif btn_id == "dest-btn":
            self._awaiting_dest = True
            self.app.push_screen(ChatPickerModal(label="Select destination chat"))
        elif btn_id == "pick-topic-btn":
            if self._source_chat_id:
                self.app.push_screen(TopicPickerModal(self._source_chat_id))
            else:
                self.notify("Select a source chat first", severity="warning")
        elif btn_id == "save-btn":
            self.action_save()
        elif btn_id == "cancel-btn":
            self.action_cancel()

    def on_chat_picker_modal_selected(self, message: ChatPickerModal.Selected) -> None:
        if self._awaiting_dest:
            self._dest_chat_id = message.chat_id
            self._dest_title = message.title
            self.query_one("#dest-btn", Button).label = (
                f"{message.title} ({message.chat_id})"
            )
        else:
            self._source_chat_id = message.chat_id
            self._source_title = message.title
            self._source_is_forum = message.is_forum
            self.query_one("#source-btn", Button).label = (
                f"{message.title} ({message.chat_id})"
            )
        self._awaiting_dest = False

    def on_topic_picker_modal_selected(self, message: TopicPickerModal.Selected) -> None:
        input_widget = self.query_one("#topics-input", Input)
        existing = [t.strip() for t in input_widget.value.split(",") if t.strip()]
        if str(message.topic_id) not in existing:
            existing.append(str(message.topic_id))
        input_widget.value = ", ".join(existing)
        # Uncheck "all topics" since a specific topic was picked
        self.query_one("#topics-all-check", Checkbox).value = False

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

        topics_all = self.query_one("#topics-all-check", Checkbox).value
        if topics_all:
            topics = "all"
        else:
            raw = self.query_one("#topics-input", Input).value
            try:
                topics = [int(t.strip()) for t in raw.split(",") if t.strip()]
            except ValueError:
                self.notify("Topic IDs must be integers", severity="error")
                return
            if not topics:
                topics = "all"

        dest_topic_raw = self.query_one("#dest-topic-input", Input).value.strip()
        try:
            dest_topic = int(dest_topic_raw) if dest_topic_raw else None
        except ValueError:
            self.notify("Destination topic ID must be an integer", severity="error")
            return

        kw_raw = self.query_one("#keywords-input", Input).value
        keywords = [k.strip() for k in kw_raw.split(",") if k.strip()]

        media_types = [
            mtype
            for mtype in ("text", "photo", "video", "audio", "document", "gif")
            if self.query_one(f"#mt-{mtype}", Checkbox).value
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
            rule_uuid = rule["uuid"]
            for i, r in enumerate(sources):
                if r.get("uuid") == rule_uuid:
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

        # Offer to restart daemon if a restart command is configured
        try:
            restart_cfg = load_config(CONFIG_PATH)
            restart_cmd = restart_cfg.get("restart_cmd", "")
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
