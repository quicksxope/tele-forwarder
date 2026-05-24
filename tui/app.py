from textual.app import App, ComposeResult
from textual.widgets import Header, Footer

from tui.screens.dashboard import DashboardScreen
from tui.screens.rules_list import RulesListScreen


class ForwarderApp(App):
    TITLE = "Tele-Forwarder"
    CSS_PATH = "tui.tcss"
    SCREENS = {"dashboard": DashboardScreen, "rules": RulesListScreen}
    BINDINGS = [
        ("d", "switch_screen('dashboard')", "Dashboard"),
        ("r", "switch_screen('rules')", "Rules"),
        ("q", "quit", "Quit"),
    ]

    def on_mount(self) -> None:
        self.push_screen("dashboard")

    def compose(self) -> ComposeResult:
        yield Header()
        yield Footer()
