import os
import json
import re
import gdown
import requests
import sys

# ---------------- CONFIG ----------------
API_KEY = "Pc1DFIKlQj2-Pe5Mc4wbM6wR"
API_BASE_URL = "https://deathstruckapi.lol"
ALLOWED_EXTENSIONS = (".lua", ".manifest", ".text")
MAIN_FOLDER = "gamefolder"

# ---------------- API FUNCTIONS ----------------
def get_all_games_from_api() -> dict:
    """Get game data from API /dev/stats endpoint"""
    try:
        url = f"{API_BASE_URL}/dev/stats"
        params = {"key": API_KEY}
        print(f"[DEBUG] Testing URL: {url}")
        
        response = requests.get(url, params=params, timeout=10)
        print(f"[DEBUG] Status Code: {response.status_code}")
        
        if response.status_code == 200:
            try:
                data = response.json()
                print(f"[DEBUG] Successfully parsed JSON")
                
                # Extract available games from the response
                games = {}
                if 'requests' in data:
                    for req in data['requests']:
                        if req.get('status') == 'added':  # Only include games that are ready
                            appid = req.get('appid')
                            game_name = req.get('gameName', f"Game {appid}")
                            games[appid] = {
                                'name': game_name,
                                'appid': appid,
                                'download_url': f"{API_BASE_URL}/lua/{appid}?key={API_KEY}"
                            }
                
                print(f"[DEBUG] Found {len(games)} available games")
                return games
                
            except Exception as json_error:
                print(f"[DEBUG] JSON parsing failed: {json_error}")
                return {}
        else:
            print(f"[API ERROR] HTTP {response.status_code}: {response.text[:200]}...")
            return {}
    except Exception as e:
        print(f"[API ERROR] Failed to get games from API: {e}")
        return {}

# ---------------- HELPERS ----------------
def extract_file_id(link: str) -> str:
    """Extract the file/folder ID from a Google Drive link"""
    if "/file/d/" in link:
        return link.split("/file/d/")[1].split("/")[0]
    if "id=" in link:
        return link.split("id=")[1].split("&")[0]
    return None

def download_link(link: str, main_folder: str):
    os.makedirs(main_folder, exist_ok=True)
    folder_id = extract_file_id(link)
    if not folder_id:
        print(f"[SKIP] Could not extract ID from {link}")
        return None

    folder_path = os.path.join(main_folder, folder_id)
    os.makedirs(folder_path, exist_ok=True)

    # Use gdown.download_folder for both files and folders
    try:
        gdown.download_folder(link, output=folder_path, quiet=False, use_cookies=False)
    except Exception as e:
        print(f"[ERROR] Failed to download {link}: {e}")
        return None

    # Filter allowed extensions
    for root, _, files in os.walk(folder_path):
        for f in files:
            if not f.endswith(ALLOWED_EXTENSIONS):
                os.remove(os.path.join(root, f))

    # Check for at least one .lua
    lua_found = any(f.endswith(".lua") for _, _, files in os.walk(folder_path) for f in files)
    if not lua_found:
        print(f"[FAILED] No .lua file in folder {folder_path}")
        return None

    print(f"[SUCCESS] Folder ready: {folder_path}")
    return folder_path

# ---------------- CLI ----------------
def show_help():
    print("""
Usage: python export_manifests_cli.py [command]

Commands:
  list                    - List all available games
  download <appid>        - Download specific game by AppID
  download-all            - Download all available games
  help                    - Show this help message

Examples:
  python export_manifests_cli.py list
  python export_manifests_cli.py download 12345
  python export_manifests_cli.py download-all
""")

def list_games():
    """List all available games"""
    print("Fetching available games...")
    games_db = get_all_games_from_api()
    
    if not games_db:
        print("No games found on API")
        return
    
    print(f"\nFound {len(games_db)} available games:")
    print("-" * 60)
    
    for i, (appid, data) in enumerate(games_db.items(), 1):
        print(f"{i:2d}. {data['name']} (ID: {appid})")
    
    print("-" * 60)

def download_game(appid):
    """Download a specific game"""
    games_db = get_all_games_from_api()
    
    if not games_db:
        print("No games found on API")
        return
    
    if appid not in games_db:
        print(f"Game {appid} not found in available games")
        print("Use 'list' command to see available games")
        return
    
    game_data = games_db[appid]
    game_name = game_data['name']
    download_url = game_data['download_url']
    
    print(f"Downloading {game_name} (ID: {appid})...")
    
    folder_path = download_link(download_url, MAIN_FOLDER)
    if not folder_path:
        print(f"Failed to download {game_name}")
        return
    
    # Rename folder to game name
    new_folder_path = os.path.join(MAIN_FOLDER, game_name.replace(" ", "_").replace("/", "_"))
    if os.path.exists(folder_path) and not os.path.exists(new_folder_path):
        os.rename(folder_path, new_folder_path)
        print(f"[SUCCESS] Game saved: {new_folder_path}")

def download_all_games():
    """Download all available games"""
    games_db = get_all_games_from_api()
    
    if not games_db:
        print("No games found on API")
        return
    
    print(f"Found {len(games_db)} games. Downloading all...")
    
    failed = []
    for i, (appid, game_data) in enumerate(games_db.items(), 1):
        game_name = game_data['name']
        download_url = game_data['download_url']
        
        print(f"\n[{i}/{len(games_db)}] Processing {game_name} (ID: {appid})")
        
        folder_path = download_link(download_url, MAIN_FOLDER)
        if not folder_path:
            failed.append(f"{game_name} ({appid})")
        else:
            # Rename folder to game name
            new_folder_path = os.path.join(MAIN_FOLDER, game_name.replace(" ", "_").replace("/", "_"))
            if os.path.exists(folder_path) and not os.path.exists(new_folder_path):
                os.rename(folder_path, new_folder_path)
                print(f"[SUCCESS] Game saved: {new_folder_path}")
    
    print(f"\nDownload complete!")
    print(f"Total: {len(games_db)} games")
    print(f"Success: {len(games_db) - len(failed)} games")
    print(f"Failed: {len(failed)} games")
    
    if failed:
        print("\nFailed downloads:")
        for game in failed:
            print(f"  - {game}")

# ---------------- MAIN ----------------
if __name__ == "__main__":
    if len(sys.argv) < 2:
        show_help()
        sys.exit(1)
    
    command = sys.argv[1].lower()
    
    if command == "help":
        show_help()
    elif command == "list":
        list_games()
    elif command == "download":
        if len(sys.argv) < 3:
            print("Error: Please provide an AppID")
            print("Usage: python export_manifests_cli.py download <appid>")
            sys.exit(1)
        download_game(sys.argv[2])
    elif command == "download-all":
        download_all_games()
    else:
        print(f"Unknown command: {command}")
        show_help()
        sys.exit(1)
