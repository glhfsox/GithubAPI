import urllib.request
import urllib.error
import json
from enum import Enum
from typing import List , Optional, Dict, Tuple
from datetime import datetime, timedelta
from pathlib import Path
from collections import Counter
import tkinter as tk
from tkinter import ttk, messagebox
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg


class EventType(Enum):
    PUSH = "PushEvent"
    ISSUES = "IssuesEvent"
    WATCH = "WatchEvent"
    CREATE = "CreateEvent"
    DELETE = "DeleteEvent"
    PULL_REQUEST = "PullRequestEvent"
    FORK = "ForkEvent"

class GithubEvent:
    def __init__(self , event_data:dict):
        self.type = event_data.get("type")
        self.repo_name = event_data.get("repo" , {}).get("name")
        time_stamp =  event_data.get("created_at")
        if isinstance(time_stamp , str):
            try:
                #handling github time format 
                dt = datetime.fromisoformat(time_stamp.replace("Z" , "+00:00"))
                # normalize to naive datetime to avoid
                # comparisons between offset-aware and naive values
                if getattr(dt, "tzinfo", None) is not None:
                    dt = dt.replace(tzinfo=None)
                self.created_at = dt
            except Exception:
                self.created_at = datetime.min
        else:
            self.created_at = datetime.min
        self.actor = event_data.get("actor" , {}).get("login")
        self.payload = event_data.get("payload" ) or {}
        #trying to fix mistakes with commits count
        commits_list = self.payload.get("commits")
        if self.type == EventType.PUSH.value:
            try:
              count = int(self.payload.get("size", 1) or 0)
              if count <= 0 and isinstance(commits_list, list):
                  count = len(commits_list)
            except (ValueError, TypeError):
                  count = len(commits_list) if isinstance(commits_list, list) else 0
        else:
            if isinstance(commits_list, list) and commits_list:
                try:
                    count = sum(1 for c in commits_list if c.get("distinct", True))
                except Exception:
                    count = 0
            else:
                count = 0
        self.commit_count = max(count, 0)
    def format_date(self) -> str:
        if self.created_at == datetime.min:
            return "Unknown date"
        return self.created_at.strftime("%d.%m.%Y %H:%M:%S")
    def format(self) -> str:
        #initializing every possible input data "type"
        if self.type == EventType.PUSH.value: 
            count = getattr(self , "commit_count" , 0)
            return f"Pushed {count} commit{'s' if count!=1 else ''} to {self.repo_name}"
        elif self.type == EventType.ISSUES.value:
            action = self.payload.get("action", "").capitalize()
            return f"{action} an issue in {self.repo_name}"
        elif self.type == EventType.CREATE.value:
            ref_type = self.payload.get("ref_type" , "repository")
            return f"Created {ref_type} in {self.repo_name}"
        elif self.type == EventType.DELETE.value:
            return f"Deleted a branch in {self.repo_name}"
        elif self.type == EventType.PULL_REQUEST.value:
            action = self.payload.get("action" , "").capitalize()
            return f"{action} a pull request in {self.repo_name}"
        elif self.type == EventType.WATCH.value:
            return f"Watched {self.repo_name}"
        else:
            return f"{self.type} in {self.repo_name}"
        
#sorting stuff by field
class SortStrat: 
    
    def sort(self , events : List[GithubEvent]) -> List[GithubEvent]:
        raise NotImplementedError

class SortByDate(SortStrat):
    def sort(self , events : List[GithubEvent]) -> List[GithubEvent]:
        return sorted(events, key=lambda e: e.created_at, reverse=True)

class SortByRepository(SortStrat):
    def sort(self , events : List[GithubEvent]) -> List[GithubEvent]:
        return sorted(events , key=lambda e: e.repo_name)

class SortByType(SortStrat):
    def sort(self , events : List[GithubEvent]) -> List[GithubEvent]:
        return sorted(events, key=lambda e: e.type if e.type is not None else "", reverse=False)


