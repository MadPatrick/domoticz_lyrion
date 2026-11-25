# 🎵 Lyrion Music Server Plugin (Extended)
![Status](https://img.shields.io/badge/Status-Stable-brightgreen)
![Domoticz](https://img.shields.io/badge/Domoticz-2024%2B-blue)
![Python](https://img.shields.io/badge/Python-3.x-yellow)
![License](https://img.shields.io/badge/License-MIT-lightgrey)

This plugin provides full integration between **Lyrion Music Server (LMS)** and **Domoticz**.  
The plugin has been fully rewritten, extended, tested, and is stable on Domoticz 2024+.

---
### 🎛️ **LMS Remote Control**
- Power On/Off
- Play / Pause / Stop
- Volume control (dimmer)
- Shuffle (Off / Songs / Albums) and Repeat (Off / Playlist / Track)
- Sync all players to this player / Unsync
- Send a configurable text to the player display

### 📡 **Automatic Player Detection**
- Detects all connected LMS players automatically
- Creates a set of Domoticz devices for each player:
  Control, Volume, Track, Actions, Shuffle, Repeat, Playlists and Favorites

### 📊 **Player Information**
- Playback status
- Volume
- Current station, artist and title (Track text device)
- Notification when an LMS update is available

### 🎶 **Playlists and Favorites**
- Start one of the player's playlists from a selector
- Start one of the (global) LMS favorites from a selector

### 🧠 **JSON-RPC Communication**
- Uses the LMS `jsonrpc.js` interface
- Optional HTTPS: enter a full URL (e.g. `https://lms.example.com:9443`) as Server IP
- Note: over plain HTTP the username and password are sent unencrypted (Basic Auth). Use HTTPS through a reverse proxy if that matters on your network.

---

## 🛠️ Technical Improvements

- New plugin structure following Domoticz 2024+ standards
- Heartbeat fix (no crashes for missing functions)
- Faster player status parsing
- Reduced API requests → more efficient CPU usage
- Improved error handling + debug logging

---

## 📦 Installation

### Prerequisites

1. Domoticz 2024+ with Python plugin support enabled.
2. Python 3 with the matching Python development package for your system.
3. Install the required Python library:

```bash
sudo apt-get update
sudo apt-get install python3 python3-dev python3-requests
```

Or install the Python dependency with pip:

```bash
python3 -m pip install -r requirements.txt
```

`Domoticz` is provided by Domoticz and is not a pip dependency.

Clone the plugin into the Domoticz plugin folder:

```bash
cd /home/<user>/domoticz/plugins
git clone https://github.com/MadPatrick/domoticz_lyrion.git lyrion
sudo systemctl restart domoticz

```
