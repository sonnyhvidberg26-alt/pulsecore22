import os
import json
import re
import gdown
import requests
import tkinter as tk
from tkinter import messagebox, simpledialog

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
        print(f"[DEBUG] Params: {params}")
        
        response = requests.get(url, params=params, timeout=10)
        print(f"[DEBUG] Status Code: {response.status_code}")
        
        if response.status_code == 200:
            try:
                data = response.json()
                print(f"[DEBUG] Successfully parsed JSON")
                
                # Extract available games from the response
                # The API returns stats, logs, and requests but not game data directly
                # We need to use the /lua/{appid} endpoint for each requested game
                
                # For now, let's return the requests data which contains game info
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
                print(f"[DEBUG] Raw response: {response.text[:500]}...")  # Only print the first 500 characters
                return {}
        else:
            print(f"[API ERROR] HTTP {response.status_code}: {response.text[:200]}...")  # Only print the first 200 characters
            return {}
    except Exception as e:
        print(f"[API ERROR] Failed to get games from API: {e}")
        import traceback
        print(f"[DEBUG] Traceback: {traceback.format_exc()}")
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

# ---------------- GUI ----------------
def start_download():
    # Get games from API
    games_db = get_all_games_from_api()
    if not games_db:
        messagebox.showerror("Error", "No games found on API")
        return

    # Show available games and let user select
    game_list = [(appid, data['name']) for appid, data in games_db.items()]
    if not game_list:
        messagebox.showerror("Error", "No games available")
        return

    # Create selection dialog
    selected_games = []
    for appid, game_name in game_list:
        result = messagebox.askyesno("Select Game", f"Download {game_name} (ID: {appid})?")
        if result:
            selected_games.append(appid)

    if not selected_games:
        messagebox.showinfo("Info", "No games selected")
        return

    # Process selected games
    failed = []
    for i, appid in enumerate(selected_games, 1):
        game_data = games_db[appid]
        game_name = game_data['name']
        download_url = game_data['download_url']
        
        print(f"\n[{i}/{len(selected_games)}] Processing {game_name} (ID: {appid})")
        
        # Download from the game's URL
        folder_path = download_link(download_url, MAIN_FOLDER)
        if not folder_path:
            failed.append(f"{game_name} ({appid})")
        else:
            # Rename folder to game name instead of folder ID
            new_folder_path = os.path.join(MAIN_FOLDER, game_name.replace(" ", "_").replace("/", "_"))
            if os.path.exists(folder_path) and not os.path.exists(new_folder_path):
                os.rename(folder_path, new_folder_path)
                print(f"[SUCCESS] Game saved: {new_folder_path}")

    messagebox.showinfo("Done", f"Finished processing {len(selected_games)} games.\nFailed: {len(failed)}")
    if failed:
        print("Failed games:\n" + "\n".join(failed))

# ---------------- MAIN WINDOW ----------------
root = tk.Tk()
root.title("Game Downloader")
root.geometry("400x200")

label = tk.Label(root, text="Download games from Google Drive links", font=("Arial", 12))
label.pack(pady=20)

btn = tk.Button(root, text="Start Download", command=start_download, width=20, height=2)
btn.pack(pady=20)

root.mainloop()