class Request :
    #reading info from given url and handling some errors 
    BASE_URL = "https://api.github.com/users"
    #setting up caching and time-to-live(TTL) to improve performance
    memory_cache: Dict[str , Tuple[List[GithubEvent] , datetime]] = {}
    CACHE_DIR = Path(".cache")
    TTL = timedelta(minutes=10)
    def __init__(self , username : str , sort_strategy: Optional[SortStrat] = None):
        self.username = username
        self.url = f"{self.BASE_URL}/{username}/events"
        self.sortStrategy = sort_strategy or SortByDate()
        self.events: List[GithubEvent] = []
        self.CACHE_DIR.mkdir(exist_ok=True)


    
    def fetch(self) -> bool:
        cache_key = self.username
       #checking if the program has already fetched users' info during TTL
        if cache_key in self.memory_cache:
            events , cache_time = self.memory_cache[cache_key]
            if datetime.now() - cache_time < self.TTL:
                self.events = events
                return True
        file_cache = self.load_from_file_cache()
        #overwriting cache 
        if file_cache:
            self.events = file_cache
            self.memory_cache [cache_key] = (file_cache , datetime.now())
            return True 
        try:
            response = urllib.request.urlopen(self.url)
            data = json.loads(response.read().decode())

            if not data :
                print("No activity for this user")
                return False
            
            self.events = [GithubEvent(event_data) for event_data in data ]
            #saving new data for the user
            self.memory_cache[cache_key] = (self.events , datetime.now())
            self.save_to_file_cache(self.events)
            return True
        except urllib.error.HTTPError as e:
            if e.code == 404:
                print(f"Error: User '{self.username}' not found")
            else:
                print(f"API Error: {e.code}")
            return False
        except Exception as e:
            print(f"Error: {e}")
            return False
    def set_sort_strategy(self , strat:SortStrat) -> None:
        self.sortStrategy = strat
    def get_sorted_events(self) -> List[GithubEvent]:
        return self.sortStrategy.sort(self.events)

    def get_cache_path(self) -> Path:
        return self.CACHE_DIR/f"{self.username}.json"

    def load_from_file_cache(self) -> Optional[List[GithubEvent]]:
        cache_path = self.get_cache_path()
        if not cache_path.exists():
            return None
        try:
            with open(cache_path , 'r') as f:
                cache_data = json.load(f)
                cache_time = datetime.fromisoformat(cache_data['timestamp'])

                if datetime.now() - cache_time < self.TTL:
                    return [GithubEvent(event) for event in cache_data['events']]
        except Exception:
            pass #бляяя , лан похуй
        return None

    def save_to_file_cache(self , events: List[GithubEvent] ) -> None:
        #rrecursively transforms data types into json-compatible
        def convert_to_json_serializable(obj):
            if isinstance(obj, set):
                return list(obj)
            elif isinstance(obj, dict):
                return {k: convert_to_json_serializable(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [convert_to_json_serializable(item) for item in obj]
            elif obj is None:
                return None
            elif isinstance(obj, (str, int, float, bool)):
                return obj
            else:
                return str(obj)

        
        cache_path = self.get_cache_path()
        temp_path = cache_path.with_suffix('.tmp')
        try:
            cache_data = { 
             'timestamp': datetime.now().isoformat(),
             'events': [
                {
                'type': e.type,
                'repo': {'name' : e.repo_name},
                'actor': {'login' : e.actor},
                'created_at' : e.created_at.isoformat(),
                'payload' : convert_to_json_serializable(e.payload)
            }for e in events 
        ]
    }
            cache_data = convert_to_json_serializable(cache_data)
        
         #ensuring that file is written completely (in case with no mistakes) 
         # , or not at all in case of a mistake 
            with open(temp_path , 'w') as f:
                json.dump(cache_data , f)
            temp_path.replace(cache_path)
        except Exception as e: 
            print(f"Error saving cache : {e}")
            if  temp_path.exists():
                 temp_path.unlink()


STATS_DAYS = 30
UI_SORT_OPTIONS = [
    ("By Date (newest first)", SortByDate),
    ("By Repository", SortByRepository),
    ("By Event Type", SortByType),
]


def filter_recent_events(events: List[GithubEvent], days: int) -> List[GithubEvent]:
    cutoff = datetime.now() - timedelta(days=days)
    return [e for e in events if e.created_at != datetime.min and e.created_at >= cutoff]


def aggregate_stats(events: List[GithubEvent], days: int) -> Optional[Dict[str, int]]:
    recent_events = filter_recent_events(events, days)

    if not recent_events:
        return None

    stats: Dict[str, int] = Counter()

    for e in recent_events:
        if e.type == EventType.PUSH.value:
            stats["Commits"] += max(getattr(e, "commit_count", 0), 0)
        elif e.type == EventType.PULL_REQUEST.value:
            stats["Pull requests"] += 1
        elif e.type == EventType.ISSUES.value:
            stats["Issues"] += 1
        elif e.type == EventType.WATCH.value:
            stats["Stars / Watches"] += 1
        elif e.type == EventType.FORK.value:
            stats["Forks"] += 1
        elif e.type == EventType.CREATE.value:
            stats["Creates"] += 1
        elif e.type == EventType.DELETE.value:
            stats["Deletes"] += 1
        else:
            stats["Other"] += 1

    return dict(stats)


def build_figure_from_stats(stats: Dict[str, int]) -> Figure:
    labels = list(stats.keys())
    values = list(stats.values())

    fig = Figure(figsize=(14, 7), dpi=100, facecolor="#fafafa")
    ax_pie, ax_bar = fig.subplots(
        1, 2, gridspec_kw={"width_ratios": [1.1, 1]}, squeeze=True
    )
    fig.subplots_adjust(left=0.05, right=0.98, top=0.9, bottom=0.12, wspace=0.35)

    total = sum(values)
    if total == 0:
        return fig

    colors = ["#1f77b4", "#f28e2b", "#2ca02c", "#d62728", "#9467bd", "#8c564b", "#e377c2"]

    ax_pie.pie(values, labels=labels, autopct="%1.1f%%", startangle=140, colors=colors)
    ax_pie.set_box_aspect(1)
    ax_pie.set_title("Action share")

    x_pos = list(range(len(labels)))
    ax_bar.bar(x_pos, values, color=colors[: len(labels)])
    ax_bar.set_xticks(x_pos)
    ax_bar.set_xticklabels(labels, rotation=30, ha="right")
    ax_bar.set_title("Action count")
    ax_bar.set_ylabel("Count")
    ax_bar.grid(axis="y", linestyle="--", alpha=0.4)

    fig.text(
        0.27,
        0.05,
        f"Total actions: {total}",
        ha="center",
        fontsize=12,
        fontweight="bold",
        color="#444",
    )

    return fig


def center_window(window, width: int, height: int) -> None:
    #Position a Tk window roughly in the center of the screen.
    
    window.update_idletasks()
    screen_width = window.winfo_screenwidth()
    screen_height = window.winfo_screenheight()
    x = (screen_width - width) // 2
    y = (screen_height - height) // 3
    window.geometry(f"{width}x{height}+{x}+{y}")


def parse_username(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if "github.com" in value:
        part = value.split("github.com", 1)[1]
        part = part.lstrip("/ ")
        if "/" in part:
            part = part.split("/", 1)[0]
        return part.strip()
    return value


def show_stats_window(root: tk.Tk, request: Request, sort_label: str) -> None:
    events = request.get_sorted_events()
    recent_events = filter_recent_events(events, STATS_DAYS)
    stats = aggregate_stats(events, STATS_DAYS)

    win = tk.Toplevel(root)
    win.title(f"Activity stats: {request.username}")
    width, height = 1200, 800
    win.geometry(f"{width}x{height}")
    center_window(win, width, height)

    # layout: header (row 0), charts (row 1), events list (row 2)
    win.rowconfigure(0, weight=0)
    win.rowconfigure(1, weight=3)
    win.rowconfigure(2, weight=2)
    win.columnconfigure(0, weight=1)

    info_frame = ttk.Frame(win, padding=(12, 10))
    info_frame.grid(row=0, column=0, sticky="ew", padx=10, pady=10)
    info_frame.columnconfigure(0, weight=1)

    ttk.Label(
        info_frame,
        text=f"User: {request.username} | Period: last {STATS_DAYS} days",
        font=("TkDefaultFont", 12, "bold"),
    ).grid(row=0, column=0, sticky="w")

    if not stats:
        ttk.Label(
            win,
            text="No activity for the selected period.",
            foreground="red",
        ).grid(row=1, column=0, pady=20)
        return

    chart_frame = ttk.Frame(win)
    chart_frame.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 6))
    chart_frame.rowconfigure(0, weight=1)
    chart_frame.columnconfigure(0, weight=1)

    fig = build_figure_from_stats(stats)
    fig_canvas = FigureCanvasTkAgg(fig, master=chart_frame)
    fig_widget = fig_canvas.get_tk_widget()
    fig_widget.configure(bg="#fafafa", highlightthickness=0)
    fig_widget.grid(row=0, column=0, sticky="nsew")

    def on_chart_resize(event):
        if event.width <= 0 or event.height <= 0:
            return
        dpi = fig.get_dpi()
        w = max(event.width, 200)
        h = max(event.height, 200)
        fig.set_size_inches(w / dpi, h / dpi, forward=True)
        fig_canvas.draw_idle()

    chart_frame.bind("<Configure>", on_chart_resize)

    list_frame = ttk.Frame(win, padding=(12, 8))
    list_frame.grid(row=2, column=0, sticky="nsew", padx=4, pady=(0, 12))
    list_frame.rowconfigure(1, weight=1)
    list_frame.columnconfigure(0, weight=1)

    style = ttk.Style(win)
    style.configure("Activity.Time.TLabel", font=("TkDefaultFont", 10, "bold"))
    style.configure("Activity.Event.TLabel", font=("TkDefaultFont", 10))

    ttk.Label(
        list_frame,
        text=f"Recent events (sorted: {sort_label})",
        font=("TkDefaultFont", 11, "bold"),
    ).grid(row=0, column=0, sticky="w", pady=(0, 6))

    list_canvas = tk.Canvas(list_frame, highlightthickness=0, bg="#ffffff")
    yscroll = ttk.Scrollbar(list_frame, orient="vertical", command=list_canvas.yview)
    list_canvas.configure(yscrollcommand=yscroll.set)

    list_canvas.grid(row=1, column=0, sticky="nsew")
    yscroll.grid(row=1, column=1, sticky="ns")

    inner = ttk.Frame(list_canvas, padding=4)
    inner_window = list_canvas.create_window((0, 0), window=inner, anchor="nw")

    def on_inner_config(event):
        list_canvas.configure(scrollregion=list_canvas.bbox("all"))
        canvas_width = list_canvas.winfo_width()
        if canvas_width > 0:
            list_canvas.itemconfig(inner_window, width=canvas_width)

    inner.bind("<Configure>", on_inner_config)
    list_canvas.update_idletasks()

    def on_mousewheel(event):
        if event.num == 4:
            delta = -1
        elif event.num == 5:
            delta = 1
        else:
            delta = -1 if event.delta > 0 else 1
        list_canvas.yview_scroll(delta, "units")
        return "break"

    for widget in (list_canvas, inner, win):
        widget.bind("<MouseWheel>", on_mousewheel, add="+")
        widget.bind("<Button-4>", on_mousewheel, add="+")
        widget.bind("<Button-5>", on_mousewheel, add="+")

    display_events = recent_events or events
    for idx, event in enumerate(display_events[:25]):
        row = ttk.Frame(inner, padding=(10, 6))
        row.pack(fill=tk.X, pady=(0, 6))

        time_label = ttk.Label(
            row,
            text=event.format_date(),
            style="Activity.Time.TLabel",
            width=22,
            anchor="w",
        )
        time_label.pack(side=tk.LEFT, anchor="w")

        event_label = ttk.Label(
            row,
            text=event.format(),
            style="Activity.Event.TLabel",
            anchor="w",
            wraplength=900,
            justify="left",
        )
        event_label.pack(side=tk.LEFT, anchor="w", fill=tk.X, expand=True, padx=(10, 0))


def run_ui() -> None:
    root = tk.Tk()
    root.title("Github Activity")
    width, height = 720, 480
    root.geometry(f"{width}x{height}")
    center_window(root, width, height)

    # slightly nicer default ttk look
    style = ttk.Style(root)
    try:
        if "clam" in style.theme_names():
            style.theme_use("clam")
    except Exception:
        # не критично, если смена темы не сработает
        pass

    main_frame = ttk.Frame(root, padding=10)
    main_frame.pack(fill=tk.BOTH, expand=True)

    ttk.Label(
        main_frame,
        text="GitHub Activity",
        font=("TkDefaultFont", 12, "bold"),
    ).pack(anchor="center", pady=(0, 6))

    ttk.Label(main_frame, text="Enter GitHub username or profile link:").pack(
        anchor="w"
    )

    username_var = tk.StringVar()
    entry = ttk.Entry(main_frame, textvariable=username_var)
    entry.pack(fill=tk.X, pady=5)
    entry.focus_set()

    ttk.Label(main_frame, text="Sorting:").pack(anchor="w", pady=(6, 0))
    sort_var = tk.StringVar(value=UI_SORT_OPTIONS[0][0])
    sort_box = ttk.Combobox(
        main_frame,
        textvariable=sort_var,
        values=[label for label, _ in UI_SORT_OPTIONS],
        state="readonly",
    )
    sort_box.pack(fill=tk.X, pady=4)

    def on_show():
        raw_value = username_var.get()
        username = parse_username(raw_value)
        if not username:
            messagebox.showerror("Error", "Enter a valid username or profile link.")
            return

        req = Request(username)
        selected_label = sort_var.get()
        for label, strat_cls in UI_SORT_OPTIONS:
            if label == selected_label:
                req.set_sort_strategy(strat_cls())
                break

        if not req.fetch():
            messagebox.showerror("Error", f"Failed to load data for '{username}'.")
            return

        show_stats_window(root, req, selected_label)

    btn = ttk.Button(main_frame, text="Show stats", command=on_show)
    btn.pack(pady=10)

    entry.bind("<Return>", lambda _: on_show())
    entry.bind("<KP_Enter>", lambda _: on_show())

    root.mainloop()


if __name__ == "__main__" :
    run_ui()
