#!/usr/bin/env python3
"""signalbox — terminal view of a running OpenPortal agent network.

Two screens, because they answer the two questions that come up while
debugging:

  Agents  which hop is broken, and what has it been doing
  Logs    one timeline across every agent, filterable

Read-only: it calls health and diagnostics, and never submits a job.
"""

import asyncio
from datetime import datetime

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Static,
    TabbedContent,
    TabPane,
)

import opdata

REFRESH_SECONDS = 5

LEVEL_STYLE = {
    "ERROR": "bold red",
    "WARN": "yellow",
    "INFO": "cyan",
    "DEBUG": "dim",
    "TRACE": "dim",
}


def humanise(seconds):
    seconds = int(seconds or 0)
    if seconds >= 3600:
        return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"
    if seconds >= 60:
        return f"{seconds // 60}m{seconds % 60:02d}s"
    return f"{seconds}s"


class AgentsPane(Vertical):
    """Agent table on the left, detail for the selected agent on the right."""

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield DataTable(id="agents", cursor_type="row", zebra_stripes=True)
            yield VerticalScroll(Static("", id="detail"), id="detail-wrap")

    def on_mount(self) -> None:
        table = self.query_one("#agents", DataTable)
        for label, width in (
            ("agent", 13), ("type", 10), ("state", 5), ("up", 6),
            ("wrk", 3), ("run", 3), ("fail", 4), ("mean", 6),
        ):
            table.add_column(label, width=width)


class LogsPane(Vertical):
    """One timeline across every agent."""

    def compose(self) -> ComposeResult:
        with Horizontal(id="log-filters"):
            yield Label("level ", classes="flabel")
            yield Input(placeholder="INFO / WARN+ / ERROR", id="level", classes="finput")
            yield Label(" search ", classes="flabel")
            yield Input(placeholder="substring", id="search", classes="finput")
        yield DataTable(id="logs", cursor_type="row", zebra_stripes=True)

    def on_mount(self) -> None:
        table = self.query_one("#logs", DataTable)
        for label, width in (
            ("time", 9), ("agent", 13), ("level", 6), ("target", 26), ("message", 0)
        ):
            table.add_column(label, width=width or None)


