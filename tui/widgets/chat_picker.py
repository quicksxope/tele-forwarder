from dataclasses import dataclass
from textual.app import ComposeResult
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Input, ListView, ListItem, Label
from textual.binding import Binding
from textual import work

# Module-level cache so dialogs are fetched once per TUI session
_dialogs_cache: list[dict] | None = None


class ChatPickerModal(ModalScreen):
    """Modal to pick a Telegram chat from the user's dialog list."""

    BINDINGS = [
        Binding("escape", "dismiss", "Cancel"),
        Binding("r", "refresh", "Refresh"),
    ]

    @dataclass
    class Selected(Message):
        chat_id: int
        title: str
        is_forum: bool
        can_post: bool

    def __init__(self, label: str = "Select chat"):
        super().__init__()
        self._label = label
        self._all_dialogs: list[dict] = []
        self._filtered: list[dict] = []

    def compose(self) -> ComposeResult:
        yield Label(self._label, id="picker-label")
        yield Input(placeholder="Filter chats...", id="filter-input")
        yield ListView(id="chat-list")

    def on_mount(self) -> None:
        self.load_dialogs()

    @work(exclusive=True)
    async def load_dialogs(self, force: bool = False) -> None:
        global _dialogs_cache
        from tui.rpc_client import call as rpc_call

        list_view = self.query_one("#chat-list", ListView)
        list_view.clear()

        if _dialogs_cache is None or force:
            try:
                resp = await rpc_call('list_dialogs', archived=False, limit=500)
                if resp.get('ok'):
                    _dialogs_cache = resp['result']
                else:
                    list_view.append(ListItem(Label(f"[red]Error: {resp.get('error')}[/red]")))
                    return
            except ConnectionRefusedError:
                list_view.append(ListItem(Label("[red]Daemon not running[/red]")))
                return

        self._all_dialogs = _dialogs_cache
        self._apply_filter("")
        # Focus the filter input
        self.query_one("#filter-input", Input).focus()

    def _apply_filter(self, query: str) -> None:
        q = query.lower()
        self._filtered = [d for d in self._all_dialogs if q in d['title'].lower()]
        list_view = self.query_one("#chat-list", ListView)
        list_view.clear()
        for d in self._filtered:
            warn = " ⚠" if not d.get('can_post', True) else ""
            forum = " [forum]" if d.get('is_forum') else ""
            label_text = f"{d['title']}{forum}{warn}"
            list_view.append(ListItem(Label(label_text)))

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "filter-input":
            self._apply_filter(event.value)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        index = event.list_view.index
        if index is not None and 0 <= index < len(self._filtered):
            d = self._filtered[index]
            self.post_message(self.Selected(
                chat_id=d['id'],
                title=d['title'],
                is_forum=d.get('is_forum', False),
                can_post=d.get('can_post', True),
            ))
            self.dismiss()

    def action_refresh(self) -> None:
        global _dialogs_cache
        _dialogs_cache = None
        self.load_dialogs(force=True)
