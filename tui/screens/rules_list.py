import asyncio
from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import DataTable, Static, Label
from textual.binding import Binding
from textual import work
from tui.config_io import load_config, save_config, ensure_rule_uuid
from paths import CONFIG_PATH


class RulesListScreen(Screen):
    BINDINGS = [
        Binding("a", "add_rule", "Add"),
        Binding("e", "edit_rule", "Edit"),
        Binding("d", "delete_rule", "Delete"),
        Binding("r", "refresh", "Refresh"),
    ]

    def compose(self) -> ComposeResult:
        yield Label("Forwarding Rules", id="title")
        yield DataTable(id="rules-table")
        yield Static("a=Add  e=Edit  d=Delete  r=Refresh", id="hint")

    def on_mount(self) -> None:
        table = self.query_one("#rules-table", DataTable)
        table.cursor_type = "row"
        table.add_columns("Name", "Source", "Topics", "Destination", "Filters")
        self.load_rules()

    @work(exclusive=True)
    async def load_rules(self) -> None:
        try:
            cfg = load_config(CONFIG_PATH)
        except FileNotFoundError:
            return
        rules = list(cfg.get('sources') or [])
        table = self.query_one("#rules-table", DataTable)
        table.clear()
        for rule in rules:
            name = rule.get('name', '(unnamed)')
            source_id = rule.get('chat_id', '?')
            source_title = rule.get('source_title', '')
            source_col = f"{source_title} ({source_id})" if source_title else str(source_id)
            topics = rule.get('topics', 'all')
            topics_str = 'all' if topics == 'all' else f"{len(topics)} topic(s)"
            dest = rule.get('destination') or {}
            dest_id = dest.get('chat_id', '?')
            dest_title = dest.get('title', '')
            dest_col = f"{dest_title} ({dest_id})" if dest_title else str(dest_id)
            kw = rule.get('filters', {}).get('keywords') or []
            mt = rule.get('filters', {}).get('media_types') or []
            filters_str = ', '.join(
                ([f"{len(kw)} kw"] if kw else []) +
                ([f"{len(mt)} types"] if mt else [])
            ) or 'all'
            table.add_row(name, source_col, topics_str, dest_col, filters_str, key=str(rule.get('uuid', name)))

    def action_refresh(self) -> None:
        self.load_rules()

    def action_add_rule(self) -> None:
        from tui.screens.rule_edit import RuleEditScreen
        self.app.push_screen(RuleEditScreen(rule=None, on_save=self._on_rule_saved))

    def action_edit_rule(self) -> None:
        table = self.query_one("#rules-table", DataTable)
        if table.cursor_row is None:
            return
        try:
            cfg = load_config(CONFIG_PATH)
            rules = list(cfg.get('sources') or [])
            if table.cursor_row < len(rules):
                rule = rules[table.cursor_row]
                from tui.screens.rule_edit import RuleEditScreen
                self.app.push_screen(RuleEditScreen(rule=dict(rule), on_save=self._on_rule_saved))
        except Exception:
            pass

    def action_delete_rule(self) -> None:
        table = self.query_one("#rules-table", DataTable)
        if table.cursor_row is None:
            return
        self._delete_rule_at(table.cursor_row)

    def _delete_rule_at(self, index: int) -> None:
        try:
            cfg = load_config(CONFIG_PATH)
            rules = list(cfg.get('sources') or [])
            if 0 <= index < len(rules):
                rules.pop(index)
                cfg['sources'] = rules
                save_config(cfg, CONFIG_PATH)
                self.notify("Rule deleted. Restart daemon to apply.", severity="warning")
                self.load_rules()
        except Exception as e:
            self.notify(f"Error: {e}", severity="error")

    def _on_rule_saved(self) -> None:
        self.load_rules()
        self.notify("Rule saved. Restart daemon to apply.", severity="information")
