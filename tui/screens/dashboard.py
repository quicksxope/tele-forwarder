from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import DataTable, RichLog, Label
from textual import work
from tui.rpc_client import call as rpc_call
from tui.stats import per_rule_stats
from tui.config_io import load_config
from paths import CONFIG_PATH, DB_PATH, LOG_PATH
REFRESH_INTERVAL = 10  # seconds between stats refresh


class DashboardScreen(Screen):
    BINDINGS = [("r", "refresh", "Refresh")]

    def compose(self) -> ComposeResult:
        yield Label("", id="health-badge")
        yield DataTable(id="stats-table")
        yield RichLog(id="log-tail", highlight=True, markup=True)

    def on_mount(self) -> None:
        # Set up stats table columns
        table = self.query_one("#stats-table", DataTable)
        table.add_columns("Rule", "Total", "24h", "Errors", "Last Activity")
        # Start background workers
        self.refresh_stats()
        self.tail_log()
        # Schedule periodic stats refresh
        self.set_interval(REFRESH_INTERVAL, self.refresh_stats)

    @work(exclusive=True)
    async def refresh_stats(self) -> None:
        # Health check via RPC
        badge = self.query_one("#health-badge", Label)
        try:
            resp = await rpc_call('health')
            if resp.get('ok'):
                uptime = int(resp['result']['uptime_s'])
                h, m = divmod(uptime // 60, 60)
                badge.update(f"[bold green]● Running[/] — uptime {h}h {m:02d}m")
            else:
                badge.update("[bold red]● Daemon error[/]")
        except ConnectionRefusedError:
            badge.update("[bold red]● Stopped[/] — run: python forwarder.py")

        # Stats table
        try:
            cfg = load_config(CONFIG_PATH)
            rules = cfg.get('sources') or []
            stats = per_rule_stats(DB_PATH, rules)
            table = self.query_one("#stats-table", DataTable)
            table.clear()
            for s in stats:
                last = s['last_activity'] or '—'
                if last and last != '—':
                    last = last[:16]  # trim to YYYY-MM-DD HH:MM
                err_cell = f"[red]{s['error_count']}[/]" if s['error_count'] else str(s['error_count'])
                table.add_row(s['name'], str(s['total_ok']), str(s['ok_24h']), err_cell, last)
        except Exception:
            pass

    @work(thread=True)
    def tail_log(self) -> None:
        log_widget = self.query_one("#log-tail", RichLog)
        try:
            with open(LOG_PATH) as f:
                # Seek to end, then tail
                f.seek(0, 2)
                while True:
                    line = f.readline()
                    if line:
                        self.app.call_from_thread(log_widget.write, line.rstrip())
                    else:
                        import time
                        time.sleep(0.5)
        except FileNotFoundError:
            self.app.call_from_thread(log_widget.write, "[dim]forwarder.log not found — start the daemon first[/dim]")

    def action_refresh(self) -> None:
        self.refresh_stats()