class OpenPortalTUI(App):
    CSS = """
    Screen { background: $surface; }
    #agents { width: 70; border-right: solid $panel-lighten-2; }
    #detail-wrap { padding: 0 1; }
    #log-filters { height: 3; padding: 0 1; }
    .flabel { width: auto; padding: 1 0 0 0; color: $text-muted; }
    .finput { width: 28; }
    #status { padding: 0 1; color: $text-muted; }
    """

    BINDINGS = [
        Binding("q", "quit", "quit"),
        Binding("r", "refresh", "refresh"),
        Binding("p", "toggle_pause", "pause"),
        Binding("1", "show('agents-tab')", "agents"),
        Binding("2", "show('logs-tab')", "logs"),
        Binding("n", "toggle_noise", "self-noise"),
    ]

    def __init__(self):
        super().__init__()
        self.nodes = []
        self.paused = False
        self.selected = None
        self.error = None
        # Polling for diagnostics is itself logged, so the tool would otherwise
        # fill its own log view. Hidden unless asked for.
        self.include_self = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent(initial="agents-tab"):
            with TabPane("Agents", id="agents-tab"):
                yield AgentsPane()
            with TabPane("Logs", id="logs-tab"):
                yield LogsPane()
        yield Static("", id="status")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "signalbox"
        self.set_interval(REFRESH_SECONDS, self.auto_refresh)
        self.refresh_topology()

    # --- actions ---------------------------------------------------------

    def action_show(self, tab: str) -> None:
        self.query_one(TabbedContent).active = tab

    def action_toggle_pause(self) -> None:
        self.paused = not self.paused
        self.update_status()

    def action_toggle_noise(self) -> None:
        self.include_self = not self.include_self
        self.action_refresh()

    def action_refresh(self) -> None:
        self.refresh_topology()
        if self.query_one(TabbedContent).active == "logs-tab":
            self.refresh_logs()

    def auto_refresh(self) -> None:
        if self.paused:
            return
        self.refresh_topology()
        if self.query_one(TabbedContent).active == "logs-tab":
            self.refresh_logs()

    # --- data ------------------------------------------------------------

    @work(exclusive=True, thread=True)
    def refresh_topology(self) -> None:
        """Bridge calls are blocking, so they run off the UI thread."""
        try:
            data = opdata.topology()
            self.error = None
        except Exception as exc:
            self.error = str(exc)
            data = None
        self.call_from_thread(self.apply_topology, data)

    @work(exclusive=True, thread=True)
    def refresh_detail(self, path: str) -> None:
        try:
            detail = opdata.agent_detail(path, include_self=self.include_self)
        except Exception as exc:
            detail = {"ok": False, "error": str(exc)}
        self.call_from_thread(self.apply_detail, detail)

    @work(exclusive=True, thread=True)
    def refresh_logs(self) -> None:
        level = (self.query_one("#level", Input).value or "").strip() or None
        search = (self.query_one("#search", Input).value or "").strip() or None
        paths = [(n["id"], n["name"]) for n in self.nodes]
        try:
            rows = opdata.merged_logs(paths, level, search,
                                      include_self=self.include_self)
        except Exception:
            rows = []
        self.call_from_thread(self.apply_logs, rows)

    # --- rendering -------------------------------------------------------

    def apply_topology(self, data) -> None:
        if data:
            self.nodes = data["nodes"]

        table = self.query_one("#agents", DataTable)
        cursor = table.cursor_row
        table.clear()

        for node in self.nodes:
            failed = node["totals"]["failed"]
            if not node["connected"]:
                state, style = "down", "bold red"
            elif failed:
                state, style = "warn", "yellow"
            else:
                state, style = "ok", "green"

            table.add_row(
                node["name"],
                str(node["type"]),
                f"[{style}]{state}[/]",
                humanise(node["uptime_seconds"]),
                str(node["workers"]),
                str(node["jobs"]["running"]),
                f"[yellow]{failed}[/]" if failed else "0",
                f"{node['job_time_mean_ms']:.0f}",
                key=node["id"],
            )

        if self.nodes:
            table.move_cursor(row=min(cursor, len(self.nodes) - 1))
            if self.selected is None:
                self.select_row(0)
        self.update_status()

    def select_row(self, index: int) -> None:
        if 0 <= index < len(self.nodes):
            node = self.nodes[index]
            self.selected = node
            self.render_detail_header(node)
            self.refresh_detail(node["id"])

    def render_detail_header(self, node) -> None:
        mb = node["memory_bytes"] / 1048576
        lines = [
            f"[bold]{node['name']}[/]  [dim]{node['type']}[/]",
            f"[dim]{node['id'] or '(bridge)'}[/]",
            "",
            f"engine   {node['engine']} {node['version']}",
            f"uptime   {humanise(node['uptime_seconds'])}"
            f"   workers {node['workers']}",
            f"memory   {mb:.1f} MB   cpu {node['cpu_percent']}%",
            f"jobs     running {node['jobs']['running']}"
            f"  completed {node['totals']['completed']}"
            f"  failed {node['totals']['failed']}"
            f"  slow {node['totals']['slow']}",
            f"mean job {node['job_time_mean_ms']:.0f} ms",
            "",
            "[dim]loading diagnostics…[/]",
        ]
        self.query_one("#detail", Static).update("\n".join(lines))

    def apply_detail(self, detail) -> None:
        node = self.selected
        if node is None:
            return
        if not detail.get("ok"):
            self.query_one("#detail", Static).update(
                f"[red]diagnostics unavailable:[/] {detail.get('error')}"
            )
            return

        def section(title, items, style="", limit=6):
            if not items:
                return [f"[bold]{title}[/]", "  [dim]none[/]", ""]
            out = [f"[bold]{title}[/]"]
            for item in items[:limit]:
                text = item.replace("[", r"\[")
                out.append(f"  [{style}]{text}[/]" if style else f"  {text}")
            return out + [""]

        mb = node["memory_bytes"] / 1048576
        body = [
            f"[bold]{node['name']}[/]  [dim]{node['type']}[/]",
            f"[dim]{node['id'] or '(bridge)'}[/]",
            "",
            f"engine   {node['engine']} {node['version']}",
            f"uptime   {humanise(node['uptime_seconds'])}   workers {node['workers']}",
            f"memory   {mb:.1f} MB   cpu {node['cpu_percent']}%",
            f"jobs     running {node['jobs']['running']}"
            f"  completed {node['totals']['completed']}"
            f"  failed {node['totals']['failed']}"
            f"  slow {node['totals']['slow']}",
            f"mean job {node['job_time_mean_ms']:.0f} ms",
            "",
        ]
        body += section("running", detail["running_jobs"])
        body += section("failed", detail["failed_jobs"], "red")
        body += section("slowest", detail["slowest_jobs"], "yellow")
        body += section("warnings", detail["warnings"], "yellow")

        body.append("[bold]recent log[/]")
        for row in detail["logs"][-12:]:
            style = LEVEL_STYLE.get(row["level"].upper(), "")
            message = row["message"].replace("[", r"\[")
            body.append(
                f"  [dim]{row['time']}[/] [{style}]{row['level']:<5}[/] {message[:400]}"
                if style
                else f"  [dim]{row['time']}[/] {row['level']:<5} {message[:400]}"
            )

        self.query_one("#detail", Static).update("\n".join(body))

    def apply_logs(self, rows) -> None:
        table = self.query_one("#logs", DataTable)
        table.clear()
        for row in rows[-500:]:
            style = LEVEL_STYLE.get(row["level"].upper(), "")
            level = f"[{style}]{row['level']}[/]" if style else row["level"]
            table.add_row(
                row["time"],
                row["agent"],
                level,
                row["target"][-26:],
                row["message"].replace("[", r"\[")[:300],
            )
        if rows:
            table.move_cursor(row=table.row_count - 1)

    def update_status(self) -> None:
        if self.error:
            text = f"[red]bridge unreachable:[/] {self.error}"
        else:
            up = sum(1 for n in self.nodes if n["connected"])
            failed = sum(n["totals"]["failed"] for n in self.nodes)
            text = (
                f"{up}/{len(self.nodes)} connected   failures {failed}   "
                f"updated {datetime.now().strftime('%H:%M:%S')}"
                f"{'   [yellow]paused[/]' if self.paused else ''}"
                f"{'   [dim]+self-noise[/]' if self.include_self else ''}"
            )
        self.query_one("#status", Static).update(text)

    # --- events ----------------------------------------------------------

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id == "agents":
            self.select_row(event.cursor_row)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.refresh_logs()

    def on_tabbed_content_tab_activated(self, event) -> None:
        if self.query_one(TabbedContent).active == "logs-tab":
            self.refresh_logs()


def main():
    OpenPortalTUI().run()


if __name__ == "__main__":
    asyncio.run  # noqa: B018  (kept for clarity that Textual owns the loop)
    main()
