from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Header, Footer

from tui.screens.dashboard import DashboardScreen
from tui.screens.rules_list import RulesListScreen


class ForwarderApp(App):
    TITLE = "Tele-Forwarder"
    CSS_PATH = "tui.tcss"
    SCREENS = {"dashboard": DashboardScreen, "rules": RulesListScreen}
    BINDINGS = [
        Binding("1", "switch_screen('dashboard')", "Dashboard", priority=True),
        Binding("2", "switch_screen('rules')", "Rules", priority=True),
        Binding("q", "quit", "Quit", priority=True),
    ]

    def on_mount(self) -> None:
        self.push_screen("dashboard")

    def compose(self) -> ComposeResult:
        yield Header()
        yield Footer()
