"""
<plugin key="LyrionMediaServer" name="Lyrion Media Server" author="MadPatrick" version="1.5.0" wikilink="https://lyrion.org" externallink="https://github.com/MadPatrick/domoticz_LMS">
    <description>
        <h2>Lyrion Media Server Plugin - Extended</h2>
        <p>Version 1.5.0</p>
        <p>Detects players, creates devices, and provides:</p>
        <ul>
            <li>Power / Play / Pause / Stop</li>
            <li>Volume (Dimmer)</li>
            <li>Track info (Text)</li>
            <li>Playlists (Selector)</li>
            <li>Sync / Unsync</li>
            <li>Display text (via Actions device)</li>
            <li>Shuffle (Selector)</li>
            <li>Repeat (Selector)</li>
        </ul>
    </description>    
    <params>
        <param field="Address" label="Server IP" width="200px" required="true" default="192.168.1.6"/>
        <param field="Port" label="Port" width="100px" required="true" default="9000"/>
        <param field="Username" label="Username" width="150px"/>
        <param field="Password" label="Password" width="150px" password="true"/>
        <param field="Mode1" label="Polling interval (sec)" width="100px" default="10"/>
        <param field="Mode2" label="Max playlists to expose" width="100px" default="10"/>
        <param field="Mode3" label="Debug logging" width="100px" default="No">
            <options>
                <option label="No" value="False"/>
                <option label="Yes" value="True"/>
            </options>
        </param>
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
        self.imageID = 0
        self.debug = False

    def onStart(self):
        Domoticz.Log("LMS plugin started (v1.5.0.")

        _IMAGE = "lms"

        if _IMAGE not in Images:
            Domoticz.Log(f"LMS icons not found, loading { _IMAGE }.zip ...")
            Domoticz.Image(f"{_IMAGE}.zip").Create()

        if _IMAGE in Images:
            self.imageID = Images[_IMAGE].ID
            Domoticz.Log(f"LMS icons loaded (ImageID={self.imageID})")
        else:
            Domoticz.Error(f"Unable to load LMS icon pack '{_IMAGE}.zip'!")

        self.pollInterval = int(Parameters.get("Mode1", 30))
        self.max_playlists = int(Parameters.get("Mode2", 50))
        self.debug = Parameters.get("Mode3", "False").lower() == "true"

        if self.debug:
            Domoticz.Log("Debug logging enabled.")

        self.url = f"http://{Parameters['Address']}:{Parameters['Port']}/jsonrpc.js"
        username = Parameters.get("Username", "")
        password = Parameters.get("Password", "")
        self.auth = (username, password) if username else None

        Domoticz.Heartbeat(10)
        self.nextPoll = time.time() + 10
        Domoticz.Log("LMS plugin initialization delayed until first heartbeat.")

    def onStop(self):
        Domoticz.Log("LMS plugin stopped.")

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
            r = requests.post(self.url, json=data, auth=self.auth, timeout=10)
            r.raise_for_status()
            result = r.json().get("result")
            if self.debug:
                Domoticz.Log(f"DEBUG LMS Query: player={player}, command={cmd_array}, result={result}")
            return result
        except Exception as e:
            Domoticz.Error(f"LMS query error ({player}): {e}")
            return None

    def get_serverstatus(self):
        return self.lms_query_raw("", ["serverstatus", 0, 999])

    def get_status(self, playerid):
        return self.lms_query_raw(playerid, ["status", "-", 1, "tags:adclmntyK"])

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
        Domoticz.Log(f"{playerid}: Display text sent ({s1} / {s2})")

    # -------------------------
    # Device helpers
    # -------------------------
    def create_player_devices(self, name, mac):
        base = name
        unit = 1
        while unit in Devices:
            unit += 10

        opts_main = {
            "LevelNames": "Off|Pause|Play|Stop",
            "LevelActions": "||||",
            "SelectorStyle": "0"
        }

        Domoticz.Device(
            Name=base,
            Unit=unit,
            TypeName="Selector Switch",
            Switchtype=18,
            Options=opts_main,
            Image=self.imageID,
            Description=mac,
            Used=1
        ).Create()

        Domoticz.Device(
            Name=f"{base} Volume",
            Unit=unit + 1,
            Type=244,
            Subtype=73,
            Switchtype=7,
            Description=mac,
            Used=1
        ).Create()

        Domoticz.Device(
            Name=f"{base} Track",
            Unit=unit + 2,
            TypeName="Text",
            Image=self.imageID,
            Description=mac,
            Used=1
        ).Create()

        opts_act = {
            "LevelNames": "None|SendText|Sync to this|Unsync",
            "LevelActions": "||",
            "SelectorStyle": "0"
        }

        Domoticz.Device(
            Name=f"{base} Actions",
            Unit=unit + 3,
            TypeName="Selector Switch",
            Switchtype=18,
            Options=opts_act,
            Image=self.imageID,
            Description=mac,
            Used=1
        ).Create()

        opts_shuffle = {
            "LevelNames": "Off|Songs|Albums",
            "LevelActions": "||",
            "SelectorStyle": "0"
        }

        Domoticz.Device(
            Name=f"{base} Shuffle",
            Unit=unit + 4,
            TypeName="Selector Switch",
            Switchtype=18,
            Options=opts_shuffle,
            Image=self.imageID,
            Description=mac,
            Used=1
        ).Create()

        opts_repeat = {
            "LevelNames": "Off|Track|Playlist",
            "LevelActions": "||",
            "SelectorStyle": "0"
        }

        Domoticz.Device(
            Name=f"{base} Repeat",
            Unit=unit + 5,
            TypeName="Selector Switch",
            Switchtype=18,
            Options=opts_repeat,
            Image=self.imageID,
            Description=mac,
            Used=1
        ).Create()

        Domoticz.Log(f"Devices created for '{name}' (Used=1)")
        return (unit, unit + 1, unit + 2, unit + 3, unit + 4, unit + 5)

    def find_player_devices(self, mac):
        main = vol = text = actions = shuffle = repeat = None
        for uid, dev in Devices.items():
            if dev.Description == mac:
                if dev.Name.endswith("Volume"):
                    vol = uid
                elif dev.Name.endswith("Track"):
                    text = uid
                elif dev.Name.endswith("Actions"):
                    actions = uid
                elif dev.Name.endswith("Shuffle"):
                    shuffle = uid
                elif dev.Name.endswith("Repeat"):
                    repeat = uid
                else:
                    main = uid

        if main:
            return (main, vol, text, actions, shuffle, repeat)
        return None

    # -------------------------
    # Playlists
    # -------------------------
    def reload_playlists(self):
        root = self.get_playlists()
        if not root:
            self.playlists = []
        else:
            pl = root.get("playlists_loop", [])
            self.playlists = [{
                "id": p.get("id"),
                "playlist": p.get("playlist", ""),
                "refid": int(p.get("id", 0)) % 256
            } for p in pl[:self.max_playlists]]

        levelnames = "Select|" + "|".join(
            p["playlist"] for p in self.playlists
        ) if self.playlists else "Select|No playlists"

        opts = {
            "LevelNames": levelnames,
            "LevelActions": "",
            "SelectorStyle": "1"
        }

        if PLAYLISTS_DEVICE_UNIT not in Devices:
            Domoticz.Device(
                Name="LMS Playlists",
                Unit=PLAYLISTS_DEVICE_UNIT,
                TypeName="Selector Switch",
                Switchtype=18,
                Options=opts,
                Image=self.imageID,
                Used=1
            ).Create()
        else:
            dev = Devices[PLAYLISTS_DEVICE_UNIT]
            if dev.Options.get("LevelNames", "") != levelnames:
                dev.Update(nValue=0, sValue="0", Options=opts)

    def play_playlist_by_level(self, Level):

        if Level < 10:
            Domoticz.Log("Playlist selection: 'Select' chosen (no playlist started).")
            return

        idx = int(Level // 10) - 1

        if idx < 0 or idx >= len(self.playlists):
            Domoticz.Log(f"Playlist selection error: invalid index {idx}.")
            return

        pl = self.playlists[idx]
        playlist_name = pl["playlist"]

        Domoticz.Log(f"Playlist selected: {playlist_name}")

        self.start_playlist_on_first_player(playlist_name)

    def start_playlist_on_first_player(self, playlist_name):
        if not self.players:
            Domoticz.Log("Cannot start playlist: no LMS players online.")
            return

        first = self.players[0]
        mac = first.get("playerid")
        name = first.get("name", "Unknown")

        Domoticz.Log(f"Starting playlist '{playlist_name}' on player {name} ({mac})")

        # Clear queue first
        Domoticz.Log("Clearing current playlist queue...")
        self.send_playercmd(mac, ["playlist", "clear"])

        # Load playlist by name
        Domoticz.Log(f"Loading playlist '{playlist_name}'...")
        self.send_playercmd(mac, ["playlist", "load", playlist_name])

        # Start playback
        Domoticz.Log("Starting playback...")
        self.send_playercmd(mac, ["play"])

        self.nextPoll = time.time() + 1

    # -------------------------
    # Updating players
    # -------------------------
    def updateEverything(self):
        server = self.get_serverstatus()
        if not server:
            Domoticz.Error("LMS: No response from server")
            return

        self.players = server.get("players_loop", [])

        for p in self.players:
            name, mac = p.get("name", "Unknown"), p.get("playerid", "")
            if mac and not self.find_player_devices(mac):
                self.create_player_devices(name, mac)

        self.reload_playlists()

        for p in self.players:
            name, mac = p.get("name"), p.get("playerid")
            if not mac:
                continue

            devices = self.find_player_devices(mac)
            if not devices:
                continue

            main, vol, text, actions, shuffle, repeat = devices
            st = self.get_status(mac) or {}

            if self.debug:
                Domoticz.Log(f"DEBUG STATUS for {name} ({mac}): {st}")

            power = int(st.get("power", 0))
            mode = st.get("mode", "stop")

            sel_level = {"pause": 10, "play": 20, "stop": 30}.get(mode, 0)
            if power == 0:
                sel_level = 0

            if main in Devices:
                dev_main = Devices[main]
                new_n = 1 if power else 0
                new_s = str(sel_level)
                if dev_main.nValue != new_n or dev_main.sValue != new_s:
                    dev_main.Update(nValue=new_n, sValue=new_s)

            if vol in Devices:
                dev_vol = Devices[vol]

                try:
                    old_vol = int(dev_vol.sValue)
                except:
                    old_vol = 0

                raw_vol = st.get("mixer volume", None)

                mixer_vol_valid = False
                try:
                    mixer_vol = int(str(raw_vol))
                    mixer_vol_valid = True
                except:
                    mixer_vol = old_vol

                if mixer_vol_valid and mixer_vol != old_vol:
                    new_n = 1 if mixer_vol > 0 else 0
                    dev_vol.Update(nValue=new_n, sValue=str(mixer_vol))
                    if self.debug:
                        Domoticz.Log(f"DEBUG: Volume updated from LMS: {mixer_vol}")

            # ----- TEXT / TITLE / ARTIST / ALBUM -----
            if text in Devices:
                dev_text = Devices[text]

                # basisvelden
                title = (
                    st.get("title")
                    or st.get("current_title")
                    or st.get("track", "")
                    or ""
                )
                artist = st.get("artist") or ""
                album = st.get("album") or ""

                # fallback: playlist_loop[0]
                pl_loop = st.get("playlist_loop")
                if (not title) and isinstance(pl_loop, list) and len(pl_loop) > 0:
                    entry = pl_loop[0]
                    title = entry.get("title", title)
                    if not artist:
                        artist = entry.get("artist", artist)
                    if not album:
                        album = entry.get("album", album)

                if not title:
                    title = "(unknown track)"

                if power == 0:
                    label = " "
                elif mode == "play":
                    label = title
                    if artist:
                        label += f" - {artist}"
                elif mode == "pause":
                    label = title
                else:
                    label = "Stopped"

                label = label[:255]

                if dev_text.sValue != label:
                    dev_text.Update(nValue=0, sValue=label)
                    Domoticz.Log(f"Player '{name}' ({mac}) - Now playing: '{label}'")

            if shuffle in Devices:
                dev_shuffle = Devices[shuffle]
                try:
                    shuffle_state = int(st.get("playlist shuffle", 0))
                except:
                    shuffle_state = 0
                shuffle_level = shuffle_state * 10
                if dev_shuffle.sValue != str(shuffle_level) or dev_shuffle.nValue != 0:
                    dev_shuffle.Update(nValue=0, sValue=str(shuffle_level))

            if repeat in Devices:
                dev_repeat = Devices[repeat]
                try:
                    repeat_state = int(st.get("playlist repeat", 0))
                except:
                    repeat_state = 0
                repeat_level = repeat_state * 10
                if dev_repeat.sValue != str(repeat_level) or dev_repeat.nValue != 0:
                    dev_repeat.Update(nValue=0, sValue=str(repeat_level))

    # -------------------------
    # Commands
    # -------------------------
    def onCommand(self, Unit, Command, Level, Hue):

        if Unit not in Devices:
            return

        dev = Devices[Unit]
        devname = dev.Name
        mac = dev.Description

        if self.debug:
            Domoticz.Log(f"DEBUG onCommand: Unit={Unit}, Name={devname}, Command={Command}, Level={Level}, mac={mac}")

        if Unit == PLAYLISTS_DEVICE_UNIT and Command == "Set Level":
            Domoticz.Log(f"Playlist selector level: {Level}")
            self.play_playlist_by_level(Level)
            return

        if "Shuffle" in devname:
            if Command == "Set Level":
                mode = int(Level // 10)
            elif Command == "Off":
                mode = 0
                Level = 0
            else:
                return
            self.send_playercmd(mac, ["playlist", "shuffle", str(mode)])
            dev.Update(nValue=0, sValue=str(Level))
            Domoticz.Log(f"Shuffle set to {mode} for player {devname}")
            return

        if "Repeat" in devname:
            if Command == "Set Level":
                mode = int(Level // 10)
            elif Command == "Off":
                mode = 0
                Level = 0
            else:
                return
            self.send_playercmd(mac, ["playlist", "repeat", str(mode)])
            dev.Update(nValue=0, sValue=str(Level))
            Domoticz.Log(f"Repeat set to {mode} for player {devname}")
            return

        if Command in ["On", "Off"] and not any(
            x in devname for x in ("Volume", "Track", "Actions", "Shuffle", "Repeat")
        ):
            self.send_playercmd(mac, ["power", "1" if Command == "On" else "0"])
            dev.Update(nValue=1 if Command == "On" else 0, sValue="")
            Domoticz.Log(f"Power {Command} sent to {devname}")
            return

        if "Volume" in devname and Command == "Set Level":
            self.send_playercmd(mac, ["mixer", "volume", str(Level)])
            new_n = 1 if Level > 0 else 0
            dev.Update(nValue=new_n, sValue=str(Level))
            Domoticz.Log(f"Volume set to {Level}% for {devname}")
            return

        if Command == "Set Level":
            btn_cmd = {10: "pause.single", 20: "play.single", 30: "stop"}.get(Level)
            if btn_cmd:
                self.send_button(mac, btn_cmd)
                dev.Update(nValue=1, sValue=str(Level))
                Domoticz.Log(f"Command '{btn_cmd}' sent to {devname}")


# -------------------------
# Plugin instantiation
# -------------------------
_plugin = LMSPlugin()

def onStart():
    _plugin.onStart()

def onStop():
    _plugin.onStop()

def onHeartbeat():
    _plugin.onHeartbeat()

def onCommand(Unit, Command, Level, Hue):
    _plugin.onCommand(Unit, Command, Level, Hue)
