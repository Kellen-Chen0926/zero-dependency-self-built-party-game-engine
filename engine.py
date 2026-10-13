#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""party-engine —— 自建聚会游戏引擎（壳通用层）。

把狼人杀、阿瓦隆两代游戏里验证过的「壳」抽成这一份，游戏侧只写「核」。

壳（本文件负责，游戏不用管）：
- ThreadingHTTPServer + threading.Lock + 前端轮询
- 房间管理：create / join / start / restart / force / disband / leave
- 通用动作入口 /api/action（body.action 分发到游戏核）
- 公共聊天 /api/chat
- 静态文件服务（含音频格式嗅探）+ 防缓存头
- 通用 player_view 公共字段 + 两段式确认/全员门（投票集合模式）

核（游戏侧写一个 Game 子类，覆盖钩子）：
- config_for(player_count)   人数 -> 角色/阵营配置
- setup(room)                发牌、初始化核字段、进入首个阶段
- on_action(room, pid, action, body)   处理该游戏的自定义动作
- phase_view(room, p, view)  往 poll 结果里塞该游戏阶段特有字段
- tick(room)                 时间推进（倒计时等）
- force(room)                房主强制推进
- reset(room)                重开时清理核字段

一个游戏 = 一个 Game 子类 + 一个静态前端 index.html，几十行核逻辑即可跑起来。
"""
import json
import os
import random
import string
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs


def gen_id(n=4):
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=n))


def find_player(room, pid):
    for p in room["players"]:
        if p["id"] == pid:
            return p
    return None


def add_log(room, msg):
    room["log"].append({"msg": msg})
    room["log"] = room["log"][-80:]


def active_players(room):
    """未离开的玩家（阿瓦隆用 left 标记中途离开，狼人杀用 alive）。"""
    return [p for p in room["players"] if not p.get("left")]


class Game:
    """一个游戏的规则定义。子类必须给 name 并覆盖下面带 raise 的钩子。"""

    name = "游戏"
    min_players = 3
    max_players = 10
    port = 8099
    actions = set()   # 游戏支持的动作名，自动注册为 /api/<action> 路径

    # 子类覆盖区 ----------------------------------------------------------
    def config_for(self, player_count):
        """返回该人数的角色配置（dict/list 任意结构，setup 时自己消费）。"""
        raise NotImplementedError

    def options_from_body(self, body):
        """从 create 请求体里提取该游戏的自定义选项 dict（默认取 options 字段）。"""
        return body.get("options") or {}

    def new_room_state(self, room):
        """在房间 dict 里追加核特有字段（可选）。"""
        pass

    def setup(self, room):
        """发牌 + 初始化 + 进入首个阶段。"""
        raise NotImplementedError

    def on_action(self, room, pid, action, body):
        """处理自定义动作。游戏侧自行校验阶段与身份。"""
        pass

    def phase_view(self, room, p, view):
        """往 poll 的 view 里塞该游戏阶段特有字段（可选）。"""
        pass

    def tick(self, room):
        """时间推进钩子（可选）。"""
        pass

    def force(self, room):
        """房主强制推进钩子（可选）。"""
        pass

    def reset(self, room):
        """重开时清理核特有字段（可选）。"""
        pass

    def can_chat(self, room, p):
        """返回该玩家此刻能否发公共聊天（默认都能）。"""
        return True

    def on_chat(self, room, pid, text):
        """游戏自定义聊天去向（默认写公共 chat）。阵营密聊可在此分流。"""
        p = find_player(room, pid)
        room["chat"].append({"name": p["name"], "text": text})
        room["chat"] = room["chat"][-100:]

    def on_leave(self, room, p):
        """游戏进行中玩家退出的处理（默认标记 left，可刷新回来）。"""
        p["left"] = True
        add_log(room, "%s 暂时离开（可刷新回来，房主可推进跳过）" % p["name"])

    # 通用工具（游戏核可直接用） ------------------------------------------
    def deal(self, room, role_list):
        """把 role_list 洗牌发到每个玩家，写 seat 和 role。"""
        roles = list(role_list)
        random.shuffle(roles)
        random.shuffle(room["players"])
        for i, p in enumerate(room["players"]):
            p["seat"] = i
            p["role"] = roles[i]
            p["left"] = False
            p["alive"] = True

    def winner(self, room, winner):
        room["phase"] = "over"
        room["winner"] = winner


class Engine:
    """壳：HTTP 服务 + 房间 + 轮询 + 通用动作。绑定一个 Game 实例。"""

    def __init__(self, game, base_dir):
        self.game = game
        self.base_dir = base_dir
        self.rooms = {}
        self.lock = threading.Lock()

    # ---- 房间 ----
    def new_room(self, player_count, password, host_name, options, client_id=""):
        host = {"id": gen_id(8), "name": host_name, "role": None, "seat": 0,
                "left": False, "alive": True, "client_id": client_id}
        room = {
            "id": gen_id(4),
            "password": password or "",
            "player_count": player_count,
            "config": self.game.config_for(player_count),
            "phase": "waiting",
            "players": [host],
            "host_pid": host["id"],
            "log": [],
            "chat": [],
            "winner": None,
            "options": options,
        }
        self.game.new_room_state(room)
        self.rooms[room["id"]] = room
        add_log(room, "%s 创建了房间（%d 人局）" % (host_name, player_count))
        return room

    def start_game(self, room):
        self.game.setup(room)
        room["log"] = []
        room["chat"] = []
        add_log(room, "游戏开始")

    # ---- view ----
    def player_view(self, room, pid):
        p = find_player(room, pid)
        if not p:
            return None
        phase = room["phase"]
        others = []
        for x in room["players"]:
            others.append({
                "id": x["id"], "name": x["name"], "seat": x["seat"],
                "role": x.get("role") if phase == "over" else None,
                "left": x.get("left", False), "alive": x.get("alive", True),
                "out": x.get("out", False),
            })
        view = {
            "room_id": room["id"],
            "password": room["password"],
            "phase": phase,
            "player_count": room["player_count"],
            "me": {"id": p["id"], "name": p["name"], "role": p.get("role"),
                   "seat": p.get("seat"), "is_host": room["host_pid"] == pid,
                   "left": p.get("left", False), "alive": p.get("alive", True),
                   "out": p.get("out", False)},
            "players": others,
            "log": room["log"],
            "chat": room["chat"],
            "winner": room["winner"],
        }
        self.game.phase_view(room, p, view)
        return view

    # ---- 通用聊天 ----
    def chat(self, room, pid, text):
        p = find_player(room, pid)
        if not p:
            return
        text = (text or "").strip()[:200]
        if not text:
            return
        if not self.game.can_chat(room, p):
            return
        self.game.on_chat(room, pid, text)

    def run(self):
        engine = self

        class Handler(BaseHTTPRequestHandler):
            def _json(self, obj, code=200):
                data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _ok(self, **kw):
                self._json({"ok": True, **kw})

            def _err(self, msg, code=400):
                self._json({"ok": False, "error": msg}, code)

            def _read_body(self):
                try:
                    n = int(self.headers.get("Content-Length", 0))
                    return json.loads(self.rfile.read(n).decode("utf-8"))
                except Exception:
                    return {}

            def do_GET(self):
                parsed = urlparse(self.path)
                if parsed.path in ("/", "/index.html"):
                    fp = os.path.join(engine.base_dir, "static", "index.html")
                    try:
                        with open(fp, "rb") as f:
                            data = f.read()
                    except FileNotFoundError:
                        self._err("index.html 不存在", 404)
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                if parsed.path == "/api/poll":
                    q = parse_qs(parsed.query)
                    room_id = q.get("room", [""])[0].upper()
                    pid = q.get("pid", [""])[0]
                    with engine.lock:
                        room = engine.rooms.get(room_id)
                        if not room:
                            self._err("房间不存在", 404)
                            return
                        engine.game.tick(room)
                        view = engine.player_view(room, pid)
                        if not view:
                            self._err("玩家不存在", 404)
                            return
                    self._json(view)
                    return
                if parsed.path.startswith("/static/"):
                    static_dir = os.path.join(engine.base_dir, "static")
                    rel = parsed.path[len("/static/"):]
                    fp = os.path.normpath(os.path.join(static_dir, rel))
                    if not fp.startswith(static_dir + os.sep):
                        self._err("not found", 404)
                        return
                    try:
                        with open(fp, "rb") as f:
                            data = f.read()
                    except (FileNotFoundError, IsADirectoryError, OSError):
                        self._err("not found", 404)
                        return
                    magic = data[:12]
                    if magic.startswith(b"RIFF") and magic[8:12] == b"WAVE":
                        ctype = "audio/wav"
                    elif magic.startswith(b"ID3") or data[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
                        ctype = "audio/mpeg"
                    else:
                        ctype = "application/octet-stream"
                    self.send_response(200)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                self._err("not found", 404)

            def do_POST(self):
                parsed = urlparse(self.path)
                body = self._read_body()
                with engine.lock:
                    try:
                        self._route(parsed.path, body)
                    except Exception as e:
                        self._err(str(e), 400)

            def _route(self, path, b):
                g = engine.game
                if path == "/api/create":
                    name = (b.get("name") or "").strip()[:12]
                    if not name:
                        self._err("昵称不能为空"); return
                    pc = int(b.get("player_count") or 0)
                    if not (g.min_players <= pc <= g.max_players):
                        self._err("人数必须为 %d~%d" % (g.min_players, g.max_players)); return
                    password = (b.get("password") or "").strip()[:12]
                    cid = (b.get("client_id") or "").strip()[:32]
                    options = g.options_from_body(b)
                    room = engine.new_room(pc, password, name, options, cid)
                    self._ok(room_id=room["id"], pid=room["host_pid"])
                elif path == "/api/join":
                    room = self._room(b)
                    if room["phase"] != "waiting":
                        self._err("游戏已开始"); return
                    if room["password"] and b.get("password") != room["password"]:
                        self._err("密码错误"); return
                    cid = (b.get("client_id") or "").strip()[:32]
                    for x in room["players"]:
                        if cid and x.get("client_id") == cid:
                            self._ok(pid=x["id"], room_id=room["id"])
                            return
                    if len(room["players"]) >= room["player_count"]:
                        self._err("房间已满"); return
                    name = (b.get("name") or "").strip()[:12]
                    if not name:
                        self._err("昵称不能为空"); return
                    p = {"id": gen_id(8), "name": name, "role": None, "seat": 0,
                         "left": False, "alive": True, "client_id": cid}
                    room["players"].append(p)
                    add_log(room, "%s 加入了房间（%d/%d）" % (name, len(room["players"]), room["player_count"]))
                    self._ok(pid=p["id"], room_id=room["id"])
                elif path == "/api/start":
                    room = self._room(b)
                    if room["host_pid"] != b.get("pid"):
                        self._err("只有房主能开始"); return
                    if len(room["players"]) < room["player_count"]:
                        self._err("人数未满，无法开始"); return
                    engine.start_game(room)
                    self._ok()
                elif path == "/api/action":
                    room, p = self._room_player(b)
                    g.on_action(room, p["id"], b.get("action"), b)
                    self._ok()
                elif path == "/api/chat":
                    room, p = self._room_player(b)
                    engine.chat(room, p["id"], b.get("text"))
                    self._ok()
                elif path == "/api/restart":
                    room = self._room(b)
                    if room["host_pid"] != b.get("pid"):
                        self._err("只有房主能重开"); return
                    room["phase"] = "waiting"
                    for p in room["players"]:
                        p["role"] = None
                        p["seat"] = 0
                        p["left"] = False
                        p["alive"] = True
                    room["winner"] = None
                    room["chat"] = []
                    g.reset(room)
                    add_log(room, "已重置，等待重新开始")
                    self._ok()
                elif path == "/api/force":
                    room = self._room(b)
                    if room["host_pid"] != b.get("pid"):
                        self._err("只有房主能推进"); return
                    g.force(room)
                    self._ok()
                elif path == "/api/disband":
                    room = self._room(b)
                    if room["host_pid"] != b.get("pid"):
                        self._err("只有房主能解散"); return
                    del engine.rooms[room["id"]]
                    self._ok()
                elif path == "/api/leave":
                    room, p = self._room_player(b)
                    if room["host_pid"] == p["id"] and room["phase"] in ("waiting", "over"):
                        del engine.rooms[room["id"]]
                        self._ok()
                        return
                    if room["phase"] in ("waiting", "over"):
                        room["players"] = [x for x in room["players"] if x["id"] != p["id"]]
                        add_log(room, "%s 退出了房间" % p["name"])
                    else:
                        g.on_leave(room, p)
                    self._ok()
                else:
                    name = path[len("/api/"):] if path.startswith("/api/") else ""
                    if name in g.actions:
                        room, p = self._room_player(b)
                        g.on_action(room, p["id"], name, b)
                        self._ok()
                    else:
                        self._err("未知接口", 404)

            def _room(self, b):
                room = engine.rooms.get((b.get("room") or "").upper())
                if not room:
                    raise Exception("房间不存在")
                engine.game.tick(room)
                return room

            def _room_player(self, b):
                room = self._room(b)
                p = find_player(room, b.get("pid"))
                if not p:
                    raise Exception("玩家不存在")
                return room, p

            def log_message(self, *args):
                pass

        srv = ThreadingHTTPServer(("0.0.0.0", self.game.port), Handler)
        srv.daemon_threads = True
        print("%s 服务器已启动，端口 %d" % (self.game.name, self.game.port))
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass
