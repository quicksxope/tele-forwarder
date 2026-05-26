from dataclasses import dataclass
from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.widgets import Input, ListView, ListItem, Label
from textual.binding import Binding
from textual import work


@dataclass
class TopicSelected:
    topic_id: int
    title: str


class TopicPickerModal(ModalScreen):
    BINDINGS = [
        Binding("escape", "dismiss", "Cancel"),
    ]

    Selected = TopicSelected

    def __init__(self, chat_id: int):
        super().__init__()
        self._chat_id = chat_id
        self._all_topics: list[dict] = []
        self._filtered: list[dict] = []

    def compose(self) -> ComposeResult:
        yield Label("Select topic", id="picker-label")
        yield Input(placeholder="Filter topics...", id="filter-input")
        yield ListView(id="topic-list")

    def on_mount(self) -> None:
        self.load_topics()

    @work(exclusive=True)
    async def load_topics(self) -> None:
        from tui.rpc_client import call as rpc_call

        list_view = self.query_one("#topic-list", ListView)
        list_view.clear()

        try:
            resp = await rpc_call('list_topics', chat_id=self._chat_id)
            if resp.get('ok'):
                self._all_topics = resp['result']
            else:
                list_view.append(ListItem(Label(f"[red]Error: {resp.get('error')}[/red]")))
                return
        except ConnectionRefusedError:
            list_view.append(ListItem(Label("[red]Daemon not running[/red]")))
            return

        self._apply_filter("")
        self.query_one("#filter-input", Input).focus()

    def _apply_filter(self, query: str) -> None:
        q = query.lower()
        self._filtered = [t for t in self._all_topics if q in t['title'].lower()]
        list_view = self.query_one("#topic-list", ListView)
        list_view.clear()
        for t in self._filtered:
            closed = " [closed]" if t.get('is_closed') else ""
            list_view.append(ListItem(Label(f"{t['title']}{closed}")))

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "filter-input":
            self._apply_filter(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        # Move focus to list when user presses Enter in the filter box
        self.query_one("#topic-list", ListView).focus()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        index = event.index
        if index is not None and 0 <= index < len(self._filtered):
            t = self._filtered[index]
            self.dismiss(TopicSelected(topic_id=t['id'], title=t['title']))
