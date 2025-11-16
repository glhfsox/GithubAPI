from functools import cache
from pickle import TRUE
from re import T
from tempfile import TemporaryDirectory
from token import OP
import urllib.request
import urllib.error
import json
from enum import Enum
from typing import List , Optional, Dict, Tuple
from datetime import date, datetime, timedelta
from pathlib import Path


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
                self.created_at = datetime.fromisoformat(time_stamp.replace("Z" , "+00:00"))
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
        
        
            temp_path = cache_path.with_suffix('.tmp')
            with open(temp_path , 'w') as f:
                json.dump(cache_data , f)
            temp_path.replace(cache_path)
        except Exception as e: 
            print(f"Error saving cache : {e}")
            if 'temp_path' in locals() and temp_path.exists():
                temp_path.unlink()


class Response : 
    def __init__(self , request : Request):
        self.request = request

        #basically displaying n (in our case 10) last events of given user
    def display(self , limit : int = 10 ) -> None:
        events = self.request.get_sorted_events()

        print(f"\n{'='*60}")
        print(f"Activity of the following user : {self.request.username}")
        print(f"\n{'='*60}")

        for i , event in enumerate(events[:limit] , 1):
            print(f"{i} . {event.format() } - {event.format_date()}")
        
        if len(events) > limit:
            print(f"\n.. and {len(events) - limit} more events")

def main(): 
    username = input("Please enter a valid username: ").strip()
    if not username:
        print("Not a valid username")
        return
    

    request = Request(username)
    if not request.fetch():
        return "fetch failure"
    
    print("\nSorting options:")
    print("1. By Date (newest first)")
    print("2. By Repository")
    print("3. By Event Type")

    choice = input("\nSelect sorting (1-3): ").strip()

    strategies = {
        '1' : SortByDate(),
        '2' : SortByRepository(),
        '3' : SortByType()
    }

    if choice in strategies:
        request.set_sort_strategy(strategies[choice])

    response = Response(request)
    response.display(limit=15)

if __name__ == "__main__" :
    main()