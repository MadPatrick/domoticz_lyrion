"""
<plugin key="LogitechMediaServer" name="Logitech Media Server (extended)" author="MadPatrick" version="1.4.4" wikilink="https://github.com/Logitech/slimserver" externallink="https://mysqueezebox.com">
    <description>
        <h2>Logitech Media Server Plugin - Extended</h2>
        Detecteert spelers, maakt devices aan en biedt:
        - Power / Play / Pause / Stop
        - Volume (Dimmer)
        - Track info (Text)
        - Playlists (Selector per speler)
        - Sync / Unsync
        - Display text (via Actions device)
    </description>
    <params>
        <param field="Address" label="Server IP" width="200px" required="true" default="127.0.0.1"/>
        <param field="Port" label="Port" width="100px" required="true" default="9000"/>
        <param field="Username" label="Username" width="150px"/>
        <param field="Password" label="Password" width="150px" password="true"/>
        <param field="Mode1" label="Polling interval (sec)" width="100px" default="30"/>
        <param field="Mode2" label="Max playlists to expose" width="100px" default="50"/>
    </params>
</plugin>
"""

import Domoticz
import requests
import time


class LMSPlugin:
    def __init__(self):
        self.url = ""
        self.auth = None
        self.pollInterval = 30
        self.nextPoll = 0
        self.players = []
        self.playlists = []
        self.max_playlists = 50
        self.created_players = set()  # <-- nieuw: onthoud welke spelers devices hebben

    def onStart(self):
        Domoticz.Log("LMS Plugin started.")
        self.pollInterval = int(Parameters.get("Mode1", 30))
        self.max_playlists = int(Parameters.get("Mode2", 50))
        self.url = f"http://{Parameters.get('Address', '127.0.0.1')}:{Parameters.get('Port', '9000')}/jsonrpc.js"
        username = Parameters.get("Username", "")
        password = Parameters.get("Password", "")
        self.auth = (username, password) if username else None
        Domoticz.Heartbeat(10)
        self.nextPoll = time.time() + 10
        Domoticz.Log("LMS Plugin initialization delayed until first heartbeat.")

    def onStop(self):
        Domoticz.Log("LMS Plugin stopped.")

    def onHeartbeat(self):
        if time.time() >= self.nextPoll:
            self.nextPoll = time.time() + self.pollInterval
            self.updateEverything()

    # ---- JSON helpers ----
    def lms_query_raw(self, player, cmd_array):
        data = {"id": 1, "method": "slim.request", "params": [player, cmd_array]}
        try:
            r = requests.post(self.url, json=data, auth=self.auth, timeout=6)
            r.raise_for_status()
            return r.json().get("result", {})
        except Exception as e:
            Domoticz.Error(f"LMS query error: {e}")
            return None

    def get_serverstatus(self):
        return self.lms_query_raw("", ["serverstatus", 0, 999])

    def get_status(self, playerid, tags="tags:adclmntyK"):
        return self.lms_query_raw(playerid, ["status", "-", 1, tags])

    def get_playlists(self):
        return self.lms_query_raw("", ["playlists", 0, 999])

    def send_playercmd(self, playerid, cmd_array):
        return self.lms_query_raw(playerid, cmd_array)

    def send_button(self, playerid, button):
        return self.send_playercmd(playerid, ["button", button])

    # ---- Devices ----
    def devices_exist(self, name):
        """Controleer of er al devices bestaan voor deze speler"""
        for d in Devices.values():
            if d.Name.startswith(name):
                return True
        return False

    def create_player_devices(self, name):
        """Maak speler-devices alleen als ze nog niet bestaan"""
        if self.devices_exist(name) or name in self.created_players:
            return  # <-- voorkom herhaald aanmaken
        base = 1
        while base in Devices:
            base += 10
        Domoticz.Device(Name=name, Unit=base, TypeName="Selector Switch",
                        Switchtype=18, Options={"LevelNames": "Off|Pause|Play|Stop", "SelectorStyle": "0"}).Create()
        Domoticz.Device(Name=f"{name} Volume", Unit=base + 1, TypeName="Dimmer").Create()
        Domoticz.Device(Name=f"{name} Track", Unit=base + 2, TypeName="Text").Create()
        Domoticz.Device(Name=f"{name} Actions", Unit=base + 3, TypeName="Selector Switch",
                        Switchtype=18, Options={"LevelNames": "None|SendText|Sync to this|Unsync", "SelectorStyle": "0"}).Create()
        Domoticz.Device(Name=f"{name} Playlists", Unit=base + 4, TypeName="Selector Switch",
                        Switchtype=18, Options={"LevelNames": "Loading...", "SelectorStyle": "0"}).Create()
        self.created_players.add(name)
        Domoticz.Log(f"Created devices for {name}")

    def reload_playlists(self):
        root = self.get_playlists()
        if not root:
            self.playlists = []
            return
        pl = root.get("playlists_loop", [])
        self.playlists = [{"id": p.get("id"), "playlist": p.get("playlist", "")} for p in pl[:self.max_playlists]]

    # ---- Update loop ----
    def updateEverything(self):
        server = self.get_serverstatus()
        if not server:
            Domoticz.Error("No response from LMS server.")
            return

        self.players = server.get("players_loop", [])
        self.reload_playlists()

        for p in self.players:
            name, mac = p.get("name", "Unknown"), p.get("playerid", "")
            if not mac or not name:
                continue
            self.create_player_devices(name)

            # Zoek bestaande devices
            devs = {d.Name: d for d in Devices.values() if d.Name.startswith(name)}
            st = self.get_status(mac) or {}
            power = int(st.get("power", 0))
            mode = st.get("mode", "stop")
            vol = int(st.get("mixer volume", 0))
            sel = {"pause": 10, "play": 20, "stop": 30}.get(mode, 0)
            if power == 0:
                sel = 0

            # Update devices
            for d in devs.values():
                if "Volume" in d.Name:
                    d.Update(nValue=1 if power else 0, sValue=str(vol))
                elif "Track" in d.Name:
                    title = st.get("title") or st.get("current_title") or "(onbekend nummer)"
                    artist = st.get("artist") or ""
                    album = st.get("album") or ""
                    for key in ("remote_meta", "remoteMeta"):
                        if key in st:
                            meta = st[key]
                            title = meta.get("title", title)
                            artist = meta.get("artist", artist)
                            album = meta.get("album", album)
                    label = f"{title}"
                    if artist:
                        label += f" - {artist}"
                    if album:
                        label += f" ({album})"
                    label = label.replace("??", "").strip()
                    if d.sValue != label:
                        d.Update(nValue=0, sValue=label)
                        Domoticz.Log(f"Logitech Media Server: ({name}) Playing - '{artist} - {title}'")
                elif "Playlists" in d.Name:
                    levelnames = "|".join([p["playlist"] for p in self.playlists]) if self.playlists else "No playlists"
                    opts = {"LevelNames": levelnames, "SelectorStyle": "0"}
                    d.Options = opts
                    d.Update(nValue=0, sValue="0")
                elif "Actions" not in d.Name:
                    d.Update(nValue=power, sValue=str(sel))

    # ---- Commands ----
    def onCommand(self, Unit, Command, Level, Hue):
        Domoticz.Log(f"onCommand Unit={Unit} Command={Command} Level={Level}")
        devname = Devices[Unit].Name
        name = devname.split(" Volume")[0].split(" Track")[0].split(" Actions")[0].split(" Playlists")[0]
        player = next((p for p in self.players if p.get("name") == name), None)
        if not player:
            return
        mac = player.get("playerid")

        if "Volume" not in devname and "Playlist" not in devname and "Action" not in devname:
            if Command == "On":
                self.send_playercmd(mac, ["power", "1"])
            elif Command == "Off":
                self.send_playercmd(mac, ["power", "0"])
            elif Command == "Set Level":
                btn = {10: "pause.single", 20: "play.single", 30: "stop"}.get(Level, "stop")
                self.send_button(mac, btn)
            return

        if "Volume" in devname and Command == "Set Level":
            self.send_playercmd(mac, ["mixer", "volume", str(Level)])
            return

        if "Playlists" in devname and Command == "Set Level":
            idx = int(Level) - 1
            if 0 <= idx < len(self.playlists):
                pl = self.playlists[idx]
                self.send_playercmd(mac, ["playlist", "play", pl["playlist"]])
                Domoticz.Log(f"Started playlist '{pl['playlist']}' on {name}")
            return


_plugin = LMSPlugin()

def onStart(): _plugin.onStart()
def onStop(): _plugin.onStop()
def onHeartbeat(): _plugin.onHeartbeat()
def onCommand(Unit, Command, Level, Hue): _plugin.onCommand(Unit, Command, Level, Hue)
