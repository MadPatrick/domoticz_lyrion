"""
<plugin key="LogitechMediaServer" name="Logitech Media Server (extended)" author="You" version="1.3.1" wikilink="https://github.com/Logitech/slimserver" externallink="https://mysqueezebox.com">
    <description>
        <h2>Logitech Media Server Plugin - Extended</h2>
        Detecteert spelers, maakt devices aan en biedt:
        - Power / Play / Pause / Stop
        - Volume (Dimmer)
        - Track info (Text)
        - Playlists (Selector)
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

PLAYLISTS_DEVICE_UNIT = 250


class LMSPlugin:
    def __init__(self):
        self.url = ""
        self.auth = None
        self.pollInterval = 30
        self.nextPoll = 0
        self.players = []
        self.playlists = []
        self.max_playlists = 50

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

    # -------------------------
    # LMS JSON-RPC helpers
    # -------------------------
    def lms_query_raw(self, player, cmd_array):
        data = {"id": 1, "method": "slim.request", "params": [player, cmd_array]}
        try:
            r = requests.post(self.url, json=data, auth=self.auth, timeout=6)
            r.raise_for_status()
            j = r.json()
            return j.get("result", j)
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

    def send_display_text(self, playerid, subject, text, duration=5):
        if not playerid or not text:
            return
        s1 = str(subject)[:64].replace('"', "'")
        s2 = str(text)[:128].replace('"', "'")
        cmd = ["show", f"line1:{s1}", f"line2:{s2}", f"duration:{duration}", "brightness:4", "font:huge"]
        self.send_playercmd(playerid, cmd)
        Domoticz.Log(f"Display text sent to {playerid}: {s1} / {s2}")

    # -------------------------
    # Device helpers
    # -------------------------
    def friendly_dev_name(self, name, mac):
        return f"{name} [{mac}]"

    def find_player_devices(self, mac):
        for u, dev in Devices.items():
            if dev.Name.endswith(f"[{mac}]"):
                baseprefix = dev.Name[:-(len(mac) + 3)]
                main = vol = text = actions = None
                for uid, d in Devices.items():
                    if d.Name.startswith(baseprefix) and d.Name.endswith(f"[{mac}]"):
                        if "Volume" in d.Name:
                            vol = uid
                        elif "Track" in d.Name:
                            text = uid
                        elif "Actions" in d.Name:
                            actions = uid
                        else:
                            main = uid
                return (main, vol, text, actions)
        return None

    def create_player_devices(self, name, mac):
        friendly = self.friendly_dev_name(name, mac)
        unit = 1
        while unit in Devices:
            unit += 10
        opts_main = {"LevelNames": "Off|Pause|Play|Stop", "LevelActions": "||||", "SelectorStyle": "0"}
        Domoticz.Device(Name=friendly, Unit=unit, TypeName="Selector Switch", Switchtype=18, Options=opts_main).Create()
        Domoticz.Device(Name=f"{name} Volume [{mac}]", Unit=unit + 1, TypeName="Dimmer").Create()
        Domoticz.Device(Name=f"{name} Track [{mac}]", Unit=unit + 2, TypeName="Text").Create()
        opts_act = {"LevelNames": "None|SendText|Sync to this|Unsync", "LevelActions": "||", "SelectorStyle": "0"}
        Domoticz.Device(Name=f"{name} Actions [{mac}]", Unit=unit + 3, TypeName="Selector Switch", Switchtype=18, Options=opts_act).Create()
        Domoticz.Log(f"Created devices for {name}")
        return (unit, unit + 1, unit + 2, unit + 3)

    # -------------------------
    # Updating players & playlists
    # -------------------------
    def reload_playlists(self):
        root = self.get_playlists()
        if not root:
            self.playlists = []
            return
        pl = root.get("playlists_loop", [])
        self.playlists = [{"id": p.get("id"), "playlist": p.get("playlist", ""), "refid": int(p.get("id", 0)) % 256} for p in pl[:self.max_playlists]]

        if PLAYLISTS_DEVICE_UNIT not in Devices:
            levelnames = "|".join([p["playlist"] for p in self.playlists]) if self.playlists else "No playlists"
            opts = {"LevelNames": levelnames, "LevelActions": "||", "SelectorStyle": "0"}
            Domoticz.Device(Name="LMS Playlists", Unit=PLAYLISTS_DEVICE_UNIT, TypeName="Selector Switch", Switchtype=18, Options=opts).Create()
            Domoticz.Log(f"Created Playlists device unit {PLAYLISTS_DEVICE_UNIT}")

    def updateEverything(self):
        server = self.get_serverstatus()
        if not server:
            Domoticz.Error(f"No response from LMS server at {self.url}")
            return

        self.players = server.get("players_loop", [])
        for p in self.players:
            name, mac = p.get("name", "Unknown"), p.get("playerid", "")
            if not mac:
                continue
            if not self.find_player_devices(mac):
                self.create_player_devices(name, mac)

        self.reload_playlists()

        for p in self.players:
            name, mac = p.get("name", "Unknown"), p.get("playerid", "")
            if not mac:
                continue
            devices = self.find_player_devices(mac)
            if not devices:
                continue
            main, vol, text, actions = devices
            st = self.get_status(mac) or {}
            power = int(st.get("power", 0))
            mode = st.get("mode", "stop")
            mixer_vol = int(st.get("mixer volume", 0))
            sel_level = {"pause": 10, "play": 20, "stop": 30}.get(mode, 0)
            if power == 0:
                sel_level = 0

            if main in Devices:
                nval = 1 if power == 1 else 0
                sval = str(sel_level)
                if Devices[main].nValue != nval or Devices[main].sValue != sval:
                    Devices[main].Update(nValue=nval, sValue=sval)

            if vol in Devices:
                onoff = 1 if power == 1 else 0
                sval = str(mixer_vol if power == 1 else 0)
                if Devices[vol].nValue != onoff or Devices[vol].sValue != sval:
                    Devices[vol].Update(nValue=onoff, sValue=sval)

            # --- Track info / Now Playing display ---
            if text in Devices:
                title = st.get("title") or st.get("current_title") or "(onbekend nummer)"
                artist = st.get("artist") or ""
                album = st.get("album") or ""

                # radio metadata fallback
                for key in ("remote_meta", "remoteMeta"):
                    if key in st:
                        meta = st[key]
                        title = meta.get("title", title)
                        artist = meta.get("artist", artist)
                        album = meta.get("album", album)

                # stationnaam verwijderen
                if "current_title" in st:
                    station = st["current_title"]
                    if title.startswith(station):
                        title = title[len(station):].strip(" -")
                    title = title.replace("??", "").strip()

                # fallback splitsing
                if not artist and "-" in title:
                    parts = title.split("-", 1)
                    if len(parts) == 2:
                        artist, title = parts[0].strip(), parts[1].strip()

                if not title and "remote_title" in st:
                    title = st["remote_title"]
                if not title and "url" in st:
                    title = st["url"].split("/")[-1]

                if power == 0:
                    label = "Uit"
                elif mode == "play":
                    label = f"{title}"
                    if artist:
                        label += f" - {artist}"
                    if album:
                        label += f" ({album})"
                elif mode == "pause":
                    label = f"{title}"
                else:
                    label = "Gestopt"

                label = label[:255]
                if Devices[text].sValue != label:
                    Devices[text].Update(nValue=0, sValue=label)
                    artist_part = f"{artist} - " if artist else ""
                    title_part = title or "(onbekend nummer)"
                    Domoticz.Log(f"Logitech Media Server: ({name}) Playing - '{artist_part}{title_part}'")

    # -------------------------
    # Handling commands
    # -------------------------
    def onCommand(self, Unit, Command, Level, Hue):
        Domoticz.Log(f"onCommand Unit={Unit} Command={Command} Level={Level}")
        if Unit == PLAYLISTS_DEVICE_UNIT and Command == "Set Level" and self.playlists:
            idx = int(Level) - 1
            if 0 <= idx < len(self.playlists):
                target_mac = self.players[0].get("playerid") if self.players else None
                if target_mac:
                    pl = self.playlists[idx]
                    self.send_playercmd(target_mac, ["playlist", "play", pl["playlist"]])
                    self.nextPoll = time.time() + 1
            return

        if Unit in Devices:
            devname = Devices[Unit].Name
            mac = devname.split("[")[-1].strip("]") if "[" in devname else None
            if not mac:
                return
            if Command in ["On", "Off"]:
                desired = "1" if Command == "On" else "0"
                self.send_playercmd(mac, ["power", desired])
                Devices[Unit].Update(nValue=1 if Command == "On" else 0, sValue="20" if Command == "On" else "0")
                return
            if Command == "Set Level" and "Volume" not in devname:
                btn = {10: "pause.single", 20: "play.single", 30: "stop"}.get(Level, "stop")
                self.send_button(mac, btn)
                Devices[Unit].Update(nValue=1, sValue=str(Level))
                return
            if "Volume" in devname and Command == "Set Level":
                self.send_playercmd(mac, ["mixer", "volume", str(Level)])
                Devices[Unit].Update(nValue=1, sValue=str(Level))
                return


_plugin = LMSPlugin()

def onStart(): _plugin.onStart()
def onStop(): _plugin.onStop()
def onHeartbeat(): _plugin.onHeartbeat()
def onCommand(Unit, Command, Level, Hue): _plugin.onCommand(Unit, Command, Level, Hue)
